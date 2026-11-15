import datetime
import threading
import time
from modules.ai import ask_local_brain
from modules.logger import log_error, log_info
from modules.tools import get_current_time, get_system_diagnostics
from modules.voice import speak


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

    briefing_text = ask_local_brain(prompt)
    speak(briefing_text, self.hud_callback)

  def _run_scheduler(self):
    """Background loop checking time criteria every 30 seconds."""
    while not self._stop_event.is_set():
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