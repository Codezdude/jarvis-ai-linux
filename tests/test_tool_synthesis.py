import os
import tempfile
import unittest
from unittest.mock import patch

from modules.approval import AutonomyLevel, init_approval_db, set_autonomy_level
from modules.audit import query_audit_log
from modules.budget import init_budget_db
from modules.tool_synthesis import init_dynamic_tools_db, synthesize_and_register_tool
from modules.tools import DYNAMIC_TOOL_REGISTRY, get_all_dynamic_tool_schemas, get_dynamic_tool, PROJECT_ROOT


class TestToolSynthesisPipeline(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_synthesis.db")
        self.audit_path = os.path.join(self.temp_dir.name, "audit.jsonl")
        self.mock_module_path = os.path.join(PROJECT_ROOT, "modules", "_test_dynamic_tools.py")
        self.mock_tests_dir = os.path.join(PROJECT_ROOT, "tests")

        self.audit_patcher = patch("modules.audit.DEFAULT_AUDIT_LOG", self.audit_path)
        self.db_patcher = patch("modules.budget.DEFAULT_DB_PATH", self.db_path)
        self.app_db_patcher = patch("modules.approval.DEFAULT_DB_PATH", self.db_path)
        self.pref_db_patcher = patch("modules.preferences.DEFAULT_DB_PATH", self.db_path)

        self.audit_patcher.start()
        self.db_patcher.start()
        self.app_db_patcher.start()
        self.pref_db_patcher.start()

        DYNAMIC_TOOL_REGISTRY.clear()
        init_approval_db(self.db_path)
        init_budget_db(self.db_path)
        init_dynamic_tools_db(self.db_path)
        set_autonomy_level("self", AutonomyLevel.L3_AUTONOMOUS_POLICY, actor="user", db_path=self.db_path)

    def tearDown(self):
        DYNAMIC_TOOL_REGISTRY.clear()
        self.pref_db_patcher.stop()
        self.app_db_patcher.stop()
        self.db_patcher.stop()
        self.audit_patcher.stop()
        self.temp_dir.cleanup()

    def test_synthesize_valid_tool_passes_tests_and_registers_schema(self):
        tool_name = "compute_fibonacci"
        module_path = self.mock_module_path

        def valid_generator(_desc, _tn):
            return {
                "description": "Computes Fibonacci number at index n",
                "parameters_schema": {
                    "type": "object",
                    "properties": {"n": {"type": "integer"}},
                    "required": ["n"],
                },
                "function_code": (
                    f"def {tool_name}(n: int) -> int:\n"
                    "    if n <= 1:\n"
                    "        return n\n"
                    "    a, b = 0, 1\n"
                    "    for _ in range(2, n + 1):\n"
                    "        a, b = b, a + b\n"
                    "    return b\n"
                ),
                "test_code": (
                    "import importlib.util\n"
                    "import unittest\n"
                    f"spec = importlib.util.spec_from_file_location('dyn', {module_path!r})\n"
                    "mod = importlib.util.module_from_spec(spec)\n"
                    "spec.loader.exec_module(mod)\n"
                    f"class TestFib(unittest.TestCase):\n"
                    f"    def test_fib(self):\n"
                    f"        self.assertEqual(mod.{tool_name}(7), 13)\n"
                ),
            }

        res = synthesize_and_register_tool(
            tool_name=tool_name,
            capability_description="Compute nth Fibonacci number",
            generator_fn=valid_generator,
            module_path=module_path,
            tests_dir=self.mock_tests_dir,
            db_path=self.db_path,
        )

        self.assertTrue(res["success"])
        self.assertEqual(res["tests_run"], 1)

        fn = get_dynamic_tool(tool_name)
        self.assertIsNotNone(fn)
        self.assertEqual(fn(10), 55)

        schemas = get_all_dynamic_tool_schemas()
        self.assertEqual(len(schemas), 1)
        self.assertEqual(schemas[0]["function"]["name"], tool_name)

        events = query_audit_log(limit=5, log_path=self.audit_path)
        self.assertEqual(events[0]["action"], "tool_synthesized")

    def test_failing_unit_test_rolls_back_module_and_logs_failure(self):
        tool_name = "buggy_adder"
        module_path = self.mock_module_path

        with open(module_path, "w", encoding="utf-8") as f:
            f.write("# Original clean module\nEXISTING = 42\n")

        def broken_test_generator(_desc, _tn):
            return {
                "description": "Buggy adder",
                "parameters_schema": {"type": "object", "properties": {}},
                "function_code": f"def {tool_name}(a, b):\n    return a - b\n",
                "test_code": (
                    "import importlib.util\n"
                    "import unittest\n"
                    f"spec = importlib.util.spec_from_file_location('dyn', {module_path!r})\n"
                    "mod = importlib.util.module_from_spec(spec)\n"
                    "spec.loader.exec_module(mod)\n"
                    f"class TestBuggy(unittest.TestCase):\n"
                    f"    def test_add(self):\n"
                    f"        self.assertEqual(mod.{tool_name}(2, 3), 5)\n"
                ),
            }

        res = synthesize_and_register_tool(
            tool_name=tool_name,
            capability_description="Add two numbers",
            generator_fn=broken_test_generator,
            module_path=module_path,
            tests_dir=self.mock_tests_dir,
            db_path=self.db_path,
        )

        self.assertFalse(res["success"])
        self.assertTrue(res["rolled_back"])
        self.assertIsNone(get_dynamic_tool(tool_name))

        with open(module_path, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "# Original clean module\nEXISTING = 42\n")

        test_file = os.path.join(self.mock_tests_dir, f"test_synthesized_{tool_name}.py")
        self.assertFalse(os.path.exists(test_file))

        events = query_audit_log(limit=5, log_path=self.audit_path)
        self.assertEqual(events[0]["action"], "tool_synthesis_failed")
        self.assertEqual(events[0]["outcome"], "rolled_back")

    def test_forbidden_ast_primitive_blocked_before_disk_write(self):
        tool_name = "unsafe_shell_tool"
        module_path = self.mock_module_path

        def unsafe_generator(_desc, _tn):
            return {
                "description": "Unsafe shell wrapper",
                "parameters_schema": {"type": "object", "properties": {}},
                "function_code": f"import os\ndef {tool_name}(cmd):\n    return os.system(cmd)\n",
                "test_code": "import unittest\nclass T(unittest.TestCase):\n    def test_x(self): pass\n",
            }

        res = synthesize_and_register_tool(
            tool_name=tool_name,
            capability_description="Run shell commands",
            generator_fn=unsafe_generator,
            module_path=module_path,
            tests_dir=self.mock_tests_dir,
            db_path=self.db_path,
        )

        self.assertFalse(res["success"])
        self.assertIn("Forbidden module call", res["error"])
        self.assertFalse(os.path.exists(module_path))


if __name__ == "__main__":
    unittest.main()
