import datetime
import json
import os
import sqlite3
import threading
import uuid
from typing import Any, Dict, List, Optional

from modules.audit import log_audit_event

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(PROJECT_ROOT, "data", "jarvis_memory.db")

_GOALS_LOCK = threading.Lock()


def _get_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_goals_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """Creates the durable goals table and priority index."""
    with _GOALS_LOCK:
        with _get_connection(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS goals (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL CHECK(source IN ('user', 'self')),
                    description TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 50,
                    status TEXT NOT NULL CHECK(
                        status IN ('pending', 'running', 'paused_interrupted', 'paused_budget', 'completed', 'failed', 'blocked')
                    ),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    autonomy_level INTEGER NOT NULL CHECK(autonomy_level BETWEEN 0 AND 3),
                    checkpoint_json TEXT NOT NULL DEFAULT '{}',
                    last_error TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_goals_status_priority ON goals(status, priority DESC, created_at ASC)"
            )
            conn.commit()


def enqueue_goal(
    description: str,
    source: str = "self",
    priority: int = 50,
    autonomy_level: Optional[int] = None,
    actor: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> Dict[str, Any]:
    """
    Adds a new durable goal to SQLite.
    """
    if source not in ("user", "self"):
        raise ValueError(f"Invalid goal source: {source!r}")

    init_goals_db(db_path)
    goal_id = str(uuid.uuid4())
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    effective_level = int(autonomy_level) if autonomy_level is not None else 1
    checkpoint_str = json.dumps({"current_step_index": 0, "completed_steps": []})

    with _GOALS_LOCK:
        with _get_connection(db_path) as conn:
            conn.execute(
                """
                INSERT INTO goals
                (id, source, description, priority, status, created_at, updated_at, attempts, autonomy_level, checkpoint_json, last_error)
                VALUES (?, ?, ?, ?, 'pending', ?, ?, 0, ?, ?, NULL)
                """,
                (goal_id, source, description.strip(), int(priority), now, now, effective_level, checkpoint_str),
            )
            conn.commit()

    event_id = log_audit_event(
        actor=actor or source,
        action="goal_enqueued",
        outcome="success",
        metadata={
            "goal_id": goal_id,
            "source": source,
            "priority": int(priority),
            "autonomy_level": effective_level,
            "description": description.strip(),
        },
    )
    return {
        "id": goal_id,
        "source": source,
        "description": description.strip(),
        "priority": int(priority),
        "status": "pending",
        "autonomy_level": effective_level,
        "audit_event_id": event_id,
    }


def get_goal(goal_id: str, db_path: str = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    init_goals_db(db_path)
    with _GOALS_LOCK:
        with _get_connection(db_path) as conn:
            row = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if not row:
        return None
    data = dict(row)
    data["checkpoint"] = json.loads(data.get("checkpoint_json") or "{}")
    return data


def update_goal(
    goal_id: str,
    status: Optional[str] = None,
    checkpoint: Optional[Dict[str, Any]] = None,
    last_error: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> bool:
    """Updates a goal's status, checkpoint JSON, and/or last_error in place."""
    init_goals_db(db_path)
    updates: List[str] = []
    params: List[Any] = []
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    if status is not None:
        updates.append("status = ?")
        params.append(status)
    if checkpoint is not None:
        updates.append("checkpoint_json = ?")
        params.append(json.dumps(checkpoint, ensure_ascii=False))
    if last_error is not None:
        updates.append("last_error = ?")
        params.append(last_error)

    if not updates:
        return False

    updates.append("updated_at = ?")
    params.append(now)
    params.append(goal_id)

    with _GOALS_LOCK:
        with _get_connection(db_path) as conn:
            cur = conn.execute(
                f"UPDATE goals SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            conn.commit()
            return cur.rowcount > 0


def recover_interrupted_goals(db_path: str = DEFAULT_DB_PATH) -> int:
    """Resets goals left in a non-terminal state back to 'pending' so the idle scheduler can resume them on the next cycle.

    Returns the number of goals recovered.
    """
    init_goals_db(db_path)
    now = datetime.datetime.now(datetime.UTC).isoformat()
    with _GOALS_LOCK:
        with _get_connection(db_path) as conn:
            cur = conn.execute(
                """
                UPDATE goals
                SET status = 'pending', updated_at = ?
                WHERE status IN ('running', 'paused_interrupted', 'paused_budget')
                """,
                (now,),
            )
            conn.commit()
            recovered = cur.rowcount
    if recovered:
        log_audit_event(
            actor="system",
            action="goals_recovered",
            outcome="success",
            metadata={"recovered_count": recovered},
        )
    return recovered
