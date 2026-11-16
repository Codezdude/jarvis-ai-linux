import os
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestJarvisHUD(unittest.TestCase):
    """Tests for modules.ui.JarvisHUD."""

    @patch("modules.ui.customtkinter")
    @patch("modules.ui.psutil")
    @patch("modules.ui.init_db")
    @patch("modules.ui.get_preference")
    @patch("modules.ui.get_runtime_state")
    @patch("modules.ui.get_voice_enabled")
    @patch("modules.ui.set_voice_enabled")
    @patch("modules.ui.set_preferred_provider")
    @patch("modules.ui.get_active_provider")
    def setUp(self, mock_get_active_provider, mock_set_pref, mock_get_voice,
              mock_set_voice, mock_get_rt, mock_get_pref, mock_init_db,
              mock_psutil, mock_ctk):
        # Configure mock return values
        mock_get_rt.side_effect = lambda key, default=None: {
            "visible": True,
            "ui_state": "idle",
            "opacity": 0.92,
            "show_telemetry": "true",
            "fullscreen_mode": "false",
            "preferred_provider": "auto",
        }.get(key, default)
        mock_get_pref.side_effect = lambda key, default=None: {
            "opacity": 0.92,
            "telemetry_visible": "true",
            "voice_enabled": "true",
            "fullscreen_mode": "false",
            "preferred_provider": "auto",
        }.get(key, default)
        mock_get_voice.return_value = True
        mock_get_active_provider.return_value = "auto"

        # Mock CTk root
        self.mock_root = MagicMock()
        self.mock_root.winfo_screenwidth.return_value = 1920
        self.mock_root.winfo_screenheight.return_value = 1080
        self.mock_root.winfo_x.return_value = 100
        self.mock_root.winfo_y.return_value = 100
        self.mock_root.geometry.return_value = "560x650+100+100"
        mock_ctk.CTk.return_value = self.mock_root

        # Import after patching
        from modules.ui import JarvisHUD
        self.HUD = JarvisHUD

    def test_hud_initialization_sets_defaults(self):
        hud = self.HUD(self.mock_root)
        self.assertEqual(hud.state, "idle")
        self.assertTrue(hud.visible)
        self.assertEqual(hud.voice_enabled, True)
        self.assertEqual(hud.preferred_provider, "auto")

    def test_toggle_hud_hide_show(self):
        hud = self.HUD(self.mock_root)
        hud.visible = True
        hud.hide_hud()
        self.assertFalse(hud.visible)
        self.mock_root.withdraw.assert_called()

        hud.visible = False
        hud.show_hud()
        self.assertTrue(hud.visible)
        self.mock_root.deiconify.assert_called()

    def test_toggle_settings_panel(self):
        hud = self.HUD(self.mock_root)
        hud.settings_visible = False
        hud.history_visible = False
        hud.toggle_settings_panel()
        self.assertTrue(hud.settings_visible)

    def test_toggle_history_panel(self):
        hud = self.HUD(self.mock_root)
        hud.settings_visible = False
        hud.history_visible = False
        hud.toggle_history_panel()
        self.assertTrue(hud.history_visible)

    def test_toggle_telemetry(self):
        hud = self.HUD(self.mock_root)
        hud.show_telemetry = True
        hud.toggle_telemetry()
        self.assertFalse(hud.show_telemetry)

    def test_toggle_voice_feedback(self):
        hud = self.HUD(self.mock_root)
        hud.voice_enabled = True
        hud.toggle_voice_feedback()
        self.assertFalse(hud.voice_enabled)

    def test_toggle_fullscreen(self):
        hud = self.HUD(self.mock_root)
        hud.fullscreen_mode = False
        hud._normal_geometry = None
        hud.toggle_fullscreen()
        self.assertTrue(hud.fullscreen_mode)

    def test_update_ui_queues_message(self):
        hud = self.HUD(self.mock_root)
        hud.update_ui("PROCESSING", "thinking", "test reply")
        self.assertFalse(hud.ui_queue.empty())

    def test_process_ui_queue(self):
        hud = self.HUD(self.mock_root)
        hud.ui_queue.put(("STATUS", "idle", "reply text"))
        hud.process_ui_queue()
        self.assertEqual(hud.status_var.get(), "STATUS")
        self.assertEqual(hud.state, "idle")

    def test_set_opacity(self):
        hud = self.HUD(self.mock_root)
        hud.set_opacity(0.8)
        self.assertEqual(hud.current_opacity, 0.8)
        self.mock_root.attributes.assert_called_with("-alpha", 0.8)

    def test_set_preferred_provider_for_ui_auto(self):
        hud = self.HUD(self.mock_root)
        hud.set_preferred_provider_for_ui("Auto")
        self.assertEqual(hud.preferred_provider, "auto")

    def test_set_preferred_provider_for_ui_groq(self):
        hud = self.HUD(self.mock_root)
        hud.set_preferred_provider_for_ui("Groq")
        self.assertEqual(hud.preferred_provider, "groq")

    def test_refresh_provider_status(self):
        hud = self.HUD(self.mock_root)
        hud.refresh_provider_status()
        self.assertIn("AUTO", hud.provider_status_var.get())


if __name__ == "__main__":
    unittest.main()
