import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.memory import hybrid_recall


class TestHybridRecall(unittest.TestCase):
    def test_fuses_lexical_and_semantic(self):
        lexical = [("topic_context", "blue branding", "2026-01-01")]
        semantic = [{"id": 1, "category": "branding", "content": "premium blue", "score": 0.9}]

        with patch("modules.memory.search_memories", return_value=lexical), \
             patch("modules.vector_memory.semantic_search", return_value=semantic):
            result = hybrid_recall("brand", limit=5)

        self.assertIn("blue branding", result)
        self.assertIn("premium blue", result)

    def test_item_found_by_both_ranks_first(self):
        shared = "shared blue memory"
        lexical = [("topic_context", shared, "2026-01-01"), ("topic_context", "lexical only", "2026-01-01")]
        semantic = [{"content": shared, "score": 0.5}, {"content": "semantic only", "score": 0.4}]

        with patch("modules.memory.search_memories", return_value=lexical), \
             patch("modules.vector_memory.semantic_search", return_value=semantic):
            result = hybrid_recall("blue", limit=5)

        self.assertEqual(result[0], shared)

    def test_dedupes_and_respects_limit(self):
        lexical = [("c", f"item {i}", "d") for i in range(10)]
        with patch("modules.memory.search_memories", return_value=lexical), \
             patch("modules.vector_memory.semantic_search", return_value=[]):
            result = hybrid_recall("item", limit=3)
        self.assertEqual(len(result), 3)
        self.assertEqual(len(set(result)), 3)

    def test_empty_query_returns_empty(self):
        self.assertEqual(hybrid_recall("", limit=5), [])
        self.assertEqual(hybrid_recall("   ", limit=5), [])

    def test_graceful_when_backends_fail(self):
        with patch("modules.memory.search_memories", side_effect=RuntimeError("db down")), \
             patch("modules.vector_memory.semantic_search", side_effect=RuntimeError("vec down")):
            self.assertEqual(hybrid_recall("anything", limit=5), [])


if __name__ == "__main__":
    unittest.main()