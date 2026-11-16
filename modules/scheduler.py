import datetime
import os
import threading
import time
from typing import Any, Callable, Dict

from modules.ai import ask_local_brain
from modules.budget import check_budget, record_budget_usage
from modules.logger import log_error, log_info
from modules.tools import get_current_time, get_system_diagnostics


class JarvisScheduler:

  def __init__(self, briefing_hour=7, briefing_minute=0, hud_callback=None):
    self.briefing_hour = briefing_hour
    self.briefing_minute = briefing_minute
    self.hud_callback = hud_callback
    self._stop_event = threading.Event()
    self.thread = threading.Thread(
        target=self._run_scheduler, daemon=True, name="JarvisSchedulerThread"
    )

  def start(self):
    """Starts the background scheduler loop."""
    log_info("Starting JARVIS background event scheduler...")
    self.thread.start()

  def stop(self):
    """Stops the background scheduler thread."""
    self._stop_event.set()

  def trigger_morning_briefing(self):
    """Gathers data, queries local LLM, and speaks the morning briefing."""
    log_info("Triggering automated morning briefing...")

    if self.hud_callback:
      self.hud_callback(
          "MORNING BRIEFING", "thinking", "Synthesizing morning report..."
      )

    # Gather system telemetry & time context
    current_time = get_current_time()
    diagnostics = get_system_diagnostics()

    prompt = (
        f"Act as J.A.R.V.I.S., Tony Stark's executive AI butler. Give Sir a"
        f" concise, sharp, and witty morning status report. Today's time is"
        f" {current_time}. System telemetry: {diagnostics}. Mention that"
        f" systems are operating at peak efficiency, and outline a productive"
        f" mindset for the day. Keep it natural for voice output, avoid"
        f" markdown symbols or emojis, and address the user as Sir."
    )

    briefing_text = ask_local_brain(prompt, force_provider="ollama")
    from modules.voice import speak
    speak(briefing_text, self.hud_callback)

  def _run_scheduler(self):
    """Background loop checking time criteria every 30 seconds."""
    from modules.memory import get_preference
    while not self._stop_event.is_set():
      # Skip briefing if scheduler is disabled by user preference
      enabled = get_preference("scheduler_enabled", "true")
      if str(enabled).strip().lower() in ("0", "false", "no", "off"):
        time.sleep(30)
        continue
      now = datetime.datetime.now()
      # Check if it matches the target morning hour and minute
      if (
          now.hour == self.briefing_hour
          and now.minute == self.briefing_minute
      ):
        try:
          self.trigger_morning_briefing()
          # Sleep for 65 seconds so it doesn't trigger multiple times in the same minute
          time.sleep(65)
        except Exception as e:
          log_error(f"Error in automated briefing scheduler: {e}")

      time.sleep(30)


def process_one_idle_goal(
    step_executor: Callable[[Dict[str, Any], int, Dict[str, Any]], Dict[str, Any]],
    max_steps: int = 8,
    db_path: str = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "jarvis_memory.db"),
) -> Dict[str, Any]:
    """
    Picks one pending goal from the goals database and runs it through the
    step_executor (typically modules/planner.planned_step_executor) until the
    goal completes, fails, or exhausts its step budget.

    The step_executor is called once per loop iteration with
    (goal, step_index, checkpoint) and is expected to mutate `checkpoint` in
    place and return {"step_done": bool, "goal_complete": bool, "step_output": str}.
    """
    from modules.goals import get_goal, update_goal, enqueue_goal
    from modules.reflections import evaluate_task_rubric, mine_failure_corpus

    import os as _os
    import json as _json

    project_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    default_db = _os.path.join(project_root, "data", "jarvis_memory.db")
    if db_path is None:
        db_path = default_db

    # Import sqlite here to avoid coupling at module load time
    import sqlite3 as _sqlite3

    def _get_connection(db_path: str) -> _sqlite3.Connection:
        _os.makedirs(_os.path.dirname(_os.path.abspath(db_path)), exist_ok=True)
        conn = _sqlite3.connect(db_path)
        conn.row_factory = _sqlite3.Row
        return conn

    # Ensure schema exists
    from modules.goals import init_goals_db, is_stopped
    init_goals_db(db_path)

    # Pre-flight RAM guardrail: skip heavy work when memory is low
    try:
        from modules.diagnostics import check_memory_headroom
        from modules.preferences import get_runtime_settings

        min_mb = int(get_runtime_settings().get("min_available_ram_mb", 1200))
        ok, avail_mb = check_memory_headroom(min_available_mb=min_mb)
        if not ok:
            log_error(f"[SCHEDULER] Skipping background tick: low available RAM ({avail_mb:.0f} MB)")
            return {"ran": False, "status": "low_ram", "steps_executed": 0}
    except Exception:
        pass

    # Skip processing if the stop flag is set
    if is_stopped():
        return {"ran": False, "status": "stopped", "steps_executed": 0}

    # Pick the highest-priority pending goal
    with _get_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM goals
            WHERE status = 'pending'
            ORDER BY priority DESC, created_at ASC
            LIMIT 1
            """
        ).fetchone()

    if not row:
        # Mine the failure corpus for recurring patterns during idle cycles
        try:
            mined = mine_failure_corpus(db_path=db_path)
            if mined:
                log_info(f"Mined {len(mined)} recurring failure mode(s) into self-improvement goals.")
        except Exception as e:
            log_error(f"Failure corpus mining failed: {e}")
        return {"ran": False, "status": "no_idle_goal", "steps_executed": 0}

    goal = dict(row)
    goal["checkpoint"] = _json.loads(goal.get("checkpoint_json") or "{}")
    goal_id = goal["id"]

    # Mark goal as running
    update_goal(goal_id, status="running", db_path=db_path)

    steps_executed = 0
    checkpoint = goal["checkpoint"]

    try:
        for step_index in range(max_steps):
            result = step_executor(goal=goal, step_index=step_index, checkpoint=checkpoint)
            steps_executed += 1

            # Persist checkpoint after every step
            update_goal(goal_id, checkpoint=checkpoint, db_path=db_path)

            if result.get("goal_complete"):
                update_goal(goal_id, status="completed", db_path=db_path)
                try:
                    last_output = (
                        checkpoint.get("completed_steps", [{}])[-1].get("output", "")
                        if checkpoint.get("completed_steps")
                        else result.get("step_output", "")
                    )
                except Exception:
                    last_output = result.get("step_output", "")
                evaluate_task_rubric(
                    goal_id=goal_id,
                    task_description=goal["description"],
                    output=str(last_output),
                    execution_error=None,
                    db_path=db_path,
                )
                # Mine the failure corpus after goal completion
                try:
                    mined = mine_failure_corpus(db_path=db_path)
                    if mined:
                        log_info(f"Mined {len(mined)} recurring failure mode(s) into self-improvement goals.")
                except Exception as e:
                    log_error(f"Failure corpus mining failed: {e}")
                return {
                    "ran": True,
                    "status": "completed",
                    "steps_executed": steps_executed,
                }

            if not result.get("step_done"):
                # Replan happened; continue looping with updated checkpoint
                continue

    except RuntimeError as exc:
        err_msg = str(exc)
        if "Budget exhausted" in err_msg:
            update_goal(
                goal_id,
                status="paused_budget",
                last_error=err_msg,
                checkpoint=checkpoint,
                db_path=db_path,
            )
            record_budget_usage("failure", actor="autonomous_planner", goal_id=goal_id, amount=1, db_path=db_path)
            # Mine the failure corpus after budget pause
            try:
                mined = mine_failure_corpus(db_path=db_path)
                if mined:
                    log_info(f"Mined {len(mined)} recurring failure mode(s) into self-improvement goals.")
            except Exception as e:
                log_error(f"Failure corpus mining failed: {e}")
            return {
                "ran": True,
                "status": "paused_budget",
                "steps_executed": steps_executed,
                "error": err_msg,
            }
        # Max replans exceeded or other runtime failure
        update_goal(
            goal_id,
            status="failed",
            last_error=err_msg,
            checkpoint=checkpoint,
            db_path=db_path,
        )
        try:
            last_output = (
                checkpoint.get("completed_steps", [{}])[-1].get("output", "")
                if checkpoint.get("completed_steps")
                else ""
            )
        except Exception:
            last_output = ""
        evaluate_task_rubric(
            goal_id=goal_id,
            task_description=goal["description"],
            output=str(last_output),
            execution_error=err_msg,
            db_path=db_path,
        )
        # Mine the failure corpus after goal failure
        try:
            mined = mine_failure_corpus(db_path=db_path)
            if mined:
                log_info(f"Mined {len(mined)} recurring failure mode(s) into self-improvement goals.")
        except Exception as e:
            log_error(f"Failure corpus mining failed: {e}")
        return {
            "ran": True,
            "status": "failed",
            "steps_executed": steps_executed,
            "error": err_msg,
        }

    # Exhausted max_steps without completion
    update_goal(
        goal_id,
        status="pending",
        checkpoint=checkpoint,
        db_path=db_path,
    )

    # Mine the failure corpus for recurring patterns after every idle cycle
    try:
        mined = mine_failure_corpus(db_path=db_path)
        if mined:
            log_info(f"Mined {len(mined)} recurring failure mode(s) into self-improvement goals.")
    except Exception as e:
        log_error(f"Failure corpus mining failed: {e}")

    return {
        "ran": True,
        "status": "pending",
        "steps_executed": steps_executed,
    }


def run_reactive_tick(db_path: str = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "jarvis_memory.db")) -> dict[str, Any]:
    """Runs a reactive tick: mines the failure corpus for recurring patterns and checks budget status.

    Intended to be called periodically from the scheduler loop or jarvis.py main loop.
    """
    from modules.reflections import mine_failure_corpus

    results: dict[str, Any] = {
        "mined_goals": [],
        "budget_ok": True,
    }

    # Pre-flight RAM guardrail
    try:
        from modules.diagnostics import check_memory_headroom
        from modules.preferences import get_runtime_settings

        min_mb = int(get_runtime_settings().get("min_available_ram_mb", 1200))
        ok, avail_mb = check_memory_headroom(min_available_mb=min_mb)
        if not ok:
            log_error(f"[SCHEDULER] Skipping background tick: low available RAM ({avail_mb:.0f} MB)")
            return {"ran": False, "status": "low_ram", "mined_goals": [], "budget_ok": True}
    except Exception:
        pass

    # Proactive maintenance triggers (disk/backup guard + daily digest)
    try:
        from modules.triggers import run_daily_health_digest, run_disk_and_backup_guard

        guard = run_disk_and_backup_guard()
        results["disk_guard"] = guard
        digest = run_daily_health_digest()
        results["digest"] = digest
    except Exception as e:
        log_error(f"Reactive tick proactive triggers failed: {e}")

    # Self-update detection: prompt once per revision when code/config changed
    try:
        from modules.update_watcher import check_and_request_restart

        check_and_request_restart(check_remote=True)
    except Exception as exc:
        print(f"[UPDATE] Watcher check skipped: {exc}")

    try:
        mined = mine_failure_corpus(db_path=db_path)
        results["mined_goals"] = mined
        if mined:
            log_info(f"Reactive tick: mined {len(mined)} recurring failure mode(s).")
    except Exception as e:
        log_error(f"Reactive tick failure corpus mining failed: {e}")

    try:
        budget = check_budget(actor="system", db_path=db_path)
        results["budget_ok"] = budget.get("allowed", True)
        if not results["budget_ok"]:
            log_error(f"Reactive tick: budget tripped — {budget.get('reason', 'unknown')}")
    except Exception as e:
        log_error(f"Reactive tick budget check failed: {e}")
        results["budget_ok"] = False

    return results


if __name__ == "__main__":
    pass
