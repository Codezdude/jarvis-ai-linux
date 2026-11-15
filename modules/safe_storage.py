import os
import tempfile
import threading
from contextlib import contextmanager

from config.settings import BASE_DIR

_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def project_safe_path(filepath: str) -> str:
    """Resolve a project-local path and reject attempts to escape the workspace root.

    All model-driven reads and writes are intentionally permitted anywhere under the
    JARVIS project directory, but never outside it.
    """
    if not filepath or not str(filepath).strip():
        raise ValueError("A file path is required.")

    workspace_root = os.path.realpath(BASE_DIR)
    candidate = os.path.realpath(
        os.path.normpath(filepath if os.path.isabs(filepath) else os.path.join(BASE_DIR, filepath))
    )

    if os.path.commonpath([workspace_root, candidate]) != workspace_root:
        raise ValueError("Access denied: path escapes the project directory.")

    return candidate


@contextmanager
def file_lock(path: str):
    """Cross-thread lock guard for atomic transactional reads and writes."""
    safe_path = project_safe_path(path)
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(safe_path, threading.RLock())
    with lock:
        yield


def atomic_write_text(filepath: str, content: str, encoding: str = "utf-8") -> str:
    """Atomically writes text to a project-scoped file using a temp file + replace pattern."""
    safe_path = project_safe_path(filepath)
    directory = os.path.dirname(safe_path) or BASE_DIR
    os.makedirs(directory, exist_ok=True)

    with file_lock(filepath):
        fd, temp_path = tempfile.mkstemp(prefix=".jarvis-write-", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding=encoding) as handle:
                handle.write(content)
            os.replace(temp_path, safe_path)
        except Exception:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise

    return safe_path


def read_text_file(filepath: str, encoding: str = "utf-8") -> str:
    """Safely read text from a project-scoped file under a file lock."""
    safe_path = project_safe_path(filepath)
    with file_lock(filepath):
        with open(safe_path, "r", encoding=encoding, errors="ignore") as handle:
            return handle.read()


def list_project_directory(directory_path: str = ".") -> list[str]:
    """List the visible contents of a project directory in a stable, safe manner."""
    safe_path = project_safe_path(directory_path)
    if not os.path.isdir(safe_path):
        raise FileNotFoundError(f"Directory does not exist: {directory_path}")
    return sorted(os.listdir(safe_path))


def write_and_backup(filepath: str, content: str, encoding: str = "utf-8") -> dict:
    """Write content with a backup snapshot, returning metadata for tooling and auditing."""
    safe_path = project_safe_path(filepath)
    backup_path = None
    if os.path.exists(safe_path):
        backup_path = safe_path + ".bak"
        with open(safe_path, "r", encoding=encoding, errors="ignore") as src:
            payload = src.read()
        with open(backup_path, "w", encoding=encoding) as dst:
            dst.write(payload)

    atomic_write_text(filepath, content, encoding=encoding)
    return {
        "path": safe_path,
        "backup": backup_path,
        "bytes_written": len(content.encode(encoding)),
    }
