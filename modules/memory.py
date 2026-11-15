import json
import os
import sqlite3
import uuid
from datetime import datetime

from config.settings import DB_PATH
from modules.db import log_long_term_memory, search_long_term_memory

_RUNTIME_STATE = {}


def set_runtime_state(key: str, value):
    """Store a runtime-only value that is not persisted in the long-term memory database."""
    _RUNTIME_STATE[key] = value
    return value


def get_runtime_state(key: str, default=None):
    """Fetch the current runtime-only value for a given key."""
    return _RUNTIME_STATE.get(key, default)


def clear_runtime_state():
    """Reset runtime-only values after a restart or explicit reset."""
    _RUNTIME_STATE.clear()


def init_db():
    """Ensure the memory, conversation logs, history, and reflection tables exist."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT,
            key TEXT UNIQUE,
            value TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            speaker TEXT,
            message TEXT
        )
        """
    )
    # Unified history table used by the UI drawer
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            query TEXT,
            response TEXT
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS reflections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            category TEXT,
            query TEXT,
            raw_response TEXT,
            final_response TEXT,
            critique TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def save_memory(content: str, category: str = "general") -> str:
    """Save a new memory entry without overwriting older memories."""
    if not content or not content.strip():
        return "No memory content provided, Sir."

    memory_key = uuid.uuid4().hex
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    stored = content.strip()
    cursor.execute(
        "INSERT INTO memory (category, key, value, created_at) VALUES (?, ?, ?, ?)",
        (
            category,
            memory_key,
            stored,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    conn.commit()
    conn.close()

    try:
        from modules.vector_memory import save_vector_memory
        save_vector_memory(stored, category=category)
    except Exception:
        pass

    return f"Saved memory under key: {memory_key}"


def remember(category: str, key: str, value: str):
    """Backward-compatible helper for older memory code."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    stored = str(value)
    cursor.execute(
        "INSERT OR REPLACE INTO memory (category, key, value, created_at) VALUES (?, ?, ?, ?)",
        (category, key, stored, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()
    conn.close()

    try:
        from modules.vector_memory import save_vector_memory
        save_vector_memory(stored, category=category)
    except Exception:
        pass


def recall_memories(limit: int = 20):
    """Return recent chat history records or memories depending on context."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Check if the UI history table has records first
    cursor.execute(
        "SELECT timestamp, query, response FROM history ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    rows = cursor.fetchall()
    
    if rows:
        conn.close()
        return rows  # Returns formatted rows for the UI history drawer
    
    # Fallback to general memory table if history is empty
    cursor.execute(
        "SELECT category, value, created_at FROM memory ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    memory_rows = cursor.fetchall()
    conn.close()

    if not memory_rows:
        return []

    return [(timestamp, category, value) for category, value, timestamp in memory_rows]


def get_recent_history(limit: int = 15):
    """Retrieves recent chat history records for the UI drawer."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT timestamp, query, response FROM history ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = cursor.fetchall()
        conn.close()
        return rows
    except Exception as e:
        print(f"Database read error: {e}")
        return []


def save_interaction(query: str, response: str):
    """Saves a user query and JARVIS response to the UI history table."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO history (query, response, timestamp) VALUES (?, ?, ?)",
            (query, response, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Database save error: {e}")


def save_reflection(query: str, raw_response: str, final_response: str, critique: str = "", category: str = "general") -> str:
    """Persist a reviewed interaction so JARVIS can learn from its own critique and final answer."""
    try:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "INSERT INTO reflections (timestamp, category, query, raw_response, final_response, critique) VALUES (?, ?, ?, ?, ?, ?)",
            (timestamp, category, str(query or ""), str(raw_response or ""), str(final_response or ""), str(critique or "")),
        )
        conn.commit()
        conn.close()
        return "Reflection saved to local database."
    except Exception as exc:
        return f"Failed to save reflection: {exc}"


def get_recent_reflections(limit: int = 20):
    """Get the most recent reflections for learning and memory reuse."""
    try:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT timestamp, category, query, raw_response, final_response, critique FROM reflections ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = cursor.fetchall()
        conn.close()
        return rows
    except Exception:
        return []


def recall_reflections(limit: int = 10):
    """Compatibility wrapper that returns recent reflection entries as reusable memory rows."""
    rows = get_recent_reflections(limit=limit)
    normalized = []
    for row in rows:
        timestamp, category, query, raw_response, final_response, critique = row
        summary = critique or final_response or raw_response or ""
        normalized.append((timestamp, category, query, summary))
    return normalized


def summarize_recent_reflections(limit: int = 10) -> str:
    """Turn the latest reflections into a structured learned guidance object and summary for future memory context."""
    rows = get_recent_reflections(limit=limit)
    if not rows:
        return ""

    learned_points = []
    seen = set()
    for _, category, query_text, raw_response, final_response, critique in rows:
        summary = (critique or final_response or raw_response or "").strip()
        if not summary:
            continue
        clean = " ".join(summary.split())
        if len(clean) < 12:
            continue
        normalized = clean.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        learned_points.append(clean[:220])

    if not learned_points:
        return ""

    # Generate structured guidance from reflections
    structured_guidance = build_learned_preferences_from_reflections(limit=limit)
    
    # Store structured guidance object
    reflection_obj = {
        "guidance": structured_guidance,
        "key_takeaways": learned_points[:5],
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    remember("profile", "reflection_preferences", json.dumps(reflection_obj, ensure_ascii=False))

    # Format actionable summary text
    guidance_parts = [f"{k}: {', '.join(v)}" for k, v in structured_guidance.items() if v and isinstance(v, list)]
    guidance_str = " | ".join(guidance_parts) if guidance_parts else "; ".join(learned_points[:5])
    summary = f"Learned Reflection Guidance: [{guidance_str}]"
    remember("profile", "reflection_summary", summary)
    return summary


def build_learned_preferences_from_reflections(limit: int = 10) -> dict:
    """Convert recent reflections into structured learning preferences that can guide future answers."""
    rows = get_recent_reflections(limit=limit)
    preferences = {
        "tone": [],
        "formats": [],
        "content_goals": [],
        "signals": [],
    }

    for _, category, query_text, raw_response, final_response, critique in rows:
        combined = " ".join(part for part in [query_text, raw_response, final_response, critique] if part)
        text = (combined or "").lower()

        if any(word in text for word in ["premium", "luxury", "professional", "executive", "clean"]):
            preferences["tone"].append("premium professional")
        if any(word in text for word in ["calm", "minimal", "simple", "elegant"]):
            preferences["tone"].append("calm minimal")
        if any(word in text for word in ["carousel", "slides", "storyboard"]):
            preferences["formats"].append("carousel")
        if any(word in text for word in ["cta", "call to action", "value prop", "hook"]):
            preferences["content_goals"].append("strong CTA and clearer value prop")
        if any(word in text for word in ["actionable", "proof", "metrics", "results"]):
            preferences["signals"].append("ground recommendations in evidence")

    cleaned = {}
    for key, values in preferences.items():
        unique = sorted(set(v.strip().lower() for v in values if v and str(v).strip()))
        if unique:
            cleaned[key] = unique[:5]
    if not cleaned:
        cleaned = {"note": ["keep answers practical and concise"]}

    remember("profile", "learned_preferences", json.dumps(cleaned, ensure_ascii=False))
    return cleaned


def save_learned_preferences(preferences: dict) -> dict:
    """Persist structured learned preferences into the memory database."""
    normalized = preferences or {}
    remember("profile", "learned_preferences", json.dumps(normalized, ensure_ascii=False))
    return normalized


def recall(key: str) -> str:
    """Look up a memory by its exact key."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM memory WHERE key = ? ORDER BY id DESC", (key,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else "Data not found in memory, Sir."


def save_preference(key: str, value: str | bool | int | float) -> str:
    """Persist a user preference under the settings category for later recall."""
    remember("settings", key, str(value))
    return str(value)


def get_preference(key: str, default=None):
    """Load a persisted setting by key, falling back to a default when missing."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT value FROM memory WHERE category = ? AND key = ? ORDER BY id DESC LIMIT 1",
        ("settings", key),
    )
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return default
    return row[0]


def recall_by_category(category: str, limit: int = 20):
    """Return a list of memory values for a category."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT value, created_at FROM memory WHERE category = ? ORDER BY id DESC LIMIT ?",
        (category, limit),
    )
    rows = cursor.fetchall()
    conn.close()
    return rows


def search_memories(query: str, limit: int = 20):
    """Search memory entries by text keyword."""
    q = f"%{query.lower()}%"
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT category, value, created_at FROM memory WHERE lower(value) LIKE ? ORDER BY id DESC LIMIT ?",
        (q, limit),
    )
    rows = cursor.fetchall()
    conn.close()
    return rows


def save_structured_memory(category: str, key: str, value: dict):
    """Persist structured memory values in JSON form for profile/topic context."""
    remember(category, key, json.dumps(value, ensure_ascii=False))


def reindex_memory_pipeline(force: bool = False) -> dict:
    """Rebuild the semantic cache and memory index so retrieval stays current after new memories are saved."""
    try:
        from modules.vector_memory import reindex_semantic_memory
        semantic_count = reindex_semantic_memory(force=force)
    except Exception:
        semantic_count = 0

    try:
        from modules.workspace_growth import build_project_memory_index
        build_project_memory_index(force=True)
        memory_index_rebuilt = True
    except Exception:
        memory_index_rebuilt = False

    return {
        "semantic_reindexed": semantic_count,
        "memory_index_rebuilt": memory_index_rebuilt,
    }


def load_structured_memory(category: str, key: str, default=None):
    """Load structured memory values from JSON when present."""
    raw = recall(key)
    if raw == "Data not found in memory, Sir." or raw == "":
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def get_memory_entry(key: str, default: str = "") -> str:
    """Read a single memory entry by key, regardless of category."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT value FROM memory WHERE key = ? ORDER BY id DESC LIMIT 1",
        (key,),
    )
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else default


def log_chat(speaker: str, message: str):
    """Persist messages to the conversation log table."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO conversation_logs (speaker, message) VALUES (?, ?)",
        (speaker, message),
    )
    conn.commit()
    conn.close()