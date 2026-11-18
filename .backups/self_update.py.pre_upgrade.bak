"""Self-update module for JARVIS: Git synchronization, dependency updates, and safe process restarts."""

import glob
import logging
import os
import py_compile
import subprocess
import sys

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def verify_python_syntax() -> tuple[bool, str]:
    """Checks all .py files in the project to prevent broken restarts."""
    py_files = glob.glob(os.path.join(PROJECT_DIR, "**/*.py"), recursive=True)
    for filepath in py_files:
        if "venv" in filepath or "__pycache__" in filepath:
            continue
        try:
            py_compile.compile(filepath, doraise=True)
        except py_compile.PyCompileError as e:
            return False, f"Syntax error in {filepath}: {e}"
    return True, "All Python files passed syntax verification."


def self_update_system(
    pull_git: bool = True,
    update_ollama_models: bool = False,
    restart_after: bool = True,
) -> str:
    """
    Safely backs up local edits, pulls updates from Git, verifies code syntax,
    optionally updates Ollama models, and restarts Jarvis.
    """
    logs = []

    # 1. Verify syntax of current local code first
    ok, msg = verify_python_syntax()
    if not ok:
        return f"Update aborted before sync: {msg}"
    logs.append(msg)

    # 2. Save any local modifications so git pull never conflicts
    if pull_git:
        subprocess.run(["git", "add", "."], cwd=PROJECT_DIR, check=False)
        commit_result = subprocess.run(
            ["git", "commit", "-m", "Pre-update auto-snapshot"],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
        )
        status_note = commit_result.stdout.strip() or "Working tree clean"
        logs.append(f"Snapshot: {status_note}")

        pull = subprocess.run(
            ["git", "pull", "--rebase", "origin", "main"],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
        )
        if pull.returncode != 0:
            subprocess.run(["git", "rebase", "--abort"], cwd=PROJECT_DIR, check=False)
            return f"Git pull failed (rebase aborted safely): {pull.stderr.strip()}"
        logs.append(f"Git sync: {pull.stdout.strip()}")

        # Re-verify syntax after pulling new code
        ok, msg = verify_python_syntax()
        if not ok:
            rollback = subprocess.run(
                ["git", "reset", "--hard", "HEAD@{1}"],
                cwd=PROJECT_DIR,
                capture_output=True,
                text=True,
            )
            logs.append(f"Rollback status: {rollback.stdout.strip() or rollback.stderr.strip()}")
            return f"Pulled code had syntax errors and was rolled back: {msg}"

    # 3. Optionally refresh local Ollama models
    if update_ollama_models:
        for model in ["llama3.2:3b", "qwen2.5:7b"]:
            pull_result = subprocess.run(
                ["ollama", "pull", model],
                capture_output=True,
                text=True,
            )
            logs.append(f"Ollama model {model}: {'updated' if pull_result.returncode == 0 else 'failed'}")

    # 4. Restart Jarvis cleanly in-place
    if restart_after:
        logs.append("Restarting Jarvis now...")
        print("\n".join(logs), flush=True)
        logging.shutdown()
        os.chdir(PROJECT_DIR)
        script_path = os.path.abspath(sys.argv[0])
        os.execv(sys.executable, [sys.executable, script_path] + sys.argv[1:])

    return "\n".join(logs)
