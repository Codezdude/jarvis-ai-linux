"""Self-update detection and clean-restart orchestration for J.A.R.V.I.S.

Watches only code/config targets (``jarvis.py``, ``modules/**/*.py``,
``data/mcp_servers.json``). Runtime state (``memory/``, ``logs/``,
``data/trigger_state.json``, ``__pycache__/``) never triggers an update.

The approval prompt adapts to whatever confirmation entry point
``modules/approval.py`` exposes (``request_approval``/``ask_approval``/
``prompt_user``, ``check_approval``, or a registered UI callback).
"""

import hashlib
import inspect
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_BOOT_HEAD: str = ""
_BOOT_MTIME: float = 0.0
_PROMPTED_FINGERPRINTS: set[str] = set()
_LAST_REMOTE_CHECK_TS: float = 0.0
_PENDING_APPROVAL: bool = False


def _git_output(args: list[str], cwd: Path = PROJECT_ROOT, timeout: int = 8) -> str:
    try:
        res = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return res.stdout.strip() if res.returncode == 0 else ""
    except Exception:
        return ""


def _scan_watched_files(root: Path = PROJECT_ROOT) -> tuple[float, list[str]]:
    candidates: list[Path] = []
    entry = root / "jarvis.py"
    if entry.is_file():
        candidates.append(entry)
    modules_dir = root / "modules"
    if modules_dir.is_dir():
        candidates.extend(p for p in modules_dir.rglob("*.py") if "__pycache__" not in p.parts)
    mcp_cfg = root / "data" / "mcp_servers.json"
    if mcp_cfg.is_file():
        candidates.append(mcp_cfg)

    max_mtime = 0.0
    mtimes: dict[str, float] = {}
    for path in candidates:
        try:
            mt = path.stat().st_mtime
            mtimes[str(path.relative_to(root))] = mt
            if mt > max_mtime:
                max_mtime = mt
        except OSError:
            continue

    changed = [rel for rel, mt in sorted(mtimes.items()) if _BOOT_MTIME > 0 and mt > (_BOOT_MTIME + 0.5)]
    return max_mtime, changed


def init_update_baseline(root: Path = PROJECT_ROOT) -> dict:
    """Snapshot HEAD commit and watched source/config mtimes once after startup."""
    global _BOOT_HEAD, _BOOT_MTIME
    _BOOT_HEAD = _git_output(["rev-parse", "HEAD"], cwd=root)
    max_mtime, _ = _scan_watched_files(root)
    _BOOT_MTIME = max_mtime or time.time()
    return {"boot_head": _BOOT_HEAD, "boot_mtime": _BOOT_MTIME}


def detect_available_update(
    root: Path = PROJECT_ROOT,
    check_remote: bool = False,
    remote_cooldown_sec: int = 1800,
) -> Optional[dict]:
    """Return update metadata dict if code/config changed since boot, else None."""
    global _LAST_REMOTE_CHECK_TS
    if not _BOOT_HEAD and _BOOT_MTIME == 0.0:
        init_update_baseline(root)

    reasons: list[str] = []
    changed_files: list[str] = []

    # 1. Local git HEAD moved forward since boot
    current_head = _git_output(["rev-parse", "HEAD"], cwd=root)
    if _BOOT_HEAD and current_head and current_head != _BOOT_HEAD:
        summary = _git_output(["log", "-1", "--pretty=%h %s"], cwd=root) or current_head[:8]
        reasons.append(f"New commit active on disk ({summary})")
        diff_names = _git_output(["diff", "--name-only", _BOOT_HEAD, current_head], cwd=root)
        if diff_names:
            changed_files.extend(ln.strip() for ln in diff_names.splitlines() if ln.strip())

    # 2. Watched Python or MCP config files edited on disk after boot
    max_mtime, modified_since_boot = _scan_watched_files(root)
    if modified_since_boot:
        reasons.append(f"{len(modified_since_boot)} source/config file(s) modified since boot")
        for f in modified_since_boot:
            if f not in changed_files:
                changed_files.append(f)

    # 3. Optional remote upstream check (throttled)
    remote_head = ""
    now = time.time()
    if check_remote and (now - _LAST_REMOTE_CHECK_TS) >= remote_cooldown_sec:
        _LAST_REMOTE_CHECK_TS = now
        upstream = _git_output(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=root)
        if upstream:
            _git_output(["fetch", "--quiet"], cwd=root, timeout=15)
            remote_head = _git_output(["rev-parse", upstream], cwd=root)
            if remote_head and current_head and remote_head != current_head:
                reasons.append(f"Upstream updates available on {upstream} ({remote_head[:8]})")

    if not reasons:
        return None

    raw_fp = f"{current_head}|{remote_head}|{round(max_mtime, 1)}|{','.join(sorted(changed_files))}"
    fingerprint = hashlib.sha256(raw_fp.encode("utf-8")).hexdigest()[:16]
    return {
        "fingerprint": fingerprint,
        "reasons": reasons,
        "changed_files": changed_files[:12],
        "current_head": current_head,
        "remote_head": remote_head,
    }


def _invoke_approval_hook(prompt_msg: str, info: dict) -> bool:
    """Adapt dynamically to the exact user-confirmation function in modules/approval.py."""
    from modules import approval

    for candidate_name in ("request_approval", "ask_approval", "prompt_user", "request_user_approval"):
        fn = getattr(approval, candidate_name, None)
        if not callable(fn):
            continue
        sig = inspect.signature(fn)
        params = sig.parameters
        kwargs = {}
        for key, val in (
            ("action", "restart_jarvis"),
            ("title", "Restart J.A.R.V.I.S. to Apply Update"),
            ("details", prompt_msg),
            ("message", prompt_msg),
            ("prompt", prompt_msg),
            ("reason", prompt_msg),
            ("diff_preview", "\n".join(info.get("changed_files", []))),
        ):
            if key in params:
                kwargs[key] = val
        if kwargs:
            return bool(fn(**kwargs))
        positional = [
            p for p in params.values()
            if p.default is inspect.Parameter.empty
            and p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        if len(positional) == 1:
            return bool(fn(prompt_msg))
        if len(positional) >= 2:
            return bool(fn("restart_jarvis", prompt_msg))

    # Fallback A: the real approval gate (check_approval -> dict with "approved").
    check_approval = getattr(approval, "check_approval", None)
    if callable(check_approval):
        changed = info.get("changed_files") or ["code/config"]
        try:
            result = check_approval(
                action="restart_jarvis",
                target=str(changed[0]),
                actor="system",
                source="self",
                reason=prompt_msg,
                diff="\n".join(info.get("changed_files", [])),
            )
            if isinstance(result, dict) and "approved" in result:
                return bool(result["approved"])
        except Exception:
            pass

    # Fallback B: a registered UI prompt callback (modules/ui.py modal).
    callback = getattr(approval, "_UI_PROMPT_CALLBACK", None)
    if callable(callback):
        payload = {
            "request_id": info.get("fingerprint", "update"),
            "actor": "system",
            "source": "self",
            "action": "restart_jarvis",
            "risk_tier": "elevated",
            "target": "jarvis.py",
            "reason": prompt_msg,
            "reasons": info.get("reasons", []),
            "diff": "\n".join(info.get("changed_files", [])),
        }
        try:
            return bool(callback(payload))
        except Exception:
            return False

    raise RuntimeError("No compatible approval prompt function found in modules/approval.py")


def _is_systemd_service_active() -> bool:
    if os.getenv("INVOCATION_ID"):
        return True
    try:
        res = subprocess.run(
            ["systemctl", "--user", "is-active", "jarvis.service"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return res.returncode == 0 and res.stdout.strip() == "active"
    except Exception:
        return False


def perform_clean_restart(root: Path = PROJECT_ROOT, pull_remote: bool = False) -> None:
    """Optionally fast-forward remote commits, flush MCP children, and restart J.A.R.V.I.S."""
    if pull_remote:
        try:
            subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=20,
            )
        except Exception as exc:
            print(f"[UPDATE] Fast-forward pull skipped: {exc}")

    try:
        from modules.mcp_bridge import shutdown_mcp_servers

        shutdown_mcp_servers()
    except Exception:
        pass

    sys.stdout.flush()
    sys.stderr.flush()

    if _is_systemd_service_active():
        subprocess.Popen(
            ["systemctl", "--user", "restart", "jarvis.service"],
            cwd=str(root),
            start_new_session=True,
        )
        return

    os.execv(sys.executable, [sys.executable, *sys.argv])


def check_and_request_restart(
    root: Path = PROJECT_ROOT,
    check_remote: bool = False,
    force_prompt: bool = False,
    approval_fn: Optional[Callable[..., bool]] = None,
    restart_fn: Optional[Callable[..., None]] = None,
) -> dict:
    """Detect updates and prompt the user once per revision to approve a clean restart."""
    global _PENDING_APPROVAL
    if _PENDING_APPROVAL:
        return {"status": "pending_approval"}

    info = detect_available_update(
        root=root,
        check_remote=check_remote,
        remote_cooldown_sec=0 if force_prompt else 1800,
    )
    if not info:
        return {"status": "up_to_date"}

    fp = info["fingerprint"]
    if not force_prompt and fp in _PROMPTED_FINGERPRINTS:
        return {"status": "already_prompted", "fingerprint": fp}

    _PROMPTED_FINGERPRINTS.add(fp)
    files_preview = ", ".join(info["changed_files"][:6]) or "code/config updated"
    prompt_msg = (
        "J.A.R.V.I.S. detected an update on disk and needs a restart to load the new code.\n\n"
        f"• Reason: {'; '.join(info['reasons'])}\n"
        f"• Changed: {files_preview}\n\n"
        "Restart J.A.R.V.I.S. now?"
    )

    _PENDING_APPROVAL = True
    try:
        if approval_fn is not None:
            approved = bool(approval_fn(prompt_msg, info))
        else:
            approved = _invoke_approval_hook(prompt_msg, info)
    except Exception as exc:
        print(f"[UPDATE] Approval prompt failed: {exc}")
        approved = False
    finally:
        _PENDING_APPROVAL = False

    if approved:
        has_remote = bool(info.get("remote_head") and info.get("remote_head") != info.get("current_head"))
        runner = restart_fn or perform_clean_restart
        sig = inspect.signature(runner)
        if "pull_remote" in sig.parameters:
            runner(root, pull_remote=has_remote)
        else:
            runner(root)
        return {"status": "restarting", "fingerprint": fp}

    return {"status": "deferred", "fingerprint": fp}