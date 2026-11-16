import ast
import datetime
import importlib
import json
import os
import py_compile
import shutil
import subprocess
import sys
from config.settings import SAFE_MODE
from modules.logger import log_error, log_info

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP_DIR = os.path.join(PROJECT_ROOT, ".backups")
LOG_PATH = os.path.join(PROJECT_ROOT, "logs", "jarvis_system.log")


def _resolve_project_path(filepath: str) -> str:
    """Return a project-scoped absolute path and prevent escaping the workspace."""
    if not filepath or not filepath.strip():
        raise ValueError("A file path is required.")

    candidate = os.path.normpath(
        filepath if os.path.isabs(filepath) else os.path.join(PROJECT_ROOT, filepath)
    )

    if os.path.commonpath([PROJECT_ROOT, candidate]) != PROJECT_ROOT:
        raise ValueError("Access denied: path escapes the project directory.")

    return candidate


def create_backup(filepath: str) -> str:
    """Create a timestamped backup of the specified file inside .backups/."""
    safe_path = _resolve_project_path(filepath)
    if not os.path.exists(safe_path):
        return f"File {filepath} does not exist yet; no backup needed."

    os.makedirs(BACKUP_DIR, exist_ok=True)
    rel_path = os.path.relpath(safe_path, PROJECT_ROOT)
    sanitized_name = rel_path.replace(os.sep, "__").replace("/", "__")
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"{timestamp}_{sanitized_name}"
    backup_path = os.path.join(BACKUP_DIR, backup_filename)

    shutil.copy2(safe_path, backup_path)

    # Save latest backup reference pointer
    pointer_file = os.path.join(BACKUP_DIR, f"latest__{sanitized_name}.txt")
    with open(pointer_file, "w", encoding="utf-8") as f:
        f.write(backup_path)

    log_info(f"Created safety backup: {backup_path}")
    return backup_path


def rollback_file(filepath: str, backup_path: str = "") -> str:
    """Restore a file from a specified backup or the most recent automatic backup."""
    safe_path = _resolve_project_path(filepath)
    rel_path = os.path.relpath(safe_path, PROJECT_ROOT)
    sanitized_name = rel_path.replace(os.sep, "__").replace("/", "__")

    target_backup = backup_path
    if not target_backup:
        pointer_file = os.path.join(BACKUP_DIR, f"latest__{sanitized_name}.txt")
        if os.path.exists(pointer_file):
            with open(pointer_file, "r", encoding="utf-8") as f:
                target_backup = f.read().strip()

    if not target_backup or not os.path.exists(target_backup):
        # Fallback search for latest file matching sanitized pattern
        if os.path.exists(BACKUP_DIR):
            candidates = sorted(
                [
                    os.path.join(BACKUP_DIR, f)
                    for f in os.listdir(BACKUP_DIR)
                    if f.endswith(f"__{sanitized_name}")
                ],
                reverse=True,
            )
            if candidates:
                target_backup = candidates[0]

    if not target_backup or not os.path.exists(target_backup):
        return f"Error: No backup found to restore for {filepath}."

    os.makedirs(os.path.dirname(safe_path), exist_ok=True)
    shutil.copy2(target_backup, safe_path)
    log_info(f"Restored {safe_path} from backup {target_backup}")
    return f"Successfully rolled back {filepath} from {os.path.basename(target_backup)}."


def validate_code_syntax(code_string: str = "", filepath: str = "") -> tuple[bool, str]:
    """Validate Python syntax either from an in-memory string or an on-disk file."""
    if code_string:
        try:
            ast.parse(code_string)
            return True, "Syntax is valid."
        except SyntaxError as e:
            return False, f"SyntaxError at line {e.lineno}, offset {e.offset}: {e.msg}"
        except Exception as e:
            return False, f"Validation error: {e}"

    if filepath:
        try:
            safe_path = _resolve_project_path(filepath)
            if not os.path.exists(safe_path):
                return False, f"File not found: {filepath}"
            py_compile.compile(safe_path, doraise=True)
            return True, "Syntax is valid."
        except py_compile.PyCompileError as e:
            return False, f"Compilation error in {filepath}: {e}"
        except Exception as e:
            return False, f"Validation error in {filepath}: {e}"

    return False, "Either code_string or filepath must be provided."


def run_project_tests(test_target: str = "tests", timeout: int = 60) -> tuple[bool, str]:
    """Execute the project unittest suite to ensure system stability."""
    try:
        cmd = [sys.executable, "-m", "unittest"]
        if test_target.endswith(".py"):
            cmd.append(test_target)
        else:
            cmd.extend(["discover", test_target])

        result = subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        output = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
        output_cleaned = output.strip()
        passed = result.returncode == 0
        return passed, output_cleaned
    except subprocess.TimeoutExpired:
        return False, f"Test execution timed out after {timeout} seconds."
    except Exception as e:
        return False, f"Failed to run test suite: {e}"


def inspect_recent_logs(max_lines: int = 50) -> str:
    """Inspect recent operational and error logs from jarvis_system.log."""
    if not os.path.exists(LOG_PATH):
        return "Log file does not exist yet."

    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        recent = lines[-max_lines:] if len(lines) > max_lines else lines
        return "".join(recent).strip()
    except Exception as e:
        return f"Error reading system log: {e}"


def reload_jarvis_module(module_name: str) -> str:
    """Dynamically reload a modified Python module into the active runtime."""
    try:
        if module_name in sys.modules:
            reloaded = importlib.reload(sys.modules[module_name])
            log_info(f"Dynamically reloaded module: {module_name}")
            return f"Module {module_name} reloaded successfully."
        else:
            imported = importlib.import_module(module_name)
            return f"Module {module_name} was not loaded; imported fresh."
    except Exception as e:
        log_error(f"Failed to reload module {module_name}: {e}")
        return f"Failed to reload module {module_name}: {e}"


def apply_self_improvement(
    filepath: str,
    new_content: str,
    confirmed: bool = True,
    run_tests: bool = True,
    require_diff_approval: bool = False,
) -> str:
    """
    Safely apply a self-improvement code change:
    1. Pre-validates syntax (if Python).
    2. Backs up existing file.
    3. Writes the update.
    4. Runs test suite.
    5. Automatically reverts on failure.
    6. Reloads module on success.

    When ``require_diff_approval`` is True, the pending change is rendered as a
    unified diff and must be approved through the approval gate before writing.
    """
    if SAFE_MODE and not confirmed:
        return (
            "Action blocked by Safe Mode: apply_self_improvement requires explicit confirmation "
            "before modifying core project files."
        )

    try:
        safe_path = _resolve_project_path(filepath)
    except Exception as e:
        return f"Path error: {e}"

    is_python = filepath.endswith(".py")

    # Pre-write gate: require an approved unified diff before touching disk.
    if require_diff_approval:
        from modules.approval import approve_file_change

        decision = approve_file_change(
            safe_path, new_content, actor="self", reason="self_improvement_diff_review"
        )
        if not decision.get("approved"):
            return "Self-improvement denied by diff approval gate; no changes written." + (
                "\n\nProposed diff:\n" + decision.get("diff", "") if decision.get("diff") else ""
            )

    # Step 1: Pre-validation of syntax before touching disk
    if is_python:
        valid, syntax_msg = validate_code_syntax(code_string=new_content)
        if not valid:
            return f"Self-improvement aborted before writing: {syntax_msg}"

    # Step 2: Backup existing file
    backup_path = ""
    if os.path.exists(safe_path):
        backup_path = create_backup(filepath)

    # Step 3: Apply the file modification
    try:
        os.makedirs(os.path.dirname(safe_path), exist_ok=True)
        with open(safe_path, "w", encoding="utf-8") as f:
            f.write(new_content)
    except Exception as write_err:
        return f"Failed to write file {filepath}: {write_err}"

    # Step 4: Verification via test suite
    if run_tests and is_python:
        # Check file compilation on disk
        valid_disk, disk_err = validate_code_syntax(filepath=filepath)
        if not valid_disk:
            rollback_status = rollback_file(filepath, backup_path) if backup_path else "No backup available"
            return f"Self-improvement aborted and rolled back due to disk compilation error: {disk_err}. {rollback_status}"

        passed, test_output = run_project_tests()
        if not passed:
            rollback_status = rollback_file(filepath, backup_path) if backup_path else "No backup available"
            return (
                f"Self-improvement failed verification tests and was automatically rolled back!\n"
                f"Test failure output:\n{test_output[:600]}\n"
                f"Rollback status: {rollback_status}"
            )

    # Step 5: Post-success dynamic reloading if module was already loaded
    rel_path = os.path.relpath(safe_path, PROJECT_ROOT)
    if rel_path.endswith(".py"):
        mod_candidate = rel_path[:-3].replace(os.sep, ".").replace("/", ".")
        if mod_candidate in sys.modules:
            reload_jarvis_module(mod_candidate)

    log_info(f"Self-improvement successfully committed to {filepath}")
    return f"Successfully applied self-improvement to {filepath}. Tests verified and passed."
