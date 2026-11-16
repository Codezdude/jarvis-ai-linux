import os
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestJarvisProcessCommand(unittest.TestCase):
    """Tests for jarvis.py process_command function."""

    def setUp(self):
        # Patch all module dependencies before importing jarvis
        self.mock_ctk = patch("jarvis.ctk").start()
        self.mock_ai = patch("jarvis.ask_local_brain_with_tools").start()
        self.mock_log_error = patch("jarvis.log_error").start()
        self.mock_log_info = patch("jarvis.log_info").start()
        self.mock_init_db = patch("jarvis.init_db").start()
        self.mock_save_memory = patch("jarvis.save_memory").start()
        self.mock_recall_memories = patch("jarvis.recall_memories").start()
        self.mock_save_interaction = patch("jarvis.save_interaction").start()
        self.mock_speak = patch("jarvis.speak").start()
        self.mock_scheduler_cls = patch("jarvis.JarvisScheduler").start()
        self.mock_keyboard = patch("jarvis.keyboard").start()

        self.mock_scheduler = MagicMock()
        self.mock_scheduler_cls.return_value = self.mock_scheduler

        # Reset global state
        import jarvis
        jarvis.hud_instance = None
        jarvis.scheduler = None

    def tearDown(self):
        patch.stopall()

    def _import_jarvis(self):
        import jarvis
        return jarvis

    def test_process_command_remember_trigger(self):
        jarvis = self._import_jarvis()
        self.mock_save_memory.return_value = "Saved memory under key: abc123"

        def fake_callback(status, state, text):
            pass

        result = jarvis.process_command("jarvis, remember that I like blue", fake_callback)
        self.mock_save_memory.assert_called_once()
        call_args = self.mock_save_memory.call_args
        self.assertIn("I like blue", str(call_args))

    def test_process_command_recall_trigger(self):
        jarvis = self._import_jarvis()
        self.mock_recall_memories.return_value = [
            ("2026-11-16", "preference", "blue"),
        ]

        def fake_callback(status, state, text):
            pass

        jarvis.process_command("what do you remember", fake_callback)
        self.mock_recall_memories.assert_called_once()

    def test_process_command_morning_briefing(self):
        jarvis = self._import_jarvis()
        jarvis.scheduler = self.mock_scheduler

        def fake_callback(status, state, text):
            pass

        jarvis.process_command("morning briefing", fake_callback)
        self.mock_scheduler.trigger_morning_briefing.assert_called_once()

    def test_process_command_autonomous_fallback(self):
        jarvis = self._import_jarvis()
        self.mock_ai.return_value = "Autonomous response, Sir."

        def fake_callback(status, state, text):
            pass

        jarvis.process_command("explain quantum physics", fake_callback)
        self.mock_ai.assert_called_once_with("explain quantum physics")

    def test_process_command_error_handling(self):
        jarvis = self._import_jarvis()
        self.mock_ai.side_effect = Exception("AI failure")

        def fake_callback(status, state, text):
            pass

        jarvis.process_command("trigger error", fake_callback)
        self.mock_log_error.assert_called()
        self.mock_save_interaction.assert_called()

    def test_setup_hotkeys_success(self):
        jarvis = self._import_jarvis()
        jarvis.hud_instance = MagicMock()
        jarvis.setup_hotkeys()
        self.mock_keyboard.GlobalHotKeys.assert_called_once()

    def test_setup_hotkeys_failure(self):
        jarvis = self._import_jarvis()
        jarvis.hud_instance = MagicMock()
        self.mock_keyboard.GlobalHotKeys.side_effect = Exception("Hotkey failed")
        jarvis.setup_hotkeys()
        self.mock_log_error.assert_called()


if __name__ == "__main__":
    unittest.main()
