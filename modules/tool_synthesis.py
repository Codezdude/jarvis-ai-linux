import ast
import datetime
import importlib.util
import io
import json
import os
import re
import sqlite3
import threading
import unittest
from collections.abc import Callable
from typing import Any

from modules.approval import check_approval
from modules.audit import log_audit_event
from modules.budget import check_budget, record_budget_usage
from modules.tools import (
    DYNAMIC_TOOL_REGISTRY,
    PROJECT_ROOT,
    register_dynamic_tool,
    safe_write_file,
)

DEFAULT_DB_PATH = os.path.join(PROJECT_ROOT, "data", "jarvis_memory.db")
DYNAMIC_TOOLS_MODULE_PATH = os.path.join(PROJECT_ROOT, "modules", "dynamic_tools.py")
TESTS_DIR = os.path.join(PROJECT_ROOT, "tests")

_SYNTHESIS_LOCK = threading.Lock()

FORBIDDEN_CALL_NAMES = {"eval", "exec", "compile", "__import__"}
FORBIDDEN_MODULE_ATTRS = {
    ("os", "system"),
    ("os", "popen"),
    ("os", "remove"),
    ("os", "unlink"),
    ("shutil", "rmtree"),
    ("subprocess", "run"),
    ("subprocess", "Popen"),
    ("subprocess", "call"),
}


def _get_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_dynamic_tools_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """Creates the dynamic_tools table if it does not exist."""
    with _SYNTHESIS_LOCK:
        with _get_connection(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dynamic_tools (
                    tool_name TEXT PRIMARY KEY,
                    description TEXT NOT NULL,
                    parameters_schema_json TEXT NOT NULL,
                    module_path TEXT NOT NULL,
                    test_path TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active', 'disabled', 'quarantined')),
                    created_by_goal_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.commit()


def _validate_tool_name(tool_name: str) -> None:
    if not re.match(r"^[a-z_][a-z0-9_]{2,48}$", tool_name):
        raise ValueError(
            f"Invalid synthesized tool_name {tool_name!r}: must be 3-49 lowercase snake_case characters."
        )


def _validate_ast_and_compile(tool_name: str, function_code: str, test_code: str) -> None:
    """Validates syntax via compile() and inspects the AST.

    Guarantees both function_code and test_code compile cleanly, a top-level
    function matching `tool_name` is defined, and no forbidden shell/eval
    execution primitives appear in the synthesized tool.
    """
    compile(function_code, f"<synthesized:{tool_name}>", "exec")
    compile(test_code, f"<synthesized_test:{tool_name}>", "exec")

    tree = ast.parse(function_code)
    _check_ast_forbidden(tree, tool_name)


def _check_ast_forbidden(tree: ast.AST, tool_name: str) -> None:
    """Walks the AST to enforce safety rules on synthesized tool code."""
    defined_funcs = [
        node.name for node in tree.body if isinstance(node, ast.FunctionDef)
    ]
    if tool_name not in defined_funcs:
        raise ValueError(f"Synthesized code must define a top-level function named '{tool_name}'.")

    for node in ast.walk(tree):
        _check_ast_node(node)


def _check_ast_node(node: ast.AST) -> None:
    """Checks a single AST node for forbidden constructs."""
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name.split(".")[0] in ("subprocess", "pty", "ctypes"):
                raise ValueError(f"Forbidden import in synthesized tool: {alias.name}")
    elif isinstance(node, ast.ImportFrom):
        if node.module and node.module.split(".")[0] in ("subprocess", "pty", "ctypes"):
            raise ValueError(f"Forbidden import-from in synthesized tool: {node.module}")
    elif isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALL_NAMES:
            raise ValueError(f"Forbidden builtin call in synthesized tool: {node.func.id}")
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            pair = (node.func.value.id, node.func.attr)
            if pair in FORBIDDEN_MODULE_ATTRS:
                raise ValueError(f"Forbidden module call in synthesized tool: {pair[0]}.{pair[1]}")


def _draft_tool_with_ollama(
    capability_description: str,
    tool_name: str,
    goal_id: str | None = None,
    db_path: str = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Uses local Ollama exclusively to draft the Python tool, schema, and unittest."""
    from modules.ai import ask_local_brain

    prompt = (
        "You are J.A.R.V.I.S.'s Tool Synthesizer. Generate a self-contained Python tool function "
        f"named `{tool_name}` and a `unittest.TestCase` class testing it.\n"
        "Respond strictly as JSON with keys:\n"
        "- 'description': short tool summary\n"
        "- 'parameters_schema': JSON Schema object for function arguments\n"
        "- 'function_code': valid Python defining `def " + tool_name + "(...)`\n"
        "- 'test_code': valid Python `unittest` importing `from modules.dynamic_tools import "
        + tool_name
        + "` and verifying normal + edge cases.\n\n"
        f"Requested Capability: {capability_description}"
    )

    raw = ask_local_brain(prompt)
    est_tokens = max(1, (len(prompt) + len(str(raw))) // 4)
    record_budget_usage(
        "tokens",
        actor="tool_synthesis",
        goal_id=goal_id,
        amount=est_tokens,
        db_path=db_path,
    )

    match = re.search(r"\{.*\}", str(raw), flags=re.DOTALL)
    if not match:
        raise ValueError("Local Ollama failed to return valid JSON for tool synthesis.")
    return json.loads(match.group(0))


def _run_isolated_test_file(test_file_path: str) -> dict[str, Any]:
    """Executes unittest against the newly synthesized test file and captures results."""
    test_dir = os.path.dirname(os.path.abspath(test_file_path))
    test_pattern = os.path.basename(test_file_path)

    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=test_dir, pattern=test_pattern)
    stream = io.StringIO()
    runner = unittest.TextTestRunner(stream=stream, verbosity=2)
    result = runner.run(suite)

    return {
        "passed": result.wasSuccessful() and result.testsRun >= 1,
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "output": stream.getvalue(),
    }


def _rollback_synthesis_files(
    module_path: str,
    had_prior_module: bool,
    prior_module_content: str,
    test_path: str,
    had_prior_test: bool,
    prior_test_content: str,
) -> None:
    """Restores dynamic_tools.py and cleans up the test file on synthesis failure."""
    if had_prior_module:
        with open(module_path, "w", encoding="utf-8") as f:
            f.write(prior_module_content)
    elif os.path.exists(module_path):
        os.remove(module_path)

    if had_prior_test:
        with open(test_path, "w", encoding="utf-8") as f:
            f.write(prior_test_content)
    elif os.path.exists(test_path):
        os.remove(test_path)


def synthesize_and_register_tool(
    tool_name: str,
    capability_description: str,
    actor: str = "autonomous_worker",
    source: str = "self",
    goal_id: str | None = None,
    generator_fn: Callable[[str, str], dict[str, Any]] | None = None,
    signed_token: str | None = None,
    module_path: str = DYNAMIC_TOOLS_MODULE_PATH,
    tests_dir: str = TESTS_DIR,
    db_path: str = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """End-to-end Tool Synthesis pipeline: budget and approval gates, draft via Ollama, compile/AST validation, protected write, isolated test run, and registration with rollback on failure."""
    init_dynamic_tools_db(db_path)
    _validate_tool_name(tool_name)

    if tool_name in DYNAMIC_TOOL_REGISTRY:
        return {
            "success": True,
            "already_registered": True,
            "tool_name": tool_name,
        }

    # 1. Check budget and approval gates before touching codebase
    budget_res = check_budget(
        actor=actor, goal_id=goal_id, requires_self_mod=True, db_path=db_path
    )
    if not budget_res["allowed"]:
        event_id = log_audit_event(
            actor=actor,
            action="tool_synthesis_blocked",
            outcome="blocked",
            metadata={"tool_name": tool_name, "reason": budget_res["reason"]},
        )
        return {"success": False, "reason": budget_res["reason"], "audit_event_id": event_id}

    gate_res = check_approval(
        action="self_modification",
        target=module_path,
        actor=actor,
        source=source,
        reason=f"Synthesize new tool '{tool_name}': {capability_description}",
        is_protected_file=True,
        signed_token=signed_token,
        db_path=db_path,
    )
    if not gate_res["approved"]:
        return {
            "success": False,
            "blocked_by_approval_gate": True,
            "reason": gate_res["method"],
            "audit_event_id": gate_res["audit_event_id"],
        }

    test_path = os.path.join(tests_dir, f"test_synthesized_{tool_name}.py")
    had_prior_module = os.path.exists(module_path)
    prior_module_content = ""
    if had_prior_module:
        with open(module_path, encoding="utf-8") as f:
            prior_module_content = f.read()

    had_prior_test = os.path.exists(test_path)
    prior_test_content = ""
    if had_prior_test:
        with open(test_path, encoding="utf-8") as f:
            prior_test_content = f.read()

    with _SYNTHESIS_LOCK:
        try:
            return _execute_synthesis_pipeline(
                tool_name=tool_name,
                capability_description=capability_description,
                actor=actor,
                source=source,
                goal_id=goal_id,
                generator_fn=generator_fn,
                module_path=module_path,
                test_path=test_path,
                db_path=db_path,
                signed_token=signed_token,
                had_prior_module=had_prior_module,
                prior_module_content=prior_module_content,
                had_prior_test=had_prior_test,
                prior_test_content=prior_test_content,
            )
        except Exception as exc:
            _rollback_synthesis_files(
                module_path=module_path,
                had_prior_module=had_prior_module,
                prior_module_content=prior_module_content,
                test_path=test_path,
                had_prior_test=had_prior_test,
                prior_test_content=prior_test_content,
            )
            record_budget_usage(
                "failure", actor=actor, goal_id=goal_id, amount=1, db_path=db_path
            )
            event_id = log_audit_event(
                actor=actor,
                action="tool_synthesis_failed",
                outcome="rolled_back",
                paths_touched=[
                    os.path.relpath(module_path, PROJECT_ROOT),
                    os.path.relpath(test_path, PROJECT_ROOT),
                ],
                metadata={
                    "tool_name": tool_name,
                    "goal_id": goal_id,
                    "error": str(exc),
                },
            )
            return {
                "success": False,
                "rolled_back": True,
                "tool_name": tool_name,
                "error": str(exc),
                "audit_event_id": event_id,
            }


def _execute_synthesis_pipeline(
    tool_name: str,
    capability_description: str,
    actor: str,
    source: str,
    goal_id: str | None,
    generator_fn: Any,
    module_path: str,
    test_path: str,
    db_path: str,
    signed_token: str | None,
    had_prior_module: bool,
    prior_module_content: str,
    had_prior_test: bool,
    prior_test_content: str,
) -> dict[str, Any]:
    """Drafts, validates, writes, tests, and registers a synthesized tool."""
    # 2. Draft code & test
    draft = (
        generator_fn(capability_description, tool_name)
        if generator_fn is not None
        else _draft_tool_with_ollama(
            capability_description, tool_name, goal_id=goal_id, db_path=db_path
        )
    )
    function_code = str(draft.get("function_code", "")).strip()
    test_code = str(draft.get("test_code", "")).strip()
    description = str(draft.get("description", capability_description)).strip()
    parameters_schema = draft.get(
        "parameters_schema", {"type": "object", "properties": {}}
    )

    # 3. Validate syntax with compile() & AST inspection
    _validate_ast_and_compile(tool_name, function_code, test_code)

    # 4. Write protected files via safe_write_file (routes through learning.py)
    base_header = (
        prior_module_content
        if had_prior_module
        else '"""Auto-synthesized dynamic tools managed by J.A.R.V.I.S."""\n'
    )
    updated_module_content = (
        f"{base_header.rstrip()}\n\n\n"
        f"# Synthesized tool: {tool_name}\n"
        f"{function_code}\n"
    )

    mod_write = safe_write_file(
        filepath=module_path,
        content=updated_module_content,
        actor=actor,
        source=source,
        reason=f"Synthesize tool function '{tool_name}'",
        signed_token=signed_token,
        db_path=db_path,
    )
    if not mod_write.get("success"):
        raise RuntimeError(f"Protected write failed for {module_path}: {mod_write}")

    test_write = safe_write_file(
        filepath=test_path,
        content=test_code + "\n",
        actor=actor,
        source=source,
        reason=f"Add unit test for synthesized tool '{tool_name}'",
        signed_token=signed_token,
        db_path=db_path,
    )
    if not test_write.get("success"):
        raise RuntimeError(f"Protected write failed for {test_path}: {test_write}")

    # 5. Execute the synthesized unit test suite
    test_res = _run_isolated_test_file(test_path)
    if not test_res["passed"]:
        raise RuntimeError(
            f"Synthesized unit tests failed (runs={test_res['tests_run']}, "
            f"failures={test_res['failures']}, errors={test_res['errors']}):\n"
            f"{test_res['output'][-600:]}"
        )

    # 6. Hot-load callable and register in SQLite + DYNAMIC_TOOL_REGISTRY
    spec = importlib.util.spec_from_file_location("modules.dynamic_tools", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module spec from {module_path}")
    dyn_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dyn_mod)

    tool_callable = getattr(dyn_mod, tool_name, None)
    if not callable(tool_callable):
        raise RuntimeError(f"Function '{tool_name}' not found after loading {module_path}")

    register_dynamic_tool(
        tool_name=tool_name,
        func=tool_callable,
        description=description,
        parameters_schema=parameters_schema,
    )

    now_iso = datetime.datetime.now(datetime.UTC).isoformat()
    with _get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO dynamic_tools
            (tool_name, description, parameters_schema_json, module_path, test_path, status, created_by_goal_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)
            ON CONFLICT(tool_name) DO UPDATE SET
                description = excluded.description,
                parameters_schema_json = excluded.parameters_schema_json,
                status = 'active',
                updated_at = excluded.updated_at
            """,
            (
                tool_name,
                description,
                json.dumps(parameters_schema, ensure_ascii=False),
                os.path.relpath(module_path, PROJECT_ROOT),
                os.path.relpath(test_path, PROJECT_ROOT),
                goal_id,
                now_iso,
                now_iso,
            ),
        )
        conn.commit()

    record_budget_usage(
        "self_mod", actor=actor, goal_id=goal_id, amount=1, db_path=db_path
    )

    event_id = log_audit_event(
        actor=actor,
        action="tool_synthesized",
        outcome="success",
        paths_touched=[
            os.path.relpath(module_path, PROJECT_ROOT),
            os.path.relpath(test_path, PROJECT_ROOT),
        ],
        metadata={
            "tool_name": tool_name,
            "goal_id": goal_id,
            "tests_run": test_res["tests_run"],
        },
    )

    return {
        "success": True,
        "tool_name": tool_name,
        "tests_run": test_res["tests_run"],
        "audit_event_id": event_id,
    }
