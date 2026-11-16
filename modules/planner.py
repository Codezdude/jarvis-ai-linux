import json
import os
import re
from collections.abc import Callable
from typing import Any

from modules.audit import log_audit_event
from modules.budget import check_budget, record_budget_usage
from modules.preferences import get_preference

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(PROJECT_ROOT, "data", "jarvis_memory.db")


def _estimate_tokens(*texts: str) -> int:
    total_chars = sum(len(t or "") for t in texts)
    return max(1, total_chars // 4)


def _extract_json_array(raw_text: str) -> list[dict[str, Any]] | None:
    match = re.search(r"\[.*\]", str(raw_text), flags=re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
        if isinstance(parsed, list) and all(isinstance(item, dict) for item in parsed):
            return parsed
    except json.JSONDecodeError:
        return None
    return None


def _extract_json_object(raw_text: str) -> dict[str, Any] | None:
    match = re.search(r"\{.*\}", str(raw_text), flags=re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        return None
    return None


def _normalize_plan_steps(raw_steps: list[dict[str, Any]], max_steps: int) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_steps[:max_steps], start=1):
        desc = str(item.get("description", "")).strip()
        if not desc:
            continue
        expected = str(item.get("expected_outcome", "Step completes without errors")).strip()
        normalized.append(
            {
                "step_id": idx,
                "description": desc,
                "expected_outcome": expected,
                "status": "pending",
                "output": None,
                "verification_notes": None,
            }
        )
    return normalized


def decompose_goal(
    goal_description: str,
    context: dict[str, Any] | None = None,
    max_steps: int = 8,
    planner_fn: Callable[[str, dict[str, Any]], list[dict[str, Any]]] | None = None,
    goal_id: str | None = None,
    db_path: str = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    """Decomposes a goal into an ordered list of verifiable steps.

    Uses local Ollama by default and records token consumption against budget.py.
    """
    ctx = context or {}
    if planner_fn is not None:
        raw_steps = planner_fn(goal_description, ctx)
        steps = _normalize_plan_steps(raw_steps, max_steps=max_steps)
    else:
        from modules.ai import ask_local_brain

        prompt = (
            "You are J.A.R.V.I.S.'s Task Planner. Decompose the goal into a concise JSON array "
            f"of at most {max_steps} sequential steps. Each object MUST have keys: "
            "'description' (string) and 'expected_outcome' (verifiable completion criterion).\n\n"
            f"Goal: {goal_description}\n"
            f"Context: {json.dumps(ctx, ensure_ascii=False)}"
        )
        raw = ask_local_brain(prompt, force_provider="ollama")
        record_budget_usage(
            "tokens",
            actor="autonomous_planner",
            goal_id=goal_id,
            amount=_estimate_tokens(prompt, str(raw)),
            db_path=db_path,
        )
        parsed = _extract_json_array(str(raw)) or []
        steps = _normalize_plan_steps(parsed, max_steps=max_steps)

    if not steps:
        steps = _normalize_plan_steps(
            [
                {
                    "description": goal_description,
                    "expected_outcome": "Goal objective achieved cleanly without errors",
                }
            ],
            max_steps=max_steps,
        )

    log_audit_event(
        actor="autonomous_planner",
        action="goal_plan_created",
        outcome="success",
        metadata={"goal_id": goal_id, "step_count": len(steps)},
    )
    return steps


def execute_plan_step(
    goal: dict[str, Any],
    step: dict[str, Any],
    prior_steps: list[dict[str, Any]],
    executor_fn: Callable[[dict[str, Any], dict[str, Any], list[dict[str, Any]]], str] | None = None,
    db_path: str = DEFAULT_DB_PATH,
) -> str:
    """Executes a single planned step using prior verified outputs as context.

    Autonomous goals run strictly on local Ollama.
    """
    if executor_fn is not None:
        return str(executor_fn(goal, step, prior_steps))

    from modules.ai import ask_local_brain

    history_summary = [
        {"step_id": s["step_id"], "description": s["description"], "output": str(s.get("output", ""))[:400]}
        for s in prior_steps
        if s.get("status") == "completed"
    ]
    prompt = (
        f"Goal: {goal['description']}\n"
        f"Completed Prior Steps: {json.dumps(history_summary, ensure_ascii=False)}\n"
        f"Current Step ({step['step_id']}): {step['description']}\n"
        f"Expected Outcome: {step['expected_outcome']}\n"
        "Execute this step concisely and provide concrete output."
    )
    raw = ask_local_brain(prompt, force_provider="ollama")
    record_budget_usage(
        "tokens",
        actor="autonomous_planner",
        goal_id=goal.get("id"),
        amount=_estimate_tokens(prompt, str(raw)),
        db_path=db_path,
    )
    return str(raw).strip()


def verify_step_output(
    goal_description: str,
    step: dict[str, Any],
    step_output: str,
    verifier_fn: Callable[[dict[str, Any], str], dict[str, Any]] | None = None,
    goal_id: str | None = None,
    db_path: str = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Checks whether `step_output` satisfies `step['expected_outcome']` before proceeding.

    Returns {'verified': bool, 'reason': str}.
    """
    if verifier_fn is not None:
        res = verifier_fn(step, step_output)
        return {
            "verified": bool(res.get("verified", False)),
            "reason": str(res.get("reason", "")),
        }

    text = (step_output or "").strip()
    if not text:
        return {"verified": False, "reason": "Step produced empty output."}

    if re.search(r"\b(Traceback \(most recent call last\)|SyntaxError:|NameError:|TypeError:)\b", text):
        return {"verified": False, "reason": "Step output contains an unhandled Python exception."}

    from modules.ai import ask_local_brain

    prompt = (
        "You are J.A.R.V.I.S.'s Step Verifier. Determine if the step output satisfies the "
        'expected outcome. Respond strictly as JSON: {"verified": true|false, "reason": "..."}.\n\n'
        f"Goal: {goal_description}\n"
        f"Step: {step['description']}\n"
        f"Expected Outcome: {step['expected_outcome']}\n"
        f"Actual Output:\n{text[:1200]}"
    )
    raw = ask_local_brain(prompt, force_provider="ollama")
    record_budget_usage(
        "tokens",
        actor="autonomous_planner",
        goal_id=goal_id,
        amount=_estimate_tokens(prompt, str(raw)),
        db_path=db_path,
    )
    parsed = _extract_json_object(str(raw))
    if parsed is not None and "verified" in parsed:
        return {
            "verified": bool(parsed["verified"]),
            "reason": str(parsed.get("reason", "Verified by local model.")),
        }

    return {"verified": True, "reason": "Non-empty output with no error signatures."}


def replan_from_failure(
    goal_description: str,
    completed_steps: list[dict[str, Any]],
    failed_step: dict[str, Any],
    failure_reason: str,
    remaining_step_budget: int,
    replan_fn: Callable[[str, list[dict[str, Any]], dict[str, Any], str], list[dict[str, Any]]] | None = None,
    goal_id: str | None = None,
    db_path: str = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    """Generates a revised sequence of steps starting from the failure point, preserving already completed work."""
    budget = max(1, remaining_step_budget)
    if replan_fn is not None:
        raw_steps = replan_fn(goal_description, completed_steps, failed_step, failure_reason)
        steps = _normalize_plan_steps(raw_steps, max_steps=budget)
    else:
        from modules.ai import ask_local_brain

        prompt = (
            "You are J.A.R.V.I.S.'s Replanner. A plan step failed verification. Produce a revised "
            f"JSON array of at most {budget} recovery steps (keys: 'description', 'expected_outcome') "
            "starting from the failure point without repeating completed steps.\n\n"
            f"Goal: {goal_description}\n"
            f"Completed Steps: {json.dumps([s['description'] for s in completed_steps], ensure_ascii=False)}\n"
            f"Failed Step: {failed_step['description']}\n"
            f"Failure Reason: {failure_reason}"
        )
        raw = ask_local_brain(prompt, force_provider="ollama")
        record_budget_usage(
            "tokens",
            actor="autonomous_planner",
            goal_id=goal_id,
            amount=_estimate_tokens(prompt, str(raw)),
            db_path=db_path,
        )
        parsed = _extract_json_array(str(raw)) or []
        steps = _normalize_plan_steps(parsed, max_steps=budget)

    if not steps:
        steps = _normalize_plan_steps(
            [
                {
                    "description": f"Retry with corrective adjustment: {failed_step['description']} (fix: {failure_reason})",
                    "expected_outcome": failed_step.get("expected_outcome", "Step succeeds cleanly"),
                }
            ],
            max_steps=budget,
        )

    offset = len(completed_steps)
    for i, s in enumerate(steps, start=1):
        s["step_id"] = offset + i

    return steps


def planned_step_executor(
    goal: dict[str, Any],
    step_index: int,
    checkpoint: dict[str, Any],
    planner_fn: Callable[[str, dict[str, Any]], list[dict[str, Any]]] | None = None,
    executor_fn: Callable[[dict[str, Any], dict[str, Any], list[dict[str, Any]]], str] | None = None,
    verifier_fn: Callable[[dict[str, Any], str], dict[str, Any]] | None = None,
    replan_fn: Callable[[str, list[dict[str, Any]], dict[str, Any], str], list[dict[str, Any]]] | None = None,
    db_path: str = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Plugs into modules/scheduler.py's process_one_idle_goal(...) loop.

    Performs one bounded Plan -> Execute -> Verify -> Replan cycle per call,
    mutating `checkpoint` in place so scheduler.py persists it immediately.
    """
    goal_id = goal["id"]
    max_steps_pref, _ = get_preference("planner.max_steps_per_goal", default=8, db_path=db_path)
    max_replans_pref, _ = get_preference("planner.max_replans_per_goal", default=2, db_path=db_path)
    max_steps = int(max_steps_pref)
    max_replans = int(max_replans_pref)

    if "plan" not in checkpoint or not checkpoint["plan"]:
        checkpoint["plan"] = decompose_goal(
            goal_description=goal["description"],
            context=checkpoint.get("context"),
            max_steps=max_steps,
            planner_fn=planner_fn,
            goal_id=goal_id,
            db_path=db_path,
        )
        checkpoint["plan_cursor"] = 0
        checkpoint["replan_count"] = 0

    plan: list[dict[str, Any]] = checkpoint["plan"]
    cursor: int = int(checkpoint.get("plan_cursor", 0))

    if cursor >= len(plan):
        return {"step_done": True, "goal_complete": True, "step_output": "All planned steps completed."}

    budget_status = check_budget(actor="autonomous_planner", goal_id=goal_id, db_path=db_path)
    if not budget_status["allowed"]:
        raise RuntimeError(f"Budget exhausted during planned execution: {budget_status['reason']}")

    current_step = plan[cursor]
    prior_steps = plan[:cursor]

    raw_output = execute_plan_step(
        goal=goal,
        step=current_step,
        prior_steps=prior_steps,
        executor_fn=executor_fn,
        db_path=db_path,
    )

    verification = verify_step_output(
        goal_description=goal["description"],
        step=current_step,
        step_output=raw_output,
        verifier_fn=verifier_fn,
        goal_id=goal_id,
        db_path=db_path,
    )

    if verification["verified"]:
        current_step["status"] = "completed"
        current_step["output"] = raw_output
        current_step["verification_notes"] = verification["reason"]
        checkpoint["plan_cursor"] = cursor + 1

        log_audit_event(
            actor="autonomous_planner",
            action="plan_step_verified",
            outcome="success",
            metadata={
                "goal_id": goal_id,
                "step_id": current_step["step_id"],
                "reason": verification["reason"],
            },
        )

        is_done = checkpoint["plan_cursor"] >= len(plan)
        return {
            "step_done": True,
            "goal_complete": is_done,
            "step_output": raw_output,
        }

    replan_count = int(checkpoint.get("replan_count", 0))
    log_audit_event(
        actor="autonomous_planner",
        action="plan_step_verification_failed",
        outcome="error",
        metadata={
            "goal_id": goal_id,
            "step_id": current_step["step_id"],
            "reason": verification["reason"],
            "replan_count": replan_count,
        },
    )

    if replan_count >= max_replans:
        raise RuntimeError(
            f"Step {current_step['step_id']} failed verification after {max_replans} replans: {verification['reason']}"
        )

    checkpoint["replan_count"] = replan_count + 1
    remaining_budget = max(1, max_steps - (step_index + 1))
    revised_tail = replan_from_failure(
        goal_description=goal["description"],
        completed_steps=prior_steps,
        failed_step=current_step,
        failure_reason=verification["reason"],
        remaining_step_budget=remaining_budget,
        replan_fn=replan_fn,
        goal_id=goal_id,
        db_path=db_path,
    )
    checkpoint["plan"] = prior_steps + revised_tail

    log_audit_event(
        actor="autonomous_planner",
        action="goal_replanned",
        outcome="success",
        metadata={
            "goal_id": goal_id,
            "replan_attempt": checkpoint["replan_count"],
            "new_remaining_steps": len(revised_tail),
        },
    )

    return {
        "step_done": False,
        "goal_complete": False,
        "step_output": f"Verification failed ({verification['reason']}); replanned from step {current_step['step_id']}.",
    }
