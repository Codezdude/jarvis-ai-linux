"""Tests for force_provider='ollama' restricting autonomous AI calls to local-only."""

import unittest
from unittest.mock import MagicMock, patch

from modules.ai import ask_local_brain_with_tools


class TestForceProviderOllama(unittest.TestCase):
    """Verify that force_provider='ollama' skips cloud providers entirely."""

    def setUp(self):
        # Reset any cooldown state so provider order isn't affected
        import modules.ai as ai_mod
        ai_mod._PROVIDER_COOLDOWN.clear()

    def _make_ollama_response(self, content="Local response from Ollama."):
        return {"message": {"content": content, "tool_calls": None}}

    def test_cloud_providers_never_called_when_force_provider_ollama(self):
        """When force_provider='ollama', Groq, Gemini, Cloudflare, Kilo, and AGY must not be invoked."""
        cloud_calls = []

        # Track Groq
        mock_groq = MagicMock()
        mock_groq.chat.completions.create.side_effect = (
            lambda *a, **kw: cloud_calls.append("groq") or MagicMock()
        )

        # Track Gemini
        mock_gemini = MagicMock()
        mock_gemini.models.generate_content.side_effect = (
            lambda *a, **kw: cloud_calls.append("gemini") or MagicMock()
        )

        # Track Cloudflare
        with patch("modules.ai.call_cloudflare_workers_ai", side_effect=lambda *a, **kw: cloud_calls.append("cloudflare") or ""):
            # Track Kilo
            with patch("modules.ai.call_kilo_gateway", side_effect=lambda *a, **kw: cloud_calls.append("kilo") or ""):
                # Track AGY
                with patch("modules.ai.call_antigravity_cli", side_effect=lambda *a, **kw: cloud_calls.append("agy") or ""):
                    # Mock ollama to return a valid response
                    mock_ollama = MagicMock()
                    mock_ollama.chat.return_value = self._make_ollama_response()

                    # Simulate Groq being first in provider order (normal routing)
                    with patch("modules.ai.groq_client", mock_groq), \
                         patch("modules.ai.gemini_client", mock_gemini), \
                         patch("modules.ai.ollama", mock_ollama), \
                         patch("modules.ai.get_available_providers", return_value=["groq", "ollama"]), \
                         patch("modules.ai.choose_provider_order", return_value=["groq", "ollama"]), \
                         patch("modules.ai.cognitive_review_loop", side_effect=lambda q, r: r), \
                         patch("modules.ai._is_provider_cooling_down", return_value=False), \
                         patch("modules.ai.set_active_provider"), \
                         patch("modules.ai.save_user_profile_memory", return_value={}), \
                         patch("modules.ai.format_preferences_for_prompt", return_value=("", [])), \
                         patch("modules.ai.get_relevant_memory_context", return_value=""), \
                         patch("modules.ai.trim_and_summarize_conversation", side_effect=lambda h: h), \
                         patch("modules.ai._finalize_ai_turn", side_effect=lambda trace, reply, outcome="success": reply):
                        result = ask_local_brain_with_tools("hello world", force_provider="ollama")

        # Cloud providers must never have been called
        self.assertEqual(cloud_calls, [], f"Cloud providers were called despite force_provider='ollama': {cloud_calls}")
        # Ollama should have been called
        mock_ollama.chat.assert_called_once()
        # Result should be the ollama response
        self.assertEqual(result, "Local response from Ollama.")

    def test_force_provider_none_allows_cloud_failover(self):
        """Without force_provider, Groq should be tried first (normal failover)."""
        cloud_calls = []

        mock_groq = MagicMock()
        mock_groq.chat.completions.create.side_effect = Exception("Groq simulated failure")

        mock_ollama = MagicMock()
        mock_ollama.chat.return_value = self._make_ollama_response("Fallback from Ollama.")

        with patch("modules.ai.groq_client", mock_groq), \
             patch("modules.ai.ollama", mock_ollama), \
             patch("modules.ai.get_available_providers", return_value=["groq", "ollama"]), \
             patch("modules.ai.choose_provider_order", return_value=["groq", "ollama"]), \
             patch("modules.ai.cognitive_review_loop", side_effect=lambda q, r: r), \
             patch("modules.ai._is_provider_cooling_down", return_value=False), \
             patch("modules.ai.set_active_provider"), \
             patch("modules.ai._mark_provider_failed"), \
             patch("modules.ai.save_user_profile_memory", return_value={}), \
             patch("modules.ai.format_preferences_for_prompt", return_value=("", [])), \
             patch("modules.ai.get_relevant_memory_context", return_value=""), \
             patch("modules.ai.trim_and_summarize_conversation", side_effect=lambda h: h), \
             patch("modules.ai._finalize_ai_turn", side_effect=lambda trace, reply, outcome="success": reply):
            result = ask_local_brain_with_tools("hello world", force_provider=None)

        # Groq should have been attempted (and failed)
        mock_groq.chat.completions.create.assert_called_once()
        # Ollama should have been the fallback
        mock_ollama.chat.assert_called_once()
        self.assertEqual(result, "Fallback from Ollama.")


if __name__ == "__main__":
    unittest.main()