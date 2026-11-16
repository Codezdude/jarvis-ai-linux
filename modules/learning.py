import os
import sys
from modules.memory import log_long_term_memory
from modules.tools import inspect_recent_logs
from typing import Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def record_interaction(query: str, response: str) -> str:
    """Persist a user‑assistant interaction into long‑term memory.
    Stored under the "interaction" category for later retrieval or analysis.
    """
    try:
        content = f"Q: {query}\nA: {response}"
        log_long_term_memory(category="interaction", content=content)
        return "Interaction recorded successfully."
    except Exception as e:
        return f"Failed to record interaction: {e}"


def summarize_recent_logs(max_lines: int = 100) -> str:
    """Return the most recent log excerpt.  This can be fed to the LLM
    for summarisation or learning purposes.
    """
    try:
        logs = inspect_recent_logs(max_lines=max_lines)
        return logs
    except Exception as e:
        return f"Error retrieving logs: {e}"


def auto_learn_from_logs(max_lines: int = 200) -> str:
    """A placeholder for an automatic learning step.  It collects recent logs
    and stores them as a memory entry.  Future implementations could feed the
    logs to a fine‑tuning pipeline.
    """
    logs = summarize_recent_logs(max_lines=max_lines)
    if logs.startswith("Error"):
        return logs
    try:
        log_long_term_memory(category="learning", content=logs)
        return "Recent logs stored for learning."
    except Exception as e:
        return f"Failed to store logs for learning: {e}"


def apply_safe_modification(target_file: str, new_content: str, reason: str) -> dict:
    """
    Wrap the backup -> compile() -> test -> rollback pipeline for protected-file writes.
    Returns {"success": bool, "backup_path": str, "error": Optional[str]}.
    Never calls safe_write_file internally (avoids infinite recursion).
    """
    from modules.self_improvement import (
        validate_code_syntax,
        create_backup,
        rollback_file,
        run_project_tests,
        reload_jarvis_module,
    )
    from modules.logger import log_error, log_info

    is_python = target_file.endswith(".py")
    backup_path = ""

    try:
        # Step 1: Pre-validation of syntax before touching disk
        if is_python:
            valid, syntax_msg = validate_code_syntax(code_string=new_content)
            if not valid:
                return {"success": False, "backup_path": "", "error": syntax_msg}

        # Step 2: Backup existing file
        if os.path.exists(target_file):
            backup_path = create_backup(target_file)

        # Step 3: Apply the file modification
        os.makedirs(os.path.dirname(target_file), exist_ok=True)
        with open(target_file, "w", encoding="utf-8") as f:
            f.write(new_content)

        # Step 4: Verification via test suite
        if is_python:
            # Check file compilation on disk
            valid_disk, disk_err = validate_code_syntax(filepath=target_file)
            if not valid_disk:
                if backup_path:
                    rollback_file(target_file, backup_path)
                return {
                    "success": False,
                    "backup_path": backup_path,
                    "error": f"Disk compilation error: {disk_err}",
                }

            passed, test_output = run_project_tests()
            if not passed:
                if backup_path:
                    rollback_file(target_file, backup_path)
                return {
                    "success": False,
                    "backup_path": backup_path,
                    "error": f"Tests failed: {test_output[:600]}",
                }

        # Step 5: Post-success dynamic reloading if module was already loaded
        rel_path = os.path.relpath(target_file, PROJECT_ROOT)
        if rel_path.endswith(".py"):
            mod_candidate = rel_path[:-3].replace(os.sep, ".").replace("/", ".")
            if mod_candidate in sys.modules:
                reload_jarvis_module(mod_candidate)

        log_info(f"Safe modification applied to {target_file}")
        return {"success": True, "backup_path": backup_path, "error": None}

    except Exception as exc:
        log_error(f"Safe modification failed for {target_file}: {exc}")
        if backup_path:
            try:
                rollback_file(target_file, backup_path)
            except Exception:
                pass
        return {"success": False, "backup_path": backup_path, "error": str(exc)}
