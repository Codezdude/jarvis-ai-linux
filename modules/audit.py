import datetime
import json
import os
import threading
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_AUDIT_LOG = os.path.join(PROJECT_ROOT, "logs", "audit.jsonl")

_AUDIT_LOCK = threading.Lock()


@dataclass
class DecisionTrace:
    """Structured provenance attached to every AI turn and surfaced in the HUD."""
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    actor: str = "user"
    provider: str = "unknown"
    model: str = "unknown"
    memory_ids_retrieved: List[int] = field(default_factory=list)
    preference_keys_used: List[str] = field(default_factory=list)
    tools_invoked: List[str] = field(default_factory=list)
    audit_event_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def log_audit_event(
    actor: str,
    action: str,
    outcome: str,
    paths_touched: Optional[List[str]] = None,
    memory_ids: Optional[List[int]] = None,
    preference_keys: Optional[List[str]] = None,
    provider_info: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    log_path: Optional[str] = None,
) -> str:
    """
    Appends a single immutable audit record to audit.jsonl.
    Returns the generated event_id.
    """
    if log_path is None:
        log_path = DEFAULT_AUDIT_LOG
    event_id = str(uuid.uuid4())
    entry = {
        "event_id": event_id,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "actor": actor,
        "action": action,
        "paths_touched": paths_touched or [],
        "memory_ids": memory_ids or [],
        "preference_keys": preference_keys or [],
        "provider_info": provider_info or {},
        "outcome": outcome,
        "metadata": metadata or {},
    }

    os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
    serialized = json.dumps(entry, ensure_ascii=False)

    with _AUDIT_LOCK:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(serialized + "\n")
            f.flush()
            os.fsync(f.fileno())

    return event_id


def query_audit_log(
    limit: int = 50,
    actor: Optional[str] = None,
    action: Optional[str] = None,
    path_filter: Optional[str] = None,
    log_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Reads audit.jsonl in reverse chronological order with optional filtering."""
    if log_path is None:
        log_path = DEFAULT_AUDIT_LOG
    if not os.path.exists(log_path):
        return []

    results: List[Dict[str, Any]] = []
    with _AUDIT_LOCK:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        if actor and record.get("actor") != actor:
            continue
        if action and record.get("action") != action:
            continue
        if path_filter and not any(path_filter in p for p in record.get("paths_touched", [])):
            continue

        results.append(record)
        if len(results) >= limit:
            break

    return results


def explain_event(event_id: str, log_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieves a specific audit event by event_id so the agent can cite its reasoning."""
    if log_path is None:
        log_path = DEFAULT_AUDIT_LOG
    if not os.path.exists(log_path):
        return None

    with _AUDIT_LOCK:
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                if event_id in line:
                    try:
                        rec = json.loads(line.strip())
                        if rec.get("event_id") == event_id:
                            return rec
                    except json.JSONDecodeError:
                        continue
    return None
