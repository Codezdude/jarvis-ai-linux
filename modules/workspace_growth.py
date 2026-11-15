import json
import os
import re
import sqlite3
from datetime import datetime

from config.settings import BASE_DIR

EXPERIMENTS_DIR = os.path.join(BASE_DIR, "experiments")
WORKSPACE_GROWTH_LOG = os.path.join(BASE_DIR, "logs", "workspace_growth.log")
WORKSPACE_GROWTH_MD = os.path.join(BASE_DIR, "workspace_growth.md")
PROJECT_MEMORY_INDEX_PATH = os.path.join(BASE_DIR, "memory", "jarvis_project_memory_index.db")
_PROJECT_MEMORY_INDEX_CACHE_TTL_SECONDS = 30
_PROJECT_MEMORY_INDEX_CACHE = {"rows": None, "timestamp": 0.0}


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", (value or "experiment").strip())
    cleaned = cleaned.strip(".-")
    return cleaned or "experiment"


def ensure_experiment_directory(name: str, base_dir: str = BASE_DIR) -> str:
    """Create a project-scoped experiment folder and a starter README for model experiments."""
    slug = _slugify(name)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target_dir = os.path.join(base_dir, "experiments", f"{slug}_{stamp}")
    os.makedirs(target_dir, exist_ok=True)

    readme_path = os.path.join(target_dir, "README.md")
    if not os.path.exists(readme_path):
        with open(readme_path, "w", encoding="utf-8") as handle:
            handle.write(
                f"# {slug}\n\n"
                f"Created: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                "This folder is reserved for project experiments, prototypes, and model-driven testing.\n"
            )

    return target_dir


def append_workspace_growth_log(decision: str, context: str = "") -> str:
    """Append a model decision to the shared project log for traceable growth and review."""
    if not decision or not str(decision).strip():
        raise ValueError("decision text is required")

    os.makedirs(os.path.dirname(WORKSPACE_GROWTH_LOG), exist_ok=True)
    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "context": context or "general",
        "decision": str(decision).strip(),
    }

    with open(WORKSPACE_GROWTH_LOG, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    os.makedirs(os.path.dirname(WORKSPACE_GROWTH_MD), exist_ok=True)
    with open(WORKSPACE_GROWTH_MD, "a", encoding="utf-8") as handle:
        handle.write(f"\n## {entry['timestamp']}\n**Context:** {entry['context']}\n**Decision:** {entry['decision']}\n")

    return f"Workspace growth decision logged: {entry['timestamp']}"


def search_workspace_growth_log(query: str, limit: int = 10) -> list[dict]:
    """Search the shared workspace decision log for relevant model decisions."""
    if not os.path.exists(WORKSPACE_GROWTH_LOG):
        return []
    if limit <= 0:
        limit = 10

    needle = (query or "").lower()
    matches = []
    with open(WORKSPACE_GROWTH_LOG, "r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            haystack = json.dumps(payload, ensure_ascii=False).lower()
            if not needle or needle in haystack:
                matches.append(payload)

    matches = matches[-limit:]
    return matches


def _ensure_project_memory_index_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(PROJECT_MEMORY_INDEX_PATH), exist_ok=True)
    conn = sqlite3.connect(PROJECT_MEMORY_INDEX_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS project_memory_index (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            category TEXT,
            content TEXT,
            created_at TEXT,
            keywords TEXT,
            digest TEXT UNIQUE
        )
        """
    )
    return conn


def _keyword_tokens(value: str) -> list[str]:
    return [token for token in re.findall(r"[A-Za-z0-9_]+", (value or "").lower()) if len(token) > 2]


def build_project_memory_index(force: bool = False) -> list[dict]:
    """Build a project-wide searchable memory index from the primary memory DB and vector memory records."""
    now = datetime.now().timestamp()
    if not force and _PROJECT_MEMORY_INDEX_CACHE["rows"] is not None and (now - _PROJECT_MEMORY_INDEX_CACHE["timestamp"]) < _PROJECT_MEMORY_INDEX_CACHE_TTL_SECONDS:
        return _PROJECT_MEMORY_INDEX_CACHE["rows"]

    conn = _ensure_project_memory_index_db()
    if not force:
        existing = conn.execute("SELECT source, category, content, created_at, keywords, digest FROM project_memory_index ORDER BY id DESC").fetchall()
        if existing:
            rows = [{
                "source": source,
                "category": category,
                "content": content,
                "created_at": created_at,
                "keywords": keywords,
                "digest": digest,
            } for source, category, content, created_at, keywords, digest in existing]
            conn.close()
            _PROJECT_MEMORY_INDEX_CACHE["rows"] = rows
            _PROJECT_MEMORY_INDEX_CACHE["timestamp"] = now
            return rows

    if force:
        conn.execute("DELETE FROM project_memory_index")

    seen = set()
    rows = []

    primary_db = os.path.join(BASE_DIR, "memory", "jarvis_memory.db")
    if os.path.exists(primary_db):
        primary_conn = sqlite3.connect(primary_db)
        primary_rows = primary_conn.execute(
            "SELECT category, value, created_at FROM memory ORDER BY id DESC"
        ).fetchall()
        primary_conn.close()
        for category, value, created_at in primary_rows:
            if not value or not str(value).strip():
                continue
            digest = f"sqlite::{category}::{str(value).strip()}"
            if digest in seen:
                continue
            seen.add(digest)
            row = {
                "source": "sqlite_memory",
                "category": category or "general",
                "content": str(value).strip(),
                "created_at": created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "keywords": json.dumps(_keyword_tokens(str(value)), ensure_ascii=False),
                "digest": digest,
            }
            rows.append(row)

    vector_db = os.path.join(BASE_DIR, "memory", "jarvis_vector_memory.db")
    if os.path.exists(vector_db):
        vector_conn = sqlite3.connect(vector_db)
        vector_rows = vector_conn.execute(
            "SELECT category, content, created_at FROM vector_memory ORDER BY id DESC"
        ).fetchall()
        vector_conn.close()
        for category, value, created_at in vector_rows:
            if not value or not str(value).strip():
                continue
            digest = f"vector::{category}::{str(value).strip()}"
            if digest in seen:
                continue
            seen.add(digest)
            row = {
                "source": "vector_memory",
                "category": category or "general",
                "content": str(value).strip(),
                "created_at": created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "keywords": json.dumps(_keyword_tokens(str(value)), ensure_ascii=False),
                "digest": digest,
            }
            rows.append(row)

    for row in rows:
        conn.execute(
            "INSERT OR REPLACE INTO project_memory_index (source, category, content, created_at, keywords, digest) VALUES (?, ?, ?, ?, ?, ?)",
            (row["source"], row["category"], row["content"], row["created_at"], row["keywords"], row["digest"]),
        )

    conn.commit()
    conn.close()
    _PROJECT_MEMORY_INDEX_CACHE["rows"] = rows
    _PROJECT_MEMORY_INDEX_CACHE["timestamp"] = now
    return rows


def search_project_memory_index(query: str, limit: int = 10, category: str | None = None) -> list[dict]:
    """Return best matches from the project-wide memory index for a query or category."""
    rows = build_project_memory_index(force=False)
    if not rows:
        rows = build_project_memory_index(force=True)
    if not rows:
        return []

    query_tokens = {token for token in _keyword_tokens(query or "")}
    def score(item: dict) -> int:
        text = (item["content"] + " " + (item["category"] or "")).lower()
        if not query_tokens:
            return 1
        score = 0
        for token in query_tokens:
            if token in text:
                score += 2
        if query and query.lower() in text:
            score += 4
        if category and item["category"] == category:
            score += 3
        return score

    filtered = [item for item in rows if not category or item["category"] == category]
    filtered.sort(key=score, reverse=True)
    return [{
        "source": item["source"],
        "category": item["category"],
        "content": item["content"],
        "created_at": item["created_at"],
        "score": score(item),
    } for item in filtered[: max(1, int(limit))]]
