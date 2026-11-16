import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestChatSessions(unittest.TestCase):
    """Cycle 2: session isolation, chronological hydration, rewind on edit."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "chat.db")
        self.db_patcher = patch("modules.memory.DB_PATH", self.db_path)
        self.db_patcher.start()
        # Avoid touching the real vector index during message saves.
        self.index_patcher = patch("modules.memory.index_memory_entry", return_value=None)
        self.index_patcher.start()
        from modules.memory import init_db
        init_db()

    def tearDown(self):
        self.index_patcher.stop()
        self.db_patcher.stop()
        self.temp_dir.cleanup()

    def test_session_isolation(self):
        from modules.memory import save_chat_message, get_recent_history

        save_chat_message("user", "hello in default", session_id="default")
        save_chat_message("user", "hello in other", session_id="other")

        default_hist = get_recent_history(session_id="default")
        other_hist = get_recent_history(session_id="other")

        self.assertEqual(len(default_hist), 1)
        self.assertEqual(default_hist[0]["content"], "hello in default")
        self.assertEqual(len(other_hist), 1)
        self.assertEqual(other_hist[0]["content"], "hello in other")

    def test_chronological_hydration(self):
        from modules.memory import save_chat_message, get_recent_history

        save_chat_message("user", "first", session_id="default")
        save_chat_message("assistant", "second", session_id="default")
        save_chat_message("user", "third", session_id="default")

        hist = get_recent_history(session_id="default")
        self.assertEqual([m["content"] for m in hist], ["first", "second", "third"])
        # ids ascend in chronological order
        self.assertLess(hist[0]["id"], hist[-1]["id"])

    def test_delete_history_from_id_rewinds(self):
        from modules.memory import save_chat_message, get_recent_history, delete_history_from_id

        id1 = save_chat_message("user", "keep", session_id="default")
        save_chat_message("assistant", "drop1", session_id="default")
        id3 = save_chat_message("user", "drop2", session_id="default")

        deleted = delete_history_from_id(id3, session_id="default")
        remaining = get_recent_history(session_id="default")

        # Rewind removes the edited message and everything after it; earlier turns stay.
        self.assertEqual(deleted, 1)
        self.assertEqual([m["content"] for m in remaining], ["keep", "drop1"])
        self.assertTrue(all(m["id"] < id3 for m in remaining))

    def test_rewind_is_session_scoped(self):
        from modules.memory import save_chat_message, get_recent_history, delete_history_from_id

        save_chat_message("user", "a1", session_id="default")
        target = save_chat_message("user", "a2", session_id="default")
        save_chat_message("user", "b1", session_id="other")

        delete_history_from_id(target, session_id="default")
        # 'other' session must be untouched
        self.assertEqual(len(get_recent_history(session_id="other")), 1)

    def test_list_sessions_includes_default_and_recent(self):
        from modules.memory import save_chat_message, list_sessions

        save_chat_message("user", "x", session_id="default")
        save_chat_message("user", "y", session_id="chat_123")
        sessions = list_sessions()
        self.assertIn("default", sessions)
        self.assertIn("chat_123", sessions)

    def test_session_summary_roundtrip(self):
        from modules.memory import save_session_summary, get_session_summary

        save_session_summary("default", "my summary")
        self.assertEqual(get_session_summary("default"), "my summary")


if __name__ == "__main__":
    unittest.main()