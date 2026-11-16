import datetime
import enum
import fnmatch
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import uuid
from typing import Any, Callable, Dict, List, Optional

from modules.audit import log_audit_event

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(PROJECT_ROOT, "data", "jarvis_memory.db")
SECRET_KEY_PATH = os.path.join(PROJECT_ROOT, "data", ".approval_hmac_key")

_APPROVAL_LOCK = threading.Lock()
_UI_PROMPT_CALLBACK: Optional[Callable[[Dict[str, Any]], bool]] = None


class AutonomyLevel(enum.IntEnum):
    L0_SUGGEST_ONLY = 0
    L1_ACT_AND_REPORT = 1
    L2_ACT_AND_NOTIFY = 2
    L3_AUTONOMOUS_POLICY = 3


class RiskTier(str, enum.Enum):
    SAFE = "safe"
    ELEVATED = "elevated"
    CRITICAL = "critical"


# Non-negotiable patterns requiring human approval even at L3
CREDENTIAL_PATTERNS = [
    ".env",
    "*.env",
    "*secret*",
    "*credential*",
    "*token*",
    "*password*",
    "*.pem",
    "*.key",
    "id_rsa*",
    "id_ed25519*",
]

DESTRUCTIVE_SHELL_PATTERNS = [
    r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f",
    r"\brm\s+-[a-zA-Z]*f[a-zA-Z]*r",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r":\(\)\s*\{\s*:\|:\s*&\s*\}\s*;",
    r"\bchmod\s+-R\s+777\b",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bsudo\b",
]


def _get_hmac_secret() -> bytes:
    """Loads or generates a local 256-bit HMAC key for signing approval tokens."""
    os.makedirs(os.path.dirname(os.path.abspath(SECRET_KEY_PATH)), exist_ok=True)
    if os.path.exists(SECRET_KEY_PATH):
        with open(SECRET_KEY_PATH, "rb") as f:
            data = f.read().strip()
            if len(data) >= 32:
                return data
    key = secrets.token_bytes(32)
    with open(SECRET_KEY_PATH, "wb") as f:
        f.write(key)
    try:
        os.chmod(SECRET_KEY_PATH, 0o600)
    except OSError:
        pass
    return key


def _get_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_approval_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """Creates autonomy_settings and approval_requests tables with safe defaults."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _APPROVAL_LOCK:
        with _get_connection(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS autonomy_settings (
                    source TEXT PRIMARY KEY CHECK(source IN ('user', 'self')),
                    level INTEGER NOT NULL CHECK(level BETWEEN 0 AND 3),
                    updated_at TEXT NOT NULL,
                    updated_by TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS approval_requests (
                    request_id TEXT PRIMARY KEY,
                    actor TEXT NOT NULL,
                    source TEXT NOT NULL,
                    action TEXT NOT NULL,
                    risk_tier TEXT NOT NULL CHECK(risk_tier IN ('safe', 'elevated', 'critical')),
                    target TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending', 'approved', 'denied', 'expired')),
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    resolved_by TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO autonomy_settings (source, level, updated_at, updated_by)
                VALUES (?, ?, ?, 'system_init')
                """,
                ('user', int(AutonomyLevel.L1_ACT_AND_REPORT), now),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO autonomy_settings (source, level, updated_at, updated_by)
                VALUES (?, ?, ?, 'system_init')
                """,
                ('self', int(AutonomyLevel.L0_SUGGEST_ONLY), now),
            )
            conn.commit()


def register_ui_prompt_callback(callback: Optional[Callable[[Dict[str, Any]], bool]]) -> None:
    """Registers the CustomTkinter modal confirmation handler from modules/ui.py."""
    global _UI_PROMPT_CALLBACK
    _UI_PROMPT_CALLBACK = callback


def get_autonomy_level(source: str, db_path: str = DEFAULT_DB_PATH) -> AutonomyLevel:
    """Retrieves the configured autonomy level (L0-L3) for 'user' or 'self'."""
    if source not in ("user", "self"):
        raise ValueError(f"Invalid goal source: {source!r}")
    init_approval_db(db_path)
    with _APPROVAL_LOCK:
        with _get_connection(db_path) as conn:
            row = conn.execute(
                "SELECT level FROM autonomy_settings WHERE source = ?", (source,)
            ).fetchone()
    if row is None:
        return AutonomyLevel.L0_SUGGEST_ONLY
    return AutonomyLevel(int(row["level"]))


def set_autonomy_level(
    source: str,
    level: int,
    actor: str = "user",
    db_path: str = DEFAULT_DB_PATH,
) -> Dict[str, Any]:
    """
    Adjusts the autonomy level for a goal source.
    SECURITY: Only 'user' may change autonomy levels. Autonomous actors are blocked.
    """
    if source not in ("user", "self"):
        raise ValueError(f"Invalid goal source: {source!r}")
    lvl = AutonomyLevel(int(level))

    if actor != "user":
        event_id = log_audit_event(
            actor=actor,
            action="autonomy_escalation_blocked",
            outcome="blocked",
            metadata={"source": source, "attempted_level": int(lvl)},
        )
        raise PermissionError(
            f"Actor {actor!r} is forbidden from modifying autonomy levels (audit: {event_id})."
        )

    init_approval_db(db_path)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _APPROVAL_LOCK:
        with _get_connection(db_path) as conn:
            conn.execute(
                """
                INSERT INTO autonomy_settings (source, level, updated_at, updated_by)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET
                    level = excluded.level,
                    updated_at = excluded.updated_at,
                    updated_by = excluded.updated_by
                """,
                (source, int(lvl), now, actor),
            )
            conn.commit()

    event_id = log_audit_event(
        actor=actor,
        action="autonomy_level_updated",
        outcome="success",
        metadata={"source": source, "new_level": int(lvl), "level_name": lvl.name},
    )
    return {"source": source, "level": int(lvl), "level_name": lvl.name, "audit_event_id": event_id}


def _is_outside_project(target_path: str) -> bool:
    abs_root = os.path.realpath(PROJECT_ROOT)
    candidate = (
        target_path if os.path.isabs(target_path) else os.path.join(abs_root, target_path)
    )
    real_target = os.path.realpath(candidate)
    try:
        rel = os.path.relpath(real_target, abs_root)
    except ValueError:
        return True
    return rel.startswith("..") or os.path.isabs(rel)


def _matches_credential_path(target_path: str) -> bool:
    basename = os.path.basename(target_path).lower()
    full_lower = target_path.lower()
    if "/.ssh/" in full_lower or "\\.ssh\\" in full_lower:
        return True
    for pat in CREDENTIAL_PATTERNS:
        if fnmatch.fnmatch(basename, pat):
            return True
    return False


def classify_action(
    action: str,
    target: str = "",
    actor: str = "user",
    is_protected_file: bool = False,
) -> Dict[str, Any]:
    """
    Classifies an action into RiskTier.SAFE, RiskTier.ELEVATED, or RiskTier.CRITICAL,
    and flags whether hard human approval is mandatory regardless of L3 status.
    """
    hard_human_required = False
    reasons: List[str] = []

    # 1. Check Critical conditions
    if action in ("file_write", "file_delete", "file_read") and target:
        if _is_outside_project(target):
            hard_human_required = True
            reasons.append("out_of_project_directory_access")
        if _matches_credential_path(target):
            hard_human_required = True
            reasons.append("credential_or_secret_access")

    if action == "shell_exec" and target:
        for regex in DESTRUCTIVE_SHELL_PATTERNS:
            if re.search(regex, target):
                hard_human_required = True
                reasons.append("destructive_shell_command")
                break

    if hard_human_required or action in ("credential_access", "out_of_project_write"):
        return {
            "risk_tier": RiskTier.CRITICAL,
            "hard_human_required": True,
            "reasons": reasons or [action],
        }

    # 2. Check Elevated conditions
    if action == "network_egress" and actor != "user":
        return {
            "risk_tier": RiskTier.ELEVATED,
            "hard_human_required": True,
            "reasons": ["autonomous_network_egress"],
        }

    if (
        is_protected_file
        or action in ("protected_file_route", "self_modification", "file_delete", "network_egress", "shell_exec")
    ):
        return {
            "risk_tier": RiskTier.ELEVATED,
            "hard_human_required": False,
            "reasons": [action],
        }

    # 3. Default: routine in-project workspace operations
    return {
        "risk_tier": RiskTier.SAFE,
        "hard_human_required": False,
        "reasons": ["routine_workspace_operation"],
    }


def issue_approval_token(
    action: str,
    target: str,
    actor: str,
    ttl_seconds: int = 300,
) -> str:
    """Issues a short-lived HMAC-SHA256 approval token bound to (action, target, actor)."""
    expires_at = int(datetime.datetime.now(datetime.timezone.utc).timestamp()) + ttl_seconds
    payload = f"{action}|{target}|{actor}|{expires_at}".encode("utf-8")
    sig = hmac.new(_get_hmac_secret(), payload, hashlib.sha256).hexdigest()
    return f"{expires_at}.{sig}"


def verify_approval_token(
    token: Optional[str],
    action: str,
    target: str,
    actor: str,
) -> bool:
    """Validates an HMAC approval token against expiry and bound action parameters."""
    if not token or "." not in token:
        return False
    exp_str, provided_sig = token.split(".", 1)
    try:
        expires_at = int(exp_str)
    except ValueError:
        return False

    now_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    if now_ts > expires_at:
        return False

    payload = f"{action}|{target}|{actor}|{expires_at}".encode("utf-8")
    expected_sig = hmac.new(_get_hmac_secret(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided_sig, expected_sig)


def check_approval(
    action: str,
    target: str = "",
    actor: str = "user",
    source: str = "user",
    reason: str = "",
    is_protected_file: bool = False,
    signed_token: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> Dict[str, Any]:
    """
    Central policy gate evaluated before executing state-mutating or sensitive actions.
    Returns {'approved': bool, 'risk_tier': str, 'autonomy_level': int, 'audit_event_id': str, ...}
    """
    init_approval_db(db_path)
    classification = classify_action(
        action=action,
        target=target,
        actor=actor,
        is_protected_file=is_protected_file,
    )
    risk: RiskTier = classification["risk_tier"]
    hard_human: bool = classification["hard_human_required"]
    level = get_autonomy_level(source, db_path=db_path)

    # Check if valid pre-signed token is provided
    if signed_token and verify_approval_token(signed_token, action, target, actor):
        event_id = log_audit_event(
            actor=actor,
            action="approval_gate",
            outcome="success",
            paths_touched=[target] if target else [],
            metadata={
                "decision": "approved_via_signed_token",
                "risk_tier": risk.value,
                "autonomy_level": int(level),
                "reason": reason,
            },
        )
        return {
            "approved": True,
            "method": "signed_token",
            "risk_tier": risk.value,
            "autonomy_level": int(level),
            "audit_event_id": event_id,
        }

    # L0 Suggest-Only blocks all execution
    if level == AutonomyLevel.L0_SUGGEST_ONLY:
        event_id = log_audit_event(
            actor=actor,
            action="approval_gate",
            outcome="blocked",
            paths_touched=[target] if target else [],
            metadata={
                "decision": "blocked_L0_suggest_only",
                "risk_tier": risk.value,
                "reason": reason,
            },
        )
        return {
            "approved": False,
            "method": "L0_suggest_only",
            "risk_tier": risk.value,
            "autonomy_level": int(level),
            "audit_event_id": event_id,
        }

    # Safe actions pass automatically at L1, L2, L3
    if risk == RiskTier.SAFE and not hard_human:
        event_id = log_audit_event(
            actor=actor,
            action="approval_gate",
            outcome="success",
            paths_touched=[target] if target else [],
            metadata={
                "decision": "auto_approved_safe",
                "risk_tier": risk.value,
                "autonomy_level": int(level),
            },
        )
        return {
            "approved": True,
            "method": "policy_safe",
            "risk_tier": risk.value,
            "autonomy_level": int(level),
            "audit_event_id": event_id,
        }

    # Elevated in-project actions pass autonomously ONLY at L3 when not hard-gated
    if risk == RiskTier.ELEVATED and not hard_human and level == AutonomyLevel.L3_AUTONOMOUS_POLICY:
        event_id = log_audit_event(
            actor=actor,
            action="approval_gate",
            outcome="success",
            paths_touched=[target] if target else [],
            metadata={
                "decision": "auto_approved_L3_policy",
                "risk_tier": risk.value,
                "autonomy_level": int(level),
                "reason": reason,
            },
        )
        return {
            "approved": True,
            "method": "L3_policy",
            "risk_tier": risk.value,
            "autonomy_level": int(level),
            "audit_event_id": event_id,
        }

    # Otherwise: Elevated (at L1/L2) or Critical / Hard-Gated -> Require UI Confirmation
    request_id = str(uuid.uuid4())
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    req_payload = {
        "request_id": request_id,
        "actor": actor,
        "source": source,
        "action": action,
        "risk_tier": risk.value,
        "target": target,
        "reason": reason,
        "reasons": classification["reasons"],
    }

    with _APPROVAL_LOCK:
        with _get_connection(db_path) as conn:
            conn.execute(
                """
                INSERT INTO approval_requests
                (request_id, actor, source, action, risk_tier, target, reason, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (request_id, actor, source, action, risk.value, target, reason, now),
            )
            conn.commit()

    approved_by_ui = False
    if _UI_PROMPT_CALLBACK is not None:
        try:
            approved_by_ui = bool(_UI_PROMPT_CALLBACK(req_payload))
        except Exception:
            approved_by_ui = False

    final_status = "approved" if approved_by_ui else "denied"
    resolved_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _APPROVAL_LOCK:
        with _get_connection(db_path) as conn:
            conn.execute(
                """
                UPDATE approval_requests
                SET status = ?, resolved_at = ?, resolved_by = ?
                WHERE request_id = ?
                """,
                (final_status, resolved_at, "ui_user" if _UI_PROMPT_CALLBACK else "no_ui_deny", request_id),
            )
            conn.commit()

    event_id = log_audit_event(
        actor=actor,
        action="approval_gate",
        outcome="success" if approved_by_ui else "blocked",
        paths_touched=[target] if target else [],
        metadata={
            "decision": f"ui_{final_status}",
            "request_id": request_id,
            "risk_tier": risk.value,
            "autonomy_level": int(level),
            "reasons": classification["reasons"],
        },
    )

    return {
        "approved": approved_by_ui,
        "method": "ui_confirmation",
        "request_id": request_id,
        "risk_tier": risk.value,
        "autonomy_level": int(level),
        "audit_event_id": event_id,
    }
