import importlib
import os
import sys
import unittest

from modules import tools
from modules.ai import (
    choose_provider_order,
    extract_user_preferences_from_message,
    get_active_provider,
    get_relevant_memory_context,
    learn_from_analytics,
    save_user_profile_memory,
    set_active_provider,
    set_preferred_provider,
    track_topic_context,
    trim_and_summarize_conversation,
)
from modules.automation import (
    capture_engagement_summary,
    generate_brand_design_concepts,
    generate_social_media_pack,
    review_social_media_pack,
    safe_browser_task,
    schedule_social_media_publish,
)
from modules.memory import get_runtime_state, remember, recall, set_runtime_state

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class SecretConfigTests(unittest.TestCase):
    def test_api_keys_are_loaded_from_environment(self):
        os.environ["GROQ_API_KEY"] = "test-groq-key"
        os.environ["GEMINI_API_KEY"] = "test-gemini-key"

        import modules.ai as ai
        importlib.reload(ai)

        self.assertEqual(ai.GROQ_API_KEY, "test-groq-key")
        self.assertEqual(ai.GEMINI_API_KEY, "test-gemini-key")

    def test_kilo_gateway_key_is_loaded_and_guarded(self):
        import modules.ai as ai

        os.environ["KILO_API_KEY"] = "test-kilo-key"
        importlib.reload(ai)
        self.assertEqual(ai.KILO_API_KEY, "test-kilo-key")
        self.assertIn("KILO_API_KEY", os.environ)

        os.environ["KILO_API_KEY"] = ""
        importlib.reload(ai)
        self.assertEqual(ai.KILO_API_KEY, "")

        os.environ.pop("KILO_API_KEY", None)

    def test_runtime_state_is_the_single_source_of_truth(self):
        set_runtime_state("voice_enabled", True)
        set_runtime_state("ui_state", "thinking")

        self.assertTrue(get_runtime_state("voice_enabled"))
        self.assertEqual(get_runtime_state("ui_state"), "thinking")

        set_runtime_state("voice_enabled", False)
        self.assertFalse(get_runtime_state("voice_enabled"))

    def test_provider_order_is_question_aware(self):
        coding_order = choose_provider_order("debug this Python bug and fix the syntax error", ["groq", "gemini", "kilo", "ollama"])
        self.assertEqual(coding_order[0], "groq")

        research_order = choose_provider_order("what is the latest AI news today", ["groq", "gemini", "kilo", "ollama"])
        self.assertEqual(research_order[0], "gemini")

        set_preferred_provider("kilo")
        forced_order = choose_provider_order("answer generally", ["groq", "gemini", "kilo", "ollama"])
        self.assertEqual(forced_order[0], "kilo")
        set_preferred_provider(None)

        set_preferred_provider("gemini")
        self.assertEqual(get_runtime_state("preferred_provider"), "gemini")
        set_preferred_provider(None)

    def test_active_provider_state_tracks_the_live_route(self):
        set_active_provider("gemini")
        self.assertEqual(get_active_provider(), "gemini")
        self.assertEqual(get_runtime_state("active_provider"), "gemini")

        set_active_provider("auto")
        self.assertEqual(get_active_provider(), "auto")

    def test_conversation_context_stays_coherent_over_time(self):
        history = []
        for index in range(25):
            history.append({"role": "user", "content": f"I want a brand with calm blue tones and premium style. Message {index}"})
            history.append({"role": "assistant", "content": f"Understood, I will keep the palette calm blue and premium. Response {index}"})

        trimmed = trim_and_summarize_conversation(history)

        self.assertLessEqual(len(trimmed), 12)
        self.assertTrue(any(
            item.get("role") == "system" and "calm" in item.get("content", "").lower() and "blue" in item.get("content", "").lower()
            for item in trimmed
        ))

    def test_user_profile_and_topic_memory_are_extracted(self):
        profile = extract_user_preferences_from_message("I prefer calm blue branding and premium style")
        self.assertIn("blue", " ".join(profile.values()).lower())
        self.assertIn("premium", " ".join(profile.values()).lower())

        saved = save_user_profile_memory("I prefer calm blue branding and premium style")
        self.assertIn("blue", saved.get("visual_style", "").lower())

        track_topic_context("branding", "User likes calm blue colors and premium style")
        context = get_relevant_memory_context("brand colors")
        self.assertIn("blue", context.lower())

    def test_automation_capability_is_safe_and_brand_ready(self):
        blocked = safe_browser_task(
            "https://not-allowed.example.com",
            "Review landing page",
            confirmed=False,
        )
        self.assertIn("blocked", blocked.lower())

        pack = generate_social_media_pack(
            brand_name="Nova Studio",
            audience="creators",
            tone="premium and bold",
            platform="instagram",
            colors="blue, gold",
        )
        self.assertIn("Nova Studio", pack)
        self.assertIn("instagram", pack.lower())
        self.assertIn("blue", pack.lower())

        review = review_social_media_pack(pack, approved=False)
        self.assertIn("pending-approval", review.lower())

        scheduled = schedule_social_media_publish(pack)
        self.assertIn("blocked", scheduled.lower())

        approved = review_social_media_pack(pack, approved=True, review_notes="Ready to publish")
        self.assertIn("approved-for-publish", approved.lower())

        scheduled = schedule_social_media_publish(approved)
        self.assertIn("scheduled", scheduled.lower())

    def test_brand_design_and_analytics_pipeline_is_ready(self):
        concepts = generate_brand_design_concepts(
            brand_name="Nova Studio",
            audience="creators",
            tone="premium and bold",
            colors="blue, gold",
        )
        self.assertIn("concepts", concepts.lower())
        self.assertIn("nova", concepts.lower())

        analytics = capture_engagement_summary(
            reach=1400,
            likes=240,
            comments=32,
            saves=64,
            shares=18,
        )
        self.assertIn("engagement", analytics.lower())
        self.assertIn("1.8", analytics)

    def test_analytics_learning_loop_updates_memory(self):
        actual = learn_from_analytics({
            "brand_name": "Nova Studio",
            "platform": "instagram",
            "reach": 1400,
            "likes": 240,
            "comments": 32,
            "saves": 64,
            "shares": 18,
            "engagement_rate": 24.57,
            "engagement_score": 1.77,
            "summary": "High engagement from premium blue creative.",
        })

        self.assertIn("engagement", actual.lower())
        self.assertIn("nova", actual.lower())

        context = get_relevant_memory_context("what should my next content style be for Nova Studio")
        self.assertIn("nova", context.lower())
        self.assertIn("engagement", context.lower())

    def test_voice_state_is_global_and_consistent(self):
        import modules.voice as voice

        voice.set_voice_enabled(False)
        self.assertFalse(voice.get_voice_enabled())

        voice.set_voice_enabled(True)
        self.assertTrue(voice.get_voice_enabled())

    def test_voice_state_initializes_without_unbound_local_error(self):
        import modules.voice as voice

        set_runtime_state("voice_enabled", None)
        value = voice.get_voice_enabled()
        self.assertIsInstance(value, bool)
        self.assertIn(value, (True, False))

    def test_file_writes_require_explicit_confirmation_in_safe_mode(self):
        original_safe_mode = tools.SAFE_MODE
        try:
            tools.SAFE_MODE = True
            result = tools.write_local_file("/tmp/test_write.txt", "hello", confirmed=False)
            self.assertIn("blocked", result.lower())

            result = tools.edit_file_section("/tmp/test_edit.txt", "old", "new", confirmed=False)
            self.assertIn("blocked", result.lower())
        finally:
            tools.SAFE_MODE = original_safe_mode

    def test_user_settings_are_persisted_in_memory(self):
        remember("settings", "voice_enabled", "false")
        self.assertEqual(recall("voice_enabled"), "false")

        remember("settings", "telemetry_visible", "true")
        self.assertEqual(recall("telemetry_visible"), "true")
