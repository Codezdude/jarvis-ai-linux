"""Proactive maintenance triggers: disk/backup guard and daily health digest.

Python stdlib only. Never touches project source files during cleanup and never
deletes the newest ``keep_min_files`` backups.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "trigger_state.json"


def _load_trigger_state(state_path: Path = DEFAULT_STATE_PATH) -> dict:
    try:
        if state_path.exists():
            return json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_trigger_state(state: dict, state_path: Path = DEFAULT_STATE_PATH) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _audit(metadata: dict) -> None:
    try:
        from modules.audit import log_audit_event

        log_audit_event(actor="system", action="proactive_trigger", outcome="success", metadata=metadata)
    except Exception:
        pass


def run_disk_and_backup_guard(
    project_root: Path = None,
    min_free_pct: float = 15.0,
    max_backups_mb: float = 500.0,
    max_age_days: int = 7,
    keep_min_files: int = 10,
    cooldown_sec: int = 1800,
    state_path: Path = DEFAULT_STATE_PATH,
    force: bool = False,
) -> dict:
    """Enforce disk free-space and backup-size limits by pruning old backups."""
    root = Path(project_root) if project_root else Path(__file__).resolve().parent.parent
    state = _load_trigger_state(state_path)

    if not force and time.time() - state.get("last_disk_guard_ts", 0) < cooldown_sec:
        return {"triggered": False, "deleted_files": 0, "reclaimed_mb": 0.0, "free_pct": 100.0}

    usage = shutil.disk_usage(str(root))
    free_pct = (usage.free / usage.total) * 100.0 if usage.total else 100.0

    backups_dir = root / ".backups"
    backup_files = []
    backups_bytes = 0
    if backups_dir.exists():
        for p in backups_dir.iterdir():
            if p.is_file():
                try:
                    st = p.stat()
                    backup_files.append((p, st.st_mtime, st.st_size))
                    backups_bytes += st.st_size
                except OSError:
                    continue
    backups_mb = backups_bytes / (1024 * 1024)

    state["last_disk_guard_ts"] = time.time()
    _save_trigger_state(state, state_path)

    deleted = 0
    reclaimed = 0
    if free_pct < min_free_pct or backups_mb > max_backups_mb:
        # oldest first
        backup_files.sort(key=lambda t: t[1])
        # newest keep_min_files are always protected
        protected = set(id(t[0]) for t in backup_files[-keep_min_files:]) if keep_min_files > 0 else set()
        now = time.time()
        for path, mtime, size in backup_files:
            if id(path) in protected:
                continue
            age_days = (now - mtime) / 86400.0
            should_delete = age_days > max_age_days or backups_mb > max_backups_mb
            if should_delete:
                try:
                    path.unlink()
                    deleted += 1
                    reclaimed += size
                    backups_mb -= size / (1024 * 1024)
                except OSError:
                    continue
        reclaimed_mb = reclaimed / (1024 * 1024)
        _audit({"trigger": "disk_backup_guard", "deleted_files": deleted, "reclaimed_mb": round(reclaimed_mb, 2), "free_pct": round(free_pct, 1)})
    else:
        reclaimed_mb = 0.0

    return {
        "triggered": bool(deleted),
        "deleted_files": deleted,
        "reclaimed_mb": round(reclaimed_mb, 2),
        "free_pct": round(free_pct, 1),
    }


def run_daily_health_digest(
    project_root: Path = None,
    digest_path: Path = Path("/home/millennium/notes/daily_digest.md"),
    interval_sec: int = 86400,
    state_path: Path = DEFAULT_STATE_PATH,
    force: bool = False,
) -> dict:
    """Write a daily markdown health digest (tests, git status, disk/RAM)."""
    root = Path(project_root) if project_root else Path(__file__).resolve().parent.parent
    state = _load_trigger_state(state_path)

    if not force and time.time() - state.get("last_daily_digest_ts", 0) < interval_sec:
        return {"written": False, "digest_path": str(digest_path), "tests_summary": ""}

    # Respect stop flag and low-RAM conditions (retry later without stamping time)
    try:
        from modules.goals import is_stopped

        if is_stopped():
            return {"written": False, "digest_path": str(digest_path), "tests_summary": ""}
    except Exception:
        pass
    try:
        from modules.diagnostics import check_memory_headroom

        ok, _ = check_memory_headroom(min_available_mb=800)
        if not ok:
            return {"written": False, "digest_path": str(digest_path), "tests_summary": ""}
    except Exception:
        pass

    # Run test suite (extract final summary line). Use a headless-safe loader
    # that excludes tests/test_ui.py, which hangs under mocked Tk.
    summary_line = ""
    try:
        _safe_runner = (
            "import unittest, glob; "
            "loader = unittest.TestLoader(); "
            "suite = unittest.TestSuite([loader.loadTestsFromName(f[:-3].replace('/', '.')) "
            "for f in sorted(glob.glob('tests/test_*.py')) if f != 'tests/test_ui.py']); "
            "res = unittest.TextTestRunner(verbosity=0).run(suite); "
            "print(f'Ran {res.testsRun} tests'); "
            "print('OK' if res.wasSuccessful() else 'FAILED')"
        )
        proc = subprocess.run(
            [sys.executable, "-c", _safe_runner],
            cwd=str(root), capture_output=True, text=True, timeout=180,
        )
        out = (proc.stdout or "").strip().splitlines()
        for line in reversed(out):
            if line.startswith("Ran ") or line.startswith("OK") or line.startswith("FAILED"):
                summary_line = line
                break
        if not summary_line:
            err = proc.stderr.strip().splitlines()
            summary_line = err[-1] if err else "no output"
    except Exception as e:
        summary_line = f"test run error: {e}"

    # Git status
    git_status = ""
    try:
        proc = subprocess.run(
            ["git", "status", "--short"],
            cwd=str(root), capture_output=True, text=True, timeout=10,
        )
        lines = [l for l in proc.stdout.strip().splitlines() if l.strip()]
        git_status = f"{len(lines)} changed/untracked file(s)"
    except Exception as e:
        git_status = f"git error: {e}"

    # Unreviewed reflections
    unreviewed = "n/a"
    try:
        from modules.reflections import get_reflections_for_review

        refs = get_reflections_for_review(unreviewed_only=True, failures_only=True, limit=100)
        unreviewed = str(len(refs))
    except Exception:
        pass

    # Disk & RAM
    try:
        free_gb = shutil.disk_usage(str(root)).free / (1024 ** 3)
    except Exception:
        free_gb = 0.0
    try:
        from modules.diagnostics import get_available_ram_mb

        ram_mb = get_available_ram_mb()
        ram_str = f"{ram_mb:.0f} MB" if ram_mb != float("inf") else "n/a"
    except Exception:
        ram_str = "n/a"

    now = datetime.now(timezone.utc)
    section = (
        f"## Daily Health Digest — {now.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        f"- Tests: {summary_line}\n"
        f"- Git: {git_status}\n"
        f"- Unreviewed failures: {unreviewed}\n"
        f"- Free disk: {free_gb:.1f} GB\n"
        f"- Available RAM: {ram_str}\n\n"
    )

    try:
        digest_path.parent.mkdir(parents=True, exist_ok=True)
        existing = digest_path.read_text(encoding="utf-8") if digest_path.exists() else ""
        digest_path.write_text(section + existing, encoding="utf-8")
    except Exception as e:
        return {"written": False, "digest_path": str(digest_path), "tests_summary": summary_line, "error": str(e)}

    state["last_daily_digest_ts"] = time.time()
    _save_trigger_state(state, state_path)
    _audit({"trigger": "daily_health_digest", "digest_path": str(digest_path)})
    return {"written": True, "digest_path": str(digest_path), "tests_summary": summary_line}