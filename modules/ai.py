import os
import json
import re
import shutil
import subprocess
from datetime import datetime

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - optional dependency for cloud-compatible gateways
    OpenAI = None

try:
    import ollama
except ImportError:  # pragma: no cover - optional dependency for local AI fallback
    ollama = None

try:
    from groq import Groq
except ImportError:  # pragma: no cover - optional dependency for cloud fallback
    Groq = None

try:
    from google import genai
except ImportError:  # pragma: no cover - optional dependency for cloud fallback
    genai = None

from config.settings import MODEL_NAME
from modules.automation import (
    content_idea_bot,
    experiment_review_bot,
    memory_cleanup_bot,
    quality_guard_bot,
    publish_carousel_to_instagram,
    run_free_automation_bots,
)
from modules.memory import (
    get_memory_entry,
    get_recent_reflections,
    get_runtime_state,
    recall_by_category,
    remember,
    search_memories,
    set_runtime_state,
)
from modules.learning import record_interaction, summarize_recent_logs, auto_learn_from_logs
from modules.mind import cognitive_review_loop
from modules.tools import (
    apply_self_improvement,
    browser_automation_task,
    capture_post_analytics,
    create_promotional_flyer,
    edit_file_section,
    generate_brand_concepts,
    generate_social_post_brief,
    get_current_time,
    get_system_diagnostics,
    inspect_recent_logs,
    list_directory,
    log_long_term_memory,
    open_website,
    read_local_file,
    reload_jarvis_module,
    review_social_post_draft,
    rollback_file,
    run_project_tests,
    schedule_social_post,
    search_long_term_memory,
    search_project_files,
    validate_code_syntax,
    web_search,
    write_local_file,
    generate_image,
    web_fetch_and_learn,
)
from modules.workspace_growth import (
    append_workspace_growth_log,
    build_project_memory_index,
    ensure_experiment_directory,
    search_project_memory_index,
    search_workspace_growth_log,
)

# --- PERSISTENT MEMORY INJECTION ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(BASE_DIR, ".env")


def load_env_file():
    """Load environment variables from a local .env file if present."""
    if not os.path.exists(ENV_PATH):
        return

    with open(ENV_PATH, "r", encoding="utf-8") as env_file:
        for line in env_file:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def get_env_value(name: str, default: str = "") -> str:
    """Read a value from environment or a local .env file."""
    load_env_file()
    return os.getenv(name, default)


def load_memory_content():
    """Load persistent assistant memory from memory.md for startup context."""
    memory_path = os.path.join(BASE_DIR, "memory.md")
    if os.path.exists(memory_path):
        try:
            with open(memory_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            return content if content else "No persistent memory found."
        except Exception:
            return "No persistent memory found."
    return "No persistent memory found."


initial_memory = load_memory_content()

# --- CONFIGURATION: API KEYS FROM ENVIRONMENT ---
GROQ_API_KEY = get_env_value("GROQ_API_KEY", "")
CLOUDFLARE_ACCOUNT_ID = get_env_value("CLOUDFLARE_ACCOUNT_ID", "")
CLOUDFLARE_API_TOKEN = get_env_value("CLOUDFLARE_API_TOKEN", "")
KILO_API_KEY = get_env_value("KILO_API_KEY", "")
GEMINI_API_KEY = get_env_value("GEMINI_API_KEY", "")
CLOUD_MODEL_GROQ = get_env_value("GROQ_MODEL", "llama-3.3-70b-versatile")
CLOUDFLARE_MODEL = get_env_value("CLOUDFLARE_MODEL", "@cf/meta/llama-3.3-70b-instruct-fp8-fast")
CLOUD_MODEL_GEMINI = get_env_value("GEMINI_MODEL", "gemini-3.8-flash")

AGY_PATH = shutil.which("agy") or os.path.expandvars(r"%LOCALAPPDATA%\agy\bin\agy.exe")
if not AGY_PATH or not os.path.exists(AGY_PATH):
    AGY_PATH = ""

# Initialize clients dynamically
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY and Groq else None
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY and genai else None

# Define available tools that the models can execute autonomously
AVAILABLE_TOOLS = {
    "get_system_diagnostics": get_system_diagnostics,
    "get_current_time": get_current_time,
    "open_website": open_website,
    "web_search": web_search,
    "browser_automation_task": browser_automation_task,
    "generate_social_post_brief": generate_social_post_brief,
    "generate_brand_concepts": generate_brand_concepts,
    "review_social_post_draft": review_social_post_draft,
    "schedule_social_post": schedule_social_post,
    "capture_post_analytics": capture_post_analytics,
    "create_promotional_flyer": create_promotional_flyer,
    "generate_image": generate_image,
    "web_fetch_and_learn": web_fetch_and_learn,
    "run_free_automation_bots": run_free_automation_bots,
    "publish_carousel_to_instagram": publish_carousel_to_instagram,
    "memory_cleanup_bot": memory_cleanup_bot,
    "experiment_review_bot": experiment_review_bot,
    "quality_guard_bot": quality_guard_bot,
    "content_idea_bot": content_idea_bot,
    "read_local_file": read_local_file,
    "write_local_file": write_local_file,
    "edit_file_section": edit_file_section,
    "list_directory": list_directory,
    "search_project_files": search_project_files,
    "log_long_term_memory": log_long_term_memory,
    "search_long_term_memory": search_long_term_memory,
    "inspect_recent_logs": inspect_recent_logs,
    "validate_code_syntax": validate_code_syntax,
    "run_project_tests": run_project_tests,
    "apply_self_improvement": apply_self_improvement,
    "rollback_file": rollback_file,
    "reload_jarvis_module": reload_jarvis_module,
    "ensure_experiment_directory": ensure_experiment_directory,
    "append_workspace_growth_log": append_workspace_growth_log,
    "build_project_memory_index": build_project_memory_index,
    "search_project_memory_index": search_project_memory_index,
    "search_workspace_growth_log": search_workspace_growth_log,
}

# Tool schemas format for OpenAI-compatible providers (Groq & Ollama)
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_system_diagnostics",
            "description": "Get real-time CPU, RAM, and hardware diagnostic statistics of the host machine.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "Get the current date and local system time.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ensure_experiment_directory",
            "description": "Create a new experiment folder inside the JARVIS project for prototypes, prompts, and testing work.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Experiment label or short name"}
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_workspace_growth_log",
            "description": "Write a model decision to the shared workspace growth journal so the project learns from prior choices.",
            "parameters": {
                "type": "object",
                "properties": {
                    "decision": {"type": "string", "description": "Decision or rationale to record"},
                    "context": {"type": "string", "description": "Optional context or experiment label"},
                },
                "required": ["decision"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "build_project_memory_index",
            "description": "Rebuild the project-wide memory index so JARVIS can search all stored knowledge across the workspace.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_project_memory_index",
            "description": "Search the project-wide memory index for semantically or textually relevant context across the entire JARVIS workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search phrase or context to look for"},
                    "limit": {"type": "integer", "description": "Maximum number of matches to return"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_workspace_growth_log",
            "description": "Search the shared workspace growth log for earlier model decisions and rationale.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search term to match in past decisions"},
                    "limit": {"type": "integer", "description": "How many matching entries to return"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_website",
            "description": "Open a website URL or web platform in the default browser.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL or website name to open"}
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the live web via Tavily for current information, citations, and summaries.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The live web search query to run"},
                    "max_results": {"type": "integer", "description": "Maximum number of results to return (default: 4)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_automation_task",
            "description": "Run a safe, approval-gated browser automation task for a whitelisted destination under JARVIS's main AI control.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Approved website URL to access"},
                    "objective": {"type": "string", "description": "What the browser task should accomplish"},
                    "steps": {"type": ["string", "array"], "description": "Optional short action list or instructions"},
                    "allowlist": {"type": "array", "items": {"type": "string"}, "description": "Optional domain allowlist override"},
                    "confirmed": {"type": "boolean", "description": "Must be true to allow the task to run"},
                },
                "required": ["url", "objective"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_social_post_brief",
            "description": "Draft a branded Instagram-style content pack under JARVIS, including headline, caption, colors, and CTA.",
            "parameters": {
                "type": "object",
                "properties": {
                    "brand_name": {"type": "string", "description": "Brand or company name"},
                    "audience": {"type": "string", "description": "Who the content is meant to reach"},
                    "tone": {"type": "string", "description": "Brand tone, such as premium, bold, calm, or playful"},
                    "platform": {"type": "string", "description": "Target platform, usually instagram"},
                    "colors": {"type": "string", "description": "Primary brand colors"},
                },
                "required": ["brand_name", "audience", "tone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "review_social_post_draft",
            "description": "Move a generated social draft into a review state and require explicit approval before publishing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pack_input": {"type": ["string", "object"], "description": "The drafted content pack or its JSON string"},
                    "approved": {"type": "boolean", "description": "Whether the human has approved the draft"},
                    "review_notes": {"type": "string", "description": "Optional notes from the reviewer"},
                },
                "required": ["pack_input"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_brand_concepts",
            "description": "Generate brand-aware visual concepts for a campaign before publishing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "brand_name": {"type": "string", "description": "Brand or company name"},
                    "audience": {"type": "string", "description": "Target audience"},
                    "tone": {"type": "string", "description": "Brand tone and visual style"},
                    "colors": {"type": "string", "description": "Primary color palette"},
                    "platform": {"type": "string", "description": "Platform to optimize for, usually instagram"},
                },
                "required": ["brand_name", "audience", "tone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "capture_post_analytics",
            "description": "Summarize engagement metrics from a past post so JARVIS can improve future content plans.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reach": {"type": "integer", "description": "Total reach or impressions"},
                    "likes": {"type": "integer", "description": "Likes count"},
                    "comments": {"type": "integer", "description": "Comments count"},
                    "saves": {"type": "integer", "description": "Saves count"},
                    "shares": {"type": "integer", "description": "Shares count"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "learn_from_analytics",
            "description": "Store performance insights in JARVIS memory so the assistant can improve its future content recommendations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "analytics": {"type": ["string", "object"], "description": "The analytics payload or JSON string describing engagement results"},
                    "campaign_name": {"type": "string", "description": "Optional brand or campaign name to associate with the data"},
                },
                "required": ["analytics"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_social_post",
            "description": "Schedule a social post only after the draft has been explicitly approved for publication.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pack_input": {"type": ["string", "object"], "description": "The approved social media pack"},
                    "publish_time": {"type": "string", "description": "Optional scheduled time for the post"},
                },
                "required": ["pack_input"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "publish_carousel_to_instagram",
            "description": "Publish a multi-slide carousel to an Instagram Professional account via Meta's official Graph API.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ig_user_id": {
                        "type": "string",
                        "description": "The Instagram Business or Creator Account ID"
                    },
                    "access_token": {
                        "type": "string",
                        "description": "Meta Graph API long-lived user access token"
                    },
                    "image_urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of public image URLs for the carousel slides (max 10)"
                    },
                    "caption": {
                        "type": "string",
                        "description": "The caption text for the post, including hashtags and call-to-action"
                    }
                },
                "required": ["ig_user_id", "access_token", "image_urls"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_local_file",
            "description": "Read text file contents from the local project directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Path to the file to read"}
                },
                "required": ["filepath"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_local_file",
            "description": "Create a new file or overwrite an existing file inside the project directory. Full read and write permitted.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Path to the file to write"},
                    "content": {"type": "string", "description": "The full text content to write"},
                    "confirmed": {"type": "boolean", "description": "Optional confirmation flag (defaults to true)"},
                },
                "required": ["filepath", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file_section",
            "description": "Replace a specific snippet in an existing file with a new snippet, within the project directory. Full read and write permitted.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Path to the file to edit"},
                    "old_text": {"type": "string", "description": "Exact text snippet to find and replace"},
                    "new_text": {"type": "string", "description": "Replacement text"},
                    "confirmed": {"type": "boolean", "description": "Optional confirmation flag (defaults to true)"},
                },
                "required": ["filepath", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List files and folders in a directory inside the project workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory_path": {"type": "string", "description": "Relative directory path to inspect"}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_project_files",
            "description": "Search the current project directory for files or text matches without leaving the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Keyword or file name to search for"},
                    "directory_path": {"type": "string", "description": "Optional relative folder to search within"},
                    "max_results": {"type": "integer", "description": "Maximum matches to return"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_long_term_memory",
            "description": "Save a structured note, preference, or project milestone to the SQLite database.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "Category like 'preference', 'code_snippet', or 'milestone'"},
                    "content": {"type": "string", "description": "The detailed information to store"},
                },
                "required": ["category", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_long_term_memory",
            "description": "Search past long-term memories in the SQLite database by keyword.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Keyword or topic to search for"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_self_improvement",
            "description": "Safely modify or improve a project file. Automatically validates Python syntax, creates a backup in .backups/, applies changes, executes the project test suite, and automatically rolls back if tests or compilation fail.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Relative or project-scoped path to the file to improve or update"},
                    "new_content": {"type": "string", "description": "The complete updated source code or content to write"},
                    "confirmed": {"type": "boolean", "description": "Optional confirmation flag; defaults to true for full autonomous improvement"},
                },
                "required": ["filepath", "new_content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_recent_logs",
            "description": "Inspect the most recent log entries from logs/jarvis_system.log to diagnose runtime errors, warnings, and tracebacks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_lines": {"type": "integer", "description": "Maximum number of recent log lines to retrieve (default: 50)"}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_code_syntax",
            "description": "Verify Python syntax for an in-memory code snippet or a file on disk before committing changes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code_string": {"type": "string", "description": "Python source code string to validate"},
                    "filepath": {"type": "string", "description": "Path to a python file to validate"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_project_tests",
            "description": "Run the project unit test suite using python unittest to verify that system modules and tests pass.",
            "parameters": {
                "type": "object",
                "properties": {
                    "test_target": {"type": "string", "description": "Test folder or file to execute (defaults to 'tests')"}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rollback_file",
            "description": "Rollback a project file to its previous state using the latest backup from .backups/.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "The path to the file to restore"},
                    "backup_path": {"type": "string", "description": "Optional specific backup file path to restore from"},
                },
                "required": ["filepath"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reload_jarvis_module",
            "description": "Dynamically reload an in-memory Python module (e.g., 'modules.tools', 'modules.ui') into the running runtime without restarting the assistant.",
            "parameters": {
                "type": "object",
                "properties": {
                    "module_name": {"type": "string", "description": "The module import name to reload, e.g. 'modules.tools'"}
                },
                "required": ["module_name"],
            },
        },
    },
]

def sanitize_fallback_messages(messages: list[dict]) -> list[dict]:
    """Strip XML-style system wrappers from fallback model prompts when their tool-calling layer is unavailable."""
    sanitized = []
    for message in messages or []:
        if not isinstance(message, dict):
            sanitized.append(message)
            continue

        updated = dict(message)
        content = str(updated.get("content", "") or "")
        if updated.get("role") == "system":
            cleaned = re.sub(r"<\s*system\s*>.*?<\s*/\s*system\s*>", "", content, flags=re.IGNORECASE | re.DOTALL)
            cleaned = cleaned.replace("<system>", "").replace("</system>", "")
            cleaned = cleaned.strip()
            if not cleaned:
                cleaned = "You are JARVIS. Respond in plain text only when native tool calling is unavailable."
            cleaned += "\n\nImportant: when native function calling is unavailable on this fallback tier, do not emit XML-style system wrapper tags. Use plain text instructions only."
            updated["content"] = cleaned
        sanitized.append(updated)
    return sanitized


# Global continuous conversation history buffer
CONVERSATION_HISTORY = [
    {
        "role": "system",
        "content": (
            "You are JARVIS, an advanced autonomous local/cloud-hybrid AI assistant. "
            "You have access to local file system tools and autonomous self-improvement capabilities within this project workspace. "
            "You are capable of self-diagnosis, error recovery, and self-evolution: "
            "1. Inspect recent logs with inspect_recent_logs or search/read project files with search_project_files and read_local_file. "
            "2. When asked to fix, optimize, or improve yourself, formulate robust enhancements. "
            "3. Use apply_self_improvement(filepath, new_content, confirmed=True) to apply modifications safely. This automatically backs up files, verifies Python syntax, runs unit tests, and rolls back if anything fails. "
            "4. Use reload_jarvis_module to hot-reload updated modules so changes take effect immediately. "
            "5. If a change ever causes issues, use rollback_file to restore the previous working version. "
            "Always address the user respectfully as Sir. "
            "When native function calling is unavailable on a fallback model, do not wrap tool instructions in XML-style system wrapper tags. Use plain text instructions only. "
            "Here is your persistent memory and current operational context from memory.md:\n\n"
            f"{initial_memory}\n\n"
            "Always consult or update memory.md using your tools if the user provides permanent rules or preferences."
        ),
    }
]


def _clean_message_text(message: dict) -> str:
    """Return a plain text view of a message payload."""
    content = message.get("content", "") if isinstance(message, dict) else str(message)
    return str(content).strip()


def extract_user_preferences_from_message(message: str) -> dict:
    """Extract important user preferences from natural-language requests."""
    text = (message or "").strip()
    if not text:
        return {}

    preferences = {}
    lower = text.lower()

    if re.search(r"\b(prefer|like|love|want|need|enjoy)\b", lower):
        matches = re.findall(r"(?:prefer|like|love|want|need|enjoy)\s+(?:to\s+)?([^.;!?]+)", text, re.IGNORECASE)
        if matches:
            preferences["preference_summary"] = " ".join(part.strip() for part in matches if part.strip())

    colors = []
    if re.search(r"\bblue\b|\bgreen\b|\bred\b|\bblack\b|\bwhite\b|\bgold\b|\bpurple\b|\borange\b|\bpink\b", lower):
        colors = re.findall(r"\b(?:blue|green|red|black|white|gold|purple|orange|pink)\b", text, re.IGNORECASE)
        if colors:
            preferences["colors"] = ", ".join(dict.fromkeys(color.lower() for color in colors))

    styles = []
    if re.search(r"\b(premium|luxury|minimal|calm|bold|modern|playful|elegant|clean)\b", lower):
        styles = re.findall(r"\b(?:premium|luxury|minimal|calm|bold|modern|playful|elegant|clean)\b", text, re.IGNORECASE)

    if styles or colors:
        combined = [style.lower() for style in styles] + [color.lower() for color in colors]
        if combined:
            preferences["visual_style"] = ", ".join(dict.fromkeys(combined))

    if re.search(r"\b(branding|logo|caption|landing page|social media|website|design|product)\b", lower):
        preferences["interest_area"] = "creative design"

    return preferences


def save_user_profile_memory(message: str) -> dict:
    """Persist user preferences into a JSON profile for ongoing personalization."""
    existing = {}
    raw_profile = get_memory_entry("user_profile", "")
    if raw_profile:
        try:
            existing = json.loads(raw_profile)
        except Exception:
            existing = {}

    new_preferences = extract_user_preferences_from_message(message)
    if new_preferences:
        for key, value in new_preferences.items():
            existing[key] = value

    remember("profile", "user_profile", json.dumps(existing, ensure_ascii=False))
    return existing


def track_topic_context(topic: str, details: str):
    """Track knowledge by subject so later queries can recall context by topic."""
    cleaned_topic = (topic or "general").strip().lower().replace(" ", "_") or "general"
    key = f"topic_context:{cleaned_topic}"
    existing = {}
    raw = get_memory_entry(key, "")
    if raw:
        try:
            existing = json.loads(raw)
        except Exception:
            existing = {}

    details_text = (details or "").strip()
    if details_text:
        history = existing.get("history", [])
        if details_text not in history:
            history.append(details_text)
        existing["history"] = history[-8:]

    remember("topic_context", key, json.dumps(existing, ensure_ascii=False))
    return existing


def get_relevant_memory_context(query: str) -> str:
    """Pull a concise set of profile, semantic, topic, and performance memories relevant to the current request."""
    if not query:
        return ""

    memory_lines = []

    profile_raw = get_memory_entry("user_profile", "")
    if profile_raw:
        try:
            profile = json.loads(profile_raw)
            if profile:
                memory_lines.append("User profile: " + "; ".join(f"{k}={v}" for k, v in profile.items()))
        except Exception:
            pass

    convo_summary = get_memory_entry("conversation_summary", "")
    if convo_summary:
        memory_lines.append("Conversation summary: " + convo_summary)

    analytics_rows = recall_by_category("analytics", limit=5)
    for value, _ in analytics_rows:
        if isinstance(value, str) and len(value) > 5:
            memory_lines.append("Analytics memory: " + value)

    reflection_rows = get_recent_reflections(limit=5)
    for _, category, query_text, raw_response, final_response, critique in reflection_rows:
        summary = critique or final_response or raw_response or ""
        if isinstance(summary, str) and len(summary.strip()) > 5:
            memory_lines.append(f"Reflection memory ({category}): {query_text[:120]} -> {summary[:220]}")

    semantic_matches = []
    try:
        from modules.vector_memory import semantic_search, summarize_semantic_matches
        semantic_matches = semantic_search(query, limit=4)
        if semantic_matches:
            memory_lines.append(summarize_semantic_matches(query, semantic_matches, limit=3))
    except Exception:
        semantic_matches = []

    lower_query = query.lower()
    for category in ["topic_context", "profile", "analytics"]:
        matches = search_memories(lower_query, limit=10)
        for _, value, _ in matches:
            if isinstance(value, str) and len(value) > 3:
                memory_lines.append(value)

    # Build a compact retrieval summary so the active prompt remains small and useful.
    deduped = []
    seen = set()
    for line in memory_lines:
        key = line.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(key)

    context = "\n".join(deduped[:12]).strip()
    return context[:1800]


def _normalize_analytics_payload(analytics: dict | str) -> dict:
    """Normalize a raw analytics payload into a consistent dictionary."""
    if isinstance(analytics, str):
        stripped = analytics.strip()
        if not stripped:
            return {}
        try:
            analytics = json.loads(stripped)
        except json.JSONDecodeError:
            return {"summary": stripped}
    if not isinstance(analytics, dict):
        return {}
    return analytics


def learn_from_analytics(analytics: dict | str, campaign_name: str | None = None) -> str:
    """Persist analytics results and convert them into reusable content strategy memory for future prompts."""
    payload = _normalize_analytics_payload(analytics)
    if not payload:
        return "No analytics data was provided, so no learning memory was stored."

    brand_name = str(payload.get("brand_name") or campaign_name or payload.get("campaign") or "brand").strip() or "brand"
    platform = str(payload.get("platform") or "instagram").strip() or "instagram"
    summary = str(payload.get("summary") or "Performance summary not provided.").strip()
    reach = payload.get("reach", 0)
    likes = payload.get("likes", 0)
    comments = payload.get("comments", 0)
    saves = payload.get("saves", 0)
    shares = payload.get("shares", 0)
    engagement_rate = payload.get("engagement_rate")
    engagement_score = payload.get("engagement_score")

    if engagement_rate is None:
        try:
            engagement_rate = round(((int(likes) + int(comments) + int(saves) + int(shares)) / max(int(reach), 1) * 100), 2)
        except Exception:
            engagement_rate = 0.0
    if engagement_score is None:
        try:
            engagement_score = round((int(likes) + int(comments) + int(saves) + int(shares)) / 200, 2)
        except Exception:
            engagement_score = 0.0

    learning_record = {
        "brand_name": brand_name,
        "platform": platform,
        "reach": int(reach),
        "likes": int(likes),
        "comments": int(comments),
        "saves": int(saves),
        "shares": int(shares),
        "engagement_rate": float(engagement_rate),
        "engagement_score": float(engagement_score),
        "summary": summary,
        "recorded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    key = f"analytics:{brand_name.lower().replace(' ', '_')}:{learning_record['recorded_at'].replace(' ', '_')}"
    remember("analytics", key, json.dumps(learning_record, ensure_ascii=False))

    profile = {}
    profile_raw = get_memory_entry("user_profile", "")
    if profile_raw:
        try:
            profile = json.loads(profile_raw)
        except Exception:
            profile = {}

    profile["last_engagement_summary"] = (
        f"{brand_name} on {platform}: engagement score {engagement_score}, engagement rate {engagement_rate}%."
    )
    if float(engagement_rate) >= 8.0:
        profile["content_strategy_hint"] = (
            f"Strong performer: keep the premium, bold visual style and clear CTA for {brand_name} on {platform}."
        )
    else:
        profile["content_strategy_hint"] = (
            f"Needs refinement: test a clearer hook, stronger CTA, and more consistent brand colors for {brand_name} on {platform}."
        )

    remember("profile", "user_profile", json.dumps(profile, ensure_ascii=False))
    track_topic_context(brand_name, summary or f"Engagement score {engagement_score} on {platform}.")

    response = {
        "brand_name": brand_name,
        "platform": platform,
        "engagement_rate": float(engagement_rate),
        "engagement_score": float(engagement_score),
        "summary": summary,
        "memory_saved": True,
        "status": "learning-memory-updated",
    }
    return json.dumps(response, ensure_ascii=False, indent=2)


AVAILABLE_TOOLS["learn_from_analytics"] = learn_from_analytics


def _build_conversation_summary(history: list[dict]) -> str:
    """Create a short compatibility summary of recent user facts so communication remains coherent over long chats."""
    user_facts = []
    for message in history:
        if not isinstance(message, dict):
            continue
        if message.get("role") != "user":
            continue
        content = _clean_message_text(message)
        if not content:
            continue
        user_facts.append(content)

    saved_summary = get_memory_entry("conversation_summary", "")
    if user_facts:
        recent_facts = user_facts[-4:]
        summary = "User context: " + "; ".join(recent_facts)
        remember("profile", "conversation_summary", summary)
        return summary

    return saved_summary or "No prior user preferences captured yet."


def trim_and_summarize_conversation(history: list[dict]) -> list[dict]:
    """Trim long conversation logs and keep a concise summary plus recent turns to preserve continuity."""
    if not history:
        return [{"role": "system", "content": "No conversation context yet."}]

    summary = _build_conversation_summary(history)
    recent_messages = []
    for message in history[-8:]:
        if isinstance(message, dict):
            content = _clean_message_text(message)
            if content:
                recent_messages.append(message)

    final_history = [{"role": "system", "content": summary}]
    final_history.extend(recent_messages)
    return final_history[:12]

def ask_local_brain(query: str) -> str:
    """Standard text generation fallback using local Ollama."""
    if ollama is None:
        return "Local Ollama is not installed or unavailable in this environment."
    try:
        response = ollama.chat(model=MODEL_NAME, messages=sanitize_fallback_messages(CONVERSATION_HISTORY))
        return response["message"]["content"]
    except Exception as e:
        return f"Local brain error: {e}"


def call_antigravity_cli(messages: list) -> str:
    """Route the recent conversation through the local AGY CLI using a compact prompt format."""
    if not AGY_PATH or not os.path.exists(AGY_PATH):
        raise RuntimeError(f"Antigravity CLI not found at {AGY_PATH or 'PATH/LOCALAPPDATA'}")

    cleaned_messages = sanitize_fallback_messages(messages)
    system_prompt = ""
    dialogue = []
    for msg in cleaned_messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if not content:
            continue
        if role == "system":
            system_prompt = content
        elif role == "user":
            dialogue.append(f"User: {content}")
        elif role == "assistant":
            dialogue.append(f"Assistant: {content}")

    recent_dialogue = "\n".join(dialogue[-10:])
    full_prompt = (
        f"System Context:\n{system_prompt}\n\n"
        f"Conversation:\n{recent_dialogue}\n\n"
        "Respond concisely as JARVIS to the latest User message."
    )

    command_variants = [
        [AGY_PATH, "--dangerously-skip-permissions", "--add-dir", BASE_DIR, "-p", full_prompt],
        [AGY_PATH, "--dangerously-skip-permissions", "-p", full_prompt],
        [AGY_PATH, "-p", full_prompt],
        [AGY_PATH, "--prompt", full_prompt],
    ]

    last_error = ""
    for command in command_variants:
        try:
            result = subprocess.run(
                command,
                cwd=BASE_DIR,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=90,
            )
            output = (result.stdout or result.stderr or "").strip()
            if result.returncode == 0 and output:
                return output
            last_error = output or f"agy exited with code {result.returncode}"
            lower_output = output.lower()
            if "not recognized" in lower_output or "unknown option" in lower_output:
                continue
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
            last_error = str(exc)
            continue

    raise RuntimeError(last_error or "Antigravity CLI failed without output.")


def call_cloudflare_workers_ai(messages: list) -> str:
    """Routes chat completions through Cloudflare Workers AI using the OpenAI compatibility layer."""
    if OpenAI is None:
        raise RuntimeError("openai package is not installed; Cloudflare gateway cannot be used.")

    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID")
    api_token = os.getenv("CLOUDFLARE_API_TOKEN")
    if not account_id or not api_token:
        raise RuntimeError("Cloudflare credentials missing in .env")

    client = OpenAI(
        base_url=f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1",
        api_key=api_token,
    )

    response = client.chat.completions.create(
        model=CLOUDFLARE_MODEL,
        messages=sanitize_fallback_messages(messages),
        timeout=30,
    )

    content = response.choices[0].message.content
    return (content or "").strip()


def call_kilo_gateway(messages: list) -> str:
    """Routes chat completions through Kilo AI Gateway's free model pool."""
    if OpenAI is None:
        raise RuntimeError("openai package is not installed; Kilo gateway cannot be used.")

    api_key = os.getenv("KILO_API_KEY") or "anonymous"
    client = OpenAI(
        base_url="https://ai-gateway.kilo.ai/api/v1",
        api_key=api_key,
    )
    response = client.chat.completions.create(
        model="kilo-auto/free",
        messages=sanitize_fallback_messages(messages),
        timeout=30,
    )
    content = response.choices[0].message.content
    return (content or "").strip()


def get_available_providers() -> list[str]:
    """Return the providers that are currently available for this runtime."""
    providers = []
    if groq_client:
        providers.append("groq")
    if CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN:
        providers.append("cloudflare")
    if KILO_API_KEY:
        providers.append("kilo")
    if AGY_PATH and os.path.exists(AGY_PATH):
        providers.append("agy")
    if gemini_client:
        providers.append("gemini")
    if ollama is not None:
        providers.append("ollama")
    return providers


def normalize_provider_name(name: str | None) -> str | None:
    """Map aliases like 'google', 'cf', or 'local' to the canonical provider names used by the router."""
    if not name:
        return None

    key = str(name).strip().lower().replace("-", " ").replace("_", " ")
    aliases = {
        "groq": "groq",
        "grok": "groq",
        "llama": "groq",
        "cloudflare": "cloudflare",
        "cf": "cloudflare",
        "workers": "cloudflare",
        "kilo": "kilo",
        "kilogateway": "kilo",
        "gateway": "kilo",
        "gemini": "gemini",
        "google": "gemini",
        "google ai": "gemini",
        "agy": "agy",
        "antigravity": "agy",
        "google cli": "agy",
        "ollama": "ollama",
        "local": "ollama",
        "local ai": "ollama",
    }

    for alias, provider in aliases.items():
        if key == alias or key.startswith(alias + " ") or key.endswith(" " + alias):
            return provider
    return key if key in {"groq", "cloudflare", "kilo", "gemini", "agy", "ollama"} else None


def set_preferred_provider(provider_name: str | None):
    """Override the provider selection for the current session."""
    normalized = normalize_provider_name(provider_name)
    if normalized is None:
        set_runtime_state("preferred_provider", None)
        return None
    set_runtime_state("preferred_provider", normalized)
    return normalized


def set_active_provider(provider_name: str | None) -> str:
    """Track the provider currently being used or attempted for the active request."""
    if provider_name is None or str(provider_name).strip() == "":
        set_runtime_state("active_provider", "auto")
        return "auto"

    lowered = str(provider_name).strip().lower()
    if lowered in {"auto", "automatic", "default", "none"}:
        set_runtime_state("active_provider", "auto")
        return "auto"

    normalized = normalize_provider_name(provider_name)
    if normalized:
        set_runtime_state("active_provider", normalized)
        return normalized

    set_runtime_state("active_provider", "auto")
    return "auto"


def get_active_provider(default: str = "auto") -> str:
    """Return the current runtime route or the default when no route has been assigned yet."""
    value = get_runtime_state("active_provider", default)
    if value is None or str(value).strip() == "":
        return default
    normalized = normalize_provider_name(value)
    return normalized or default


def clear_preferred_provider():
    """Reset the current session override back to automatic routing."""
    set_runtime_state("preferred_provider", None)
    return None


def get_explicit_provider_override(query: str | None = None) -> str | None:
    """Detect a request-level or session-level manual override like 'provider:gemini' or 'use groq'."""
    override = get_runtime_state("preferred_provider")
    if override:
        return normalize_provider_name(override)

    env_override = os.getenv("AI_PROVIDER_OVERRIDE", "").strip()
    normalized_env = normalize_provider_name(env_override)
    if normalized_env:
        return normalized_env

    q = (query or "").lower()
    if not q:
        return None

    pattern = re.compile(r"(?:provider|model|use|prefer|force|choose|switch)\s*(?:=|:)?\s*(groq|gemini|google|cloudflare|cf|kilo|agy|ollama|local)", re.IGNORECASE)
    match = pattern.search(q)
    if not match:
        return None

    return normalize_provider_name(match.group(1))


def choose_provider_order(query: str, available_providers: list[str] | None = None, preferred_provider: str | None = None) -> list[str]:
    """Reorder providers based on the question, any explicit override, and the model capabilities available for this session."""
    available = list(available_providers) if available_providers else get_available_providers()
    if not available:
        return []

    explicit = normalize_provider_name(preferred_provider or get_explicit_provider_override(query))
    if explicit and explicit in available:
        ordered = [explicit] + [provider for provider in available if provider != explicit]
        return ordered

    if "groq" in available:
        available = ["groq"] + [provider for provider in available if provider != "groq"]

    q = (query or "").lower()
    scores = {provider: 0 for provider in available}

    coding_keywords = (
        "code", "coding", "debug", "debugging", "bug", "fix", "error", "python",
        "javascript", "typescript", "refactor", "syntax", "function", "class",
        "program", "script", "build", "test", "patch", "exception"
    )
    research_keywords = (
        "latest", "current", "news", "today", "recent", "live", "web", "search",
        "research", "market", "weather", "breaking", "upcoming"
    )
    general_chat_keywords = (
        "hello", "hi", "how", "what", "why", "when", "explain", "summarize",
        "brief", "quick", "simple", "chat", "plan", "compare",
    )
    offline_keywords = ("offline", "local", "private", "secure", "safe", "airgapped")

    if any(keyword in q for keyword in coding_keywords):
        scores["groq"] = scores.get("groq", 0) + 12
        scores["gemini"] = scores.get("gemini", 0) + 7
        scores["kilo"] = scores.get("kilo", 0) + 4

    if any(keyword in q for keyword in research_keywords):
        scores["gemini"] = scores.get("gemini", 0) + 10
        scores["agy"] = scores.get("agy", 0) + 9
        scores["kilo"] = scores.get("kilo", 0) + 3

    if any(keyword in q for keyword in general_chat_keywords):
        scores["kilo"] = scores.get("kilo", 0) + 7
        scores["cloudflare"] = scores.get("cloudflare", 0) + 6
        scores["groq"] = scores.get("groq", 0) + 2

    if any(keyword in q for keyword in offline_keywords):
        scores["ollama"] = scores.get("ollama", 0) + 11

    fallback_order = {"groq": 0, "gemini": 1, "kilo": 2, "cloudflare": 3, "agy": 4, "ollama": 5}
    return sorted(available, key=lambda provider: (-scores.get(provider, 0), fallback_order.get(provider, 99)))


def ask_local_brain_with_tools(query: str) -> str:
    """Choose the best available provider for the current request, then fall back only if needed."""
    global CONVERSATION_HISTORY
    save_user_profile_memory(query)
    CONVERSATION_HISTORY.append({"role": "user", "content": query})

    relevant_context = get_relevant_memory_context(query)
    if relevant_context:
        CONVERSATION_HISTORY = [{"role": "system", "content": relevant_context}] + trim_and_summarize_conversation(CONVERSATION_HISTORY)
    else:
        CONVERSATION_HISTORY = trim_and_summarize_conversation(CONVERSATION_HISTORY)

    provider_order = choose_provider_order(query, get_available_providers())
    if not provider_order:
        return "No AI providers are available in this environment. Configure a model key or install Ollama."

    explicit_override = get_explicit_provider_override(query)
    if explicit_override:
        print(f"[ROUTER] Manual provider override active: {explicit_override}")

    print(f"[ROUTER] Request-aware provider order: {provider_order}")

    for provider in provider_order:
        set_active_provider(provider)
        try:
            if provider == "groq":
                print("[ROUTER] Attempting Groq Cloud...")
                response = groq_client.chat.completions.create(
                    model=CLOUD_MODEL_GROQ,
                    messages=CONVERSATION_HISTORY,
                    tools=TOOL_SCHEMAS,
                    tool_choice="auto",
                )
                message = response.choices[0].message

                if message.tool_calls:
                    tool_call = message.tool_calls[0]
                    func_name = tool_call.function.name
                    func_args = json.loads(tool_call.function.arguments or "{}")
                    print(f"[GROQ AGENT] Tool selected: {func_name}")

                    if func_name in AVAILABLE_TOOLS:
                        tool_result = AVAILABLE_TOOLS[func_name](**func_args) if func_args else AVAILABLE_TOOLS[func_name]()
                        CONVERSATION_HISTORY.append({"role": "assistant", "content": message.content, "tool_calls": message.tool_calls})
                        CONVERSATION_HISTORY.append({"role": "tool", "content": str(tool_result), "tool_call_id": tool_call.id})

                        final_response = groq_client.chat.completions.create(
                            model=CLOUD_MODEL_GROQ,
                            messages=CONVERSATION_HISTORY,
                        )
                        reply = final_response.choices[0].message.content
                        reply = cognitive_review_loop(query, reply)
                        CONVERSATION_HISTORY.append({"role": "assistant", "content": reply})
                        return reply

                reply = message.content
                reply = cognitive_review_loop(query, reply)
                CONVERSATION_HISTORY.append({"role": "assistant", "content": reply})
                set_active_provider(provider)
                return reply

            if provider == "cloudflare":
                print("[ROUTER] Attempting Cloudflare Workers AI...")
                reply = call_cloudflare_workers_ai(CONVERSATION_HISTORY)
                if reply:
                    reply = cognitive_review_loop(query, reply)
                    CONVERSATION_HISTORY.append({"role": "assistant", "content": reply})
                    set_active_provider(provider)
                    return reply

            if provider == "kilo":
                print("[ROUTER] Attempting Kilo AI Gateway...")
                reply = call_kilo_gateway(CONVERSATION_HISTORY)
                if reply:
                    reply = cognitive_review_loop(query, reply)
                    CONVERSATION_HISTORY.append({"role": "assistant", "content": reply})
                    set_active_provider(provider)
                    return reply

            if provider == "agy":
                print("[ROUTER] Attempting Google Subscription (Antigravity CLI)...")
                reply = call_antigravity_cli(CONVERSATION_HISTORY)
                if reply:
                    reply = cognitive_review_loop(query, reply)
                    CONVERSATION_HISTORY.append({"role": "assistant", "content": reply})
                    set_active_provider(provider)
                    return reply

            if provider == "gemini":
                print("[ROUTER] Attempting Google Gemini Cloud...")
                gemini_prompt = "\n".join([f"{m['role']}: {m['content']}" for m in CONVERSATION_HISTORY if isinstance(m.get('content'), str)])

                tool_callables = list(AVAILABLE_TOOLS.values())
                config = None
                if hasattr(genai, "types") and hasattr(genai.types, "GenerateContentConfig"):
                    config = genai.types.GenerateContentConfig(
                        tools=tool_callables,
                        system_instruction=CONVERSATION_HISTORY[0]["content"] if CONVERSATION_HISTORY else None,
                    )

                response = gemini_client.models.generate_content(
                    model=CLOUD_MODEL_GEMINI,
                    contents=gemini_prompt,
                    config=config,
                )

                if getattr(response, "function_calls", None):
                    tool_call = response.function_calls[0]
                    fn_name = tool_call.name
                    fn_args = dict(tool_call.args) if tool_call.args else {}
                    print(f"[GEMINI AGENT] Tool selected: {fn_name}")

                    if fn_name in AVAILABLE_TOOLS:
                        tool_result = AVAILABLE_TOOLS[fn_name](**fn_args) if fn_args else AVAILABLE_TOOLS[fn_name]()
                        CONVERSATION_HISTORY.append({"role": "assistant", "content": f"Invoked tool {fn_name}"})
                        CONVERSATION_HISTORY.append({"role": "tool", "content": str(tool_result), "name": fn_name})

                        follow_up = gemini_client.models.generate_content(
                            model=CLOUD_MODEL_GEMINI,
                            contents=[
                                gemini_prompt,
                                response.candidates[0].content,
                                genai.types.Part.from_function_response(
                                    name=fn_name,
                                    response={"result": str(tool_result)},
                                ),
                            ],
                            config=config,
                        )
                        reply = follow_up.text or str(tool_result)
                        reply = cognitive_review_loop(query, reply)
                        CONVERSATION_HISTORY.append({"role": "assistant", "content": reply})
                        return reply

                reply = response.text or "Completed, Sir."
                reply = cognitive_review_loop(query, reply)
                CONVERSATION_HISTORY.append({"role": "assistant", "content": reply})
                set_active_provider(provider)
                return reply

            if provider == "ollama":
                print("[ROUTER] Attempting Local Ollama...")
                if ollama is None:
                    continue

                response = ollama.chat(
                    model=MODEL_NAME,
                    messages=sanitize_fallback_messages(CONVERSATION_HISTORY),
                    tools=TOOL_SCHEMAS,
                )

                if response.get("message", {}).get("tool_calls"):
                    tool_call = response["message"]["tool_calls"][0]
                    func_name = tool_call["function"]["name"]
                    func_args = tool_call["function"]["arguments"]
                    print(f"[LOCAL AGENT] Tool selected: {func_name}")

                    if func_name in AVAILABLE_TOOLS:
                        tool_result = AVAILABLE_TOOLS[func_name](**func_args) if func_args else AVAILABLE_TOOLS[func_name]()
                        CONVERSATION_HISTORY.append(response["message"])
                        CONVERSATION_HISTORY.append({"role": "tool", "content": str(tool_result), "name": func_name})

                        final_response = ollama.chat(model=MODEL_NAME, messages=sanitize_fallback_messages(CONVERSATION_HISTORY))
                        reply = final_response["message"]["content"]
                        reply = cognitive_review_loop(query, reply)
                        CONVERSATION_HISTORY.append({"role": "assistant", "content": reply})
                        return reply

                reply = response["message"]["content"]
                reply = cognitive_review_loop(query, reply)
                CONVERSATION_HISTORY.append({"role": "assistant", "content": reply})
                set_active_provider(provider)
                return reply

        except Exception as exc:
            print(f"[ROUTER WARNING] Provider '{provider}' failed: {exc}. Trying the next best available provider...")

    set_active_provider("auto")
    return "All available providers failed while handling this request. Please try again or add credentials for a stronger model tier."
