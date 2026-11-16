"""Tests for upgrade batch: sessions, compaction, RAM guard, telemetry, settings, triggers, router, MCP."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestSessionsAndCompaction(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        from modules.memory import init_db
        # init_db uses global DB_PATH; create tables in a temp DB manually
        import sqlite3
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE chat_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL DEFAULT 'default', role TEXT NOT NULL, content TEXT NOT NULL, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)")
        conn.execute("CREATE TABLE session_summaries (session_id TEXT PRIMARY KEY, summary TEXT, updated_at TEXT)")
        conn.commit()
        conn.close()

    def test_session_isolation(self):
        from modules.memory import save_chat_message, get_recent_history
        save_chat_message("user", "hello default", session_id="default", db_path=self.db)
        save_chat_message("user", "hello other", session_id="chat_1", db_path=self.db)
        default_msgs = get_recent_history(limit=40, session_id="default", db_path=self.db)
        other_msgs = get_recent_history(limit=40, session_id="chat_1", db_path=self.db)
        self.assertEqual(len(default_msgs), 1)
        self.assertEqual(default_msgs[0]["content"], "hello default")
        self.assertEqual(len(other_msgs), 1)
        self.assertEqual(other_msgs[0]["content"], "hello other")
        self.assertIn("id", default_msgs[0])
        self.assertIn("role", default_msgs[0])

    def test_delete_history_from_id(self):
        from modules.memory import save_chat_message, get_recent_history, delete_history_from_id
        id1 = save_chat_message("user", "first", session_id="s", db_path=self.db)
        save_chat_message("assistant", "second", session_id="s", db_path=self.db)
        deleted = delete_history_from_id(id1, session_id="s", db_path=self.db)
        self.assertGreaterEqual(deleted, 2)
        self.assertEqual(get_recent_history(session_id="s", db_path=self.db), [])

    def test_compact_session_prunes_and_summarizes(self):
        from modules.memory import save_chat_message, get_recent_history
        from modules.memory_consolidation import compact_session_if_needed
        for i in range(15):
            save_chat_message("user", f"message {i}", session_id="c", db_path=self.db)
        with patch("modules.ai.ask_local_brain", return_value="SUMMARY"):
            result = compact_session_if_needed(session_id="c", max_messages=10, keep_recent=4, db_path=self.db)
        self.assertIsNotNone(result)
        remaining = get_recent_history(session_id="c", db_path=self.db)
        self.assertEqual(len(remaining), 4)

    def test_list_sessions_includes_default(self):
        from modules.memory import save_chat_message, list_sessions
        save_chat_message("user", "x", session_id="chat_9", db_path=self.db)
        sessions = list_sessions(db_path=self.db)
        self.assertIn("default", sessions)
        self.assertIn("chat_9", sessions)


class TestRamGuard(unittest.TestCase):
    def test_get_available_ram_mb_linux(self):
        from modules.diagnostics import get_available_ram_mb
        val = get_available_ram_mb()
        self.assertIsInstance(val, float)

    def test_check_memory_headroom_with_mock(self):
        from modules.diagnostics import check_memory_headroom
        with patch("modules.diagnostics.get_available_ram_mb", return_value=2000.0):
            ok, avail = check_memory_headroom(min_available_mb=1200)
            self.assertTrue(ok)
        with patch("modules.diagnostics.get_available_ram_mb", return_value=500.0):
            ok, avail = check_memory_headroom(min_available_mb=1200)
            self.assertFalse(ok)

    def test_idle_goal_skips_on_low_ram(self):
        from modules.scheduler import process_one_idle_goal
        with patch("modules.diagnostics.get_available_ram_mb", return_value=100.0):
            res = process_one_idle_goal(step_executor=lambda **k: {}, db_path=self._mkdb())
        self.assertEqual(res["status"], "low_ram")

    def _mkdb(self):
        self._tmp = getattr(self, "_tmp", tempfile.mkdtemp())
        return os.path.join(self._tmp, "g.db")


class TestProviderTelemetry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "b.db")

    def test_record_and_summary(self):
        from modules.budget import record_provider_usage, get_provider_telemetry_summary
        record_provider_usage("groq", model="x", prompt_tokens=100, completion_tokens=50, latency_ms=120.5, db_path=self.db)
        record_provider_usage("ollama", prompt_tokens=10, completion_tokens=5, latency_ms=30.0, db_path=self.db)
        summary = {r["provider"]: r for r in get_provider_telemetry_summary(db_path=self.db)}
        self.assertEqual(summary["groq"]["calls"], 1)
        self.assertEqual(summary["groq"]["prompt_tokens"], 100)
        self.assertIn("ollama", summary)

    def test_offload_threshold(self):
        from modules.budget import record_provider_usage, should_offload_to_ollama
        # 79% of 1000 cap
        record_provider_usage("groq", prompt_tokens=790, completion_tokens=0, db_path=self.db)
        self.assertFalse(should_offload_to_ollama(is_urgent=False, daily_cap=1000, db_path=self.db))
        # push to 80%
        record_provider_usage("groq", prompt_tokens=10, completion_tokens=0, db_path=self.db)
        self.assertTrue(should_offload_to_ollama(is_urgent=False, daily_cap=1000, db_path=self.db))
        self.assertFalse(should_offload_to_ollama(is_urgent=True, daily_cap=1000, db_path=self.db))


class TestRuntimeSettings(unittest.TestCase):
    def test_defaults_and_persistence(self):
        from modules.preferences import get_runtime_settings, update_runtime_settings
        tmp = Path(tempfile.mkdtemp()) / "settings.json"
        s = get_runtime_settings(settings_path=tmp)
        self.assertEqual(s["routing_mode"], "auto")
        update_runtime_settings({"routing_mode": "ollama_only", "unknown_key": 1}, settings_path=tmp)
        s2 = get_runtime_settings(settings_path=tmp)
        self.assertEqual(s2["routing_mode"], "ollama_only")
        self.assertNotIn("unknown_key", s2)


class TestRouterAndTriggers(unittest.TestCase):
    def test_should_attach_tools(self):
        from modules.ai import should_attach_tools
        self.assertFalse(should_attach_tools("hi"))
        self.assertFalse(should_attach_tools("hello there"))
        self.assertTrue(should_attach_tools("read the config file"))
        self.assertTrue(should_attach_tools("run tests"))

    def test_disk_guard_prunes_old_backups(self):
        from modules.triggers import run_disk_and_backup_guard
        root = Path(tempfile.mkdtemp())
        backups = root / ".backups"
        backups.mkdir()
        import time
        for i in range(15):
            f = backups / f"b{i}.bak"
            f.write_text("x" * 100)
            os.utime(f, (time.time() - 100000, time.time() - 100000))
        state = root / "state.json"
        res = run_disk_and_backup_guard(project_root=root, max_backups_mb=0.001, keep_min_files=10, state_path=state, force=True)
        self.assertTrue(res["triggered"])
        remaining = list(backups.glob("*.bak"))
        self.assertEqual(len(remaining), 10)
        self.assertGreaterEqual(res["deleted_files"], 5)

    def test_daily_digest_writes_and_cools_down(self):
        from modules.triggers import run_daily_health_digest
        root = Path(tempfile.mkdtemp())
        digest = root / "digest.md"
        state = root / "state.json"
        mock_proc = MagicMock()
        mock_proc.stderr = "Ran 5 tests in 0.1s\nOK\n"
        mock_proc.stdout = " M file.py\n"
        with patch("modules.triggers.subprocess.run", return_value=mock_proc):
            res = run_daily_health_digest(project_root=root, digest_path=digest, state_path=state, force=True)
        self.assertTrue(res["written"])
        self.assertTrue(digest.exists())
        # second immediate call skips
        res2 = run_daily_health_digest(project_root=root, digest_path=digest, state_path=state, force=False)
        self.assertFalse(res2["written"])


class TestMcpBridge(unittest.TestCase):
    def test_is_mcp_tool(self):
        from modules.mcp_bridge import is_mcp_tool
        self.assertTrue(is_mcp_tool("mcp__fetch__fetch"))
        self.assertFalse(is_mcp_tool("get_current_time"))

    def test_hydrate_missing_config_graceful(self):
        from modules.mcp_bridge import hydrate_mcp_servers
        schemas = hydrate_mcp_servers(config_path="/nonexistent/path.json")
        self.assertEqual(schemas, [])

    def test_set_server_enabled(self):
        from modules.mcp_bridge import set_mcp_server_enabled, list_mcp_server_configs
        cfg = Path(tempfile.mkdtemp()) / "mcp.json"
        cfg.write_text('{"mcpServers": {"s1": {"enabled": false}}}')
        set_mcp_server_enabled("s1", True, config_path=str(cfg))
        self.assertTrue(list_mcp_server_configs(config_path=str(cfg))["s1"])

    def test_call_mcp_tool_not_connected(self):
        from modules.mcp_bridge import call_mcp_tool
        res = call_mcp_tool("mcp__fetch__fetch", {"url": "http://x"})
        self.assertIn("Error", res)


class TestHybridMemory(unittest.TestCase):
    def test_embed_and_search(self):
        from modules.memory import _embed_text
        v = _embed_text("hello world")
        self.assertEqual(len(v), 128)
        self.assertAlmostEqual(sum(x * x for x in v), 1.0, places=4)


if __name__ == "__main__":
    unittest.main()