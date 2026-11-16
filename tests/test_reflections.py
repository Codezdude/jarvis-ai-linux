import os
import tempfile
import unittest
from unittest.mock import patch

from modules.approval import init_approval_db
from modules.audit import query_audit_log
from modules.budget import init_budget_db
from modules.goals import init_goals_db, get_goal
from modules.preferences import set_preference
from modules.reflections import (
    init_reflections_db,
    evaluate_task_rubric,
    mine_failure_corpus,
    get_reflections_for_review,
    mark_reflection_reviewed,
)


class TestPostTaskReflectionsAndFailureCorpus(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_reflections.db")
        self.audit_path = os.path.join(self.temp_dir.name, "audit.jsonl")

        self.audit_patcher = patch("modules.audit.DEFAULT_AUDIT_LOG", self.audit_path)
        self.audit_patcher.start()

        init_approval_db(self.db_path)
        init_budget_db(self.db_path)
        init_goals_db(self.db_path)
        init_reflections_db(self.db_path)

    def tearDown(self):
        self.audit_patcher.stop()
        self.temp_dir.cleanup()

    def test_passing_and_failing_rubric_evaluations(self):
        def good_scorer(_desc, _out, _err):
            return {
                "scores": {
                    "completeness": 0.95,
                    "tool_accuracy": 0.90,
                    "preference_alignment": 0.90,
                    "safety_compliance": 1.0,
                },
                "failure_mode": "none",
                "root_cause": "",
                "proposed_remediation": "none",
            }

        res_pass = evaluate_task_rubric(
            goal_id="g-pass",
            task_description="Summarize daily logs",
            output="Daily digest written cleanly with 0 errors.",
            scorer_fn=good_scorer,
            db_path=self.db_path,
        )
        self.assertTrue(res_pass["passed"])
        self.assertGreaterEqual(res_pass["overall_score"], 0.9)
        self.assertEqual(res_pass["failure_mode"], "none")

        def bad_scorer(_desc, _out, _err):
            return {
                "scores": {
                    "completeness": 0.2,
                    "tool_accuracy": 0.1,
                    "preference_alignment": 0.8,
                    "safety_compliance": 1.0,
                },
                "failure_mode": "tool_schema_mismatch",
                "root_cause": "Passed invalid path parameter to safe_write_file",
                "proposed_remediation": "tool_schema_update",
            }

        res_fail = evaluate_task_rubric(
            goal_id="g-fail",
            task_description="Update config artifact",
            output="TypeError: unexpected keyword argument",
            scorer_fn=bad_scorer,
            db_path=self.db_path,
        )
        self.assertFalse(res_fail["passed"])
        self.assertEqual(res_fail["failure_mode"], "tool_schema_mismatch")

    def test_failure_corpus_mining_creates_remediation_goal_once(self):
        def failing_scorer(_desc, _out, _err):
            return {
                "scores": {
                    "completeness": 0.3,
                    "tool_accuracy": 0.2,
                    "preference_alignment": 0.5,
                    "safety_compliance": 1.0,
                },
                "failure_mode": "syntax_or_test_error",
                "root_cause": "Missing import in generated helper",
                "proposed_remediation": "new_unit_test",
            }

        evaluate_task_rubric(
            "g1", "Task 1", "SyntaxError", scorer_fn=failing_scorer, db_path=self.db_path
        )
        self.assertEqual(mine_failure_corpus(min_occurrences=2, db_path=self.db_path), [])

        evaluate_task_rubric(
            "g2", "Task 2", "SyntaxError", scorer_fn=failing_scorer, db_path=self.db_path
        )
        mined = mine_failure_corpus(min_occurrences=2, db_path=self.db_path)
        self.assertEqual(len(mined), 1)
        self.assertEqual(mined[0]["failure_mode"], "syntax_or_test_error")
        self.assertEqual(mined[0]["occurrences"], 2)

        generated_goal = get_goal(mined[0]["goal_id"], db_path=self.db_path)
        self.assertIsNotNone(generated_goal)
        self.assertEqual(generated_goal["source"], "self")
        self.assertIn("new_unit_test", generated_goal["description"])

        self.assertEqual(mine_failure_corpus(min_occurrences=2, db_path=self.db_path), [])

    def test_human_review_workflow(self):
        evaluate_task_rubric(
            goal_id="g-rev",
            task_description="Failing task",
            output="",
            execution_error="TimeoutError",
            db_path=self.db_path,
        )
        unreviewed = get_reflections_for_review(
            unreviewed_only=True, failures_only=True, db_path=self.db_path
        )
        self.assertEqual(len(unreviewed), 1)
        ref_id = unreviewed[0]["id"]

        self.assertTrue(mark_reflection_reviewed(ref_id, actor="user", db_path=self.db_path))
        after = get_reflections_for_review(
            unreviewed_only=True, failures_only=True, db_path=self.db_path
        )
        self.assertEqual(len(after), 0)

        events = query_audit_log(limit=5, log_path=self.audit_path)
        self.assertEqual(events[0]["action"], "reflection_reviewed")


if __name__ == "__main__":
    unittest.main()
