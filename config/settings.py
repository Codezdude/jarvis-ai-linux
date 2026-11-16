import os

# --- PATHS ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "memory", "jarvis_memory.db")
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
CODE_DIR = os.path.join(BASE_DIR, "generated_code")

# --- AI CONFIG ---
OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL_NAME = "qwen2.5:7b"

# --- VOICE CONFIG ---
LANG_CODE = "b"
VOICE_ID = "bm_george"
SPEECH_RATE = 185

# --- AUTOMATION SAFETY ---
# Set to False to grant JARVIS full read/write access anywhere inside the project tree.
# The agents are still blocked from escaping the workspace root and writing outside JARVIS.
SAFE_MODE = False

# All model actions are intentionally allowed to read and write inside this project folder
# so the assistant can grow its codebase, memory, assets, and generated output over time.
WORKSPACE_ROOT = BASE_DIR

# --- JARVIS TOOLBELT (Phase 2) ---
JARVIS_DIR = os.path.join(BASE_DIR, ".jarvis")
IG_DIR = os.path.join(JARVIS_DIR, "ig")
IG_EXPORT_PATH = os.getenv("IG_EXPORT_PATH", IG_DIR)
POST_HISTORY_PATH = os.getenv("POST_HISTORY_PATH", os.path.join(IG_DIR, "post_history.json"))
SCHEDULE_PATH = os.getenv("SCHEDULE_PATH", os.path.join(IG_DIR, "schedule.json"))
CANVA_TEMPLATE_ID = os.getenv("CANVA_TEMPLATE_ID", "")
LEONARDO_API_KEY = os.getenv("LEONARDO_API_KEY", "")
PUBLISH_MODE = os.getenv("PUBLISH_MODE", "schedule")
IMAGERY_MODE = os.getenv("IMAGERY_MODE", "illustration")
