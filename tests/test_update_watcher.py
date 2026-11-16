import os
import sys
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules import update_watcher as uw


def _reset_globals():
    uw._BOOT_HEAD = ""
    uw._BOOT_MTIME = 0.0
    uw._PROMPTED_FINGERPRINTS.clear()
    uw._LAST_REMOTE_CHECK_TS = 0.0
    uw._PENDING_APPROVAL = False


class UpdateWatcherTestBase(unittest.TestCase):
    def setUp(self):
        _reset_globals()
        self.tmp = tempfile.mkdtemp()
        self.root = Path(self.tmp)
        (self.root / "jarvis.py").write_text("# jarvis\n", encoding="utf-8")
        (self.root / "modules").mkdir()
        (self.root / "modules" / "core.py").write_text("# core\n", encoding="utf-8")
        (self.root / "data").mkdir()
        (self.root / "data" / "mcp_servers.json").write_text("{}", encoding="utf-8")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)
        _reset_globals()


class TestBaselineAndDetection(UpdateWatcherTestBase):
    def test_no_change_returns_none(self):
        uw.init_update_baseline(self.root)
        self.assertIsNone(uw.detect_available_update(root=self.root))

    def test_modified_module_is_detected(self):
        uw.init_update_baseline(self.root)
        target = self.root / "modules" / "core.py"
        future = time.time() + 10
        os.utime(target, (future, future))

        info = uw.detect_available_update(root=self.root)
        self.assertIsNotNone(info)
        self.assertIn("modules/core.py", info["changed_files"])
        self.assertTrue(any("modified since boot" in r for r in info["reasons"]))

    def test_modified_mcp_config_is_detected(self):
        uw.init_update_baseline(self.root)
        target = self.root / "data" / "mcp_servers.json"
        future = time.time() + 10
        os.utime(target, (future, future))

        info = uw.detect_available_update(root=self.root)
        self.assertIsNotNone(info)
        self.assertIn("data/mcp_servers.json", info["changed_files"])

    def test_runtime_state_changes_do_not_trigger(self):
        uw.init_update_baseline(self.root)
        for rel in ("memory/session_history.json", "logs/jarvis.log", "data/trigger_state.json"):
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("runtime noise", encoding="utf-8")
            future = time.time() + 10
            os.utime(p, (future, future))

        self.assertIsNone(uw.detect_available_update(root=self.root))

    def test_pycache_changes_do_not_trigger(self):
        uw.init_update_baseline(self.root)
        cache = self.root / "modules" / "__pycache__" / "core.cpython-312.pyc"
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(b"\x00\x01")
        future = time.time() + 10
        os.utime(cache, (future, future))

        self.assertIsNone(uw.detect_available_update(root=self.root))


class TestCheckAndRequestRestart(UpdateWatcherTestBase):
    def _info(self, fp="fp-1"):
        return {
            "fingerprint": fp,
            "reasons": ["New commit active on disk (abc123 test)"],
            "changed_files": ["modules/core.py"],
            "current_head": "abc123",
            "remote_head": "def456",
        }

    def test_approved_restarts_and_passes_pull_remote(self):
        calls = {"approvals": 0, "restarts": []}

        def _approval(msg, info):
            calls["approvals"] += 1
            return True

        def _restart(root, pull_remote=False):
            calls["restarts"].append((str(root), pull_remote))

        with patch.object(uw, "detect_available_update", return_value=self._info()):
            res = uw.check_and_request_restart(
                root=self.root, approval_fn=_approval, restart_fn=_restart
            )

        self.assertEqual(res["status"], "restarting")
        self.assertEqual(calls["approvals"], 1)
        self.assertEqual(len(calls["restarts"]), 1)
        self.assertEqual(calls["restarts"][0][0], str(self.root))
        self.assertTrue(calls["restarts"][0][1])  # remote_head != current_head

    def test_denied_defers_and_suppresses_duplicate_prompt(self):
        calls = {"approvals": 0, "restarts": 0}

        def _approval(msg, info):
            calls["approvals"] += 1
            return False

        def _restart(root, pull_remote=False):
            calls["restarts"] += 1

        with patch.object(uw, "detect_available_update", return_value=self._info()):
            first = uw.check_and_request_restart(
                root=self.root, approval_fn=_approval, restart_fn=_restart
            )
            second = uw.check_and_request_restart(
                root=self.root, approval_fn=_approval, restart_fn=_restart
            )

        self.assertEqual(first["status"], "deferred")
        self.assertEqual(second["status"], "already_prompted")
        self.assertEqual(calls["approvals"], 1)
        self.assertEqual(calls["restarts"], 0)

    def test_force_prompt_reprompts_after_defer(self):
        calls = {"approvals": 0}

        def _approval(msg, info):
            calls["approvals"] += 1
            return False

        with patch.object(uw, "detect_available_update", return_value=self._info()):
            uw.check_and_request_restart(root=self.root, approval_fn=_approval)
            res = uw.check_and_request_restart(
                root=self.root, approval_fn=_approval, force_prompt=True
            )

        self.assertEqual(res["status"], "deferred")
        self.assertEqual(calls["approvals"], 2)

    def test_up_to_date_short_circuits(self):
        approvals = {"n": 0}

        def _approval(msg, info):
            approvals["n"] += 1
            return True

        with patch.object(uw, "detect_available_update", return_value=None):
            res = uw.check_and_request_restart(root=self.root, approval_fn=_approval)

        self.assertEqual(res["status"], "up_to_date")
        self.assertEqual(approvals["n"], 0)


class TestApprovalHookAdapter(UpdateWatcherTestBase):
    def test_adapts_to_check_approval_dict_result(self):
        from modules import approval

        with patch.object(approval, "check_approval", return_value={"approved": True}) as mock_check:
            ok = uw._invoke_approval_hook("Restart J.A.R.V.I.S. now?", {"changed_files": ["jarvis.py"]})
        self.assertTrue(ok)
        mock_check.assert_called_once()
        self.assertEqual(mock_check.call_args.kwargs["action"], "restart_jarvis")

    def test_adapts_to_check_approval_denial(self):
        from modules import approval

        with patch.object(approval, "check_approval", return_value={"approved": False}):
            ok = uw._invoke_approval_hook("Restart J.A.R.V.I.S. now?", {"changed_files": []})
        self.assertFalse(ok)

    def test_adapts_to_named_prompt_function(self):
        from modules import approval

        def request_approval(action, title, details):
            return details.startswith("Restart")

        with patch.object(approval, "request_approval", request_approval, create=True):
            ok = uw._invoke_approval_hook("Restart J.A.R.V.I.S. now?", {"changed_files": []})
        self.assertTrue(ok)

    def test_adapts_to_registered_ui_callback(self):
        from modules import approval

        with patch.object(approval, "check_approval", side_effect=RuntimeError("no gate")), \
             patch.object(approval, "_UI_PROMPT_CALLBACK", lambda payload: payload["action"] == "restart_jarvis"):
            ok = uw._invoke_approval_hook("Restart J.A.R.V.I.S. now?", {"changed_files": []})
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()