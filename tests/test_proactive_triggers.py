import os
import sys
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules import triggers


class TestDiskAndBackupGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = Path(self.tmp)
        self.backups = self.root / ".backups"
        self.backups.mkdir()
        self.state_path = self.root / "trigger_state.json"
        # 12 backup files (1 MB each): 2 old (40d), 10 recent.
        # keep_min_files=10 protects the 10 recent; the 2 oldest are pruned.
        now = time.time()
        for i in range(12):
            p = self.backups / f"backup_{i:02d}.bak"
            p.write_bytes(b"x" * (1024 * 1024))
            age_days = 40 if i < 2 else 0
            mtime = now - age_days * 86400
            os.utime(p, (mtime, mtime))

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_guard_prunes_old_keeps_newest(self):
        res = triggers.run_disk_and_backup_guard(
            project_root=self.root, min_free_pct=100.0, max_age_days=30,
            keep_min_files=10, state_path=self.state_path, force=True,
        )
        self.assertTrue(res["triggered"])
        self.assertEqual(res["deleted_files"], 2)
        self.assertGreater(res["reclaimed_mb"], 0.0)
        # The 2 oldest are gone; the 10 newest remain.
        remaining = sorted(p.name for p in self.backups.iterdir())
        self.assertEqual(len(remaining), 10)
        self.assertNotIn("backup_00.bak", remaining)
        self.assertNotIn("backup_01.bak", remaining)

    def test_guard_cooldown_skips_without_force(self):
        triggers.run_disk_and_backup_guard(
            project_root=self.root, min_free_pct=100.0, max_age_days=30,
            keep_min_files=10, state_path=self.state_path, force=True,
        )
        # Recreate an old file; a non-forced call within cooldown must not prune it.
        p = self.backups / "backup_old.bak"
        p.write_bytes(b"y" * 2048)
        old = time.time() - 50 * 86400
        os.utime(p, (old, old))
        res = triggers.run_disk_and_backup_guard(
            project_root=self.root, min_free_pct=100.0, max_age_days=30,
            keep_min_files=10, state_path=self.state_path, force=False,
        )
        self.assertFalse(res["triggered"])
        self.assertTrue(p.exists())

    def test_guard_noop_when_disk_healthy(self):
        # min_free_pct=0 means the prune branch is never entered.
        res = triggers.run_disk_and_backup_guard(
            project_root=self.root, min_free_pct=0.0, state_path=self.state_path, force=True,
        )
        self.assertFalse(res["triggered"])
        self.assertEqual(res["deleted_files"], 0)


class TestDailyHealthDigest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = Path(self.tmp)
        self.digest_path = self.root / "daily_digest.md"
        self.state_path = self.root / "trigger_state.json"

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_run(self, cmd, **kwargs):
        res = MagicMock()
        joined = " ".join(str(c) for c in cmd)
        if "-c" in cmd or "unittest" in joined:
            res.stdout = "Ran 215 tests in 1.0s\nOK\n"
        else:
            res.stdout = " M modules/foo.py\n?? new.txt\n"
        res.stderr = ""
        res.returncode = 0
        return res

    def test_digest_writes_file_and_stamps_state(self):
        with patch("modules.triggers.subprocess.run", side_effect=self._fake_run), \
             patch("modules.goals.is_stopped", return_value=False), \
             patch("modules.diagnostics.check_memory_headroom", return_value=(True, 4096)), \
             patch("modules.diagnostics.get_available_ram_mb", return_value=4096.0), \
             patch("modules.reflections.get_reflections_for_review", return_value=[]):
            res = triggers.run_daily_health_digest(
                project_root=self.root, digest_path=self.digest_path,
                state_path=self.state_path, force=True,
            )
        self.assertTrue(res["written"])
        self.assertTrue(self.digest_path.exists())
        body = self.digest_path.read_text(encoding="utf-8")
        self.assertIn("Daily Health Digest", body)
        self.assertIn("Tests:", body)
        self.assertTrue("OK" in body or "FAILED" in body)
        state = triggers._load_trigger_state(self.state_path)
        self.assertIn("last_daily_digest_ts", state)

    def test_digest_cooldown_skips(self):
        with patch("modules.triggers.subprocess.run", side_effect=self._fake_run), \
             patch("modules.goals.is_stopped", return_value=False), \
             patch("modules.diagnostics.check_memory_headroom", return_value=(True, 4096)), \
             patch("modules.diagnostics.get_available_ram_mb", return_value=4096.0), \
             patch("modules.reflections.get_reflections_for_review", return_value=[]):
            triggers.run_daily_health_digest(
                project_root=self.root, digest_path=self.digest_path,
                state_path=self.state_path, force=True,
            )
            res2 = triggers.run_daily_health_digest(
                project_root=self.root, digest_path=self.digest_path,
                state_path=self.state_path, force=False,
            )
        self.assertFalse(res2["written"])

    def test_digest_respects_stop_flag(self):
        with patch("modules.goals.is_stopped", return_value=True):
            res = triggers.run_daily_health_digest(
                project_root=self.root, digest_path=self.digest_path,
                state_path=self.state_path, force=True,
            )
        self.assertFalse(res["written"])
        self.assertFalse(self.digest_path.exists())


class TestTriggerStateIO(unittest.TestCase):
    def test_load_missing_returns_empty(self):
        self.assertEqual(triggers._load_trigger_state(Path("/nonexistent/xyz.json")), {})

    def test_save_and_reload_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            triggers._save_trigger_state({"a": 1}, p)
            self.assertEqual(triggers._load_trigger_state(p), {"a": 1})


if __name__ == "__main__":
    unittest.main()