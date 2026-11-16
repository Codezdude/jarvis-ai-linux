"""Continuous hands-free wake-word listener for JARVIS ("Jarvis" / "Hey Jarvis")."""

import re
import threading
import time

import speech_recognition as sr

from modules.logger import log_error, log_info
from modules.voice import IS_SPEAKING


class ContinuousVoiceListener:
    """Hands-free wake-word listener that pauses automatically while JARVIS is speaking."""

    def __init__(self, command_callback, hud_callback=None, wake_words=("jarvis", "hey jarvis")):
        self.command_callback = command_callback
        self.hud_callback = hud_callback
        self.wake_words = tuple(w.lower() for w in wake_words)
        self.running = False
        self.enabled = True
        self._thread = None
        self.recognizer = sr.Recognizer()
        self.recognizer.dynamic_energy_threshold = True
        self.recognizer.pause_threshold = 0.8

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()
        log_info("Continuous hands-free voice listener started.")

    def stop(self) -> None:
        self.running = False

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable the listener, starting it if newly enabled."""
        self.enabled = bool(enabled)
        if self.enabled and not self.running:
            self.start()

    def _extract_command(self, transcript: str, follow_up_active: bool) -> str | None:
        text = transcript.strip()
        lower = text.lower()

        # Check for wake word prefix
        for wake in sorted(self.wake_words, key=len, reverse=True):
            if lower.startswith(wake):
                stripped = re.sub(rf"^{re.escape(wake)}[,:\s]*", "", text, flags=re.IGNORECASE).strip()
                return stripped if stripped else "Yes, how can I help?"
            if wake in lower:
                idx = lower.find(wake) + len(wake)
                stripped = text[idx:].lstrip(",: ").strip()
                return stripped if stripped else "Yes, how can I help?"

        # Allow follow-up commands within 15 seconds of last interaction without repeating wake word
        if follow_up_active and len(text) > 2:
            return text
        return None

    def _listen_loop(self) -> None:
        last_interaction_ts = 0.0
        try:
            mic = sr.Microphone()
            with mic as source:
                self.recognizer.adjust_for_ambient_noise(source, duration=1.0)
        except Exception as e:
            log_error(f"Microphone initialization failed (voice listener disabled): {e}")
            return

        while self.running:
            if not self.enabled or IS_SPEAKING.is_set():
                time.sleep(0.15)
                continue

            try:
                with sr.Microphone() as source:
                    audio = self.recognizer.listen(source, timeout=2.0, phrase_time_limit=10.0)

                if IS_SPEAKING.is_set():
                    continue

                transcript = self.recognizer.recognize_google(audio)
                if not transcript:
                    continue

                follow_up = (time.time() - last_interaction_ts) < 15.0
                command = self._extract_command(transcript, follow_up)
                if command:
                    last_interaction_ts = time.time()
                    log_info(f"[VOICE] Heard command: {command}")
                    if self.hud_callback:
                        self.hud_callback("VOICE INPUT", "thinking", f"Heard: {command}")
                    threading.Thread(
                        target=self.command_callback,
                        args=(command, self.hud_callback),
                        daemon=True,
                    ).start()

            except sr.WaitTimeoutError:
                continue
            except sr.UnknownValueError:
                continue
            except Exception as e:
                log_error(f"Voice listener loop warning: {e}")
                time.sleep(1.0)
