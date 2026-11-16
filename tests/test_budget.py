import os
import tempfile
import unittest
from unittest.mock import patch

from modules.audit import query_audit_log
from modules.budget import (
    init_budget_db,
    check_budget,
    record_budget_usage,
    record_provider_usage,
    get_daily_cloud_tokens,
    get_provider_telemetry_summary,
    reset_circuit_breaker,
    register_budget_alert_callback,
)
from modules.audit import DecisionTrace
from modules.preferences import set_preference


class TestBudgetAndCircuitBreakers(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_budget.db")
        self.audit_path = os.path.join(self.temp_dir.name, "audit.jsonl")

        self.audit_patcher = patch("modules.audit.DEFAULT_AUDIT_LOG", self.audit_path)
        self.db_patcher = patch("modules.budget.DEFAULT_DB_PATH", self.db_path)
        self.pref_db_patcher = patch("modules.preferences.DEFAULT_DB_PATH", self.db_path)

        self.audit_patcher.start()
        self.db_patcher.start()
        self.pref_db_patcher.start()

        register_budget_alert_callback(None)
        init_budget_db(self.db_path)

    def tearDown(self):
        register_budget_alert_callback(None)
        self.pref_db_patcher.stop()
        self.db_patcher.stop()
        self.audit_patcher.stop()
        self.temp_dir.cleanup()

    def test_hourly_cycle_cap_trips_breaker_and_notifies_hud(self):
        set_preference("user", "budget.max_cycles_per_hour", 2, db_path=self.db_path)
        alerts = []
        register_budget_alert_callback(alerts.append)

        self.assertTrue(check_budget(db_path=self.db_path)["allowed"])
        record_budget_usage("cycle", goal_id="g1", db_path=self.db_path)
        record_budget_usage("cycle", goal_id="g2", db_path=self.db_path)

        blocked = check_budget(goal_id="g3", db_path=self.db_path)
        self.assertFalse(blocked["allowed"])
        self.assertIn("max_cycles_per_hour exceeded", blocked["reason"])
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["status"], "paused")

    def test_per_cycle_token_budget_enforced(self):
        set_preference("user", "budget.max_tokens_per_cycle", 1000, db_path=self.db_path)

        record_budget_usage("tokens", goal_id="goal-abc", amount=700, db_path=self.db_path)
        ok = check_budget(goal_id="goal-abc", requested_tokens=200, db_path=self.db_path)
        self.assertTrue(ok["allowed"])

        exceeded = check_budget(goal_id="goal-abc", requested_tokens=400, db_path=self.db_path)
        self.assertFalse(exceeded["allowed"])
        self.assertIn("max_tokens_per_cycle exceeded", exceeded["reason"])

    def test_daily_self_modification_cap(self):
        set_preference("user", "budget.max_self_mods_per_day", 1, db_path=self.db_path)

        self.assertTrue(check_budget(requires_self_mod=True, db_path=self.db_path)["allowed"])
        record_budget_usage("self_mod", goal_id="fix-1", db_path=self.db_path)

        blocked = check_budget(requires_self_mod=True, db_path=self.db_path)
        self.assertFalse(blocked["allowed"])
        self.assertIn("max_self_mods_per_day exceeded", blocked["reason"])

    def test_consecutive_failures_trip_breaker_and_user_reset_clears_it(self):
        set_preference("user", "budget.max_consecutive_failures", 2, db_path=self.db_path)

        r1 = record_budget_usage("failure", goal_id="g1", db_path=self.db_path)
        self.assertFalse(r1["tripped"])

        r2 = record_budget_usage("failure", goal_id="g2", db_path=self.db_path)
        self.assertTrue(r2["tripped"])

        self.assertFalse(check_budget(db_path=self.db_path)["allowed"])

        # Non-user cannot reset breaker
        with self.assertRaises(PermissionError):
            reset_circuit_breaker(actor="autonomous_worker", db_path=self.db_path)

        # User reset restores execution
        reset_circuit_breaker(actor="user", db_path=self.db_path)
        self.assertTrue(check_budget(db_path=self.db_path)["allowed"])

        events = query_audit_log(limit=5, log_path=self.audit_path)
        self.assertEqual(events[0]["action"], "budget_circuit_reset")

    def test_record_provider_usage_persists_telemetry(self):
        record_provider_usage(
            provider="groq",
            model="llama-3",
            prompt_tokens=120,
            completion_tokens=45,
            latency_ms=812.5,
            success=True,
            is_urgent=True,
            db_path=self.db_path,
        )
        # Cloud tokens (non-ollama) accumulate toward the daily cap
        self.assertEqual(get_daily_cloud_tokens(db_path=self.db_path), 165)
        summary = get_provider_telemetry_summary(db_path=self.db_path)
        self.assertTrue(summary)
        row = summary[0]
        self.assertEqual(row["provider"], "groq")
        self.assertEqual(row["prompt_tokens"], 120)
        self.assertEqual(row["completion_tokens"], 45)
        self.assertAlmostEqual(float(row["avg_latency_ms"]), 812.5, places=1)

    def test_record_provider_usage_ignores_ollama_for_cloud_cap(self):
        record_provider_usage(
            provider="ollama",
            model="qwen",
            prompt_tokens=500,
            completion_tokens=200,
            latency_ms=300.0,
            db_path=self.db_path,
        )
        self.assertEqual(get_daily_cloud_tokens(db_path=self.db_path), 0)

    def test_finalize_ai_turn_records_latency_and_tokens(self):
        import modules.ai as ai

        trace = DecisionTrace(actor="autonomous_worker")
        trace.provider = "groq"
        trace.model = "llama-3"
        trace.start_perf = __import__("time").perf_counter()
        trace.prompt_tokens = 88
        trace.completion_tokens = 12

        captured = {}

        def _fake_record(**kwargs):
            captured.update(kwargs)

        with patch("modules.budget.record_provider_usage", side_effect=_fake_record):
            reply = ai._finalize_ai_turn(trace, "hello", "success")

        self.assertEqual(reply, "hello")
        self.assertGreaterEqual(trace.latency_ms, 0.0)
        self.assertEqual(captured["provider"], "groq")
        self.assertEqual(captured["model"], "llama-3")
        self.assertEqual(captured["prompt_tokens"], 88)
        self.assertEqual(captured["completion_tokens"], 12)
        self.assertTrue(captured["success"])

    def test_finalize_ai_turn_skips_telemetry_for_unknown_provider(self):
        import modules.ai as ai

        trace = DecisionTrace(actor="autonomous_worker")
        called = {"n": 0}

        def _fake_record(**kwargs):
            called["n"] += 1

        with patch("modules.budget.record_provider_usage", side_effect=_fake_record):
            ai._finalize_ai_turn(trace, "no providers", "error")

        self.assertEqual(called["n"], 0)


if __name__ == "__main__":
    unittest.main()
