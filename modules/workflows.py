"""Workflow promotion: persist and replay successful multi-step tool sequences.

A "workflow" is a named, ordered list of tool steps (``{"tool", "args"}``)
that the agent can replay later. Promoting a successful ad-hoc sequence into a
named workflow lets the assistant reuse proven procedures instead of
rediscovering them each time.
"""

import datetime
import json
import os
import sqlite3
import threading
from typing import Any, Callable, Dict, List, Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(PROJECT_ROOT, "data", "jarvis_memory.db")

_LOCK = threading.Lock()


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_workflows_db(db_path: str = DEFAULT_DB_PATH) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    with _LOCK:
        with _connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS promoted_workflows (
                    name TEXT PRIMARY KEY,
                    description TEXT DEFAULT '',
                    steps_json TEXT NOT NULL,
                    source TEXT DEFAULT 'manual',
                    uses INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.commit()


def _normalize_steps(steps: Any) -> List[Dict[str, Any]]:
    if not isinstance(steps, list):
        raise ValueError("steps must be a list of {'tool', 'args'} objects")
    normalized = []
    for step in steps:
        if not isinstance(step, dict) or "tool" not in step:
            raise ValueError("each step must be a dict with a 'tool' key")
        normalized.append({"tool": str(step["tool"]), "args": dict(step.get("args") or {})})
    return normalized


def promote_workflow(
    name: str,
    description: str,
    steps: List[Dict[str, Any]],
    source: str = "manual",
    db_path: str = DEFAULT_DB_PATH,
) -> Dict[str, Any]:
    """Create or update a named, replayable workflow. Returns the stored record."""
    name = (name or "").strip()
    if not name:
        raise ValueError("workflow name is required")
    normalized = _normalize_steps(steps)
    init_workflows_db(db_path)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _LOCK:
        with _connect(db_path) as conn:
            existing = conn.execute(
                "SELECT uses FROM promoted_workflows WHERE name = ?", (name,)
            ).fetchone()
            uses = int(existing["uses"]) if existing else 0
            conn.execute(
                """
                INSERT INTO promoted_workflows (name, description, steps_json, source, uses, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    description = excluded.description,
                    steps_json = excluded.steps_json,
                    source = excluded.source,
                    updated_at = excluded.updated_at
                """,
                (name, description or "", json.dumps(normalized), source, uses, now, now),
            )
            conn.commit()
    return get_workflow(name, db_path=db_path)


def get_workflow(name: str, db_path: str = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    init_workflows_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM promoted_workflows WHERE name = ?", (name,)
        ).fetchone()
    if row is None:
        return None
    return {
        "name": row["name"],
        "description": row["description"],
        "steps": json.loads(row["steps_json"]),
        "source": row["source"],
        "uses": int(row["uses"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_workflows(db_path: str = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    init_workflows_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM promoted_workflows ORDER BY updated_at DESC"
        ).fetchall()
    result = []
    for row in rows:
        result.append(
            {
                "name": row["name"],
                "description": row["description"],
                "steps": json.loads(row["steps_json"]),
                "source": row["source"],
                "uses": int(row["uses"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
    return result


def delete_workflow(name: str, db_path: str = DEFAULT_DB_PATH) -> bool:
    init_workflows_db(db_path)
    with _LOCK:
        with _connect(db_path) as conn:
            cur = conn.execute("DELETE FROM promoted_workflows WHERE name = ?", (name,))
            conn.commit()
            return cur.rowcount > 0


def replay_workflow(
    name: str,
    dispatch: Callable[[str, Dict[str, Any]], Any],
    db_path: str = DEFAULT_DB_PATH,
) -> Dict[str, Any]:
    """Replay a promoted workflow's steps through ``dispatch(tool, args)``.

    Returns ``{"name", "results": [...], "ok": bool}``. Each step's result is
    captured; a step that raises is recorded as an error string and stops the
    replay. The workflow's ``uses`` counter is incremented on success.
    """
    workflow = get_workflow(name, db_path=db_path)
    if workflow is None:
        return {"name": name, "results": [], "ok": False, "error": "workflow_not_found"}

    results = []
    ok = True
    for step in workflow["steps"]:
        try:
            results.append(dispatch(step["tool"], step.get("args") or {}))
        except Exception as exc:
            results.append(f"Error in step {step['tool']}: {exc}")
            ok = False
            break

    if ok:
        with _LOCK:
            with _connect(db_path) as conn:
                conn.execute(
                    "UPDATE promoted_workflows SET uses = uses + 1 WHERE name = ?", (name,)
                )
                conn.commit()
    return {"name": name, "results": results, "ok": ok}