import os
import sys
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules import mcp_bridge


def _write_config(servers: dict) -> str:
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"mcpServers": servers}, fh)
    return path


class TestMcpToolHelpers(unittest.TestCase):
    def test_is_mcp_tool_true_for_namespaced(self):
        self.assertTrue(mcp_bridge.is_mcp_tool("mcp__filesystem__read_file"))

    def test_is_mcp_tool_false_for_regular(self):
        self.assertFalse(mcp_bridge.is_mcp_tool("search_web"))
        self.assertFalse(mcp_bridge.is_mcp_tool(""))

    def test_schema_from_tool_builds_function_schema(self):
        tool = MagicMock()
        tool.name = "read_file"
        tool.description = "Read a file"
        tool.inputSchema = {"type": "object", "properties": {"path": {"type": "string"}}}
        schema = mcp_bridge._schema_from_tool("filesystem", tool)
        self.assertEqual(schema["type"], "function")
        self.assertEqual(schema["function"]["name"], "mcp__filesystem__read_file")
        self.assertIn("filesystem", schema["function"]["description"])
        self.assertEqual(schema["function"]["parameters"]["properties"]["path"]["type"], "string")


class TestMcpCallRouting(unittest.TestCase):
    def test_call_rejects_non_mcp_tool(self):
        out = mcp_bridge.call_mcp_tool("search_web", {})
        self.assertIn("not an MCP tool", out)

    def test_call_rejects_malformed_name(self):
        out = mcp_bridge.call_mcp_tool("mcp__onlyserver", {})
        self.assertIn("malformed", out)

    def test_call_reports_disconnected_server(self):
        out = mcp_bridge.call_mcp_tool("mcp__nosuchserver__do", {})
        self.assertIn("not connected", out)


class TestMcpConfig(unittest.TestCase):
    def setUp(self):
        self.cfg = _write_config({
            "filesystem": {"transport": "stdio", "command": "npx", "args": [], "enabled": True},
            "fetch": {"transport": "stdio", "command": "uvx", "args": [], "enabled": False},
        })

    def tearDown(self):
        try:
            os.unlink(self.cfg)
        except Exception:
            pass

    def test_list_mcp_server_configs_returns_enabled_map(self):
        result = mcp_bridge.list_mcp_server_configs(self.cfg)
        self.assertEqual(result["filesystem"], True)
        self.assertEqual(result["fetch"], False)

    def test_set_mcp_server_enabled_flips_and_persists(self):
        with patch.object(mcp_bridge, "hydrate_mcp_servers") as mock_hydrate:
            mcp_bridge.set_mcp_server_enabled("fetch", True, config_path=self.cfg)
            mock_hydrate.assert_called()
        result = mcp_bridge.list_mcp_server_configs(self.cfg)
        self.assertTrue(result["fetch"])

    def test_set_mcp_server_enabled_ignores_unknown(self):
        with patch.object(mcp_bridge, "hydrate_mcp_servers"):
            mcp_bridge.set_mcp_server_enabled("ghost", True, config_path=self.cfg)
        # Unknown server is a no-op; config unchanged.
        self.assertNotIn("ghost", mcp_bridge.list_mcp_server_configs(self.cfg))


class TestMcpDegradation(unittest.TestCase):
    def test_hydrate_returns_empty_when_mcp_missing(self):
        real_import = __import__

        def _fake_import(name, *args, **kwargs):
            if name == "mcp":
                raise ImportError("mcp not installed")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_fake_import):
            schemas = mcp_bridge.hydrate_mcp_servers(config_path="/nonexistent.json")
        self.assertEqual(schemas, [])
        self.assertEqual(mcp_bridge.get_mcp_tool_schemas(), [])

    def test_get_mcp_tool_schemas_returns_copy(self):
        with mcp_bridge._LOCK:
            mcp_bridge._TOOL_SCHEMAS.clear()
            mcp_bridge._TOOL_SCHEMAS.append({"type": "function", "function": {"name": "x"}})
        first = mcp_bridge.get_mcp_tool_schemas()
        first.append({"type": "function", "function": {"name": "y"}})
        second = mcp_bridge.get_mcp_tool_schemas()
        self.assertEqual(len(second), 1)  # mutating the copy doesn't affect the store

    def test_shutdown_is_safe_without_loop(self):
        # Should not raise even if the manager never started a loop.
        mcp_bridge.shutdown_mcp_servers()


if __name__ == "__main__":
    unittest.main()