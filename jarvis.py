import re
import threading
import time
import customtkinter as ctk
from config.settings import SAFE_MODE
from modules.ai import ask_local_brain_with_tools
from modules.logger import log_error, log_info
from modules.memory import init_db, recall_memories, save_memory, save_interaction
from modules.scheduler import JarvisScheduler
from modules.ui import JarvisHUD
from modules.voice import speak
from pynput import keyboard


def process_command(query: str, hud_callback=None):
  """Autonomous agent router leveraging LLM tool-calling and memory integration."""
  global hud_instance, scheduler
  q_lower = query.lower()

  if hud_callback:
    hud_callback("PROCESSING", "thinking", f"Autonomous analysis: '{query}'")

  # Check if voice feedback is enabled in the HUD settings panel
  speak_allowed = True
  if hud_instance and hasattr(hud_instance, "voice_enabled"):
    speak_allowed = hud_instance.voice_enabled

  response_text = ""
  try:
    # 1. Specialized Memory Saving Trigger (only explicit imperative commands)
    remember_match = re.match(r"^(?:jarvis[, ]+)?(?:remember(?: that)?|save memory[: ]+)\s+(.+)$", query.strip(), re.IGNORECASE)
    if remember_match:
      content = remember_match.group(1).strip()
      result_msg = save_memory(content, category="user_preference")
      response_text = f"I have committed that to long-term memory, Sir. [{result_msg}]"
      if speak_allowed:
        speak("I have committed that to long-term memory, Sir.", hud_callback)

    # 2. Specialized Memory Recall Trigger
    elif "recall" in q_lower or "what do you remember" in q_lower:
      memories = recall_memories()
      if isinstance(memories, list) and memories:
        response_text = "\n".join([f"[{ts}] {q} -> {r}" if isinstance(r, str) else str(item) for item in memories for ts, q, r in [item] if len(item)==3]) if memories and len(memories[0])==3 else str(memories)
      else:
        response_text = str(memories)
      if speak_allowed:
        speak("Here are your stored long-term memories, Sir.", hud_callback)

    # 3. Morning Briefing Trigger
    elif "morning briefing" in q_lower:
      if scheduler:
        threading.Thread(
            target=scheduler.trigger_morning_briefing, daemon=True
        ).start()
        response_text = "Morning briefing initiated."
      else:
        response_text = "Scheduler is not active, Sir."
      if speak_allowed:
        speak(response_text, hud_callback)

    # 4. Autonomous Agent Loop (LLM Tool Calling)
    else:
      response_text = ask_local_brain_with_tools(query)
      if speak_allowed:
        speak(response_text, hud_callback)

    if hud_callback:
      hud_callback("STANDBY // READY", "idle", response_text)

    # Automatically save interaction to database history
    save_interaction(query, response_text)

  except Exception as e:
    err_msg = f"Autonomous error: {e}"
    log_error(err_msg)
    if speak_allowed:
      speak("I encountered an error processing that request, Sir.", hud_callback)
    if hud_callback:
      hud_callback("SYSTEM ERROR", "idle", err_msg)
    save_interaction(query, err_msg)


# Global instances and hotkey setup
hud_instance = None
scheduler = None


def setup_hotkeys():
  def on_activate():
    if hud_instance:
      hud_instance.root.after(0, hud_instance.toggle_hud)

  try:
    hotkey = keyboard.GlobalHotKeys({"<ctrl>+<alt>+j": on_activate})
    hotkey.start()
  except Exception as e:
    log_error(f"Failed to start global hotkeys: {e}")


if __name__ == "__main__":
  log_info("Booting J.A.R.V.I.S. Autonomous Core...")
  init_db()

  root = ctk.CTk()
  root.title("J.A.R.V.I.S. Quantum HUD")
  hud_instance = JarvisHUD(root)

  # Start the background scheduler (Triggers automatically at 7:00 AM)
  try:
    scheduler = JarvisScheduler(
        briefing_hour=7, briefing_minute=0, hud_callback=hud_instance.update_ui
    )
    scheduler.start()
  except Exception as e:
    log_error(f"Scheduler initialization failed: {e}")

  setup_hotkeys()

  log_info(
      "JARVIS autonomous agent online. Press Ctrl+Alt+J to toggle interface."
  )
  root.mainloop()