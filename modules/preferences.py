import datetime
import json
import os
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Tuple

from config.settings import DB_PATH
from modules.audit import log_audit_event

DEFAULT_DB_PATH = DB_PATH

_PREF_LOCK = threading.Lock()


def _get_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_preferences_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """Creates the preferences table and index if they do not exist."""
    with _PREF_LOCK:
        with _get_connection(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS preferences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL CHECK(source IN ('user', 'ai')),
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    weight REAL NOT NULL DEFAULT 1.0,
                    updated_at TEXT NOT NULL,
                    UNIQUE(source, key)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_preferences_key ON preferences(key)"
            )
            conn.commit()


def _serialize_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _deserialize_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


def set_preference(
    source: str,
    key: str,
    value: Any,
    weight: float = 1.0,
    actor: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> Dict[str, Any]:
    """
    Sets or updates a preference with strict precedence rules:
    - source must be 'user' or 'ai'.
    - AI preferences may ONLY fill gaps where no user preference exists for `key`.
    - User preferences always win and log an override if an AI preference existed.
    """
    if source not in ("user", "ai"):
        raise ValueError(f"Invalid preference source: {source!r}. Must be 'user' or 'ai'.")

    key = key.strip()
    if not key:
        raise ValueError("Preference key cannot be empty.")

    effective_actor = actor or source
    serialized = _serialize_value(value)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    init_preferences_db(db_path)

    with _PREF_LOCK:
        with _get_connection(db_path) as conn:
            existing_user = conn.execute(
                "SELECT id, value, weight FROM preferences WHERE source = 'user' AND key = ?",
                (key,),
            ).fetchone()
            existing_ai = conn.execute(
                "SELECT id, value, weight FROM preferences WHERE source = 'ai' AND key = ?",
                (key,),
            ).fetchone()

            # Rule: AI may only fill gaps where no user preference exists
            if source == "ai" and existing_user is not None:
                event_id = log_audit_event(
                    actor=effective_actor,
                    action="preference_conflict_blocked",
                    outcome="blocked",
                    preference_keys=[f"user:{key}", f"ai:{key}"],
                    metadata={
                        "key": key,
                        "attempted_ai_value": value,
                        "preserved_user_value": _deserialize_value(existing_user["value"]),
                        "reason": "User preference takes strict precedence over AI preference.",
                    },
                )
                return {
                    "applied": False,
                    "reason": "user_preference_precedence",
                    "key": key,
                    "effective_source": "user",
                    "effective_value": _deserialize_value(existing_user["value"]),
                    "audit_event_id": event_id,
                }

            # Upsert the preference
            conn.execute(
                """
                INSERT INTO preferences (source, key, value, weight, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source, key) DO UPDATE SET
                    value = excluded.value,
                    weight = excluded.weight,
                    updated_at = excluded.updated_at
                """,
                (source, key, serialized, float(weight), now),
            )
            conn.commit()

            # Log override if user supersedes an existing AI preference
            if source == "user" and existing_ai is not None:
                event_id = log_audit_event(
                    actor=effective_actor,
                    action="preference_user_override",
                    outcome="success",
                    preference_keys=[f"user:{key}"],
                    metadata={
                        "key": key,
                        "new_user_value": value,
                        "overridden_ai_value": _deserialize_value(existing_ai["value"]),
                    },
                )
            else:
                event_id = log_audit_event(
                    actor=effective_actor,
                    action="preference_set",
                    outcome="success",
                    preference_keys=[f"{source}:{key}"],
                    metadata={
                        "source": source,
                        "key": key,
                        "value": value,
                        "weight": float(weight),
                    },
                )

            return {
                "applied": True,
                "key": key,
                "effective_source": source,
                "effective_value": value,
                "audit_event_id": event_id,
            }


def delete_preference(
    source: str,
    key: str,
    actor: str = "user",
    db_path: str = DEFAULT_DB_PATH,
) -> bool:
    """Removes a preference row for a given source and key."""
    init_preferences_db(db_path)
    with _PREF_LOCK:
        with _get_connection(db_path) as conn:
            cur = conn.execute(
                "DELETE FROM preferences WHERE source = ? AND key = ?",
                (source, key.strip()),
            )
            conn.commit()
            deleted = cur.rowcount > 0

    if deleted:
        log_audit_event(
            actor=actor,
            action="preference_delete",
            outcome="success",
            preference_keys=[f"{source}:{key}"],
        )
    return deleted


def resolve_preferences(
    prefix: Optional[str] = None,
    min_weight: float = 0.0,
    db_path: str = DEFAULT_DB_PATH,
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Blends user and AI preferences transparently:
    - User preferences always win on key collision.
    - AI preferences fill remaining gaps where weight >= min_weight.
    Returns:
        (effective_map, traced_keys)
        where traced_keys is a list like ['user:code_style', 'ai:retry_limit']
        suitable for DecisionTrace.preference_keys_used.
    """
    init_preferences_db(db_path)

    with _PREF_LOCK:
        with _get_connection(db_path) as conn:
            if prefix:
                rows = conn.execute(
                    """
                    SELECT source, key, value, weight
                    FROM preferences
                    WHERE key LIKE ? AND weight >= ?
                    ORDER BY key ASC
                    """,
                    (f"{prefix}%", float(min_weight)),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT source, key, value, weight
                    FROM preferences
                    WHERE weight >= ?
                    ORDER BY key ASC
                    """,
                    (float(min_weight),),
                ).fetchall()

    by_key: Dict[str, Dict[str, sqlite3.Row]] = {}
    for row in rows:
        by_key.setdefault(row["key"], {})[row["source"]] = row

    effective: Dict[str, Any] = {}
    traced_keys: List[str] = []

    for key in sorted(by_key.keys()):
        sources = by_key[key]
        if "user" in sources:
            chosen = sources["user"]
            chosen_source = "user"
        else:
            chosen = sources["ai"]
            chosen_source = "ai"

        effective[key] = _deserialize_value(chosen["value"])
        traced_keys.append(f"{chosen_source}:{key}")

    return effective, traced_keys


def get_preference(
    key: str,
    default: Any = None,
    db_path: str = DEFAULT_DB_PATH,
) -> Tuple[Any, Optional[str]]:
    """
    Fetches a single resolved preference value and its winning source ('user', 'ai', or None).
    """
    effective, traced = resolve_preferences(db_path=db_path)
    if key not in effective:
        return default, None
    source = "user" if f"user:{key}" in traced else "ai"
    return effective[key], source


def format_preferences_for_prompt(
    prefix: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> Tuple[str, List[str]]:
    """
    Formats active blended preferences into a compact system-prompt block
    and returns (prompt_block, traced_keys).
    """
    effective, traced_keys = resolve_preferences(prefix=prefix, db_path=db_path)
    if not effective:
        return "", []

    lines = ["Active System & User Preferences (User rules strictly override AI defaults):"]
    for traced in traced_keys:
        src, key = traced.split(":", 1)
        lines.append(f"- [{src.upper()}] {key}: {effective[key]}")

    return "\n".join(lines), traced_keys


# --- Persistent runtime settings (JSON-backed, used by the Settings UI) ---

import json as _json
from pathlib import Path as _Path

RUNTIME_SETTINGS_PATH = _Path(__file__).resolve().parent.parent / "data" / "runtime_settings.json"

DEFAULT_RUNTIME_SETTINGS = {
    "routing_mode": "auto",
    "daily_cloud_token_cap": 250000,
    "min_available_ram_mb": 1200,
    "require_edit_approval": True,
    "tts_enabled": True,
    "mic_muted": False,
    "hud_always_on_top": False,
    "auto_update_on_startup": True,
    "scheduler_enabled": True,
}


def get_runtime_settings(settings_path=None) -> dict:
    settings = dict(DEFAULT_RUNTIME_SETTINGS)
    path = _Path(settings_path) if settings_path else RUNTIME_SETTINGS_PATH
    try:
        if path.exists():
            loaded = _json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                settings.update(loaded)
    except Exception:
        pass
    return settings


def update_runtime_settings(updates: dict, settings_path=None) -> dict:
    path = _Path(settings_path) if settings_path else RUNTIME_SETTINGS_PATH
    current = get_runtime_settings(settings_path=path)
    current.update({k: v for k, v in updates.items() if k in DEFAULT_RUNTIME_SETTINGS})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json.dumps(current, indent=2), encoding="utf-8")
    return current
