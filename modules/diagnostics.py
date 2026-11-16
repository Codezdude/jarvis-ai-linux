import datetime
import io
import os
import unittest
from typing import Any, Dict, List, Optional

from modules.audit import log_audit_event
from modules.goals import enqueue_goal

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_full_diagnostics(test_dir: Optional[str] = None) -> Dict[str, Any]:
    """
    Programmatically runs the entire unittest suite and captures output and failures.
    Returns a structured summary dictionary.
    """
    target_dir = test_dir or os.path.join(PROJECT_ROOT, "tests")
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=target_dir)

    stream = io.StringIO()
    runner = unittest.TextTestRunner(stream=stream, verbosity=2)
    start_time = datetime.datetime.now(datetime.timezone.utc)
    result = runner.run(suite)
    duration = (datetime.datetime.now(datetime.timezone.utc) - start_time).total_seconds()

    output = stream.getvalue()
    failures = len(result.failures)
    errors = len(result.errors)
    passed = result.testsRun - (failures + errors)

    failure_details: List[str] = []
    for test, traceback_str in result.failures + result.errors:
        failure_details.append(f"FAIL/ERR in {test}: {traceback_str}")

    success = (failures == 0) and (errors == 0)

    log_audit_event(
        actor="diagnostics_engine",
        action="self_diagnostics_run",
        outcome="success" if success else "error",
        metadata={
            "tests_run": result.testsRun,
            "passed": passed,
            "failures": failures,
            "errors": errors,
            "duration_seconds": round(duration, 3),
        },
    )

    return {
        "success": success,
        "tests_run": result.testsRun,
        "passed": passed,
        "failures": failures,
        "errors": errors,
        "duration_seconds": round(duration, 3),
        "failure_details": failure_details,
        "raw_output": output,
    }


def trigger_self_repair_if_needed(
    diag_result: Optional[Dict[str, Any]] = None,
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Runs diagnostics if not provided. If test failures or errors are detected,
    enqueues an autonomous self-repair goal into modules/goals.py.
    """
    result = diag_result if diag_result is not None else run_full_diagnostics()
    if result["success"]:
        return None

    tracebacks_combined = "\n".join(result["failure_details"])[:2000]
    description = (
        f"Self-Repair Diagnostic Trigger: {result['failures']} test failures, {result['errors']} errors "
        f"detected out of {result['tests_run']} tests.\nTraceback snippet:\n{tracebacks_combined}"
    )

    db_kwargs = {"db_path": db_path} if db_path else {}
    goal = enqueue_goal(
        description=description,
        source="self",
        priority=95,
        actor="diagnostics_engine",
        **db_kwargs,
    )

    log_audit_event(
        actor="diagnostics_engine",
        action="self_repair_goal_triggered",
        outcome="success",
        metadata={"goal_id": goal["id"], "failures": result["failures"], "errors": result["errors"]},
    )

    return {"goal_id": goal["id"], "diagnostics": result}
