import json
import os
import tempfile
import unittest
from unittest.mock import patch

from modules.audit import log_audit_event, query_audit_log, explain_event, DecisionTrace
from modules.tools import is_protected_path, safe_write_file, PROJECT_ROOT


class TestAuditAndProtectedRouting(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.audit_file = os.path.join(self.temp_dir.name, "audit.jsonl")
        self.audit_patcher = patch("modules.audit.DEFAULT_AUDIT_LOG", self.audit_file)
        self.audit_patcher.start()

    def tearDown(self):
        self.audit_patcher.stop()
        self.temp_dir.cleanup()

    def test_audit_log_append_and_query(self):
        ev1 = log_audit_event(
            actor="user",
            action="ai_response",
            outcome="success",
            memory_ids=[1, 2],
            preference_keys=["user.style"],
            provider_info={"provider": "ollama", "model": "qwen2.5-coder"},
        )
        ev2 = log_audit_event(
            actor="autonomous_worker",
            action="file_write",
            outcome="success",
            paths_touched=["workspace/notes.txt"],
        )

        all_events = query_audit_log(limit=10, log_path=self.audit_file)
        self.assertEqual(len(all_events), 2)
        # Reverse chronological order
        self.assertEqual(all_events[0]["event_id"], ev2)
        self.assertEqual(all_events[1]["event_id"], ev1)

        explained = explain_event(ev1, log_path=self.audit_file)
        self.assertIsNotNone(explained)
        self.assertEqual(explained["memory_ids"], [1, 2])
        self.assertEqual(explained["preference_keys"], ["user.style"])

    def test_protected_glob_detection_and_traversal_resistance(self):
        self.assertTrue(is_protected_path("jarvis.py"))
        self.assertTrue(is_protected_path(os.path.join(PROJECT_ROOT, "modules", "ai.py")))
        self.assertTrue(is_protected_path(os.path.join(PROJECT_ROOT, "tests", "test_jarvis.py")))
        # Path traversal attempt must still resolve as protected
        traversal = os.path.join(PROJECT_ROOT, "workspace", "..", "modules", "tools.py")
        self.assertTrue(is_protected_path(traversal))

        # Non-protected workspace/data paths
        self.assertFalse(is_protected_path(os.path.join(PROJECT_ROOT, "workspace", "report.md")))
        self.assertFalse(is_protected_path(os.path.join(PROJECT_ROOT, "logs", "jarvis.log")))

    @patch("modules.learning.apply_safe_modification")
    def test_protected_write_routes_through_learning_pipeline(self, mock_apply_mod):
        mock_apply_mod.return_value = {
            "success": True,
            "backup_path": "/tmp/backup_tools.py",
            "error": None,
        }

        target = os.path.join(PROJECT_ROOT, "modules", "dummy_protected.py")
        res = safe_write_file(
            filepath=target,
            content="x = 1\n",
            actor="self_repair",
            reason="unit test routing check",
        )

        self.assertTrue(res["success"])
        self.assertTrue(res["routed_through_pipeline"])
        mock_apply_mod.assert_called_once_with(
            target_file=os.path.realpath(target),
            new_content="x = 1\n",
            reason="unit test routing check",
        )

        events = query_audit_log(limit=5, log_path=self.audit_file)
        self.assertEqual(events[0]["action"], "protected_file_route")
        self.assertEqual(events[0]["outcome"], "success")

    def test_unprotected_write_executes_directly_and_audits(self):
        workspace_target = os.path.join(self.temp_dir.name, "output.txt")
        res = safe_write_file(
            filepath=workspace_target,
            content="hello world",
            actor="user",
            reason="test direct write",
        )

        self.assertTrue(res["success"])
        self.assertFalse(res["routed_through_pipeline"])
        with open(workspace_target, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "hello world")

        events = query_audit_log(limit=5, log_path=self.audit_file)
        self.assertEqual(events[0]["action"], "file_write")
        self.assertEqual(events[0]["outcome"], "success")

    def test_decision_trace_serialization(self):
        trace = DecisionTrace(
            actor="user",
            provider="groq",
            model="llama-3.3-70b",
            memory_ids_retrieved=[10],
            preference_keys_used=["user.concise"],
        )
        data = trace.to_dict()
        self.assertEqual(data["provider"], "groq")
        self.assertEqual(data["memory_ids_retrieved"], [10])
        self.assertIn("trace_id", data)


if __name__ == "__main__":
    unittest.main()
