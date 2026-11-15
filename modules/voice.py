import threading
from config.settings import LANG_CODE, SPEECH_RATE, VOICE_ID

try:
  from kokoro import KPipeline
except ImportError:  # pragma: no cover - optional audio dependency
  KPipeline = None

try:
  import pyttsx3
except ImportError:  # pragma: no cover - optional TTS dependency
  pyttsx3 = None

try:
  import sounddevice as sd
except ImportError:  # pragma: no cover - optional audio playback dependency
  sd = None

from modules.logger import log_error, log_info
from modules.memory import get_preference, get_runtime_state, set_runtime_state

VOICE_ENABLED = True


def _coerce_bool(value):
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)

# Initialize Kokoro Pipeline
log_info("Initializing Kokoro-82M British Neural Voice Engine...")
try:
  kokoro_pipeline = KPipeline(lang_code=LANG_CODE) if KPipeline else None
except Exception as e:
  log_error(f"Failed to initialize Kokoro pipeline: {e}")
  kokoro_pipeline = None

# Fallback system TTS
if pyttsx3 is not None:
  fallback_engine = pyttsx3.init()
  fallback_engine.setProperty("rate", SPEECH_RATE)
else:
  fallback_engine = None

speech_lock = threading.Lock()


def set_voice_enabled(enabled: bool):
  """Globally enable or disable spoken responses, and keep runtime state synchronized."""
  global VOICE_ENABLED
  VOICE_ENABLED = bool(enabled)
  set_runtime_state("voice_enabled", VOICE_ENABLED)
  return VOICE_ENABLED


def get_voice_enabled() -> bool:
  """Return the global voice state from the runtime source of truth."""
  global VOICE_ENABLED

  runtime_value = get_runtime_state("voice_enabled")
  if runtime_value is not None:
    VOICE_ENABLED = _coerce_bool(runtime_value)
    return VOICE_ENABLED

  saved_value = get_preference("voice_enabled", str(VOICE_ENABLED).lower())
  VOICE_ENABLED = _coerce_bool(saved_value)
  set_runtime_state("voice_enabled", VOICE_ENABLED)
  return VOICE_ENABLED


def sync_voice_state_from_settings():
  """Ensure persisted settings and runtime voice state stay aligned."""
  saved_value = get_preference("voice_enabled", str(VOICE_ENABLED).lower())
  return set_voice_enabled(_coerce_bool(saved_value))


def speak(text: str, hud_callback=None):
  """Speaks text aloud using local Kokoro-82M British voice synthesis."""
  if not VOICE_ENABLED:
    print(f"\n[VOICE MUTED] JARVIS: {text}")
    if hud_callback:
      hud_callback("VOICE MUTED", "idle", text)
    return

  print(f"\nJARVIS: {text}")
  if hud_callback:
    hud_callback("SPEAKING", "speaking", text)

  with speech_lock:
    try:
      if kokoro_pipeline and sd is not None:
        generator = kokoro_pipeline(text, voice=VOICE_ID, speed=1.0)
        for _, _, audio in generator:
          sd.play(audio, samplerate=24000)
          sd.wait()
      elif fallback_engine is not None:
        fallback_engine.say(text)
        fallback_engine.runAndWait()
      else:
        print(f"[VOICE UNAVAILABLE] JARVIS: {text}")
    except Exception as e:
      log_error(f"Kokoro audio error: {e}. Falling back to system TTS.")
      if fallback_engine is not None:
        fallback_engine.say(text)
        fallback_engine.runAndWait()
      else:
        print(f"[VOICE UNAVAILABLE] JARVIS: {text}")

  if hud_callback:
    hud_callback("STANDBY // LISTENING", "idle")