from modules.memory import get_runtime_state, set_runtime_state
"""Interruptible TTS for JARVIS: Kokoro with pyttsx3 fallback, speech interrupt, and echo suppression."""

import gc
import re
import threading

import sounddevice as sd

from config.settings import LANG_CODE, SPEECH_RATE, VOICE_ID
from modules.logger import log_error, log_info
from modules.memory import get_preference, get_runtime_state, set_runtime_state

IS_SPEAKING = threading.Event()
STOP_SPEECH = threading.Event()
_SPEECH_LOCK = threading.Lock()
VOICE_ENABLED = True

_kokoro_pipeline = None
_pyttsx3_engine = None

_VOICE_STATE_CALLBACKS = []


def register_voice_state_callback(cb) -> None:
    """Register a listener callback invoked with (enabled: bool) whenever voice toggles."""
    if cb not in _VOICE_STATE_CALLBACKS:
        _VOICE_STATE_CALLBACKS.append(cb)


def unload_voice_engine() -> None:
    """Stop active speech and free the Kokoro PyTorch pipeline from RAM/CPU."""
    global _kokoro_pipeline
    stop_speaking()
    if _kokoro_pipeline not in (None, False):
        _kokoro_pipeline = None
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass


def _get_kokoro():
    """Lazily initialize Kokoro pipeline."""
    global _kokoro_pipeline
    if _kokoro_pipeline is None:
        try:
            from kokoro import KPipeline
            _kokoro_pipeline = KPipeline(lang_code=LANG_CODE)
        except Exception as e:
            log_error(f"Kokoro TTS init failed, will use pyttsx3 fallback: {e}")
            _kokoro_pipeline = False
    return _kokoro_pipeline if _kokoro_pipeline is not False else None


def _get_pyttsx3():
    """Lazily initialize pyttsx3 engine."""
    global _pyttsx3_engine
    if _pyttsx3_engine is None:
        try:
            import pyttsx3
            eng = pyttsx3.init()
            eng.setProperty("rate", SPEECH_RATE)
            _pyttsx3_engine = eng
        except Exception as e:
            log_error(f"pyttsx3 fallback init failed: {e}")
            _pyttsx3_engine = False
    return _pyttsx3_engine if _pyttsx3_engine is not False else None


def set_voice_enabled(enabled: bool) -> bool:
    """Globally enable or disable spoken responses, and keep runtime state synchronized."""
    global VOICE_ENABLED
    VOICE_ENABLED = bool(enabled)
    set_runtime_state("voice_enabled", VOICE_ENABLED)
    if not bool(enabled):
        unload_voice_engine()
    for _cb in list(_VOICE_STATE_CALLBACKS):
        try:
            _cb(bool(enabled))
        except Exception:
            pass
    return VOICE_ENABLED


def get_voice_enabled() -> bool:
    """Return the global voice state from the runtime source of truth."""
    global VOICE_ENABLED
    runtime_value = get_runtime_state("voice_enabled")
    if runtime_value is not None:
        VOICE_ENABLED = str(runtime_value).strip().lower() in {"1", "true", "yes", "on"}
        return VOICE_ENABLED
    saved_value = get_preference("voice_enabled", str(VOICE_ENABLED).lower())
    VOICE_ENABLED = str(saved_value).strip().lower() in {"1", "true", "yes", "on"}
    set_runtime_state("voice_enabled", VOICE_ENABLED)
    return VOICE_ENABLED


def stop_speaking() -> None:
    """Immediately interrupt any active TTS playback."""
    STOP_SPEECH.set()
    try:
        sd.stop()
    except Exception:
        pass
    eng = _get_pyttsx3()
    if eng is not None:
        try:
            eng.stop()
        except Exception:
            pass
    IS_SPEAKING.clear()
    log_info("Speech playback interrupted by user.")


def _clean_for_speech(text: str) -> str:
    """Strip markdown symbols and code blocks so TTS reads naturally."""
    cleaned = re.sub(r"```.*?```", " Code block omitted. ", text, flags=re.DOTALL)
    cleaned = re.sub(r"[*#`_~>\[\]]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def speak(text: str, hud_callback=None) -> None:
    """Speak text aloud via Kokoro TTS (interruptible) with automatic pyttsx3 fallback."""
    spoken = _clean_for_speech(str(text or ""))
    if not spoken:
        return

    STOP_SPEECH.clear()

    def _run():
        with _SPEECH_LOCK:
            IS_SPEAKING.set()
            if hud_callback:
                hud_callback("SPEAKING", "speaking", text)
            try:
                pipeline = _get_kokoro()
                if pipeline is not None:
                    generator = pipeline(spoken, voice=VOICE_ID, speed=1.0)
                    for _, _, audio in generator:
                        if STOP_SPEECH.is_set():
                            break
                        sd.play(audio, samplerate=24000)
                        while sd.get_stream().active:
                            if STOP_SPEECH.is_set():
                                sd.stop()
                                break
                            sd.sleep(50)
                else:
                    eng = _get_pyttsx3()
                    if eng is not None and not STOP_SPEECH.is_set():
                        eng.say(spoken)
                        eng.runAndWait()
            except Exception as e:
                log_error(f"TTS playback error: {e}")
            finally:
                IS_SPEAKING.clear()
                if hud_callback:
                    hud_callback("STANDBY // READY", "idle", text)

    threading.Thread(target=_run, daemon=True).start()
