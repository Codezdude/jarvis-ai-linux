"""Model Context Protocol (MCP) client bridge.

Graceful degradation: if the ``mcp`` package is not installed, the config file is
missing, or a server fails to start, all functions log a warning and continue
without raising. A single background asyncio daemon thread owns all connections so
synchronous HUD/scheduler callers never block or leak subprocesses.
"""

import asyncio
import json
import os
import threading
from typing import Any, Optional

DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "mcp_servers.json"
)

_TOOL_SCHEMAS: list[dict] = []
_SERVER_ENABLED: dict[str, bool] = {}
_LOCK = threading.Lock()


def _load_config(config_path: str = DEFAULT_CONFIG_PATH) -> dict:
    try:
        with open(config_path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def list_mcp_server_configs(config_path: str = DEFAULT_CONFIG_PATH) -> dict:
    """Return ``{server_name: enabled_bool}`` for all configured servers."""
    cfg = _load_config(config_path)
    servers = cfg.get("mcpServers", {}) if isinstance(cfg, dict) else {}
    return {name: bool(entry.get("enabled", True)) for name, entry in servers.items()}


def set_mcp_server_enabled(server_name: str, enabled: bool, config_path: str = DEFAULT_CONFIG_PATH) -> None:
    """Flip ``enabled`` for a server in the config file, then re-hydrate."""
    cfg = _load_config(config_path)
    servers = cfg.setdefault("mcpServers", {})
    if server_name in servers:
        servers[server_name]["enabled"] = bool(enabled)
        try:
            os.makedirs(os.path.dirname(os.path.abspath(config_path)), exist_ok=True)
            with open(config_path, "w", encoding="utf-8") as fh:
                json.dump(cfg, fh, indent=2)
        except Exception:
            pass
    hydrate_mcp_servers(config_path=config_path)


class _MCPManager:
    """Owns a background asyncio loop and manages MCP stdio/http sessions."""

    def __init__(self):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._sessions: dict[str, Any] = {}
        self._schemas: list[dict] = []
        self._lock = threading.Lock()

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None or not self._loop.is_running():
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(target=self._loop.run_forever, daemon=True, name="MCPLoop")
            self._thread.start()
        return self._loop

    def run(self, coro, timeout_sec: int = 15):
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=timeout_sec)

    def shutdown(self):
        loop = self._loop
        if loop and loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(self._close_all(), loop).result(timeout=5)
            except Exception:
                pass
            loop.call_soon_threadsafe(loop.stop)

    async def _close_all(self):
        for name, sess in list(self._sessions.items()):
            try:
                await sess.__aexit__(None, None, None)
            except Exception:
                pass
        self._sessions.clear()


_MANAGER = _MCPManager()


def _schema_from_tool(server_name: str, tool: Any) -> dict:
    name = getattr(tool, "name", "tool")
    description = getattr(tool, "description", "") or name
    input_schema = getattr(tool, "inputSchema", None) or {"type": "object", "properties": {}}
    return {
        "type": "function",
        "function": {
            "name": f"mcp__{server_name}__{name}",
            "description": f"[MCP:{server_name}] {description}",
            "parameters": input_schema,
        },
    }


def hydrate_mcp_servers(config_path: str = DEFAULT_CONFIG_PATH, timeout_sec: int = 15) -> list[dict]:
    """Connect to enabled MCP servers and cache their tool schemas.

    Returns the list of OpenAI-compatible tool schemas. Degrades gracefully if
    ``mcp`` is unavailable or a server fails.
    """
    schemas: list[dict] = []
    try:
        import mcp  # noqa: F401
    except Exception:
        with _LOCK:
            _TOOL_SCHEMAS.clear()
        return schemas

    cfg = _load_config(config_path)
    servers = cfg.get("mcpServers", {}) if isinstance(cfg, dict) else {}
    enabled = {name: entry for name, entry in servers.items() if entry.get("enabled", True)}

    async def _connect():
        results = []
        for name, entry in enabled.items():
            try:
                transport = entry.get("transport", "stdio")
                if transport == "stdio" or entry.get("command"):
                    from mcp import StdioServerParameters
                    from mcp.client.stdio import stdio_client

                    params = StdioServerParameters(
                        command=entry["command"],
                        args=entry.get("args", []),
                        env={**os.environ, **entry.get("env", {})} if entry.get("env") else None,
                    )
                    read, write = await stdio_client(params).__aenter__()
                    session = await __import__("mcp").ClientSession(read, write).__aenter__()
                    await session.initialize()
                elif entry.get("url"):
                    from mcp.client.streamable_http import streamablehttp_client

                    read, write, _ = await streamablehttp_client(entry["url"]).__aenter__()
                    session = await __import__("mcp").ClientSession(read, write).__aenter__()
                    await session.initialize()
                else:
                    continue
                _MANAGER._sessions[name] = session
                resp = await session.list_tools()
                for tool in resp.tools:
                    results.append(_schema_from_tool(name, tool))
            except Exception as e:
                print(f"[MCP] Failed to connect server '{name}': {e}")
                continue
        return results

    try:
        schemas = _MANAGER.run(_connect(), timeout_sec=timeout_sec)
    except Exception as e:
        print(f"[MCP] hydrate error: {e}")
        schemas = []

    with _LOCK:
        _TOOL_SCHEMAS.clear()
        _TOOL_SCHEMAS.extend(schemas)
    return schemas


def get_mcp_tool_schemas() -> list[dict]:
    with _LOCK:
        return list(_TOOL_SCHEMAS)


def is_mcp_tool(tool_name: str) -> bool:
    return str(tool_name).startswith("mcp__")


def call_mcp_tool(tool_name: str, arguments: dict | None = None, timeout_sec: int = 15) -> str:
    """Execute an MCP tool by namespaced name. Returns a text result string."""
    if not is_mcp_tool(tool_name):
        return f"Error: '{tool_name}' is not an MCP tool."
    parts = tool_name.split("__")
    if len(parts) < 3:
        return f"Error: malformed MCP tool name '{tool_name}'."
    server_name = parts[1]
    remote_name = "__".join(parts[2:])

    session = _MANAGER._sessions.get(server_name)
    if session is None:
        return f"Error: MCP server '{server_name}' is not connected."

    async def _call():
        result = await session.call_tool(remote_name, arguments or {})
        chunks = []
        for block in getattr(result, "content", []) or []:
            text = getattr(block, "text", None)
            if text is not None:
                chunks.append(text)
        return "\n".join(chunks) if chunks else str(result)

    try:
        return _MANAGER.run(_call(), timeout_sec=timeout_sec)
    except Exception as e:
        return f"Error: MCP tool '{tool_name}' failed: {e}"


def shutdown_mcp_servers() -> None:
    _MANAGER.shutdown()


try:
    import atexit

    atexit.register(shutdown_mcp_servers)
except Exception:
    pass