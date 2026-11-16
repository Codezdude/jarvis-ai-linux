import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from modules.diagnostics import run_full_diagnostics, trigger_self_repair_if_needed


class TestDiagnosticsEngine(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_goals.db")
        self.audit_path = os.path.join(self.temp_dir.name, "audit.jsonl")

        self.audit_patcher = patch("modules.diagnostics.log_audit_event")
        self.goals_patcher = patch("modules.diagnostics.enqueue_goal")
        self.mock_audit = self.audit_patcher.start()
        self.mock_enqueue = self.goals_patcher.start()

    def tearDown(self):
        self.audit_patcher.stop()
        self.goals_patcher.stop()
        self.temp_dir.cleanup()

    def test_run_full_diagnostics_all_pass(self):
        diag_dir = os.path.join(self.temp_dir.name, "tests_pass")
        os.makedirs(diag_dir)
        passing_test_path = os.path.join(diag_dir, "test_good.py")
        with open(passing_test_path, "w") as f:
            f.write(
                "import unittest\n"
                "class TestGood(unittest.TestCase):\n"
                "    def test_add(self):\n"
                "        self.assertEqual(1 + 1, 2)\n"
            )

        result = run_full_diagnostics(test_dir=diag_dir)

        self.assertTrue(result["success"])
        self.assertEqual(result["failures"], 0)
        self.assertEqual(result["errors"], 0)
        self.assertEqual(result["passed"], 1)
        self.assertEqual(result["tests_run"], 1)
        self.assertEqual(result["failure_details"], [])
        self.mock_audit.assert_called_once()
        audit_call = self.mock_audit.call_args
        self.assertEqual(audit_call[1]["action"], "self_diagnostics_run")
        self.assertEqual(audit_call[1]["outcome"], "success")

    def test_run_full_diagnostics_with_failures(self):
        diag_dir = os.path.join(self.temp_dir.name, "tests_fail")
        os.makedirs(diag_dir)
        failing_test_path = os.path.join(diag_dir, "test_bad.py")
        with open(failing_test_path, "w") as f:
            f.write(
                "import unittest\n"
                "class TestBad(unittest.TestCase):\n"
                "    def test_fail(self):\n"
                "        self.assertEqual(1, 2)\n"
            )

        result = run_full_diagnostics(test_dir=diag_dir)

        self.assertFalse(result["success"])
        self.assertEqual(result["failures"], 1)
        self.assertEqual(result["errors"], 0)
        self.assertEqual(result["passed"], 0)
        self.assertEqual(result["tests_run"], 1)
        self.assertEqual(len(result["failure_details"]), 1)
        audit_call = self.mock_audit.call_args
        self.assertEqual(audit_call[1]["action"], "self_diagnostics_run")
        self.assertEqual(audit_call[1]["outcome"], "error")

    def test_trigger_self_repair_returns_none_on_success(self):
        diag_result = {
            "success": True,
            "tests_run": 5,
            "passed": 5,
            "failures": 0,
            "errors": 0,
            "failure_details": [],
        }
        result = trigger_self_repair_if_needed(diag_result=diag_result)

        self.assertIsNone(result)
        self.mock_enqueue.assert_not_called()

    def test_trigger_self_repair_enqueues_goal_on_failure(self):
        diag_result = {
            "success": False,
            "tests_run": 3,
            "passed": 1,
            "failures": 2,
            "errors": 0,
            "failure_details": ["FAIL in test_x: AssertionError"],
        }
        self.mock_enqueue.return_value = {"id": "goal-123", "source": "self"}

        result = trigger_self_repair_if_needed(diag_result=diag_result, db_path=self.db_path)

        self.assertIsNotNone(result)
        self.assertEqual(result["goal_id"], "goal-123")
        self.assertTrue(self.mock_enqueue.called)
        enqueue_call = self.mock_enqueue.call_args
        self.assertEqual(enqueue_call[1]["source"], "self")
        self.assertEqual(enqueue_call[1]["priority"], 95)
        self.assertEqual(enqueue_call[1]["actor"], "diagnostics_engine")
        self.assertIn("Self-Repair Diagnostic Trigger", enqueue_call[1]["description"])
        self.mock_audit.assert_called()
        repair_audit = self.mock_audit.call_args_list[-1]
        self.assertEqual(repair_audit[1]["action"], "self_repair_goal_triggered")
        self.assertEqual(repair_audit[1]["outcome"], "success")

    def test_trigger_self_repair_calls_diagnostics_when_none_provided(self):
        with patch("modules.diagnostics.run_full_diagnostics") as mock_run:
            mock_run.return_value = {
                "success": False,
                "tests_run": 1,
                "passed": 0,
                "failures": 1,
                "errors": 0,
                "failure_details": ["FAIL"],
            }
            self.mock_enqueue.return_value = {"id": "goal-abc"}
            trigger_self_repair_if_needed()
            mock_run.assert_called_once()

    def test_trigger_self_repair_errors_counted(self):
        diag_result = {
            "success": False,
            "tests_run": 2,
            "passed": 0,
            "failures": 0,
            "errors": 2,
            "failure_details": ["ERR in test_x: Exception", "ERR in test_y: Exception"],
        }
        self.mock_enqueue.return_value = {"id": "goal-xyz"}
        result = trigger_self_repair_if_needed(diag_result=diag_result, db_path=self.db_path)

        self.assertIsNotNone(result)
        self.assertEqual(result["goal_id"], "goal-xyz")
        enqueue_call = self.mock_enqueue.call_args
        self.assertIn("errors", enqueue_call[1]["description"])
        self.assertIn("failures", enqueue_call[1]["description"])


if __name__ == "__main__":
    unittest.main()
