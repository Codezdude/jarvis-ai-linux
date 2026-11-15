import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from collections import Counter
from datetime import datetime

from config.settings import BASE_DIR

VECTOR_DB_PATH = os.path.join(BASE_DIR, "memory", "jarvis_vector_memory.db")
_CACHE_DB_PATH = os.path.join(BASE_DIR, "memory", "jarvis_semantic_cache.db")
_VECTOR_DIMENSIONS = 128
_SEMANTIC_RESULT_CACHE_TTL_SECONDS = 30
_SEMANTIC_RESULT_CACHE = {}
_SEMANTIC_RESULT_CACHE_LOCK = threading.Lock()


def _ensure_vector_db() -> None:
    os.makedirs(os.path.dirname(VECTOR_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(VECTOR_DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vector_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT,
            content TEXT,
            embedding TEXT,
            created_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def _ensure_cache_db() -> None:
    os.makedirs(os.path.dirname(_CACHE_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_CACHE_DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS semantic_cache (
            query_hash TEXT PRIMARY KEY,
            query TEXT,
            category TEXT,
            matches TEXT,
            summary TEXT,
            created_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def _tokenize(text: str) -> list[str]:
    if not text:
        return []
    tokens = re.findall(r"[a-zA-Z0-9_]+", str(text).lower())
    return [token for token in tokens if len(token) > 1]


def _build_embedding(text: str, dimensions: int = _VECTOR_DIMENSIONS) -> list[float]:
    counts = Counter(_tokenize(text))
    vector = [0.0] * dimensions
    if not counts:
        return vector

    for token, weight in counts.items():
        index = int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16) % dimensions
        vector[index] += float(weight)

    magnitude = (sum(value * value for value in vector)) ** 0.5
    if magnitude:
        vector = [value / magnitude for value in vector]
    return vector


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0

    dot = sum(a * b for a, b in zip(left, right))
    left_norm = (sum(value * value for value in left)) ** 0.5
    right_norm = (sum(value * value for value in right)) ** 0.5
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _semantic_cache_key(query: str, category: str | None = None) -> str:
    payload = f"{(category or 'all').strip().lower()}::{(query or '').strip().lower()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_cached_semantic_results(query: str, category: str | None = None) -> list[dict] | None:
    if not query or not str(query).strip():
        return None

    key = _semantic_cache_key(query, category)
    now = time.time()
    with _SEMANTIC_RESULT_CACHE_LOCK:
        cached = _SEMANTIC_RESULT_CACHE.get(key)
        if cached and now - cached["timestamp"] < _SEMANTIC_RESULT_CACHE_TTL_SECONDS:
            return cached["matches"]
        if cached:
            _SEMANTIC_RESULT_CACHE.pop(key, None)

    _ensure_cache_db()
    conn = sqlite3.connect(_CACHE_DB_PATH)
    row = conn.execute(
        "SELECT matches FROM semantic_cache WHERE query_hash = ?",
        (key,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    try:
        cached = json.loads(row[0])
        if isinstance(cached, list):
            with _SEMANTIC_RESULT_CACHE_LOCK:
                _SEMANTIC_RESULT_CACHE[key] = {"matches": cached, "timestamp": now}
            return cached
    except (TypeError, ValueError):
        return None
    return None


def _write_cached_semantic_results(query: str, category: str | None, matches: list[dict], summary: str) -> None:
    _ensure_cache_db()
    query_hash = _semantic_cache_key(query, category)
    conn = sqlite3.connect(_CACHE_DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO semantic_cache (query_hash, query, category, matches, summary, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            query_hash,
            (query or "").strip(),
            category,
            json.dumps(matches, ensure_ascii=False),
            summary,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    conn.commit()
    conn.close()
    with _SEMANTIC_RESULT_CACHE_LOCK:
        _SEMANTIC_RESULT_CACHE[query_hash] = {"matches": matches, "timestamp": time.time()}


def summarize_semantic_matches(query: str, matches: list[dict], limit: int = 3) -> str:
    """Turn semantically related entries into compact context that can be injected into a model prompt."""
    if not matches:
        return "No relevant semantic memory found."

    strongest = matches[: max(1, int(limit))]
    snippets = []
    for item in strongest:
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        label = (item.get("category") or "memory").strip() or "memory"
        snippets.append(f"{label}: {content[:180]}")

    if not snippets:
        return f"Relevant context for '{query}': no usable memory entries."
    return f"Context for '{query[:80]}': " + "; ".join(snippets)


def save_vector_memory(content: str, category: str = "general") -> dict:
    """Persist a memory fragment as a vector so it can be semantically matched later."""
    if not content or not str(content).strip():
        raise ValueError("content is required")

    _ensure_vector_db()
    embedding = _build_embedding(content)
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(VECTOR_DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO vector_memory (category, content, embedding, created_at) VALUES (?, ?, ?, ?)",
        (category, content.strip(), json.dumps(embedding), created_at),
    )
    conn.commit()
    record_id = cursor.lastrowid
    conn.close()
    return {"id": record_id, "category": category, "content": content.strip(), "score": 1.0}


def reindex_semantic_memory(force: bool = False) -> int:
    """Rebuild semantic embeddings and cache entries so the retrieval pipeline stays aligned with the memory store."""
    _ensure_vector_db()
    _ensure_cache_db()
    conn = sqlite3.connect(VECTOR_DB_PATH)
    rows = conn.execute("SELECT id, category, content, embedding FROM vector_memory").fetchall()
    conn.close()

    if not rows:
        return 0

    reindexed = 0
    for record_id, category, content, embedding_blob in rows:
        try:
            payload = json.loads(embedding_blob) if embedding_blob else None
        except (TypeError, ValueError):
            payload = None
        if not force and payload and len(payload) == _VECTOR_DIMENSIONS:
            continue
        vector = _build_embedding(content or "")
        conn = sqlite3.connect(VECTOR_DB_PATH)
        conn.execute(
            "UPDATE vector_memory SET embedding = ? WHERE id = ?",
            (json.dumps(vector), record_id),
        )
        conn.commit()
        conn.close()
        reindexed += 1

    return reindexed


def semantic_search(query: str, limit: int = 5, category: str | None = None) -> list[dict]:
    """Find semantically related memory entries using cosine similarity over hashed vectors with a reusable cache."""
    if not query or not str(query).strip():
        return []

    cached = _read_cached_semantic_results(query, category)
    if cached is not None:
        return cached[: max(1, int(limit))]

    _ensure_vector_db()
    query_embedding = _build_embedding(query)

    conn = sqlite3.connect(VECTOR_DB_PATH)
    if category:
        rows = conn.execute(
            "SELECT id, category, content, embedding FROM vector_memory WHERE category = ? ORDER BY id DESC",
            (category,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, category, content, embedding FROM vector_memory ORDER BY id DESC"
        ).fetchall()
    conn.close()

    matches = []
    for record_id, item_category, item_content, embedding_blob in rows:
        try:
            stored_vector = json.loads(embedding_blob)
        except (TypeError, ValueError):
            continue
        score = _cosine_similarity(query_embedding, stored_vector)
        if score > 0.0:
            matches.append(
                {
                    "id": record_id,
                    "category": item_category,
                    "content": item_content,
                    "score": round(float(score), 4),
                }
            )

    matches.sort(key=lambda item: item["score"], reverse=True)
    matches = matches[: max(1, int(limit))]
    summary = summarize_semantic_matches(query, matches)
    _write_cached_semantic_results(query, category, matches, summary)
    return matches
