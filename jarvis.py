"""J.A.R.V.I.S. Autonomous Core - Desktop entry point with HUD, CLI, wake-word listener, and speech interrupt."""

import re
import sys
import threading
import time

import customtkinter as ctk

from config.settings import SAFE_MODE
from modules.ai import ask_local_brain_with_tools
from modules.goals import recover_interrupted_goals
from modules.listener import ContinuousVoiceListener
from modules.logger import log_error, log_info
from modules.memory import init_db, recall_memories, save_interaction, save_memory
from modules.scheduler import JarvisScheduler
from modules.tools import hydrate_dynamic_tools
from modules.ui import JarvisHUD
from modules.voice import speak, stop_speaking, get_voice_enabled, register_voice_state_callback
from pynput import keyboard

hud_instance = None
scheduler = None
voice_listener = None


def process_command(query: str, hud_callback=None):
    """Autonomous agent router leveraging multi-step LLM tool-calling and memory integration."""
    global hud_instance, scheduler
    q_lower = query.lower().strip()
    if not q_lower:
        return ""

    if hud_callback:
        hud_callback("PROCESSING", "thinking", f"Autonomous analysis: '{query}'")

    speak_allowed = True
    if hud_instance and hasattr(hud_instance, "voice_enabled"):
        speak_allowed = hud_instance.voice_enabled

    response_text = ""
    try:
        # 0. Immediate speech stop command
        if q_lower in ("stop", "quiet", "silence", "shut up", "stop talking"):
            stop_speaking()
            response_text = "Standing by, Sir."
            if hud_callback:
                hud_callback("STANDBY // READY", "idle", response_text)
            return response_text

        # 1. Specialized Memory Saving Trigger
        remember_match = re.match(
            r"^(?:jarvis[, ]+)?(?:remember(?: that)?|save memory[: ]+)\s+(.+)$",
            query.strip(),
            re.IGNORECASE,
        )
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
                response_text = (
                    "\n".join(
                        [
                            f"[{ts}] {q} -> {r}" if isinstance(r, str) else str(item)
                            for item in memories
                            for ts, q, r in [item]
                            if len(item) == 3
                        ]
                    )
                    if memories and len(memories[0]) == 3
                    else str(memories)
                )
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

        # 4. Autonomous 5-Step Agent Loop
        else:
            response_text = ask_local_brain_with_tools(query)
            if speak_allowed:
                speak(response_text, hud_callback)

        if hud_callback:
            hud_callback("STANDBY // READY", "idle", response_text)

        save_interaction(query, response_text)
        return response_text

    except Exception as e:
        err_msg = f"Autonomous error: {e}"
        log_error(err_msg)
        if speak_allowed:
            speak("I encountered an error processing that request, Sir.", hud_callback)
        if hud_callback:
            hud_callback("SYSTEM ERROR", "idle", err_msg)
        save_interaction(query, err_msg)
        return err_msg


def setup_hotkeys():
    """Register global hotkeys: Ctrl+Alt+J toggles HUD, Ctrl+Alt+S stops speech."""

    def on_toggle_hud():
        if hud_instance:
            hud_instance.root.after(0, hud_instance.toggle_hud)

    def on_stop_speech():
        stop_speaking()

    try:
        hotkey = keyboard.GlobalHotKeys(
            {
                "<ctrl>+<alt>+j": on_toggle_hud,
                "<ctrl>+<alt>+s": on_stop_speech,
            }
        )
        hotkey.start()
    except Exception as e:
        log_error(f"Failed to start global hotkeys: {e}")


def run_cli_loop():
    """Interactive terminal loop for typing commands directly to JARVIS."""
    print("\n=== J.A.R.V.I.S. Terminal Interface Online (type 'exit' to quit) ===")
    while True:
        try:
            user_input = input("\nYou > ").strip()
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit"):
                stop_speaking()
                break
            reply = process_command(user_input)
            print(f"\nJARVIS > {reply}")
        except (KeyboardInterrupt, EOFError):
            stop_speaking()
            break


if __name__ == "__main__":
    log_info("Booting J.A.R.V.I.S. Autonomous Core...")
    init_db()

    # Recover any goals left in a non-terminal state from a previous run
    try:
        recovered = recover_interrupted_goals()
        if recovered:
            log_info(f"Recovered {recovered} interrupted goal(s) back to pending.")
    except Exception as e:
        log_error(f"Goal recovery failed: {e}")

    # Hydrate previously synthesized dynamic tools into the live registry
    try:
        loaded = hydrate_dynamic_tools()
        if loaded:
            log_info(f"Hydrated {loaded} dynamic tool(s) into registry.")
    except Exception as e:
        log_error(f"Dynamic tool hydration failed: {e}")

    if "--cli" in sys.argv:
        if get_voice_enabled():
            voice_listener = ContinuousVoiceListener(command_callback=process_command)
            voice_listener.start()
        run_cli_loop()
        sys.exit(0)

    root = ctk.CTk()
    root.title("J.A.R.V.I.S. Quantum HUD")
    hud_instance = JarvisHUD(root, command_callback=process_command)

    try:
        scheduler = JarvisScheduler(
            briefing_hour=7, briefing_minute=0, hud_callback=hud_instance.update_ui
        )
        scheduler.start()
    except Exception as e:
        log_error(f"Scheduler initialization failed: {e}")

    try:
        voice_listener = ContinuousVoiceListener(
            command_callback=process_command,
            hud_callback=hud_instance.update_ui,
        )
        if get_voice_enabled():
            voice_listener.start()
        register_voice_state_callback(voice_listener.set_enabled)
    except Exception as e:
        log_error(f"Voice listener startup warning: {e}")

    setup_hotkeys()

    log_info(
        "JARVIS autonomous agent online. Ctrl+Alt+J toggles HUD | Ctrl+Alt+S stops speech."
    )
    root.mainloop()
