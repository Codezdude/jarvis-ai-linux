"""Regression tests for functools.partial tool callable handling in Gemini provider."""

import functools
import unittest

from modules.ai import _safe_tool_name


def _dummy_tool(query: str = "") -> str:
    """A dummy tool function for testing."""
    return f"dummy:{query}"


class TestPartialToolNameResolution(unittest.TestCase):
    """Verify functools.partial tool callables don't break Gemini's __name__ access."""

    def test_safe_tool_name_resolves_regular_function(self):
        self.assertEqual(_safe_tool_name(_dummy_tool), "_dummy_tool")

    def test_safe_tool_name_resolves_partial_via_func_attribute(self):
        partial_tool = functools.partial(_dummy_tool, query="hello")
        # functools.partial itself has no __name__, but _safe_tool_name walks .func
        self.assertEqual(_safe_tool_name(partial_tool), "_dummy_tool")

    def test_safe_tool_name_resolves_update_wrapped_partial(self):
        """functools.update_wrapper copies __name__ so even direct access works."""
        partial_tool = functools.partial(_dummy_tool, query="hello")
        functools.update_wrapper(partial_tool, _dummy_tool)
        # Now __name__ is directly accessible
        self.assertEqual(partial_tool.__name__, "_dummy_tool")
        self.assertEqual(_safe_tool_name(partial_tool), "_dummy_tool")

    def test_partial_without_update_wrapper_has_no_name_attr(self):
        """Without update_wrapper, partial has no __name__ — _safe_tool_name must handle it."""
        partial_tool = functools.partial(_dummy_tool, query="hello")
        with self.assertRaises(AttributeError):
            _ = partial_tool.__name__
        # But _safe_tool_name resolves it safely
        self.assertEqual(_safe_tool_name(partial_tool), "_dummy_tool")

    def test_gemini_tool_list_with_partial_does_not_raise(self):
        """Simulate Gemini's GenerateContentConfig(tools=[...]) accessing __name__ on all callables."""
        partial_tool = functools.partial(_dummy_tool, query="test")
        functools.update_wrapper(partial_tool, _dummy_tool)

        tool_callables = [_dummy_tool, partial_tool]

        # This simulates what the Gemini SDK does internally: access __name__ on each
        names = []
        for fn in tool_callables:
            name = getattr(fn, "__name__", _safe_tool_name(fn))
            names.append(name)

        self.assertEqual(names, ["_dummy_tool", "_dummy_tool"])


if __name__ == "__main__":
    unittest.main()
