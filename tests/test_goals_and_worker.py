import os
import tempfile
import unittest
from unittest.mock import patch

from modules.goals import (
    init_goals_db,
    enqueue_goal,
    update_goal,
    get_goal,
    request_stop,
    clear_stop,
    is_stopped,
)
from modules.approval import init_approval_db
from modules.budget import init_budget_db
from modules.scheduler import process_one_idle_goal
from modules.planner import planned_step_executor
from functools import partial


class TestStopResumeWorker(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_worker.db")
        self.audit_path = os.path.join(self.temp_dir.name, "audit.jsonl")

        self.audit_patcher = patch("modules.audit.DEFAULT_AUDIT_LOG", self.audit_path)
        self.audit_patcher.start()

        init_approval_db(self.db_path)
        init_budget_db(self.db_path)
        init_goals_db(self.db_path)
        clear_stop()

    def tearDown(self):
        self.audit_patcher.stop()
        clear_stop()
        self.temp_dir.cleanup()

    def test_request_stop_clears_and_resumes_from_checkpoint(self):
        # Enqueue a goal
        goal = enqueue_goal("Multi-step task", source="self", priority=80, db_path=self.db_path)

        def fake_planner(_desc, _ctx):
            return [
                {"description": "Step A", "expected_outcome": "A done"},
                {"description": "Step B", "expected_outcome": "B done"},
            ]

        def fake_executor(_goal, step, _prior):
            return f"OK: {step['description']}"

        def fake_verifier(_step, output):
            return {"verified": output.startswith("OK:"), "reason": "valid"}

        bound_executor = partial(
            planned_step_executor,
            planner_fn=fake_planner,
            executor_fn=fake_executor,
            verifier_fn=fake_verifier,
            db_path=self.db_path,
        )

        # Set stop flag — scheduler should skip
        request_stop()
        self.assertTrue(is_stopped())
        res_stopped = process_one_idle_goal(step_executor=bound_executor, max_steps=6, db_path=self.db_path)
        self.assertEqual(res_stopped["status"], "stopped")

        # Clear stop — scheduler should resume
        clear_stop()
        self.assertFalse(is_stopped())

        # Set a checkpoint to simulate mid-execution state
        checkpoint = {
            "plan": [
                {"step_id": 1, "description": "Step A", "expected_outcome": "A done", "status": "completed", "output": "OK: Step A", "verification_notes": "ok"},
                {"step_id": 2, "description": "Step B", "expected_outcome": "B done", "status": "pending", "output": None, "verification_notes": None},
            ],
            "plan_cursor": 1,
            "replan_count": 0,
        }
        update_goal(goal["id"], checkpoint=checkpoint, db_path=self.db_path)

        res = process_one_idle_goal(step_executor=bound_executor, max_steps=6, db_path=self.db_path)
        self.assertEqual(res["status"], "completed")

        stored = get_goal(goal["id"], db_path=self.db_path)
        self.assertEqual(stored["status"], "completed")
        self.assertEqual(stored["checkpoint"]["plan_cursor"], 2)

    def test_is_stopped_returns_false_by_default(self):
        clear_stop()
        self.assertFalse(is_stopped())

    def test_request_stop_sets_flag(self):
        clear_stop()
        request_stop()
        self.assertTrue(is_stopped())


if __name__ == "__main__":
    unittest.main()