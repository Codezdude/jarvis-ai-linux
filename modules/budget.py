import datetime
import os
import sqlite3
import threading
from typing import Any, Callable, Dict, Optional

from modules.audit import log_audit_event
from modules.preferences import get_preference

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(PROJECT_ROOT, "data", "jarvis_memory.db")

_BUDGET_LOCK = threading.Lock()
_HUD_ALERT_CALLBACK: Optional[Callable[[Dict[str, Any]], None]] = None

DEFAULT_LIMITS: Dict[str, int] = {
    "budget.max_tokens_per_cycle": 32000,
    "budget.max_cycles_per_hour": 20,
    "budget.max_self_mods_per_day": 5,
    "budget.max_consecutive_failures": 3,
    "budget.cooldown_seconds": 900,
}


def register_budget_alert_callback(callback: Optional[Callable[[Dict[str, Any]], None]]) -> None:
    """Registers a HUD notification callback triggered when a budget limit or breaker trips."""
    global _HUD_ALERT_CALLBACK
    _HUD_ALERT_CALLBACK = callback


def _notify_hud(payload: Dict[str, Any]) -> None:
    if _HUD_ALERT_CALLBACK is not None:
        try:
            _HUD_ALERT_CALLBACK(payload)
        except Exception:
            pass


def _get_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_budget_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """Creates budget_events and circuit_state tables if they do not exist."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _BUDGET_LOCK:
        with _get_connection(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS budget_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL CHECK(event_type IN ('cycle', 'tokens', 'self_mod', 'failure', 'success')),
                    actor TEXT NOT NULL,
                    goal_id TEXT,
                    amount INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_budget_events_type_ts ON budget_events(event_type, timestamp)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS circuit_state (
                    breaker_name TEXT PRIMARY KEY,
                    is_paused INTEGER NOT NULL DEFAULT 0,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    paused_until TEXT,
                    trip_reason TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO circuit_state
                (breaker_name, is_paused, consecutive_failures, paused_until, trip_reason, updated_at)
                VALUES ('autonomous_worker', 0, 0, NULL, NULL, ?)
                """,
                (now,),
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS provider_telemetry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    date_utc TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT DEFAULT '',
                    prompt_tokens INTEGER DEFAULT 0,
                    completion_tokens INTEGER DEFAULT 0,
                    latency_ms REAL DEFAULT 0.0,
                    success INTEGER DEFAULT 1,
                    is_urgent INTEGER DEFAULT 1
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_telemetry_date_provider ON provider_telemetry(date_utc, provider)"
            )
            conn.commit()


DEFAULT_DAILY_CLOUD_TOKEN_CAP = int(os.getenv("JARVIS_DAILY_CLOUD_TOKEN_CAP", "250000"))
OFFLOAD_THRESHOLD_RATIO = 0.80


def record_provider_usage(
    provider: str,
    model: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    latency_ms: float = 0.0,
    success: bool = True,
    is_urgent: bool = True,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    """Persist a single provider call's token/latency telemetry."""
    init_budget_db(db_path)
    now = datetime.datetime.now(datetime.timezone.utc)
    with _BUDGET_LOCK:
        with _get_connection(db_path) as conn:
            conn.execute(
                """
                INSERT INTO provider_telemetry
                (timestamp, date_utc, provider, model, prompt_tokens, completion_tokens, latency_ms, success, is_urgent)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now.isoformat(),
                    now.strftime("%Y-%m-%d"),
                    provider.lower(),
                    model,
                    max(0, int(prompt_tokens)),
                    max(0, int(completion_tokens)),
                    round(float(latency_ms), 2),
                    1 if success else 0,
                    1 if is_urgent else 0,
                ),
            )
            conn.commit()


def get_daily_cloud_tokens(date_utc: Optional[str] = None, db_path: str = DEFAULT_DB_PATH) -> int:
    """Return total cloud (non-ollama) tokens used on the given UTC date."""
    init_budget_db(db_path)
    target = date_utc or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    with _get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(prompt_tokens + completion_tokens), 0) "
            "FROM provider_telemetry WHERE date_utc = ? AND provider != 'ollama'",
            (target,),
        ).fetchone()
        return int(row[0] or 0)


def should_offload_to_ollama(
    is_urgent: bool = True,
    daily_cap: int = DEFAULT_DAILY_CLOUD_TOKEN_CAP,
    threshold_ratio: float = OFFLOAD_THRESHOLD_RATIO,
    db_path: str = DEFAULT_DB_PATH,
) -> bool:
    """Return True when non-urgent requests should skip cloud and use local Ollama."""
    if is_urgent or daily_cap <= 0:
        return False
    used = get_daily_cloud_tokens(db_path=db_path)
    return used >= int(daily_cap * threshold_ratio)


def get_provider_telemetry_summary(
    date_utc: Optional[str] = None, db_path: str = DEFAULT_DB_PATH
) -> list:
    """Return per-provider aggregated usage for the given UTC date."""
    init_budget_db(db_path)
    target = date_utc or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    with _get_connection(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT provider,
                   COUNT(*) AS calls,
                   SUM(prompt_tokens) AS prompt_tokens,
                   SUM(completion_tokens) AS completion_tokens,
                   ROUND(AVG(latency_ms), 1) AS avg_latency_ms,
                   SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS errors
            FROM provider_telemetry
            WHERE date_utc = ?
            GROUP BY provider
            ORDER BY calls DESC
            """,
            (target,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_effective_limits(db_path: str = DEFAULT_DB_PATH) -> Dict[str, int]:
    """Resolves budget limits from user/AI preferences, falling back to DEFAULT_LIMITS."""
    limits: Dict[str, int] = {}
    for key, default_val in DEFAULT_LIMITS.items():
        val, _ = get_preference(key, default=default_val, db_path=db_path)
        try:
            limits[key] = int(val)
        except (TypeError, ValueError):
            limits[key] = default_val
    return limits


def _trip_breaker(
    conn: sqlite3.Connection,
    reason: str,
    cooldown_seconds: int,
    actor: str,
    goal_id: Optional[str] = None,
) -> Dict[str, Any]:
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    paused_until_dt = now_dt + datetime.timedelta(seconds=cooldown_seconds)
    now_iso = now_dt.isoformat()
    paused_until_iso = paused_until_dt.isoformat()

    conn.execute(
        """
        UPDATE circuit_state
        SET is_paused = 1,
            paused_until = ?,
            trip_reason = ?,
            updated_at = ?
        WHERE breaker_name = 'autonomous_worker'
        """,
        (paused_until_iso, reason, now_iso),
    )
    conn.commit()

    event_id = log_audit_event(
        actor=actor,
        action="budget_circuit_tripped",
        outcome="blocked",
        metadata={
            "reason": reason,
            "goal_id": goal_id,
            "paused_until": paused_until_iso,
            "cooldown_seconds": cooldown_seconds,
        },
    )
    alert = {
        "status": "paused",
        "reason": reason,
        "paused_until": paused_until_iso,
        "audit_event_id": event_id,
    }
    _notify_hud(alert)
    return alert


def check_budget(
    actor: str = "autonomous_worker",
    goal_id: Optional[str] = None,
    requested_tokens: int = 0,
    requires_self_mod: bool = False,
    db_path: str = DEFAULT_DB_PATH,
) -> Dict[str, Any]:
    """
    Evaluates all circuit breakers and rolling rate caps before executing an autonomous cycle or step.
    Returns {'allowed': bool, 'reason': Optional[str], 'usage': dict, 'limits': dict}
    """
    init_budget_db(db_path)
    limits = get_effective_limits(db_path=db_path)
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    one_hour_ago = (now_dt - datetime.timedelta(hours=1)).isoformat()
    one_day_ago = (now_dt - datetime.timedelta(days=1)).isoformat()

    with _BUDGET_LOCK:
        with _get_connection(db_path) as conn:
            state = conn.execute(
                "SELECT * FROM circuit_state WHERE breaker_name = 'autonomous_worker'"
            ).fetchone()

            # 1. Check active pause / cooldown
            if state and int(state["is_paused"]) == 1:
                paused_until_raw = state["paused_until"]
                if paused_until_raw:
                    paused_until_dt = datetime.datetime.fromisoformat(paused_until_raw)
                    if now_dt < paused_until_dt:
                        return {
                            "allowed": False,
                            "reason": f"cooldown_active: {state['trip_reason']} (until {paused_until_raw})",
                            "paused_until": paused_until_raw,
                            "limits": limits,
                        }
                    # Cooldown expired -> auto-recover and reset failure streak
                    conn.execute(
                        """
                        UPDATE circuit_state
                        SET is_paused = 0, consecutive_failures = 0, paused_until = NULL,
                            trip_reason = NULL, updated_at = ?
                        WHERE breaker_name = 'autonomous_worker'
                        """,
                        (now_dt.isoformat(),),
                    )
                    conn.commit()
                    log_audit_event(
                        actor="system",
                        action="budget_circuit_recovered",
                        outcome="success",
                        metadata={"trigger": "cooldown_expired"},
                    )
                else:
                    return {
                        "allowed": False,
                        "reason": f"manual_pause: {state['trip_reason']}",
                        "limits": limits,
                    }

            # 2. Check hourly cycle cap
            cycles_last_hour = conn.execute(
                """
                SELECT COALESCE(SUM(amount), 0) AS total
                FROM budget_events
                WHERE event_type = 'cycle' AND timestamp >= ?
                """,
                (one_hour_ago,),
            ).fetchone()["total"]

            if cycles_last_hour >= limits["budget.max_cycles_per_hour"]:
                alert = _trip_breaker(
                    conn,
                    reason=f"max_cycles_per_hour exceeded ({cycles_last_hour}/{limits['budget.max_cycles_per_hour']})",
                    cooldown_seconds=limits["budget.cooldown_seconds"],
                    actor=actor,
                    goal_id=goal_id,
                )
                return {"allowed": False, "reason": alert["reason"], "limits": limits}

            # 3. Check per-cycle token cap
            tokens_this_cycle = 0
            if goal_id:
                tokens_this_cycle = conn.execute(
                    """
                    SELECT COALESCE(SUM(amount), 0) AS total
                    FROM budget_events
                    WHERE event_type = 'tokens' AND goal_id = ?
                    """,
                    (goal_id,),
                ).fetchone()["total"]

            if (tokens_this_cycle + requested_tokens) > limits["budget.max_tokens_per_cycle"]:
                reason = (
                    f"max_tokens_per_cycle exceeded "
                    f"({tokens_this_cycle + requested_tokens}/{limits['budget.max_tokens_per_cycle']})"
                )
                log_audit_event(
                    actor=actor,
                    action="budget_token_cap_blocked",
                    outcome="blocked",
                    metadata={"goal_id": goal_id, "reason": reason},
                )
                _notify_hud({"status": "token_cap_reached", "goal_id": goal_id, "reason": reason})
                return {"allowed": False, "reason": reason, "limits": limits}

            # 4. Check daily self-modification cap
            self_mods_today = conn.execute(
                """
                SELECT COALESCE(SUM(amount), 0) AS total
                FROM budget_events
                WHERE event_type = 'self_mod' AND timestamp >= ?
                """,
                (one_day_ago,),
            ).fetchone()["total"]

            if requires_self_mod and self_mods_today >= limits["budget.max_self_mods_per_day"]:
                alert = _trip_breaker(
                    conn,
                    reason=f"max_self_mods_per_day exceeded ({self_mods_today}/{limits['budget.max_self_mods_per_day']})",
                    cooldown_seconds=limits["budget.cooldown_seconds"],
                    actor=actor,
                    goal_id=goal_id,
                )
                return {"allowed": False, "reason": alert["reason"], "limits": limits}

            return {
                "allowed": True,
                "reason": None,
                "usage": {
                    "cycles_last_hour": cycles_last_hour,
                    "tokens_this_cycle": tokens_this_cycle,
                    "self_mods_today": self_mods_today,
                    "consecutive_failures": int(state["consecutive_failures"]) if state else 0,
                },
                "limits": limits,
            }


def record_budget_usage(
    event_type: str,
    actor: str = "autonomous_worker",
    goal_id: Optional[str] = None,
    amount: int = 1,
    db_path: str = DEFAULT_DB_PATH,
) -> Dict[str, Any]:
    """
    Records resource consumption ('cycle', 'tokens', 'self_mod') or task outcomes
    ('failure', 'success'). Trips the circuit breaker automatically if consecutive
    failures reach `budget.max_consecutive_failures`.
    """
    if event_type not in ("cycle", "tokens", "self_mod", "failure", "success"):
        raise ValueError(f"Unsupported budget event_type: {event_type!r}")

    init_budget_db(db_path)
    limits = get_effective_limits(db_path=db_path)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    with _BUDGET_LOCK:
        with _get_connection(db_path) as conn:
            conn.execute(
                """
                INSERT INTO budget_events (timestamp, event_type, actor, goal_id, amount)
                VALUES (?, ?, ?, ?, ?)
                """,
                (now_iso, event_type, actor, goal_id, int(amount)),
            )

            if event_type == "success":
                conn.execute(
                    """
                    UPDATE circuit_state
                    SET consecutive_failures = 0, updated_at = ?
                    WHERE breaker_name = 'autonomous_worker'
                    """,
                    (now_iso,),
                )
                conn.commit()
                return {"tripped": False, "consecutive_failures": 0}

            if event_type == "failure":
                row = conn.execute(
                    "SELECT consecutive_failures FROM circuit_state WHERE breaker_name = 'autonomous_worker'"
                ).fetchone()
                new_failures = (int(row["consecutive_failures"]) if row else 0) + 1
                conn.execute(
                    """
                    UPDATE circuit_state
                    SET consecutive_failures = ?, updated_at = ?
                    WHERE breaker_name = 'autonomous_worker'
                    """,
                    (new_failures, now_iso),
                )
                conn.commit()

                if new_failures >= limits["budget.max_consecutive_failures"]:
                    alert = _trip_breaker(
                        conn,
                        reason=f"consecutive_failures reached ({new_failures}/{limits['budget.max_consecutive_failures']})",
                        cooldown_seconds=limits["budget.cooldown_seconds"],
                        actor=actor,
                        goal_id=goal_id,
                    )
                    return {"tripped": True, "consecutive_failures": new_failures, "alert": alert}

                return {"tripped": False, "consecutive_failures": new_failures}

            conn.commit()
            return {"tripped": False}


def reset_circuit_breaker(actor: str = "user", db_path: str = DEFAULT_DB_PATH) -> Dict[str, Any]:
    """Allows the human user to clear a tripped breaker or manual pause immediately."""
    if actor != "user":
        raise PermissionError("Only 'user' may manually reset the autonomous circuit breaker.")

    init_budget_db(db_path)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _BUDGET_LOCK:
        with _get_connection(db_path) as conn:
            conn.execute(
                """
                UPDATE circuit_state
                SET is_paused = 0, consecutive_failures = 0, paused_until = NULL,
                    trip_reason = NULL, updated_at = ?
                WHERE breaker_name = 'autonomous_worker'
                """,
                (now_iso,),
            )
            conn.commit()

    event_id = log_audit_event(
        actor=actor,
        action="budget_circuit_reset",
        outcome="success",
        metadata={"reset_by": actor},
    )
    _notify_hud({"status": "active", "reason": "reset_by_user", "audit_event_id": event_id})
    return {"reset": True, "audit_event_id": event_id}
