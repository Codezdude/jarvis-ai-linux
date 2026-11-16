import os
import tempfile
import unittest
from functools import partial
from unittest.mock import patch

from modules.approval import init_approval_db
from modules.audit import query_audit_log
from modules.budget import init_budget_db, record_budget_usage
from modules.goals import enqueue_goal, get_goal, init_goals_db
from modules.planner import planned_step_executor
from modules.preferences import set_preference
from modules.scheduler import process_one_idle_goal


class TestPlanExecuteVerifyReplan(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_planner.db")
        self.audit_path = os.path.join(self.temp_dir.name, "audit.jsonl")

        self.audit_patcher = patch("modules.audit.DEFAULT_AUDIT_LOG", self.audit_path)
        self.audit_patcher.start()

        init_approval_db(self.db_path)
        init_budget_db(self.db_path)
        init_goals_db(self.db_path)

    def tearDown(self):
        self.audit_patcher.stop()
        self.temp_dir.cleanup()

    def test_plan_execute_verify_completes_multi_step_goal(self):
        goal = enqueue_goal("Generate module dependency map", source="self", priority=80, db_path=self.db_path)

        def fake_planner(_desc, _ctx):
            return [
                {"description": "Scan imports in modules/", "expected_outcome": "Import list collected"},
                {"description": "Write markdown graph to workspace/deps.md", "expected_outcome": "File written"},
            ]

        def fake_executor(_goal, step, _prior):
            return f"Executed: {step['description']}"

        def fake_verifier(_step, output):
            return {"verified": "Executed:" in output, "reason": "Output format valid"}

        bound_executor = partial(
            planned_step_executor,
            planner_fn=fake_planner,
            executor_fn=fake_executor,
            verifier_fn=fake_verifier,
            db_path=self.db_path,
        )

        res = process_one_idle_goal(step_executor=bound_executor, max_steps=6, db_path=self.db_path)
        self.assertTrue(res["ran"])
        self.assertEqual(res["status"], "completed")
        self.assertEqual(res["steps_executed"], 2)

        stored = get_goal(goal["id"], db_path=self.db_path)
        self.assertEqual(stored["status"], "completed")
        self.assertEqual(stored["checkpoint"]["plan_cursor"], 2)
        self.assertEqual(stored["checkpoint"]["replan_count"], 0)

    def test_verification_failure_replans_from_failure_point_preserving_completed_steps(self):
        goal = enqueue_goal("Analyze and patch config parser", source="self", priority=85, db_path=self.db_path)
        executed_descriptions = []

        def fake_planner(_desc, _ctx):
            return [
                {"description": "Step A: Read config", "expected_outcome": "Config loaded"},
                {"description": "Step B: Flaky parse attempt", "expected_outcome": "Parsed dict"},
            ]

        def fake_executor(_goal, step, _prior):
            executed_descriptions.append(step["description"])
            if "Flaky" in step["description"]:
                return "ERROR: malformed token"
            return f"OK: {step['description']}"

        def fake_verifier(_step, output):
            ok = output.startswith("OK:")
            return {"verified": ok, "reason": "Valid output" if ok else "Malformed token error"}

        def fake_replanner(_desc, completed, _failed, _reason):
            self.assertEqual(len(completed), 1)
            self.assertIn("Step A", completed[0]["description"])
            return [
                {"description": "Step B-Revised: Safe JSON fallback parse", "expected_outcome": "Parsed dict"},
            ]

        bound_executor = partial(
            planned_step_executor,
            planner_fn=fake_planner,
            executor_fn=fake_executor,
            verifier_fn=fake_verifier,
            replan_fn=fake_replanner,
            db_path=self.db_path,
        )

        res = process_one_idle_goal(step_executor=bound_executor, max_steps=6, db_path=self.db_path)
        self.assertEqual(res["status"], "completed")
        self.assertEqual(
            executed_descriptions,
            [
                "Step A: Read config",
                "Step B: Flaky parse attempt",
                "Step B-Revised: Safe JSON fallback parse",
            ],
        )

        stored = get_goal(goal["id"], db_path=self.db_path)
        self.assertEqual(stored["checkpoint"]["replan_count"], 1)

        events = query_audit_log(limit=10, log_path=self.audit_path)
        actions = [e["action"] for e in events]
        self.assertIn("goal_replanned", actions)

    def test_exceeding_max_replans_fails_goal_cleanly(self):
        set_preference("user", "planner.max_replans_per_goal", 1, db_path=self.db_path)
        enqueue_goal("Unreachable external target", source="self", priority=60, db_path=self.db_path)

        def fake_planner(_desc, _ctx):
            return [{"description": "Attempt impossible step", "expected_outcome": "Success"}]

        def always_bad_exec(_goal, _step, _prior):
            return "BAD_OUTPUT"

        def always_fail_verify(_step, _output):
            return {"verified": False, "reason": "Constraint unsatisfied"}

        def fake_replanner(_desc, _completed, _failed, _reason):
            return [{"description": "Retry impossible step", "expected_outcome": "Success"}]

        bound_executor = partial(
            planned_step_executor,
            planner_fn=fake_planner,
            executor_fn=always_bad_exec,
            verifier_fn=always_fail_verify,
            replan_fn=fake_replanner,
            db_path=self.db_path,
        )

        res = process_one_idle_goal(step_executor=bound_executor, max_steps=6, db_path=self.db_path)
        self.assertIn(res["status"], ("pending", "failed"))
        self.assertIn("failed verification after 1 replans", res["error"])

    def test_token_budget_halts_planned_execution(self):
        set_preference("user", "budget.max_tokens_per_cycle", 10, db_path=self.db_path)
        goal = enqueue_goal("Token heavy goal", source="self", priority=70, db_path=self.db_path)
        record_budget_usage("tokens", goal_id=goal["id"], amount=11, db_path=self.db_path)

        def fake_planner(_desc, _ctx):
            return [{"description": "Step 1", "expected_outcome": "Done"}]

        bound_executor = partial(planned_step_executor, planner_fn=fake_planner, db_path=self.db_path)
        res = process_one_idle_goal(step_executor=bound_executor, db_path=self.db_path)
        self.assertEqual(res["status"], "paused_budget")


if __name__ == "__main__":
    unittest.main()
