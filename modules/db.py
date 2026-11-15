import os
import sqlite3
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    from config.settings import DB_PATH as SETTINGS_DB_PATH

    DB_PATH = SETTINGS_DB_PATH
except Exception:
    DB_PATH = os.path.join(BASE_DIR, "memory", "jarvis_memory.db")


def init_db():
    """Initialize the SQLite database and create the long-term memory table."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS long_term_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            category TEXT,
            content TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def log_long_term_memory(category: str, content: str) -> str:
    """Save a structured note, preference, or project milestone to the database."""
    try:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "INSERT INTO long_term_memory (timestamp, category, content) VALUES (?, ?, ?)",
            (timestamp, category, content),
        )
        conn.commit()
        conn.close()
        return f"Successfully logged memory under category '{category}'."
    except Exception as e:
        return f"Error logging memory: {e}"


def search_long_term_memory(query: str) -> str:
    """Search past long-term memories for keywords or category matches."""
    try:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT timestamp, category, content FROM long_term_memory WHERE content LIKE ? OR category LIKE ? ORDER BY id DESC",
            (f"%{query}%", f"%{query}%"),
        )
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return f"No memories found matching '{query}'."

        results = [f"[{row[0]}] ({row[1]}): {row[2]}" for row in rows]
        return "Found memories:\n" + "\n".join(results)
    except Exception as e:
        return f"Error searching memory: {e}"
