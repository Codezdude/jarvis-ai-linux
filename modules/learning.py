import os
from modules.memory import log_long_term_memory
from modules.tools import inspect_recent_logs
from typing import Optional


def record_interaction(query: str, response: str) -> str:
    """Persist a user‑assistant interaction into long‑term memory.
    Stored under the "interaction" category for later retrieval or analysis.
    """
    try:
        content = f"Q: {query}\nA: {response}"
        log_long_term_memory(category="interaction", content=content)
        return "Interaction recorded successfully."
    except Exception as e:
        return f"Failed to record interaction: {e}"


def summarize_recent_logs(max_lines: int = 100) -> str:
    """Return the most recent log excerpt.  This can be fed to the LLM
    for summarisation or learning purposes.
    """
    try:
        logs = inspect_recent_logs(max_lines=max_lines)
        return logs
    except Exception as e:
        return f"Error retrieving logs: {e}"


def auto_learn_from_logs(max_lines: int = 200) -> str:
    """A placeholder for an automatic learning step.  It collects recent logs
    and stores them as a memory entry.  Future implementations could feed the
    logs to a fine‑tuning pipeline.
    """
    logs = summarize_recent_logs(max_lines=max_lines)
    if logs.startswith("Error"):
        return logs
    try:
        log_long_term_memory(category="learning", content=logs)
        return "Recent logs stored for learning."
    except Exception as e:
        return f"Failed to store logs for learning: {e}"
