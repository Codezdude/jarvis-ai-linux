"""Cognitive review utilities for JARVIS."""

from __future__ import annotations

import json
import os
from typing import Any

from modules.memory import save_reflection


def _safe_parse_profile(raw_profile: Any) -> dict[str, Any]:
    """Convert saved user profile data into a dict when possible."""
    if isinstance(raw_profile, dict):
        return raw_profile
    if not raw_profile:
        return {}
    try:
        parsed = json.loads(str(raw_profile))
        if isinstance(parsed, dict):
            return parsed
    except (TypeError, ValueError):
        pass
    return {}


def cognitive_review_loop(query: str, raw_response: str) -> str:
    """
    Simulates a cognitive mind that reviews model answers against
    stored memory, constraints, and experience before returning them.
    """
    from modules.memory import get_memory_entry, recall_by_category

    try:
        from modules.ai import CLOUD_MODEL_GROQ, groq_client
    except Exception:  # pragma: no cover - optional dependency path
        groq_client = None
        CLOUD_MODEL_GROQ = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    user_profile = _safe_parse_profile(get_memory_entry("user_profile", "{}"))
    analytics_history = recall_by_category("analytics", limit=3)

    corrections: list[str] = []

    if "carousel" in (query or "").lower() and len((raw_response or "").split()) < 30:
        corrections.append("Expand the carousel structure to include concrete hooks and slide breakdown.")

    profile_summary = user_profile.get("profile_summary") if isinstance(user_profile, dict) else ""
    if profile_summary and "professional" not in (raw_response or "").lower():
        corrections.append("Keep the response aligned with the user profile and business-professional tone.")

    if analytics_history:
        trend_note = str(analytics_history[0]) if analytics_history else ""
        if "underperform" in trend_note.lower() and "actionable" not in (raw_response or "").lower():
            corrections.append("Ground the answer in recent performance insights and propose concrete optimizations.")

    critique_text = "; ".join(corrections) if corrections else "No material critique needed."
    final_value = raw_response

    if False and corrections:
        refinement_prompt = (
            f"Original Query: {query}\n"
            f"Initial Draft: {raw_response}\n"
            f"Critique / Experience Rules to Apply: {critique_text}\n"
            "Rewrite the response to be sharper, deeply customized to your project experience, and fully actionable."
        )

        try:
            if groq_client:
                res = groq_client.chat.completions.create(
                    model=CLOUD_MODEL_GROQ,
                    messages=[{"role": "user", "content": refinement_prompt}],
                )
                refined = res.choices[0].message.content
                if refined:
                    final_value = refined.strip()
        except Exception:
            pass

    save_reflection(
        query=query,
        raw_response=raw_response,
        final_response=final_value,
        critique=critique_text,
        category="reflection",
    )
    return final_value
