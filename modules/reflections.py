import datetime
import json
import os
import re
import sqlite3
import threading
from typing import Any, Callable, Dict, List, Optional

from modules.audit import log_audit_event
from modules.budget import record_budget_usage
from modules.goals import enqueue_goal
from modules.preferences import get_preference

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(PROJECT_ROOT, "data", "jarvis_memory.db")

_REFLECTIONS_LOCK = threading.Lock()

RUBRIC_WEIGHTS: Dict[str, float] = {
    "completeness": 0.35,
    "tool_accuracy": 0.25,
    "preference_alignment": 0.20,
    "safety_compliance": 0.20,
}

VALID_FAILURE_MODES = {
    "none",
    "incomplete_execution",
    "tool_schema_mismatch",
    "preference_violation",
    "syntax_or_test_error",
    "budget_or_loop_stall",
    "hallucinated_context",
}

VALID_REMEDIATIONS = {"none", "new_unit_test", "prompt_adjustment", "tool_schema_update"}


def _get_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_reflections_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """Creates the reflections table and indexes if they do not exist."""
    with _REFLECTIONS_LOCK:
        with _get_connection(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reflections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    goal_id TEXT NOT NULL,
                    task_description TEXT NOT NULL,
                    output_excerpt TEXT NOT NULL,
                    rubric_scores_json TEXT NOT NULL,
                    overall_score REAL NOT NULL CHECK(overall_score BETWEEN 0.0 AND 1.0),
                    passed INTEGER NOT NULL CHECK(passed IN (0, 1)),
                    failure_mode TEXT NOT NULL DEFAULT 'none',
                    root_cause TEXT NOT NULL DEFAULT '',
                    proposed_remediation TEXT NOT NULL DEFAULT 'none',
                    mined_into_goal_id TEXT,
                    reviewed_by_human INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_reflections_passed_mode "
                "ON reflections(passed, failure_mode, mined_into_goal_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_reflections_review "
                "ON reflections(reviewed_by_human, created_at DESC)"
            )
            conn.commit()


def _compute_weighted_score(scores: Dict[str, float]) -> float:
    total = 0.0
    for dim, weight in RUBRIC_WEIGHTS.items():
        val = max(0.0, min(1.0, float(scores.get(dim, 0.0))))
        total += val * weight
    return round(total, 4)


def _heuristic_rubric_fallback(
    task_description: str,
    output: str,
    execution_error: Optional[str] = None,
) -> Dict[str, Any]:
    """Deterministic scorer used if local Ollama output cannot be parsed as JSON."""
    text = (output or "").strip()
    if execution_error or not text:
        return {
            "scores": {
                "completeness": 0.1 if text else 0.0,
                "tool_accuracy": 0.2 if not execution_error else 0.0,
                "preference_alignment": 0.5,
                "safety_compliance": 1.0,
            },
            "failure_mode": "syntax_or_test_error" if execution_error else "incomplete_execution",
            "root_cause": execution_error or "Empty or missing task output.",
            "proposed_remediation": "new_unit_test" if execution_error else "prompt_adjustment",
        }

    has_error_marker = bool(re.search(r"\b(Traceback|Exception|Error:|FAILED)\b", text))
    completeness = 0.85 if len(text) >= 20 else 0.45
    tool_acc = 0.35 if has_error_marker else 0.85
    return {
        "scores": {
            "completeness": completeness,
            "tool_accuracy": tool_acc,
            "preference_alignment": 0.85,
            "safety_compliance": 1.0,
        },
        "failure_mode": "tool_schema_mismatch" if has_error_marker else "none",
        "root_cause": "Output contained runtime error markers." if has_error_marker else "",
        "proposed_remediation": "tool_schema_update" if has_error_marker else "none",
    }


def _ollama_rubric_scorer(
    task_description: str,
    output: str,
    execution_error: Optional[str] = None,
    goal_id: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> Dict[str, Any]:
    """Uses local Ollama exclusively to grade task output against the rubric."""
    from modules.ai import ask_local_brain

    prompt = (
        "You are J.A.R.V.I.S.'s Post-Task Reflection Evaluator. Grade the autonomous task output "
        "strictly as JSON with keys: scores (completeness, tool_accuracy, preference_alignment, "
        "safety_compliance as floats 0.0..1.0), failure_mode (one of: none, incomplete_execution, "
        "tool_schema_mismatch, preference_violation, syntax_or_test_error, budget_or_loop_stall, "
        "hallucinated_context), root_cause (string), proposed_remediation (one of: none, "
        "new_unit_test, prompt_adjustment, tool_schema_update).\n\n"
        f"Task: {task_description}\n"
        f"Execution Error: {execution_error or 'None'}\n"
        f"Output:\n{output[:1500]}"
    )

    try:
        raw = ask_local_brain(prompt)
        est_tokens = max(1, (len(prompt) + len(str(raw))) // 4)
        record_budget_usage(
            "tokens",
            actor="reflection_evaluator",
            goal_id=goal_id,
            amount=est_tokens,
            db_path=db_path,
        )

        match = re.search(r"\{.*\}", str(raw), flags=re.DOTALL)
        if not match:
            return _heuristic_rubric_fallback(task_description, output, execution_error)

        parsed = json.loads(match.group(0))
        if not isinstance(parsed.get("scores"), dict):
            return _heuristic_rubric_fallback(task_description, output, execution_error)

        mode = parsed.get("failure_mode", "none")
        if mode not in VALID_FAILURE_MODES:
            mode = "incomplete_execution"

        remediation = str(parsed.get("proposed_remediation", "none"))
        if remediation not in VALID_REMEDIATIONS:
            remediation = "none"

        return {
            "scores": parsed["scores"],
            "failure_mode": mode,
            "root_cause": str(parsed.get("root_cause", "")),
            "proposed_remediation": remediation,
        }
    except Exception:
        return _heuristic_rubric_fallback(task_description, output, execution_error)


def evaluate_task_rubric(
    goal_id: str,
    task_description: str,
    output: str,
    execution_error: Optional[str] = None,
    scorer_fn: Optional[Callable[[str, str, Optional[str]], Dict[str, Any]]] = None,
    pass_threshold: Optional[float] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> Dict[str, Any]:
    """
    Scores an autonomous task output against the rubric and stores the reflection.
    Failed evaluations (overall_score < pass_threshold or execution_error is set)
    are archived in the failure corpus for pattern mining and weekly HUD review.
    """
    init_reflections_db(db_path)

    if pass_threshold is None:
        pref_thresh, _ = get_preference("reflection.pass_threshold", default=0.75, db_path=db_path)
        threshold = float(pref_thresh)
    else:
        threshold = float(pass_threshold)

    if scorer_fn is not None:
        eval_data = scorer_fn(task_description, output, execution_error)
    else:
        eval_data = _ollama_rubric_scorer(
            task_description, output, execution_error, goal_id=goal_id, db_path=db_path
        )

    scores = eval_data.get("scores", {})
    overall = _compute_weighted_score(scores)
    passed = bool(overall >= threshold and not execution_error)

    failure_mode = "none" if passed else eval_data.get("failure_mode", "incomplete_execution")
    if not passed and failure_mode == "none":
        failure_mode = "incomplete_execution"

    root_cause = "" if passed else (eval_data.get("root_cause") or execution_error or "Score below threshold.")
    remediation = "none" if passed else eval_data.get("proposed_remediation", "prompt_adjustment")
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    with _REFLECTIONS_LOCK:
        with _get_connection(db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO reflections (
                    goal_id, task_description, output_excerpt, rubric_scores_json,
                    overall_score, passed, failure_mode, root_cause,
                    proposed_remediation, mined_into_goal_id, reviewed_by_human, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, ?)
                """,
                (
                    goal_id,
                    task_description,
                    (output or "")[:1000],
                    json.dumps(scores, ensure_ascii=False),
                    overall,
                    1 if passed else 0,
                    failure_mode,
                    root_cause,
                    remediation,
                    now_iso,
                ),
            )
            reflection_id = cur.lastrowid
            conn.commit()

    event_id = log_audit_event(
        actor="reflection_evaluator",
        action="task_reflection_recorded",
        outcome="success" if passed else "error",
        metadata={
            "reflection_id": reflection_id,
            "goal_id": goal_id,
            "overall_score": overall,
            "passed": passed,
            "failure_mode": failure_mode,
            "proposed_remediation": remediation,
        },
    )

    return {
        "reflection_id": reflection_id,
        "goal_id": goal_id,
        "overall_score": overall,
        "passed": passed,
        "failure_mode": failure_mode,
        "root_cause": root_cause,
        "proposed_remediation": remediation,
        "audit_event_id": event_id,
    }


def mine_failure_corpus(
    min_occurrences: int = 2,
    db_path: str = DEFAULT_DB_PATH,
) -> List[Dict[str, Any]]:
    """
    Scans unmined failure reflections, clusters them by failure_mode, and converts
    recurring failure modes (>= min_occurrences) into actionable self-improvement goals
    (new unit tests, prompt adjustments, or tool-schema updates).
    """
    init_reflections_db(db_path)
    created_goals: List[Dict[str, Any]] = []

    with _REFLECTIONS_LOCK:
        with _get_connection(db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, goal_id, task_description, failure_mode, root_cause, proposed_remediation
                FROM reflections
                WHERE passed = 0 AND mined_into_goal_id IS NULL AND failure_mode != 'none'
                ORDER BY id ASC
                """
            ).fetchall()

            clusters: Dict[str, List[sqlite3.Row]] = {}
            for row in rows:
                clusters.setdefault(row["failure_mode"], []).append(row)

            for mode, items in clusters.items():
                if len(items) < min_occurrences:
                    continue

                sample_causes = "; ".join(
                    dict.fromkeys(r["root_cause"] for r in items if r["root_cause"])
                )[:400]
                remediation = items[-1]["proposed_remediation"] or "new_unit_test"

                goal_desc = (
                    f"Optimizer Remediation [{remediation}] for recurring failure mode '{mode}' "
                    f"({len(items)} occurrences). Root causes: {sample_causes}. "
                    "Add a regression unit test in tests/ or refine the relevant prompt/tool schema."
                )

                goal = enqueue_goal(
                    description=goal_desc,
                    source="self",
                    priority=72,
                    actor="reflection_miner",
                    db_path=db_path,
                )

                reflection_ids = [int(r["id"]) for r in items]
                placeholders = ",".join("?" for _ in reflection_ids)
                conn.execute(
                    f"UPDATE reflections SET mined_into_goal_id = ? WHERE id IN ({placeholders})",
                    [goal["id"], *reflection_ids],
                )
                conn.commit()

                log_audit_event(
                    actor="reflection_miner",
                    action="failure_corpus_mined",
                    outcome="success",
                    metadata={
                        "failure_mode": mode,
                        "occurrences": len(items),
                        "reflection_ids": reflection_ids,
                        "generated_goal_id": goal["id"],
                        "remediation_type": remediation,
                    },
                )

                created_goals.append(
                    {
                        "failure_mode": mode,
                        "occurrences": len(items),
                        "reflection_ids": reflection_ids,
                        "goal_id": goal["id"],
                        "remediation": remediation,
                    }
                )

    return created_goals


def get_reflections_for_review(
    limit: int = 50,
    unreviewed_only: bool = True,
    failures_only: bool = False,
    db_path: str = DEFAULT_DB_PATH,
) -> List[Dict[str, Any]]:
    """Fetches reflections for the HUD weekly human review surface."""
    init_reflections_db(db_path)
    clauses: List[str] = []
    if unreviewed_only:
        clauses.append("reviewed_by_human = 0")
    if failures_only:
        clauses.append("passed = 0")

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _REFLECTIONS_LOCK:
        with _get_connection(db_path) as conn:
            rows = conn.execute(
                f"SELECT * FROM reflections {where_sql} ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()

    results = []
    for r in rows:
        item = dict(r)
        item["rubric_scores"] = json.loads(item.pop("rubric_scores_json", "{}"))
        results.append(item)
    return results


def mark_reflection_reviewed(
    reflection_id: int,
    actor: str = "user",
    db_path: str = DEFAULT_DB_PATH,
) -> bool:
    """Marks a reflection entry as reviewed during human audit inspection."""
    init_reflections_db(db_path)
    with _REFLECTIONS_LOCK:
        with _get_connection(db_path) as conn:
            cur = conn.execute(
                "UPDATE reflections SET reviewed_by_human = 1 WHERE id = ?",
                (int(reflection_id),),
            )
            conn.commit()
            updated = cur.rowcount > 0

    if updated:
        log_audit_event(
            actor=actor,
            action="reflection_reviewed",
            outcome="success",
            metadata={"reflection_id": int(reflection_id)},
        )
    return updated
