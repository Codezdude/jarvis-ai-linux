import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.tools import run_dynamic_tool_isolated, get_dynamic_tool, DYNAMIC_TOOL_REGISTRY


class TestRunDynamicToolIsolated(unittest.TestCase):
    """Exercises the subprocess isolation runner for dynamic synthesized tools."""

    MODULE = "tests._dynamic_probe"

    def test_success_returns_result(self):
        out = run_dynamic_tool_isolated("probe_add", {"a": 2, "b": 3}, module_name=self.MODULE)
        self.assertEqual(out, "5")

    def test_string_result(self):
        out = run_dynamic_tool_isolated("probe_echo", {"text": "hi"}, module_name=self.MODULE)
        self.assertEqual(out, "echo:hi")

    def test_exception_reports_error(self):
        out = run_dynamic_tool_isolated("probe_boom", {}, module_name=self.MODULE)
        self.assertIn("error", out.lower())
        self.assertIn("intentional failure", out)

    def test_unknown_tool_reports_error(self):
        out = run_dynamic_tool_isolated("does_not_exist", {}, module_name=self.MODULE)
        self.assertIn("error", out.lower())

    def test_timeout_terminates_hanging_tool(self):
        out = run_dynamic_tool_isolated(
            "probe_sleep", {"seconds": 10}, timeout=1, module_name=self.MODULE
        )
        self.assertIn("timed out", out.lower())

    def test_dispatch_routes_dynamic_tool_through_isolation(self):
        import modules.ai as ai
        from unittest.mock import patch

        DYNAMIC_TOOL_REGISTRY["probe_add"] = {
            "name": "probe_add",
            "callable": lambda a, b: a + b,
            "description": "test",
            "parameters": {},
        }
        try:
            with patch("modules.tools.run_dynamic_tool_isolated", return_value="ISOLATED:9") as mock_iso:
                out = ai._dispatch_tool("probe_add", {"a": 4, "b": 5})
            self.assertEqual(out, "ISOLATED:9")
            mock_iso.assert_called_once()
        finally:
            DYNAMIC_TOOL_REGISTRY.pop("probe_add", None)

    def test_dispatch_unknown_tool(self):
        import modules.ai as ai
        out = ai._dispatch_tool("totally_unknown_tool", {})
        self.assertIn("Unknown tool", out)


if __name__ == "__main__":
    unittest.main()