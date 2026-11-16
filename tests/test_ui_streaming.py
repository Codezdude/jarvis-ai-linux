import os
import sys
import queue
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class FakeTextbox:
    """Minimal stand-in for a CTkTextbox that records inserted text."""

    def __init__(self):
        self.buffer = []
        self.state = "disabled"
        self.seen = 0

    def configure(self, **kwargs):
        if "state" in kwargs:
            self.state = kwargs["state"]

    def insert(self, _index, text):
        self.buffer.append(text)

    def see(self, _index):
        self.seen += 1

    def get_text(self):
        return "".join(self.buffer)


def _bare_hud():
    from modules.ui import JarvisHUD

    hud = JarvisHUD.__new__(JarvisHUD)
    hud.root = MagicMock()
    hud.ui_queue = queue.Queue()
    hud._chunk_queue = queue.Queue()
    hud._live_assistant_buffer = None
    hud._queue_job = None
    hud._rendered_messages = []
    hud._current_session_id = "default"
    hud.visible = True
    hud.state = "idle"
    hud.response_box = FakeTextbox()
    hud.status_var = MagicMock()
    hud.refresh_provider_status = MagicMock()
    return hud


class TestUIStreaming(unittest.TestCase):
    def test_push_and_drain_creates_live_bubble(self):
        hud = _bare_hud()
        hud.push_chunk("Hello ")
        hud.push_chunk("world")
        hud._drain_chunks()
        self.assertEqual(hud.response_box.get_text(), "JARVIS: Hello world")
        self.assertEqual(hud._live_assistant_buffer, "Hello world")

    def test_close_live_assistant_adds_separator_and_persists(self):
        hud = _bare_hud()
        hud.push_chunk("Done")
        hud._drain_chunks()
        with patch("modules.ui.save_chat_message") as mock_save, \
             patch("modules.ui.threading"):
            hud._close_live_assistant("Done")
        self.assertIsNone(hud._live_assistant_buffer)
        self.assertIn("\u2500" * 30, hud.response_box.get_text())
        mock_save.assert_called_once()
        self.assertEqual(len(hud._rendered_messages), 1)
        self.assertEqual(hud._rendered_messages[0]["role"], "assistant")

    def test_close_with_final_text_when_buffer_empty(self):
        hud = _bare_hud()
        hud._live_assistant_buffer = ""  # bubble created but no chunks landed
        with patch("modules.ui.save_chat_message"), patch("modules.ui.threading"):
            hud._close_live_assistant("Full reply")
        self.assertEqual(hud._rendered_messages[0]["content"], "Full reply")

    def test_process_ui_queue_streams_then_closes_without_duplicate(self):
        hud = _bare_hud()
        # Simulate: chunks queued, then the final reply status queued
        hud.push_chunk("Streamed ")
        hud.push_chunk("answer")
        hud.update_ui = MagicMock()  # avoid scheduling side effects
        hud.ui_queue.put(("STANDBY // READY", "idle", "Streamed answer"))
        with patch("modules.ui.save_chat_message") as mock_save, \
             patch("modules.ui.set_runtime_state"), \
             patch("modules.ui.threading"):
            hud.process_ui_queue()
        text = hud.response_box.get_text()
        # The reply text must appear exactly once (no duplicate assistant bubble)
        self.assertEqual(text.count("Streamed answer"), 1)
        self.assertIn("\u2500" * 30, text)
        mock_save.assert_called_once()

    def test_process_ui_queue_no_stream_appends_single_bubble(self):
        hud = _bare_hud()
        hud.ui_queue.put(("STANDBY // READY", "idle", "Plain reply"))
        with patch("modules.ui.save_chat_message"), \
             patch("modules.ui.set_runtime_state"), \
             patch("modules.ui.threading"):
            hud.process_ui_queue()
        self.assertEqual(hud.response_box.get_text().count("Plain reply"), 1)
        self.assertEqual(hud._live_assistant_buffer, None)


class TestCodeBlockAndBadges(unittest.TestCase):
    def test_extract_last_code_block(self):
        from modules.ui import _extract_last_code_block
        text = "intro\n```python\nprint(1)\n```\nthen\n```\nx=2\n```"
        self.assertEqual(_extract_last_code_block(text), "x=2\n")
        self.assertIsNone(_extract_last_code_block("no code here"))

    def test_latest_tool_badge_from_trace_dict(self):
        hud = _bare_hud()
        trace = {"tools_invoked": ["get_time", "read_file", "get_time"]}
        with patch("modules.ui.get_latest_decision_trace", return_value=trace):
            badge = hud._latest_tool_badge()
        self.assertIn("get_time", badge)
        self.assertIn("read_file", badge)
        # De-duplicated
        self.assertEqual(badge.count("get_time"), 1)

    def test_latest_tool_badge_none_without_tools(self):
        hud = _bare_hud()
        with patch("modules.ui.get_latest_decision_trace", return_value={"tools_invoked": []}):
            self.assertIsNone(hud._latest_tool_badge())
        with patch("modules.ui.get_latest_decision_trace", return_value=None):
            self.assertIsNone(hud._latest_tool_badge())

    def test_append_chat_bubble_includes_badge(self):
        hud = _bare_hud()
        hud._append_chat_bubble("assistant", "Here is code:", badge="\u2699 tools invoked: x")
        text = hud.response_box.get_text()
        self.assertIn("tools invoked: x", text)
        self.assertIn("\u2500" * 30, text)

    def test_copy_last_code_block_copies_to_clipboard(self):
        hud = _bare_hud()
        hud._rendered_messages = [
            {"role": "assistant", "content": "see\n```py\nprint('hi')\n```"},
        ]
        hud.update_ui = MagicMock()
        hud._copy_last_code_block()
        hud.root.clipboard_clear.assert_called_once()
        hud.root.clipboard_append.assert_called_once_with("print('hi')\n")

    def test_copy_last_code_block_reports_when_missing(self):
        hud = _bare_hud()
        hud._rendered_messages = [{"role": "assistant", "content": "no code"}]
        hud.update_ui = MagicMock()
        hud._copy_last_code_block()
        hud.update_ui.assert_called_once()
        args = hud.update_ui.call_args[0]
        self.assertEqual(args[0], "NO CODE")
        hud.root.clipboard_append.assert_not_called()


if __name__ == "__main__":
    unittest.main()