import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.approval import (
    build_unified_diff,
    approve_file_change,
    check_approval,
    init_approval_db,
    register_ui_prompt_callback,
)


class TestUnifiedDiffApproval(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "approval.db")
        self.audit_path = os.path.join(self.temp_dir.name, "audit.jsonl")
        self.key_path = os.path.join(self.temp_dir.name, ".hmac_key")
        self.audit_patcher = patch("modules.audit.DEFAULT_AUDIT_LOG", self.audit_path)
        self.db_patcher = patch("modules.approval.DEFAULT_DB_PATH", self.db_path)
        self.key_patcher = patch("modules.approval.SECRET_KEY_PATH", self.key_path)
        self.audit_patcher.start()
        self.db_patcher.start()
        self.key_patcher.start()
        register_ui_prompt_callback(None)
        init_approval_db(self.db_path)

    def tearDown(self):
        register_ui_prompt_callback(None)
        self.key_patcher.stop()
        self.db_patcher.stop()
        self.audit_patcher.stop()
        self.temp_dir.cleanup()

    def test_build_unified_diff_marks_changes(self):
        diff = build_unified_diff("line1\nline2\n", "line1\nline2 changed\n", path="foo.py")
        self.assertIn("-line2", diff)
        self.assertIn("+line2 changed", diff)
        self.assertIn("a/foo.py", diff)
        self.assertIn("b/foo.py", diff)

    def test_check_approval_passes_diff_to_ui_callback(self):
        captured = {}

        def cb(payload):
            captured.update(payload)
            return True

        register_ui_prompt_callback(cb)
        res = check_approval(
            action="file_write", target=os.path.join(self.temp_dir.name, "x.py"),
            actor="autonomous_worker", reason="review", diff="--- a\n+++ b\n",
            db_path=self.db_path,
        )
        self.assertTrue(res["approved"])
        self.assertEqual(captured.get("diff"), "--- a\n+++ b\n")

    def test_approve_file_change_builds_diff_and_approves(self):
        target = os.path.join(self.temp_dir.name, "mod.py")
        with open(target, "w", encoding="utf-8") as f:
            f.write("old = 1\n")

        captured = {}

        def cb(payload):
            captured.update(payload)
            return True

        register_ui_prompt_callback(cb)
        result = approve_file_change(target, "old = 2\n", actor="user", db_path=self.db_path)
        self.assertTrue(result["approved"])
        self.assertIn("-old = 1", result["diff"])
        self.assertIn("+old = 2", result["diff"])

    def test_approve_file_change_denied_when_ui_declines(self):
        target = os.path.join(self.temp_dir.name, "mod2.py")
        with open(target, "w", encoding="utf-8") as f:
            f.write("a = 1\n")
        register_ui_prompt_callback(lambda payload: False)
        result = approve_file_change(target, "a = 2\n", actor="user", db_path=self.db_path)
        self.assertFalse(result["approved"])


class TestSelfHealToolError(unittest.TestCase):
    def test_self_heal_returns_actionable_error(self):
        import modules.ai as ai

        try:
            raise ValueError("bad input")
        except ValueError as exc:
            with patch("modules.memory.save_reflection"):
                msg = ai._self_heal_tool_error("my_tool", exc, {"x": 1})

        self.assertIn("my_tool", msg)
        self.assertIn("ValueError", msg)
        self.assertIn("bad input", msg)
        self.assertIn("retry", msg.lower())


class TestWorkflowPromotion(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "wf.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_promote_and_get(self):
        from modules import workflows

        wf = workflows.promote_workflow(
            "backupRoutine",
            "Backs up and reports",
            [{"tool": "backup", "args": {"path": "a"}}, {"tool": "report", "args": {}}],
            db_path=self.db_path,
        )
        self.assertEqual(wf["name"], "backupRoutine")
        self.assertEqual(len(wf["steps"]), 2)

        loaded = workflows.get_workflow("backupRoutine", db_path=self.db_path)
        self.assertEqual(loaded["steps"][0]["tool"], "backup")

    def test_promote_upsert_preserves_usage_count(self):
        from modules import workflows

        workflows.promote_workflow("w", "d", [{"tool": "t"}], db_path=self.db_path)
        workflows.replay_workflow("w", lambda tool, args: "ok", db_path=self.db_path)
        workflows.promote_workflow("w", "d2", [{"tool": "t2"}], db_path=self.db_path)
        loaded = workflows.get_workflow("w", db_path=self.db_path)
        self.assertEqual(loaded["description"], "d2")
        self.assertEqual(loaded["uses"], 1)

    def test_replay_runs_steps_and_counts_use(self):
        from modules import workflows

        workflows.promote_workflow(
            "adder", "adds", [{"tool": "add", "args": {"a": 1, "b": 2}}], db_path=self.db_path
        )
        calls = []

        def dispatch(tool, args):
            calls.append((tool, args))
            return args.get("a", 0) + args.get("b", 0)

        result = workflows.replay_workflow("adder", dispatch, db_path=self.db_path)
        self.assertTrue(result["ok"])
        self.assertEqual(result["results"], [3])
        self.assertEqual(calls, [("add", {"a": 1, "b": 2})])
        self.assertEqual(workflows.get_workflow("adder", db_path=self.db_path)["uses"], 1)

    def test_replay_stops_on_error(self):
        from modules import workflows

        workflows.promote_workflow(
            "boom", "fails", [{"tool": "bad"}], db_path=self.db_path
        )

        def dispatch(tool, args):
            raise RuntimeError("nope")

        result = workflows.replay_workflow("boom", dispatch, db_path=self.db_path)
        self.assertFalse(result["ok"])
        self.assertIn("nope", result["results"][0])

    def test_delete_workflow(self):
        from modules import workflows

        workflows.promote_workflow("tmp", "d", [{"tool": "t"}], db_path=self.db_path)
        self.assertTrue(workflows.delete_workflow("tmp", db_path=self.db_path))
        self.assertIsNone(workflows.get_workflow("tmp", db_path=self.db_path))


if __name__ == "__main__":
    unittest.main()