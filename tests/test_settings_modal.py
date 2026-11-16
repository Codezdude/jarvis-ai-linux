import os
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _bare_hud():
    """Build a JarvisHUD without running __init__ (avoids Tk/mock init quirks)."""
    from modules.ui import JarvisHUD

    hud = JarvisHUD.__new__(JarvisHUD)
    hud.root = MagicMock()
    hud.preferred_provider = "auto"
    hud.show_telemetry = True
    hud.voice_enabled = True
    hud.fullscreen_mode = False
    hud.auto_update_enabled = True
    hud.scheduler_enabled = True
    hud._hud_always_on_top = True
    hud._settings_modal = None
    hud.update_ui = MagicMock()
    hud.set_opacity = MagicMock()
    hud.toggle_fullscreen = MagicMock()
    hud.toggle_telemetry = MagicMock()
    hud.toggle_voice_feedback = MagicMock()
    hud.toggle_auto_update = MagicMock()
    hud.toggle_scheduler = MagicMock()
    hud._on_check_updates_click = MagicMock()
    hud._on_clear_history_click = MagicMock()
    hud.set_preferred_provider_for_ui = MagicMock()
    return hud


class TestSettingsModal(unittest.TestCase):
    def test_open_settings_modal_builds_and_tracks_modal(self):
        hud = _bare_hud()
        with patch("modules.ui.ctk", MagicMock()), \
             patch("modules.ui.get_runtime_settings",
                   return_value={"routing_mode": "auto", "daily_cloud_token_cap": 250000}):
            hud._open_settings_modal()
        self.assertIsNotNone(hud._settings_modal)
        # A second open should focus the existing modal, not build a new one
        first = hud._settings_modal
        with patch("modules.ui.ctk", MagicMock()), \
             patch("modules.ui.get_runtime_settings", return_value={}):
            hud._open_settings_modal()
        self.assertIs(hud._settings_modal, first)

    def test_close_settings_modal_clears_reference(self):
        hud = _bare_hud()
        hud._settings_modal = MagicMock()
        hud._close_settings_modal()
        self.assertIsNone(hud._settings_modal)

    def test_save_routing_settings_persists_cap_and_mode(self):
        hud = _bare_hud()
        hud._modal_cap_entry = MagicMock()
        hud._modal_cap_entry.get.return_value = "500000"
        hud._modal_route_menu = MagicMock()
        hud._modal_route_menu.get.return_value = "Ollama only"

        captured = {}

        def _fake_update(updates, settings_path=None):
            captured.update(updates)
            return updates

        with patch("modules.ui.update_runtime_settings", side_effect=_fake_update):
            hud._save_routing_settings()

        self.assertEqual(captured["daily_cloud_token_cap"], 500000)
        self.assertEqual(captured["routing_mode"], "ollama_only")
        hud.update_ui.assert_called_once()

    def test_save_routing_settings_falls_back_on_bad_cap(self):
        hud = _bare_hud()
        hud._modal_cap_entry = MagicMock()
        hud._modal_cap_entry.get.return_value = "not-a-number"
        hud._modal_route_menu = MagicMock()
        hud._modal_route_menu.get.return_value = "Auto"

        captured = {}

        def _fake_update(updates, settings_path=None):
            captured.update(updates)
            return updates

        with patch("modules.ui.update_runtime_settings", side_effect=_fake_update):
            hud._save_routing_settings()

        self.assertEqual(captured["daily_cloud_token_cap"], 250000)
        self.assertEqual(captured["routing_mode"], "auto")

    def test_toggle_always_on_top_flips_flag_and_persists(self):
        hud = _bare_hud()
        hud._hud_always_on_top = False
        captured = {}

        def _fake_update(updates, settings_path=None):
            captured.update(updates)
            return updates

        with patch("modules.ui.update_runtime_settings", side_effect=_fake_update):
            hud._toggle_always_on_top()

        self.assertTrue(hud._hud_always_on_top)
        hud.root.attributes.assert_called_with("-topmost", True)
        self.assertTrue(captured["hud_always_on_top"])


if __name__ == "__main__":
    unittest.main()