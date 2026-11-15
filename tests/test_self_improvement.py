import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import modules.self_improvement as si
import modules.tools as tools


class SelfImprovementTests(unittest.TestCase):
    def setUp(self):
        self.test_file = os.path.join(ROOT, "tests", "_test_dummy_module.py")
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write("# Version 1\nVALUE = 1\n")

    def tearDown(self):
        if os.path.exists(self.test_file):
            try:
                os.remove(self.test_file)
            except OSError:
                pass

    def test_validate_code_syntax(self):
        valid, msg = si.validate_code_syntax(code_string="def hello(): return 'world'")
        self.assertTrue(valid)
        self.assertIn("valid", msg.lower())

        invalid, err_msg = si.validate_code_syntax(code_string="def broken(: return")
        self.assertFalse(invalid)
        self.assertIn("syntaxerror", err_msg.lower())

    def test_backup_and_rollback(self):
        backup_path = si.create_backup(self.test_file)
        self.assertTrue(os.path.exists(backup_path))

        # Mutate the file
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write("# Version 2\nVALUE = 2\n")

        # Rollback
        result = si.rollback_file(self.test_file, backup_path)
        self.assertIn("successfully rolled back", result.lower())

        with open(self.test_file, "r", encoding="utf-8") as f:
            restored = f.read()
        self.assertIn("Version 1", restored)

    def test_safe_mode_blocks_unconfirmed_improvement(self):
        original_mode = si.SAFE_MODE
        try:
            si.SAFE_MODE = True
            result = si.apply_self_improvement(
                self.test_file,
                "# Version 3\nVALUE = 3\n",
                confirmed=False,
            )
            self.assertIn("blocked", result.lower())
        finally:
            si.SAFE_MODE = original_mode

    def test_apply_self_improvement_rejects_syntax_errors_before_writing(self):
        result = si.apply_self_improvement(
            self.test_file,
            "def broken_syntax(::: invalid",
            confirmed=True,
            run_tests=False,
        )
        self.assertIn("aborted", result.lower())
        self.assertIn("syntaxerror", result.lower())

        # Original content should remain untouched
        with open(self.test_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Version 1", content)

    def test_apply_self_improvement_success_with_valid_code(self):
        result = si.apply_self_improvement(
            self.test_file,
            "# Version 3\nVALUE = 3\ndef get_val(): return VALUE\n",
            confirmed=True,
            run_tests=False,
        )
        self.assertIn("successfully applied", result.lower())

        with open(self.test_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Version 3", content)

    def test_tools_module_exposes_self_improvement(self):
        log_snippet = tools.inspect_recent_logs(max_lines=5)
        self.assertIsInstance(log_snippet, str)

        syntax_check = tools.validate_code_syntax(code_string="x = 10")
        self.assertIn("true", syntax_check.lower())

    def test_reload_module(self):
        result = si.reload_jarvis_module("modules.logger")
        self.assertIn("reloaded successfully", result.lower())


if __name__ == "__main__":
    unittest.main()
