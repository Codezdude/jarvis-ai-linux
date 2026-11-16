import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestEstimateTokens(unittest.TestCase):
    def test_estimate(self):
        from modules.memory_consolidation import estimate_tokens
        self.assertEqual(estimate_tokens([{"content": "a" * 40}]), 10)
        self.assertEqual(estimate_tokens([]), 0)
        # Short content still counts as at least 1 token per message.
        self.assertEqual(estimate_tokens([{"content": ""}, {"content": "hi"}]), 2)


class TestContextBudgetDB(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "budget.db")
        self.db_patcher = patch("modules.memory.DB_PATH", self.db_path)
        self.db_patcher.start()
        self.index_patcher = patch("modules.memory.index_memory_entry", return_value=None)
        self.index_patcher.start()
        from modules.memory import init_db
        init_db()

    def tearDown(self):
        self.index_patcher.stop()
        self.db_patcher.stop()
        self.temp_dir.cleanup()

    def _seed(self, n, role="user", content_len=40):
        from modules.memory import save_chat_message
        for i in range(n):
            save_chat_message(role, f"msg{i}_" + "x" * content_len, session_id="default")

    def test_compact_when_over_max_messages(self):
        from modules.memory_consolidation import compact_session_if_needed
        from modules.memory import get_recent_history

        self._seed(30)
        with patch("modules.ai.ask_local_brain", return_value="SUMMARY"):
            summary = compact_session_if_needed(
                session_id="default", max_messages=24, keep_recent=10, db_path=self.db_path
            )
        self.assertEqual(summary, "SUMMARY")
        remaining = get_recent_history(session_id="default", db_path=self.db_path)
        self.assertLessEqual(len(remaining), 10)

    def test_no_compaction_when_under_limit(self):
        from modules.memory_consolidation import compact_session_if_needed
        self._seed(5)
        result = compact_session_if_needed(
            session_id="default", max_messages=24, keep_recent=10, db_path=self.db_path
        )
        self.assertIsNone(result)

    def test_enforce_context_budget_triggers_compaction(self):
        from modules.memory_consolidation import enforce_context_budget
        # 10 long messages => ~ (10 * 50 chars / 4) ~ 125 tokens; force a tiny budget.
        self._seed(10, content_len=200)
        compacted = enforce_context_budget(
            session_id="default", max_context_tokens=200, db_path=self.db_path
        )
        self.assertTrue(compacted)

    def test_enforce_context_budget_noop_when_small(self):
        from modules.memory_consolidation import enforce_context_budget
        self._seed(2, content_len=10)
        self.assertFalse(
            enforce_context_budget(session_id="default", max_context_tokens=8192, db_path=self.db_path)
        )

    def test_mine_and_upsert_preferences(self):
        from modules.memory_consolidation import mine_and_upsert_preferences

        captured = []

        def _fake_set(**kwargs):
            captured.append(kwargs)

        messages = [{"role": "user", "content": "I always prefer dark mode"}]
        with patch("modules.ai.ask_local_brain", return_value="not json"), \
             patch("modules.preferences.set_preference", side_effect=_fake_set):
            extracted = mine_and_upsert_preferences(messages, db_path=self.db_path)

        self.assertTrue(extracted)
        self.assertTrue(any(c.get("key") == "directive" for c in captured))


class TestApplyContextBudget(unittest.TestCase):
    def test_trims_old_turns_preserving_system(self):
        from modules.ai import _apply_context_budget

        history = [{"role": "system", "content": "sys"}] + [
            {"role": "user" if i % 2 == 0 else "assistant", "content": "y" * 4000}
            for i in range(10)
        ]
        trimmed = _apply_context_budget(history, max_context_tokens=2000)
        # System preserved
        self.assertEqual(trimmed[0]["role"], "system")
        # Fewer messages than input
        self.assertLess(len(trimmed), len(history))
        # Most recent message retained
        self.assertEqual(trimmed[-1]["content"], history[-1]["content"])

    def test_under_budget_returns_unchanged(self):
        from modules.ai import _apply_context_budget
        history = [{"role": "user", "content": "short"}]
        self.assertEqual(_apply_context_budget(history, max_context_tokens=8192), history)


if __name__ == "__main__":
    unittest.main()