import json
import os

from config.settings import BASE_DIR
from modules.memory import remember, search_memories


MEMORY_MD = os.path.join(BASE_DIR, "memory.md")


def _ensure_memory_file():
    if not os.path.exists(MEMORY_MD):
        with open(MEMORY_MD, "w", encoding="utf-8") as handle:
            handle.write("# JARVIS memory\n\n")


def sync_memory_snapshot(limit: int = 20) -> dict:
    """Create a shared memory snapshot from SQLite entries and the markdown memory file."""
    _ensure_memory_file()
    rows = search_memories("", limit=limit)
    snapshot = {
        "sqlite_entries": [
            {"category": category, "value": value, "created_at": created_at}
            for category, value, created_at in rows
        ],
        "memory_md": "",
    }

    with open(MEMORY_MD, "r", encoding="utf-8", errors="ignore") as handle:
        snapshot["memory_md"] = handle.read()

    return snapshot


def append_shared_note(category: str, content: str) -> str:
    """Persist a note both in SQLite and the markdown memory file so all agents share the same state."""
    if not category or not str(content).strip():
        raise ValueError("category and content are required")

    entry = {
        "category": category,
        "content": content.strip(),
    }
    remember(category, f"shared_note_{len(search_memories('', limit=1000))}", json.dumps(entry, ensure_ascii=False))

    _ensure_memory_file()
    with open(MEMORY_MD, "a", encoding="utf-8") as handle:
        handle.write(f"\n## {category}\n{content.strip()}\n")

    return f"Shared memory note saved under category '{category}'."


def get_shared_memory_context(limit: int = 20) -> str:
    """Return a compact memory context for agents and tools working inside the same project."""
    snapshot = sync_memory_snapshot(limit=limit)
    lines = []
    for item in snapshot.get("sqlite_entries", [])[:limit]:
        lines.append(f"[{item.get('category', 'general')}]: {item.get('value', '')}")
    if snapshot.get("memory_md"):
        lines.append("--- memory.md ---")
        lines.append(snapshot["memory_md"][:1200])
    return "\n".join(lines)
