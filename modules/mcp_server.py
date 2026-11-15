import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from modules.automation import (
    content_idea_bot,
    experiment_review_bot,
    memory_cleanup_bot,
    quality_guard_bot,
    run_free_automation_bots,
    tech_ir_learning_bot,
)
from modules.safe_storage import project_safe_path, read_text_file, list_project_directory
from modules.tools import (
    browser_automation_task,
    capture_post_analytics,
    create_promotional_flyer,
    edit_file_section,
    generate_brand_concepts,
    generate_social_post_brief,
    get_current_time,
    get_system_diagnostics,
    list_directory,
    open_website,
    read_local_file,
    review_social_post_draft,
    run_project_tests,
    schedule_social_post,
    search_project_files,
    validate_code_syntax,
    web_search,
    write_local_file,
)


MCP_TOOL_MAP = {
    "get_system_diagnostics": lambda **kwargs: get_system_diagnostics(),
    "get_current_time": lambda **kwargs: get_current_time(),
    "open_website": lambda **kwargs: open_website(kwargs.get("url", "")),
    "web_search": lambda **kwargs: web_search(kwargs.get("query", ""), kwargs.get("max_results", 4)),
    "browser_automation_task": lambda **kwargs: browser_automation_task(
        kwargs.get("url", ""),
        kwargs.get("objective", ""),
        kwargs.get("steps"),
        kwargs.get("allowlist"),
        kwargs.get("confirmed", False),
    ),
    "generate_social_post_brief": lambda **kwargs: generate_social_post_brief(
        kwargs.get("brand_name", ""),
        kwargs.get("audience", ""),
        kwargs.get("tone", ""),
        kwargs.get("platform", "instagram"),
        kwargs.get("colors", "blue, gold"),
    ),
    "generate_brand_concepts": lambda **kwargs: generate_brand_concepts(
        kwargs.get("brand_name", ""),
        kwargs.get("audience", ""),
        kwargs.get("tone", ""),
        kwargs.get("colors", "blue, gold"),
        kwargs.get("platform", "instagram"),
    ),
    "review_social_post_draft": lambda **kwargs: review_social_post_draft(
        kwargs.get("pack_input", ""),
        kwargs.get("approved", False),
        kwargs.get("review_notes", ""),
    ),
    "schedule_social_post": lambda **kwargs: schedule_social_post(
        kwargs.get("pack_input", ""),
        kwargs.get("publish_time"),
    ),
    "capture_post_analytics": lambda **kwargs: capture_post_analytics(
        kwargs.get("reach", 0),
        kwargs.get("likes", 0),
        kwargs.get("comments", 0),
        kwargs.get("saves", 0),
        kwargs.get("shares", 0),
    ),
    "run_free_automation_bots": lambda **kwargs: run_free_automation_bots(),
    "memory_cleanup_bot": lambda **kwargs: memory_cleanup_bot(),
    "experiment_review_bot": lambda **kwargs: experiment_review_bot(),
    "quality_guard_bot": lambda **kwargs: quality_guard_bot(),
    "content_idea_bot": lambda **kwargs: content_idea_bot(),
    "tech_ir_learning_bot": lambda **kwargs: tech_ir_learning_bot(),
    "generate_image": lambda **kwargs: generate_image(
        kwargs.get("prompt", "JARVIS creative concept"),
        kwargs.get("filename", "jarvis_generated.png"),
        kwargs.get("width", 1080),
        kwargs.get("height", 1080),
    ),
    "web_fetch_and_learn": lambda **kwargs: web_fetch_and_learn(
        kwargs.get("url", ""),
        kwargs.get("max_chars", 2000),
    ),
    "read_local_file": lambda **kwargs: read_local_file(kwargs.get("filepath", "")),
    "write_local_file": lambda **kwargs: write_local_file(
        kwargs.get("filepath", ""),
        kwargs.get("content", ""),
        kwargs.get("confirmed", True),
    ),
    "edit_file_section": lambda **kwargs: edit_file_section(
        kwargs.get("filepath", ""),
        kwargs.get("old_text", ""),
        kwargs.get("new_text", ""),
        kwargs.get("confirmed", True),
    ),
    "list_directory": lambda **kwargs: list_directory(kwargs.get("directory_path", ".")),
    "search_project_files": lambda **kwargs: search_project_files(
        kwargs.get("query", ""),
        kwargs.get("directory_path", "."),
        kwargs.get("max_results", 20),
    ),
    "validate_code_syntax": lambda **kwargs: validate_code_syntax(
        kwargs.get("code_string", ""),
        kwargs.get("filepath", ""),
    ),
    "run_project_tests": lambda **kwargs: run_project_tests(kwargs.get("test_target", "tests")),
    "create_promotional_flyer": lambda **kwargs: create_promotional_flyer(
        kwargs.get("headline", ""),
        kwargs.get("subtext", ""),
        kwargs.get("filename", "flyer.jpg"),
    ),
}


def list_mcp_tools() -> list[dict]:
    """Return the tool metadata exposed over the local MCP interface."""
    tool_specs = [
        {
            "name": "read_local_file",
            "description": "Read the contents of a project-local file.",
            "inputSchema": {
                "type": "object",
                "properties": {"filepath": {"type": "string"}},
                "required": ["filepath"],
            },
        },
        {
            "name": "write_local_file",
            "description": "Write a project-local file, respecting project safety boundaries.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string"},
                    "content": {"type": "string"},
                    "confirmed": {"type": "boolean"},
                },
                "required": ["filepath", "content"],
            },
        },
        {
            "name": "web_search",
            "description": "Search the live web via Tavily for recent information.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "list_directory",
            "description": "List files and folders inside the project directory.",
            "inputSchema": {
                "type": "object",
                "properties": {"directory_path": {"type": "string"}},
            },
        },
        {
            "name": "search_project_files",
            "description": "Search within the project workspace for text or filenames.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "directory_path": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "get_system_diagnostics",
            "description": "Return CPU, RAM, and system diagnostic details.",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]
    return tool_specs


def call_mcp_tool(tool_name: str, params: dict | None = None) -> str:
    """Execute a named tool through the MCP interface and return its result string."""
    params = params or {}
    handler = MCP_TOOL_MAP.get(tool_name)
    if handler is None:
        raise KeyError(f"Unknown MCP tool: {tool_name}")
    return str(handler(**params))


def handle_mcp_message(message: dict) -> dict:
    """Process JSON-RPC-like MCP requests for tools and workspace operations."""
    request_id = message.get("id", 1)
    method = message.get("method")
    params = message.get("params", {})

    if method in ("tools/list", "list"):
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": list_mcp_tools()}}

    if method in ("tools/call", "call"):
        tool_name = params.get("name") or params.get("tool")
        arguments = params.get("arguments", params.get("args", {}))
        try:
            result = call_mcp_tool(tool_name, arguments)
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": result}],
                    "name": tool_name,
                },
            }
        except Exception as exc:
            response = {"jsonrpc": "2.0", "id": request_id, "error": {"message": str(exc), "code": -32601}}
        return response

    if method == "workspace/read":
        path = params.get("path") or params.get("filepath")
        try:
            text = read_text_file(path)
            return {"jsonrpc": "2.0", "id": request_id, "result": {"content": text}}
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"message": str(exc), "code": -32000}}

    if method == "workspace/write":
        path = params.get("path") or params.get("filepath")
        content = params.get("content", "")
        try:
            result = write_local_file(path, content, confirmed=params.get("confirmed", True))
            return {"jsonrpc": "2.0", "id": request_id, "result": {"content": result}}
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"message": str(exc), "code": -32000}}

    if method == "workspace/list":
        path = params.get("path") or params.get("directory_path") or "."
        try:
            entries = list_project_directory(path)
            return {"jsonrpc": "2.0", "id": request_id, "result": {"entries": entries}}
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"message": str(exc), "code": -32000}}

    return {"jsonrpc": "2.0", "id": request_id, "error": {"message": f"Unsupported method: {method}", "code": -32601}}


class MCPRequestHandler(BaseHTTPRequestHandler):
    """Minimal HTTP JSON-RPC-style MCP server for local AI clients."""

    def do_GET(self):
        if self.path == "/health":
            payload = {"status": "ok", "server": "jarvis-mcp"}
            self._send_json(payload)
            return
        self._send_json({"error": "not found"}, status=404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length)
        try:
            payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except json.JSONDecodeError:
            self._send_json({"error": "invalid JSON body"}, status=400)
            return

        response = handle_mcp_message(payload)
        self._send_json(response)

    def _send_json(self, payload: dict, status: int = 200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any):
        return


def run_mcp_server(host: str = "127.0.0.1", port: int = 8765):
    """Run the local JSON-RPC MCP server in the background."""
    server = ThreadingHTTPServer((host, int(port)), MCPRequestHandler)
    print(f"JARVIS MCP server listening on http://{host}:{port}/mcp")
    server.serve_forever()


if __name__ == "__main__":
    run_mcp_server()
