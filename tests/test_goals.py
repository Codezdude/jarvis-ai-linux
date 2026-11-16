import os
import tempfile
import unittest
from unittest.mock import patch

from modules.goals import (
    init_goals_db,
    enqueue_goal,
    update_goal,
    recover_interrupted_goals,
    get_goal,
)


class TestGoalsRecovery(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_goals.db")

        self.audit_patcher = patch("modules.audit.DEFAULT_AUDIT_LOG", os.path.join(self.temp_dir.name, "audit.jsonl"))
        self.audit_patcher.start()

        init_goals_db(self.db_path)

    def tearDown(self):
        self.audit_patcher.stop()
        self.temp_dir.cleanup()

    def test_recover_interrupted_goals_resets_non_terminal_states(self):
        g1 = enqueue_goal("running goal", source="self", priority=50, db_path=self.db_path)
        g2 = enqueue_goal("paused_interrupted goal", source="self", priority=60, db_path=self.db_path)
        g3 = enqueue_goal("paused_budget goal", source="self", priority=70, db_path=self.db_path)
        g4 = enqueue_goal("pending goal", source="self", priority=80, db_path=self.db_path)
        g5 = enqueue_goal("completed goal", source="self", priority=90, db_path=self.db_path)

        update_goal(g1["id"], status="running", db_path=self.db_path)
        update_goal(g2["id"], status="paused_interrupted", db_path=self.db_path)
        update_goal(g3["id"], status="paused_budget", db_path=self.db_path)
        update_goal(g5["id"], status="completed", db_path=self.db_path)

        recovered = recover_interrupted_goals(db_path=self.db_path)
        self.assertEqual(recovered, 3)

        self.assertEqual(get_goal(g1["id"], db_path=self.db_path)["status"], "pending")
        self.assertEqual(get_goal(g2["id"], db_path=self.db_path)["status"], "pending")
        self.assertEqual(get_goal(g3["id"], db_path=self.db_path)["status"], "pending")
        self.assertEqual(get_goal(g4["id"], db_path=self.db_path)["status"], "pending")
        self.assertEqual(get_goal(g5["id"], db_path=self.db_path)["status"], "completed")

    def test_recover_interrupted_goals_no_op_when_all_terminal(self):
        g1 = enqueue_goal("pending goal", source="self", priority=50, db_path=self.db_path)
        g2 = enqueue_goal("completed goal", source="self", priority=60, db_path=self.db_path)
        update_goal(g2["id"], status="completed", db_path=self.db_path)

        recovered = recover_interrupted_goals(db_path=self.db_path)
        self.assertEqual(recovered, 0)

    def test_update_goal_status_only(self):
        g = enqueue_goal("test goal", source="self", priority=50, db_path=self.db_path)
        result = update_goal(g["id"], status="running", db_path=self.db_path)
        self.assertTrue(result)
        self.assertEqual(get_goal(g["id"], db_path=self.db_path)["status"], "running")

    def test_update_goal_checkpoint_only(self):
        g = enqueue_goal("test goal", source="self", priority=50, db_path=self.db_path)
        result = update_goal(g["id"], checkpoint={"plan_cursor": 3}, db_path=self.db_path)
        self.assertTrue(result)
        self.assertEqual(get_goal(g["id"], db_path=self.db_path)["checkpoint"]["plan_cursor"], 3)


if __name__ == "__main__":
    unittest.main()
