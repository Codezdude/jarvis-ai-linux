import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestShouldAttachTools(unittest.TestCase):
    def test_short_conversational_skips_tools(self):
        from modules.ai import should_attach_tools
        self.assertFalse(should_attach_tools("hi"))
        self.assertFalse(should_attach_tools("how are you"))
        self.assertFalse(should_attach_tools(""))

    def test_action_queries_attach_tools(self):
        from modules.ai import should_attach_tools
        self.assertTrue(should_attach_tools("run the tests"))
        self.assertTrue(should_attach_tools("list the files in this directory"))
        self.assertTrue(should_attach_tools("read config/settings.py"))
        self.assertTrue(should_attach_tools("what is the disk usage"))


class TestRankToolSchemas(unittest.TestCase):
    def _schema(self, name, desc):
        return {"type": "function", "function": {"name": name, "description": desc, "parameters": {}}}

    def test_limits_to_top_k(self):
        from modules.tools import rank_tool_schemas
        schemas = [self._schema(f"tool_{i}", f"does thing {i}") for i in range(10)]
        self.assertEqual(len(rank_tool_schemas("thing", schemas, top_k=3)), 3)

    def test_ranks_relevant_first(self):
        from modules.tools import rank_tool_schemas
        schemas = [
            self._schema("unrelated", "cooks food"),
            self._schema("file_reader", "reads a file from disk"),
            self._schema("another", "plays music"),
        ]
        ranked = rank_tool_schemas("read file", schemas, top_k=1)
        self.assertEqual(ranked[0]["function"]["name"], "file_reader")

    def test_empty_query_returns_first_k(self):
        from modules.tools import rank_tool_schemas
        schemas = [self._schema(f"t{i}", "d") for i in range(5)]
        ranked = rank_tool_schemas("", schemas, top_k=2)
        self.assertEqual([s["function"]["name"] for s in ranked], ["t0", "t1"])

    def test_returns_all_when_under_top_k(self):
        from modules.tools import rank_tool_schemas
        schemas = [self._schema("a", "x")]
        self.assertEqual(rank_tool_schemas("a", schemas, top_k=5), schemas)


class TestQuotaOffload(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "budget.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_daily_cloud_tokens_aggregates_non_ollama(self):
        from modules.budget import record_provider_usage, get_daily_cloud_tokens

        record_provider_usage("groq", "m", prompt_tokens=100, completion_tokens=50,
                              latency_ms=10, db_path=self.db_path)
        record_provider_usage("ollama", "m", prompt_tokens=999, completion_tokens=999,
                              latency_ms=10, db_path=self.db_path)
        self.assertEqual(get_daily_cloud_tokens(db_path=self.db_path), 150)

    def test_should_offload_at_80_percent(self):
        from modules.budget import record_provider_usage, should_offload_to_ollama

        # Urgent requests never offload.
        self.assertFalse(should_offload_to_ollama(is_urgent=True, daily_cap=1000, db_path=self.db_path))

        # Below threshold -> no offload.
        record_provider_usage("groq", "m", prompt_tokens=700, completion_tokens=0,
                              latency_ms=10, db_path=self.db_path)
        self.assertFalse(should_offload_to_ollama(is_urgent=False, daily_cap=1000, db_path=self.db_path))

        # Cross 80% threshold -> offload.
        record_provider_usage("groq", "m", prompt_tokens=200, completion_tokens=0,
                              latency_ms=10, db_path=self.db_path)
        self.assertTrue(should_offload_to_ollama(is_urgent=False, daily_cap=1000, db_path=self.db_path))

    def test_zero_cap_disables_offload(self):
        from modules.budget import should_offload_to_ollama
        self.assertFalse(should_offload_to_ollama(is_urgent=False, daily_cap=0, db_path=self.db_path))


if __name__ == "__main__":
    unittest.main()