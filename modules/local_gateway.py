import json
import logging
import os
import socket
import threading
import time
from typing import Any

try:
    from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
    from fastapi.responses import JSONResponse
except ImportError:  # pragma: no cover - optional dependency
    FastAPI = None
    Request = None
    WebSocket = None
    WebSocketDisconnect = Exception
    JSONResponse = None

from config.settings import BASE_DIR
from modules.mcp_server import handle_mcp_message, list_mcp_tools
from modules.memory_sync import get_shared_memory_context
from modules.safe_storage import project_safe_path, read_text_file, atomic_write_text
from modules.workspace_growth import (
    append_workspace_growth_log,
    ensure_experiment_directory,
    search_project_memory_index,
    search_workspace_growth_log,
)

_GATEWAY_LOG_PATH = os.path.join(BASE_DIR, "logs", "jarvis_gateway.log")
_GATEWAY_REQUESTS = {}
_GATEWAY_REQUESTS_LOCK = threading.Lock()


def _get_gateway_token() -> str:
    return str(os.getenv("JARVIS_API_TOKEN", "jarvis-local-dev")).strip()


def _get_rate_limit_config() -> tuple[int, int]:
    try:
        limit = max(1, int(os.getenv("JARVIS_RATE_LIMIT", "120")))
    except ValueError:
        limit = 120
    try:
        window_seconds = max(1, int(os.getenv("JARVIS_RATE_WINDOW_SECONDS", "60")))
    except ValueError:
        window_seconds = 60
    return limit, window_seconds


def _check_rate_limit(client_ip: str) -> bool:
    limit, window_seconds = _get_rate_limit_config()
    now = time.time()
    with _GATEWAY_REQUESTS_LOCK:
        bucket = _GATEWAY_REQUESTS.setdefault(client_ip, [])
        bucket[:] = [timestamp for timestamp in bucket if now - timestamp < window_seconds]
        if len(bucket) >= limit:
            return False
        bucket.append(now)
    return True


def _ensure_gateway_logger() -> logging.Logger:
    logger = logging.getLogger("jarvis.gateway")
    if logger.handlers:
        return logger
    os.makedirs(os.path.dirname(_GATEWAY_LOG_PATH), exist_ok=True)
    handler = logging.FileHandler(_GATEWAY_LOG_PATH, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def _gateway_auth_error() -> tuple[dict, int]:
    return {"error": "Unauthorized: valid Authorization: Bearer <token> is required."}, 401


def _gateway_rate_limit_error() -> tuple[dict, int]:
    return {"error": "Rate limit exceeded: too many requests from this client."}, 429


async def _apply_gateway_security(request, call_next):
    logger = _ensure_gateway_logger()
    client_ip = request.client.host if request.client else "unknown"
    token = _get_gateway_token()
    auth_header = request.headers.get("authorization", "")
    if token:
        parts = auth_header.split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "bearer" or parts[1] != token:
            logger.warning("Rejected unauthorized request from %s for %s", client_ip, request.url.path)
            return JSONResponse(*_gateway_auth_error())

    if not _check_rate_limit(client_ip):
        logger.warning("Rate limit triggered for %s on %s", client_ip, request.url.path)
        return JSONResponse(*_gateway_rate_limit_error())

    logger.info("%s %s from %s", request.method, request.url.path, client_ip)
    return await call_next(request)


class SimpleGatewayApp:
    def __init__(self):
        self.routes = []
        self._register_route("/health", "GET", lambda: {"status": "ok", "project_root": BASE_DIR})
        self._register_route("/tools", "GET", lambda: {"tools": list_mcp_tools()})
        self._register_route("/memory", "GET", lambda: {"context": get_shared_memory_context()})

    def _register_route(self, path: str, method: str, handler):
        self.routes.append(type("Route", (), {"path": path, "method": method, "handler": handler})())

    def route(self, path: str, methods=None):
        def decorator(func):
            self._register_route(path, ",".join(methods or ["GET"]), func)
            return func
        return decorator


def create_app():
    """Create the local FastAPI app if available, otherwise a minimal fallback app with equivalent routes."""
    if FastAPI is not None:
        app = FastAPI(title="JARVIS Local Gateway", version="0.1.0")

        @app.middleware("http")
        async def gateway_security_middleware(request: Request, call_next):
            return await _apply_gateway_security(request, call_next)

        @app.get("/health")
        def health_check():
            return {"status": "ok", "project_root": BASE_DIR}

        @app.get("/tools")
        def tools_endpoint():
            return {"tools": list_mcp_tools()}

        @app.post("/execute")
        def execute_tool(payload: dict):
            tool_name = payload.get("tool") or payload.get("name")
            args = payload.get("args", payload.get("arguments", {}))
            if not tool_name:
                return {"error": "tool name is required"}
            try:
                from modules.mcp_server import call_mcp_tool
                result = call_mcp_tool(tool_name, args)
                return {"tool": tool_name, "result": result}
            except Exception as exc:
                return {"error": str(exc)}

        @app.post("/workspace/write")
        def write_file(payload: dict):
            path = payload.get("path") or payload.get("filepath")
            content = payload.get("content", "")
            confirmed = payload.get("confirmed", True)
            try:
                from modules.tools import write_local_file
                result = write_local_file(path, content, confirmed=confirmed)
                return {"path": path, "result": result}
            except Exception as exc:
                return {"error": str(exc)}

        @app.get("/workspace/read")
        def read_file(path: str):
            try:
                return {"path": path, "content": read_text_file(path)}
            except Exception as exc:
                return {"error": str(exc)}

        @app.get("/memory")
        def memory_context(query: str | None = None):
            shared_context = get_shared_memory_context()
            semantic_matches = []
            if query:
                from modules.vector_memory import semantic_search
                semantic_matches = semantic_search(query, limit=5)
            return {
                "context": shared_context,
                "semantic_matches": semantic_matches,
                "query": query,
                "project_index": search_project_memory_index(query or "", limit=5),
            }

        @app.get("/workspace/growth")
        def workspace_growth(query: str | None = None):
            return {
                "query": query,
                "memory_index": search_project_memory_index(query or "", limit=10),
                "growth_log": search_workspace_growth_log(query or "", limit=10),
            }

        @app.post("/workspace/experiment")
        def create_experiment(payload: dict):
            name = (payload or {}).get("name") or (payload or {}).get("label") or "experiment"
            path = ensure_experiment_directory(str(name))
            return {"name": name, "path": path}

        @app.post("/workspace/growth/log")
        def log_growth(payload: dict):
            decision = (payload or {}).get("decision") or (payload or {}).get("content")
            context = (payload or {}).get("context") or "workspace-growth"
            if not decision:
                return {"error": "decision is required"}
            return {"status": append_workspace_growth_log(str(decision), str(context))}

        @app.websocket("/ws")
        async def gateway_ws(websocket: WebSocket):
            token = _get_gateway_token()
            auth_header = websocket.headers.get("authorization", "")
            query_token = websocket.query_params.get("token")
            provided = None
            if auth_header:
                parts = auth_header.split(None, 1)
                if len(parts) == 2 and parts[0].lower() == "bearer":
                    provided = parts[1]
            elif query_token:
                provided = query_token
            if token and provided != token:
                await websocket.close(code=1008, reason="Unauthorized")
                return
            await websocket.accept()
            try:
                while True:
                    raw = await websocket.receive_text()
                    payload = json.loads(raw)
                    await websocket.send_text(json.dumps(handle_mcp_message(payload), ensure_ascii=False))
            except WebSocketDisconnect:
                pass

        return app

    app = SimpleGatewayApp()
    return app


def resolve_gateway_port(preferred_port: int | str | None = None) -> int:
    """Resolve a free local port, falling back to a nearby port if the preferred choice is occupied."""
    requested = int(os.getenv("JARVIS_PORT", preferred_port if preferred_port is not None else 8001))
    start_port = max(1, requested)

    for candidate in range(start_port, start_port + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", candidate))
            except OSError:
                continue
            return candidate

    raise RuntimeError(f"No free local gateway port found near {start_port}.")


def run_local_api_gateway(host: str = "127.0.0.1", port: int = 8001):
    """Start the local JARVIS gateway daemon. Uses FastAPI/Uvicorn when available; otherwise raises a clear install message."""
    if FastAPI is None:
        raise RuntimeError("FastAPI is not installed. Install it with: pip install fastapi uvicorn")

    import uvicorn

    resolved_port = resolve_gateway_port(port)
    if resolved_port != port:
        print(f"Port {port} is busy; starting JARVIS gateway on port {resolved_port} instead.")
    uvicorn.run(create_app(), host=host, port=resolved_port)


if __name__ == "__main__":
    run_local_api_gateway()
