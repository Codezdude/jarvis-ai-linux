import os
import tempfile
import unittest
from unittest.mock import patch

from modules.approval import (
    AutonomyLevel,
    RiskTier,
    init_approval_db,
    get_autonomy_level,
    set_autonomy_level,
    classify_action,
    issue_approval_token,
    verify_approval_token,
    check_approval,
    register_ui_prompt_callback,
)
from modules.tools import PROJECT_ROOT, safe_write_file


class TestApprovalGateAndAutonomy(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_approval.db")
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

    def test_default_levels_and_non_user_escalation_blocked(self):
        self.assertEqual(get_autonomy_level("user", self.db_path), AutonomyLevel.L1_ACT_AND_REPORT)
        self.assertEqual(get_autonomy_level("self", self.db_path), AutonomyLevel.L0_SUGGEST_ONLY)

        with self.assertRaises(PermissionError):
            set_autonomy_level("self", AutonomyLevel.L3_AUTONOMOUS_POLICY, actor="autonomous_worker", db_path=self.db_path)

        res = set_autonomy_level("self", AutonomyLevel.L2_ACT_AND_NOTIFY, actor="user", db_path=self.db_path)
        self.assertEqual(res["level"], 2)
        self.assertEqual(get_autonomy_level("self", self.db_path), AutonomyLevel.L2_ACT_AND_NOTIFY)

    def test_action_risk_classification(self):
        # Routine workspace file
        c_safe = classify_action(
            "file_write",
            target=os.path.join(PROJECT_ROOT, "workspace", "summary.md"),
            actor="autonomous_worker",
            is_protected_file=False,
        )
        self.assertEqual(c_safe["risk_tier"], RiskTier.SAFE)
        self.assertFalse(c_safe["hard_human_required"])

        # Protected code file
        c_elev = classify_action(
            "protected_file_route",
            target=os.path.join(PROJECT_ROOT, "modules", "tools.py"),
            actor="self_repair",
            is_protected_file=True,
        )
        self.assertEqual(c_elev["risk_tier"], RiskTier.ELEVATED)
        self.assertFalse(c_elev["hard_human_required"])

        # Out-of-project write -> CRITICAL + hard_human_required
        c_crit_out = classify_action(
            "file_write",
            target="/etc/hosts",
            actor="autonomous_worker",
        )
        self.assertEqual(c_crit_out["risk_tier"], RiskTier.CRITICAL)
        self.assertTrue(c_crit_out["hard_human_required"])

        # Credential access -> CRITICAL
        c_crit_cred = classify_action(
            "file_read",
            target=os.path.join(PROJECT_ROOT, ".env"),
            actor="autonomous_worker",
        )
        self.assertEqual(c_crit_cred["risk_tier"], RiskTier.CRITICAL)
        self.assertTrue(c_crit_cred["hard_human_required"])

        # Destructive shell -> CRITICAL
        c_crit_rm = classify_action(
            "shell_exec",
            target="rm -rf /tmp/something",
            actor="autonomous_worker",
        )
        self.assertEqual(c_crit_rm["risk_tier"], RiskTier.CRITICAL)
        self.assertTrue(c_crit_rm["hard_human_required"])

    def test_L0_blocks_even_safe_actions(self):
        res = check_approval(
            action="file_write",
            target=os.path.join(PROJECT_ROOT, "workspace", "draft.txt"),
            actor="autonomous_worker",
            source="self",  # default L0
            db_path=self.db_path,
        )
        self.assertFalse(res["approved"])
        self.assertEqual(res["method"], "L0_suggest_only")

    def test_L3_allows_in_project_elevated_but_blocks_critical_without_ui(self):
        set_autonomy_level("self", AutonomyLevel.L3_AUTONOMOUS_POLICY, actor="user", db_path=self.db_path)

        # In-project self-modification allowed at L3
        res_elev = check_approval(
            action="protected_file_route",
            target=os.path.join(PROJECT_ROOT, "modules", "scheduler.py"),
            actor="self_repair",
            source="self",
            is_protected_file=True,
            db_path=self.db_path,
        )
        self.assertTrue(res_elev["approved"])
        self.assertEqual(res_elev["method"], "L3_policy")

        # Out-of-project write STILL blocked at L3 without UI confirmation or token
        res_crit = check_approval(
            action="file_write",
            target="/tmp/outside_project.txt",
            actor="self_repair",
            source="self",
            db_path=self.db_path,
        )
        self.assertFalse(res_crit["approved"])
        self.assertEqual(res_crit["risk_tier"], "critical")

    def test_signed_token_and_ui_callback_authorization(self):
        target = "/tmp/outside_authorized.txt"
        token = issue_approval_token("file_write", target, "autonomous_worker", ttl_seconds=60)
        self.assertTrue(verify_approval_token(token, "file_write", target, "autonomous_worker"))
        self.assertFalse(verify_approval_token(token, "file_write", "/tmp/other.txt", "autonomous_worker"))

        res_tok = check_approval(
            action="file_write",
            target=target,
            actor="autonomous_worker",
            source="self",
            signed_token=token,
            db_path=self.db_path,
        )
        self.assertTrue(res_tok["approved"])
        self.assertEqual(res_tok["method"], "signed_token")

        # Test UI callback approval
        seen_prompts = []
        def fake_ui_confirm(payload):
            seen_prompts.append(payload)
            return True

        register_ui_prompt_callback(fake_ui_confirm)
        res_ui = check_approval(
            action="protected_file_route",
            target=os.path.join(PROJECT_ROOT, "jarvis.py"),
            actor="user",
            source="user",  # L1 requires confirmation for elevated
            is_protected_file=True,
            db_path=self.db_path,
        )
        self.assertTrue(res_ui["approved"])
        self.assertEqual(res_ui["method"], "ui_confirmation")
        self.assertEqual(len(seen_prompts), 1)

    def test_safe_write_file_enforces_approval_gate(self):
        outside_path = os.path.join(self.temp_dir.name, "blocked_outside.txt")
        res = safe_write_file(
            filepath=outside_path,
            content="should not write",
            actor="autonomous_worker",
            source="self",
        )
        self.assertFalse(res["success"])
        self.assertTrue(res.get("blocked_by_approval_gate"))
        self.assertFalse(os.path.exists(outside_path))


if __name__ == "__main__":
    unittest.main()
