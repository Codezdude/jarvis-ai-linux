"""Regression tests for ai.py fixes: Gemini Tool declarations, duplicate detection, greeting bypass."""

import functools
import json
import unittest
from unittest.mock import MagicMock, patch

from modules.ai import (
    _build_gemini_tools,
    _normalize_schema_for_gemini,
    _query_needs_tools,
    _safe_tool_name,
    ask_local_brain_with_tools,
)


class TestGeminiSchemaUnionType(unittest.TestCase):
    """Gemini rejects JSON-Schema union types (``"type": ["string", "object"]``)."""

    def test_normalize_collapses_union_to_first_non_null(self):
        schema = {
            "type": "object",
            "properties": {
                "pack_input": {"type": ["string", "object"], "description": "pack"},
                "publish_time": {"type": ["string", "null"]},
            },
            "required": ["pack_input"],
        }
        out = _normalize_schema_for_gemini(schema)
        self.assertEqual(out["properties"]["pack_input"]["type"], "string")
        self.assertEqual(out["properties"]["publish_time"]["type"], "string")
        # Original schema is not mutated.
        self.assertEqual(schema["properties"]["pack_input"]["type"], ["string", "object"])

    def test_normalize_recurses_into_items(self):
        schema = {"type": "array", "items": {"type": ["number", "null"]}}
        out = _normalize_schema_for_gemini(schema)
        self.assertEqual(out["items"]["type"], "number")

    def test_normalize_all_null_union_falls_back_to_string(self):
        out = _normalize_schema_for_gemini({"type": ["null"]})
        self.assertEqual(out["type"], "string")

    def test_build_gemini_tools_accepts_union_type_schema(self):
        schemas = [
            {
                "type": "function",
                "function": {
                    "name": "union_tool",
                    "description": "Tool with a union-typed parameter",
                    "parameters": {
                        "type": "object",
                        "properties": {"pack_input": {"type": ["string", "object"]}},
                        "required": ["pack_input"],
                    },
                },
            }
        ]
        tools = _build_gemini_tools(schemas)
        self.assertEqual(len(tools), 1)


class TestGeminiToolDeclaration(unittest.TestCase):
    """Verify _build_gemini_tools creates proper Tool objects, never raw partials."""

    def test_build_gemini_tools_with_partial_callable(self):
        """A functools.partial tool must not cause AttributeError in Gemini's tools config."""
        def dummy_tool(x: str = "") -> str:
            return f"dummy:{x}"

        partial_tool = functools.partial(dummy_tool, x="hello")
        functools.update_wrapper(partial_tool, dummy_tool)

        schemas = [
            {
                "type": "function",
                "function": {
                    "name": "dummy_tool",
                    "description": "A dummy tool",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

        tools = _build_gemini_tools(schemas)
        self.assertEqual(len(tools), 1)
        self.assertTrue(hasattr(tools[0], "function_declarations"))
        self.assertEqual(tools[0].function_declarations[0].name, "dummy_tool")

    def test_build_gemini_tools_returns_empty_without_genai(self):
        """When genai is unavailable, _build_gemini_tools returns an empty list."""
        with patch("modules.ai.genai", None):
            tools = _build_gemini_tools([{"function": {"name": "test"}}])
            self.assertEqual(tools, [])

    def test_gemini_tool_objects_never_contain_partial(self):
        """The tools list must contain Tool objects, not raw callables."""
        schemas = [
            {
                "type": "function",
                "function": {
                    "name": "get_time",
                    "description": "Get current time",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
        tools = _build_gemini_tools(schemas)
        for tool in tools:
            self.assertNotIsInstance(tool, functools.partial)
            self.assertTrue(hasattr(tool, "function_declarations"))


class TestDuplicateToolDetection(unittest.TestCase):
    """Verify the agent loop breaks when the same tool+args is called twice."""

    def setUp(self):
        import modules.ai as ai_mod
        ai_mod._PROVIDER_COOLDOWN.clear()

    def test_kilo_duplicate_tool_call_breaks_loop(self):
        """Kilo returning the same XML tool call twice must break, not loop 5 times."""
        call_count = [0]

        def mock_kilo(messages):
            call_count[0] += 1
            if call_count[0] <= 2:
                return '<dots_function_call><list_directory></list></dots_function_call>'
            return "Done listing."

        dispatched = []

        def mock_dispatch(func_name, func_args):
            dispatched.append((func_name, func_args))
            return "file1.txt"

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
            result = ask_local_brain_with_tools("list files", force_provider="kilo")

        # list_directory should only be dispatched once, not twice
        self.assertEqual(len(dispatched), 1)
        self.assertNotIn("dots_function_call", result)

    def test_groq_duplicate_tool_call_breaks_loop(self):
        """Groq returning the same tool call twice must break, not loop 5 times."""
        call_count = [0]

        def make_response(tool_calls):
            msg = MagicMock()
            msg.tool_calls = tool_calls
            msg.content = None
            resp = MagicMock()
            resp.choices = [MagicMock(message=msg)]
            return resp

        def mock_groq_create(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                tc = MagicMock()
                tc.function.name = "get_current_time"
                tc.function.arguments = "{}"
                tc.id = "call_1"
                return make_response([tc])
            elif call_count[0] == 2:
                tc = MagicMock()
                tc.function.name = "get_current_time"
                tc.function.arguments = "{}"
                tc.id = "call_2"
                return make_response([tc])
            else:
                msg = MagicMock()
                msg.tool_calls = None
                msg.content = "Done."
                resp = MagicMock()
                resp.choices = [MagicMock(message=msg)]
                return resp

        mock_groq = MagicMock()
        mock_groq.chat.completions.create = mock_groq_create

        dispatched = []

        def mock_dispatch(func_name, func_args):
            dispatched.append((func_name, func_args))
            return "time_value"

        with patch("modules.ai.groq_client", mock_groq), \
             patch("modules.ai._dispatch_tool", side_effect=mock_dispatch), \
             patch("modules.ai.cognitive_review_loop", side_effect=lambda q, r: r), \
             patch("modules.ai.get_available_providers", return_value=["groq"]), \
             patch("modules.ai.choose_provider_order", return_value=["groq"]), \
             patch("modules.ai._is_provider_cooling_down", return_value=False), \
             patch("modules.ai.set_active_provider"), \
             patch("modules.ai.save_user_profile_memory", return_value={}), \
             patch("modules.ai.format_preferences_for_prompt", return_value=("", [])), \
             patch("modules.ai.get_relevant_memory_context", return_value=""), \
             patch("modules.ai.trim_and_summarize_conversation", side_effect=lambda h: h), \
             patch("modules.ai._finalize_ai_turn", side_effect=lambda trace, reply, outcome="success": reply):
            result = ask_local_brain_with_tools("what time is it", force_provider="groq")

        # get_current_time should only be dispatched once
        self.assertEqual(len(dispatched), 1)


class TestGreetingBypass(unittest.TestCase):
    """Verify _query_needs_tools returns False for pure greetings."""

    def test_hi_does_not_need_tools(self):
        self.assertFalse(_query_needs_tools("hi"))

    def test_hello_does_not_need_tools(self):
        self.assertFalse(_query_needs_tools("hello"))

    def test_hey_does_not_need_tools(self):
        self.assertFalse(_query_needs_tools("hey"))

    def test_good_morning_does_not_need_tools(self):
        self.assertFalse(_query_needs_tools("good morning"))

    def test_hello_with_punctuation_does_not_need_tools(self):
        self.assertFalse(_query_needs_tools("Hello!"))
        self.assertFalse(_query_needs_tools("Hi, there."))

    def test_file_read_still_needs_tools(self):
        self.assertTrue(_query_needs_tools("read the config file"))

    def test_cpu_diagnostic_still_needs_tools(self):
        self.assertTrue(_query_needs_tools("check cpu usage"))

    def test_non_greeting_still_needs_tools(self):
        self.assertTrue(_query_needs_tools("search for python files"))


if __name__ == "__main__":
    unittest.main()