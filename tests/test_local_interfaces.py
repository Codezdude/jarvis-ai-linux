import json
import os
import socket
import unittest

from fastapi.testclient import TestClient

from config.settings import BASE_DIR
from modules.mcp_server import list_mcp_tools, call_mcp_tool
from modules.safe_storage import atomic_write_text, project_safe_path, read_text_file
from modules.local_gateway import create_app, resolve_gateway_port
from modules.ai import choose_provider_order, sanitize_fallback_messages
from modules.automation import run_free_automation_bots, tech_ir_learning_bot
from modules.memory import save_reflection
from modules.tools import generate_image
from modules.vector_memory import save_vector_memory, semantic_search
from modules.workspace_growth import (
    append_workspace_growth_log,
    build_project_memory_index,
    ensure_experiment_directory,
    search_project_memory_index,
    search_workspace_growth_log,
)


class LocalInterfaceTests(unittest.TestCase):
    def test_mcp_tool_list_includes_main_workspace_tools(self):
        tools = list_mcp_tools()
        self.assertIsInstance(tools, list)
        self.assertTrue(any(tool.get("name") == "read_local_file" for tool in tools))
        self.assertTrue(any(tool.get("name") == "web_search" for tool in tools))

    def test_mcp_tool_call_executes_read_file(self):
        file_path = os.path.join(BASE_DIR, "generated_code", "mcp_demo.txt")
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as handle:
            handle.write("hello from workspace")

        result = call_mcp_tool("read_local_file", {"filepath": file_path})
        self.assertIn("hello from workspace", result)

    def test_atomic_write_preserves_project_scope_and_content(self):
        target = os.path.join(BASE_DIR, "generated_code", "nested", "file.txt")
        atomic_write_text(target, "alpha\nbeta")
        self.assertEqual(read_text_file(target), "alpha\nbeta")

    def test_project_paths_allow_jarvis_tree_access_and_block_escape(self):
        nested_target = os.path.join(BASE_DIR, "generated_code", "workspace_access.txt")
        self.assertEqual(project_safe_path(nested_target), os.path.realpath(nested_target))

        escaped = os.path.join(BASE_DIR, "..", "outside_workspace.txt")
        with self.assertRaises(ValueError):
            project_safe_path(escaped)

    def test_gateway_app_exposes_health_and_tools_routes(self):
        app = create_app()
        routes = [route.path for route in app.routes]
        self.assertIn("/health", routes)
        self.assertIn("/tools", routes)

    def test_gateway_resolves_an_open_port_when_preferred_port_is_busy(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            busy_port = sock.getsockname()[1]
            chosen_port = resolve_gateway_port(busy_port)
            self.assertIsInstance(chosen_port, int)
            self.assertNotEqual(chosen_port, busy_port)

    def test_provider_priority_and_plaintext_fallback_rules(self):
        order = choose_provider_order("debug this python crash", ["gemini", "groq", "ollama"])
        self.assertEqual(order[0], "groq")

        cleaned = sanitize_fallback_messages([
            {"role": "system", "content": "<system>Use tool calling</system>Always be direct."},
            {"role": "user", "content": "Fix this bug."},
        ])
        self.assertNotIn("<system>", cleaned[0]["content"])
        self.assertIn("plain text", cleaned[0]["content"].lower())

    def test_free_automation_bot_suite_runs_and_reports_learning_activity(self):
        result = run_free_automation_bots()
        self.assertIn("bots", result)
        self.assertGreaterEqual(len(result["bots"]), 3)
        self.assertIn("summary", result)

    def test_tech_ir_and_image_generation_learn_from_web(self):
        image_result = generate_image("Visionary automation research", filename="tech_ir_test.png")
        self.assertIn("Successfully generated", image_result)

        learning = tech_ir_learning_bot()
        self.assertIn("status", learning)
        self.assertIn("summary", learning)

    def test_gateway_requires_valid_bearer_token(self):
        original = os.environ.get("JARVIS_API_TOKEN")
        os.environ["JARVIS_API_TOKEN"] = "demo-token"
        try:
            app = create_app()
            client = TestClient(app)
            response = client.get("/health")
            self.assertEqual(response.status_code, 401)

            response = client.get("/health", headers={"Authorization": "Bearer demo-token"})
            self.assertEqual(response.status_code, 200)
        finally:
            if original is None:
                os.environ.pop("JARVIS_API_TOKEN", None)
            else:
                os.environ["JARVIS_API_TOKEN"] = original

    def test_gateway_rate_limits_and_vector_memory_can_recall_related_context(self):
        original = os.environ.get("JARVIS_RATE_LIMIT")
        os.environ["JARVIS_RATE_LIMIT"] = "2"
        os.environ["JARVIS_RATE_WINDOW_SECONDS"] = "60"
        try:
            save_vector_memory("Customer wants a premium blue brand for creators", category="branding")
            results = semantic_search("premium blue visual identity for creators", limit=3)
            self.assertTrue(any("brand" in item["content"].lower() for item in results))

            app = create_app()
            client = TestClient(app)
            headers = {"Authorization": "Bearer demo-token"}
            os.environ["JARVIS_API_TOKEN"] = "demo-token"
            self.assertEqual(client.get("/health", headers=headers).status_code, 200)
            self.assertEqual(client.get("/health", headers=headers).status_code, 200)
            self.assertEqual(client.get("/health", headers=headers).status_code, 429)
        finally:
            if original is None:
                os.environ.pop("JARVIS_RATE_LIMIT", None)
            else:
                os.environ["JARVIS_RATE_LIMIT"] = original

            os.environ.pop("JARVIS_RATE_WINDOW_SECONDS", None)

    def test_project_growth_features_create_dirs_and_logs_and_index_memory(self):
        experiment_dir = ensure_experiment_directory("brand_testing")
        self.assertTrue(os.path.exists(experiment_dir))

        log_entry = append_workspace_growth_log("Model decided to test blue premium concept variants.", "brand-testing")
        self.assertIn("decision", log_entry.lower())
        results = search_workspace_growth_log("blue premium concept", limit=5)
        self.assertTrue(results)

        save_vector_memory("We are experimenting with premium blue brand concepts", category="branding")
        build_project_memory_index()
        indexed = search_project_memory_index("premium blue brand", limit=5)
        self.assertTrue(indexed)

    def test_semantic_cache_and_reindex_pipeline_support_repeated_queries(self):
        save_vector_memory("Premium warm colors for creator brand identities feel luxurious and calm.", category="branding")
        save_vector_memory("We prefer a premium blue brand direction with modern creator appeal.", category="branding")

        matches = semantic_search("premium blue creator brand", limit=3)
        self.assertTrue(matches)
        self.assertTrue(any(item["content"].lower().find("brand") >= 0 for item in matches))

        from modules.ai import get_relevant_memory_context
        save_reflection(
            query="premium blue creator brand",
            raw_response="generic brand ideas",
            final_response="premium blue creator brand with calm luxury CTA and proof points",
            critique="Need stronger CTA and clearer value prop.",
            category="reflection",
        )
        context = get_relevant_memory_context("premium blue creator brand")
        self.assertTrue(context)
        self.assertTrue("Reflection memory" in context or len(context) > 10)

        from modules.memory import reindex_memory_pipeline
        result = reindex_memory_pipeline(force=False)
        self.assertIsInstance(result, dict)
        self.assertIn("semantic_reindexed", result)


if __name__ == "__main__":
    unittest.main()
