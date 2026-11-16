"""Session context compaction, token budgeting, and automatic preference mining."""

import datetime
import json
import os
import re
import sqlite3
from typing import Optional

from config.settings import DB_PATH

DEFAULT_DB_PATH = DB_PATH


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def estimate_tokens(messages: list[dict]) -> int:
    """Rough token estimate: ~4 characters per token."""
    return sum(max(1, len(str(m.get("content", ""))) // 4) for m in messages)


def _fetch_session_messages(session_id: str, db_path: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, role, content FROM chat_messages WHERE session_id = ? ORDER BY id ASC",
        (session_id,),
    ).fetchall()
    conn.close()
    return [{"id": r["id"], "role": r["role"], "content": r["content"]} for r in rows]


def _summarize(older_messages: list[dict], prior_summary: str) -> str:
    """Summarize older turns via local Ollama, with a deterministic truncation fallback."""
    transcript = "\n".join(f"{m['role']}: {m['content']}" for m in older_messages)
    prompt = (
        "Summarize the following conversation turns into a concise context summary "
        "preserving key facts, decisions, and user preferences. Keep it under 200 words.\n\n"
        f"Existing summary: {prior_summary or '(none)'}\n\nConversation:\n{transcript}"
    )
    try:
        from modules.ai import ask_local_brain

        summary = ask_local_brain(prompt, force_provider="ollama")
        if summary and not summary.lower().startswith("local brain error"):
            return summary.strip()
    except Exception:
        pass
    # Deterministic fallback: truncate older content
    joined = " ".join(m["content"] for m in older_messages)
    base = prior_summary or ""
    return (base + " " + joined).strip()[:1500]


def _prune_rows(ids: list[int], db_path: str) -> None:
    if not ids:
        return
    conn = sqlite3.connect(db_path)
    placeholders = ",".join("?" * len(ids))
    conn.execute(f"DELETE FROM chat_messages WHERE id IN ({placeholders})", ids)
    conn.commit()
    conn.close()


def compact_session_if_needed(
    session_id: str = "default",
    max_messages: int = 24,
    keep_recent: int = 10,
    db_path: str = None,
) -> Optional[str]:
    """Compact older turns into a rolling summary when a session exceeds ``max_messages``.

    Returns the updated summary string if compaction happened, else ``None``.
    """
    db_path = db_path or DEFAULT_DB_PATH
    messages = _fetch_session_messages(session_id, db_path)
    if len(messages) <= max_messages:
        return None

    older = messages[:-keep_recent]
    if not older:
        return None

    from modules.memory import get_session_summary, save_session_summary

    prior_summary = get_session_summary(session_id, db_path=db_path)
    new_summary = _summarize(older, prior_summary)
    save_session_summary(session_id, new_summary, db_path=db_path)
    _prune_rows([m["id"] for m in older], db_path)
    return new_summary


def enforce_context_budget(session_id: str = "default", max_context_tokens: int = 8192, db_path: str = None) -> bool:
    """Compact the session if it exceeds 75% of the token budget. Returns True if compacted."""
    db_path = db_path or DEFAULT_DB_PATH
    messages = _fetch_session_messages(session_id, db_path)
    if estimate_tokens(messages) > int(max_context_tokens * 0.75):
        return compact_session_if_needed(session_id=session_id, db_path=db_path) is not None
    return False


_DIRECTIVE_RE = re.compile(r"\b(always|never|prefer|do not|don't)\b.+")


def mine_and_upsert_preferences(messages: list[dict], db_path: str = None) -> list[dict]:
    """Scan user messages for directives and upsert structured preferences.

    Returns the list of preference dicts that were applied.
    """
    db_path = db_path or DEFAULT_DB_PATH
    extracted: list[dict] = []

    for m in messages:
        if m.get("role") != "user":
            continue
        content = str(m.get("content", ""))
        if not _DIRECTIVE_RE.search(content):
            continue
        # Try local LLM extraction first
        parsed = None
        try:
            from modules.ai import ask_local_brain

            prompt = (
                "Extract user preferences from the sentence. Return ONLY compact JSON like "
                '{"preferences": [{"key": "...", "value": "...", "rule_type": "..."}]}. '
                f"Sentence: {content}"
            )
            raw = ask_local_brain(prompt, force_provider="ollama")
            if raw:
                start = raw.find("{")
                end = raw.rfind("}")
                if start != -1 and end != -1:
                    parsed = json.loads(raw[start:end + 1]).get("preferences")
        except Exception:
            parsed = None

        if not parsed:
            # Deterministic fallback: store the directive verbatim
            parsed = [{"key": "directive", "value": content.strip(), "rule_type": "instruction"}]

        for pref in parsed:
            key = str(pref.get("key", "")).strip()
            value = pref.get("value", "")
            if not key:
                continue
            try:
                from modules.preferences import set_preference

                set_preference(source="user", key=key, value=value, actor="preference_miner")
                extracted.append({"key": key, "value": value})
            except Exception:
                continue

    return extracted