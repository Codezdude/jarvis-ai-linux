import os
import tempfile
import unittest
from unittest.mock import patch

from modules.audit import query_audit_log
from modules.preferences import (
    init_preferences_db,
    set_preference,
    delete_preference,
    resolve_preferences,
    get_preference,
    format_preferences_for_prompt,
)


class TestPreferencesAndConflictResolver(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_prefs.db")
        self.audit_path = os.path.join(self.temp_dir.name, "audit.jsonl")

        self.audit_patcher = patch("modules.audit.DEFAULT_AUDIT_LOG", self.audit_path)
        self.audit_patcher.start()
        init_preferences_db(self.db_path)

    def tearDown(self):
        self.audit_patcher.stop()
        self.temp_dir.cleanup()

    def test_ai_fills_gap_when_no_user_preference_exists(self):
        res = set_preference(
            source="ai",
            key="response.max_bullets",
            value=5,
            weight=0.8,
            actor="learning_loop",
            db_path=self.db_path,
        )
        self.assertTrue(res["applied"])
        self.assertEqual(res["effective_source"], "ai")

        val, src = get_preference("response.max_bullets", db_path=self.db_path)
        self.assertEqual(val, 5)
        self.assertEqual(src, "ai")

    def test_user_preference_strictly_overrides_ai_and_logs_override(self):
        # AI sets initial heuristic
        set_preference(
            source="ai",
            key="code.docstring_style",
            value="numpy",
            weight=0.9,
            db_path=self.db_path,
        )

        # User sets explicit preference on the same key
        res = set_preference(
            source="user",
            key="code.docstring_style",
            value="google",
            actor="user",
            db_path=self.db_path,
        )
        self.assertTrue(res["applied"])
        self.assertEqual(res["effective_source"], "user")

        effective, traced = resolve_preferences(db_path=self.db_path)
        self.assertEqual(effective["code.docstring_style"], "google")
        self.assertIn("user:code.docstring_style", traced)
        self.assertNotIn("ai:code.docstring_style", traced)

        events = query_audit_log(limit=5, log_path=self.audit_path)
        self.assertEqual(events[0]["action"], "preference_user_override")
        self.assertEqual(events[0]["metadata"]["overridden_ai_value"], "numpy")
        self.assertEqual(events[0]["metadata"]["new_user_value"], "google")

    def test_ai_cannot_overwrite_existing_user_preference(self):
        set_preference(
            source="user",
            key="autonomous.allowed_hours",
            value="01:00-05:00",
            db_path=self.db_path,
        )

        res = set_preference(
            source="ai",
            key="autonomous.allowed_hours",
            value="00:00-23:59",
            actor="autonomous_worker",
            db_path=self.db_path,
        )
        self.assertFalse(res["applied"])
        self.assertEqual(res["reason"], "user_preference_precedence")
        self.assertEqual(res["effective_value"], "01:00-05:00")

        events = query_audit_log(limit=5, log_path=self.audit_path)
        self.assertEqual(events[0]["action"], "preference_conflict_blocked")
        self.assertEqual(events[0]["outcome"], "blocked")

    def test_blended_resolution_and_prompt_formatting(self):
        set_preference("user", "tone", "direct", db_path=self.db_path)
        set_preference("ai", "tone", "verbose", db_path=self.db_path)
        set_preference("ai", "retry_delay_sec", 3, db_path=self.db_path)

        prompt_block, traced_keys = format_preferences_for_prompt(db_path=self.db_path)
        self.assertEqual(traced_keys, ["ai:retry_delay_sec", "user:tone"])
        self.assertIn("[USER] tone: direct", prompt_block)
        self.assertIn("[AI] retry_delay_sec: 3", prompt_block)
        self.assertNotIn("verbose", prompt_block)

    def test_delete_user_preference_falls_back_cleanly(self):
        set_preference("ai", "theme", "dark", db_path=self.db_path)
        set_preference("user", "theme", "light", db_path=self.db_path)

        val, src = get_preference("theme", db_path=self.db_path)
        self.assertEqual((val, src), ("light", "user"))

        deleted = delete_preference("user", "theme", db_path=self.db_path)
        self.assertTrue(deleted)

        val_after, src_after = get_preference("theme", db_path=self.db_path)
        self.assertEqual((val_after, src_after), ("dark", "ai"))


if __name__ == "__main__":
    unittest.main()
