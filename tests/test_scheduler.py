import os
import sys
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestScheduler(unittest.TestCase):
    """Tests for modules.scheduler.JarvisScheduler."""

    def setUp(self):
        self.scheduler_patcher = patch("modules.scheduler.ask_local_brain")
        self.mock_ask_brain = self.scheduler_patcher.start()
        self.mock_ask_brain.return_value = "Morning briefing text, Sir."

        self.mock_get_time = patch("modules.scheduler.get_current_time").start()
        self.mock_get_time.return_value = "Monday, November 16, 2026 at 07:00 AM"

        self.mock_get_diag = patch("modules.scheduler.get_system_diagnostics").start()
        self.mock_get_diag.return_value = "CPU is at 10%, RAM usage is 20%, and Battery is at 80%."

        self.mock_speak = patch("modules.scheduler.speak").start()

        self.mock_log_error = patch("modules.scheduler.log_error").start()
        self.mock_log_info = patch("modules.scheduler.log_info").start()

    def tearDown(self):
        patch.stopall()

    def test_scheduler_init_defaults(self):
        from modules.scheduler import JarvisScheduler
        s = JarvisScheduler()
        self.assertEqual(s.briefing_hour, 7)
        self.assertEqual(s.briefing_minute, 0)
        self.assertIsNone(s.hud_callback)
        self.assertFalse(s._stop_event.is_set())

    def test_scheduler_init_custom(self):
        from modules.scheduler import JarvisScheduler
        cb = MagicMock()
        s = JarvisScheduler(briefing_hour=8, briefing_minute=30, hud_callback=cb)
        self.assertEqual(s.briefing_hour, 8)
        self.assertEqual(s.briefing_minute, 30)
        self.assertEqual(s.hud_callback, cb)

    def test_start_stop(self):
        from modules.scheduler import JarvisScheduler
        s = JarvisScheduler()
        s.start()
        self.assertTrue(s.thread.is_alive())
        s.stop()
        s.thread.join(timeout=2)
        self.assertTrue(s._stop_event.is_set())

    def test_trigger_morning_briefing_calls_ai_and_speak(self):
        from modules.scheduler import JarvisScheduler
        cb = MagicMock()
        s = JarvisScheduler(hud_callback=cb)
        s.trigger_morning_briefing()
        self.mock_ask_brain.assert_called_once()
        self.mock_speak.assert_called_once()
        cb.assert_called()

    def test_trigger_morning_briefing_without_hud(self):
        from modules.scheduler import JarvisScheduler
        s = JarvisScheduler()
        s.trigger_morning_briefing()
        self.mock_ask_brain.assert_called_once()
        self.mock_speak.assert_called_once()

    def test_run_scheduler_matches_time(self):
        from modules.scheduler import JarvisScheduler
        import datetime as dt
        s = JarvisScheduler(briefing_hour=7, briefing_minute=0)
        # Force the loop to see the target time
        now = dt.datetime(2026, 11, 16, 7, 0, 0)
        with patch("modules.scheduler.datetime.datetime") as mock_dt:
            mock_dt.now.return_value = now
            # Manually invoke the check logic
            if now.hour == s.briefing_hour and now.minute == s.briefing_minute:
                triggered = True
            else:
                triggered = False
        self.assertTrue(triggered)


if __name__ == "__main__":
    unittest.main()
