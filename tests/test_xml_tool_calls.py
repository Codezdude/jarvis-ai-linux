"""Regression tests for XML-style tool call parsing in OpenAI-compatible providers (Kilo)."""

import unittest
from unittest.mock import MagicMock, patch

from modules.ai import _parse_xml_tool_calls, ask_local_brain_with_tools


class TestXmlToolCallParser(unittest.TestCase):
    """Verify _parse_xml_tool_calls extracts tool names and arguments from XML blocks."""

    def test_dots_function_call_with_tag_name(self):
        """<dots_function_call><list_directory></list></dots_function_call> → list_directory"""
        text = "I'll list the directory. <dots_function_call><list_directory></list></dots_function_call>"
        calls = _parse_xml_tool_calls(text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "list_directory")
        self.assertEqual(calls[0][1], {})

    def test_function_call_with_name_and_arguments_tags(self):
        """<function_call><name>read_file</name><arguments>{"path": "/tmp/x"}</arguments></function_call>"""
        text = '<function_call><name>read_file</name><arguments>{"path": "/tmp/x"}</arguments></function_call>'
        calls = _parse_xml_tool_calls(text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "read_file")
        self.assertEqual(calls[0][1], {"path": "/tmp/x"})

    def test_dots_function_call_with_attributes(self):
        """<dots_function_call><write_file path="/tmp/a" content="hello"></write_file></dots_function_call>"""
        text = '<dots_function_call><write_file path="/tmp/a" content="hello"></write_file></dots_function_call>'
        calls = _parse_xml_tool_calls(text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "write_file")
        self.assertEqual(calls[0][1], {"path": "/tmp/a", "content": "hello"})

    def test_json_inside_dots_function_call(self):
        """<dots_function_call>{"name": "get_time", "arguments": {}}</dots_function_call>"""
        text = '<dots_function_call>{"name": "get_time", "arguments": {}}</dots_function_call>'
        calls = _parse_xml_tool_calls(text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "get_time")

    def test_multiple_xml_blocks(self):
        text = (
            '<dots_function_call><get_current_time></get_current_time></dots_function_call>'
            'Some text in between'
            '<function_call><name>get_system_diagnostics</name></function_call>'
        )
        calls = _parse_xml_tool_calls(text)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], "get_current_time")
        self.assertEqual(calls[1][0], "get_system_diagnostics")

    def test_no_xml_blocks_returns_empty(self):
        calls = _parse_xml_tool_calls("Just a normal response with no tool calls.")
        self.assertEqual(calls, [])

    def test_empty_text_returns_empty(self):
        calls = _parse_xml_tool_calls("")
        self.assertEqual(calls, [])


class TestKiloXmlToolCallIntegration(unittest.TestCase):
    """Verify Kilo provider executes XML tool calls via _dispatch_tool instead of showing raw XML."""

    def setUp(self):
        import modules.ai as ai_mod
        ai_mod._PROVIDER_COOLDOWN.clear()

    def test_kilo_xml_tool_call_executes_dispatch(self):
        """A mock Kilo response with <dots_function_call><list_directory>...</> must call _dispatch_tool."""
        # First call returns XML tool call, second call returns final text
        call_count = [0]

        def mock_kilo(messages):
            call_count[0] += 1
            if call_count[0] == 1:
                return 'Let me check. <dots_function_call><list_directory></list></dots_function_call>'
            return "The directory has been listed."

        dispatched = []

        def mock_dispatch(func_name, func_args):
            dispatched.append((func_name, func_args))
            return "file1.txt\nfile2.py"

        with patch("modules.ai.call_kilo_gateway", side_effect=mock_kilo), \
             patch("modules.ai._dispatch_tool", side_effect=mock_dispatch), \
             patch("modules.ai.cognitive_review_loop", side_effect=lambda q, r: r), \
             patch("modules.ai.get_available_providers", return_value=["kilo"]), \
             patch("modules.ai.choose_provider_order", return_value=["kilo"]), \
             patch("modules.ai._is_provider_cooling_down", return_value=False), \
             patch("modules.ai.set_active_provider"), \
             patch("modules.ai.save_user_profile_memory", return_value={}), \
             patch("modules.ai.format_preferences_for_prompt", return_value=("", [])), \
             patch("modules.ai.get_relevant_memory_context", return_value=""), \
             patch("modules.ai.trim_and_summarize_conversation", side_effect=lambda h: h), \
             patch("modules.ai._finalize_ai_turn", side_effect=lambda trace, reply, outcome="success": reply):
            result = ask_local_brain_with_tools("list the current directory", force_provider="kilo")

        # list_directory must have been dispatched
        self.assertEqual(len(dispatched), 1)
        self.assertEqual(dispatched[0][0], "list_directory")
        # Final result must not contain raw XML tags
        self.assertNotIn("dots_function_call", result)
        self.assertNotIn("function_call", result)
        self.assertEqual(result, "The directory has been listed.")

    def test_kilo_plain_response_no_xml_passes_through(self):
        """A normal Kilo response without XML should pass through unchanged."""
        def mock_kilo(messages):
            return "Just a normal response."

        with patch("modules.ai.call_kilo_gateway", side_effect=mock_kilo), \
             patch("modules.ai._dispatch_tool") as mock_dispatch, \
             patch("modules.ai.cognitive_review_loop", side_effect=lambda q, r: r), \
             patch("modules.ai.get_available_providers", return_value=["kilo"]), \
             patch("modules.ai.choose_provider_order", return_value=["kilo"]), \
             patch("modules.ai._is_provider_cooling_down", return_value=False), \
             patch("modules.ai.set_active_provider"), \
             patch("modules.ai.save_user_profile_memory", return_value={}), \
             patch("modules.ai.format_preferences_for_prompt", return_value=("", [])), \
             patch("modules.ai.get_relevant_memory_context", return_value=""), \
             patch("modules.ai.trim_and_summarize_conversation", side_effect=lambda h: h), \
             patch("modules.ai._finalize_ai_turn", side_effect=lambda trace, reply, outcome="success": reply):
            result = ask_local_brain_with_tools("hello", force_provider="kilo")

        mock_dispatch.assert_not_called()
        self.assertEqual(result, "Just a normal response.")


if __name__ == "__main__":
    unittest.main()