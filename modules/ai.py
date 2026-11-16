import os
import json
import re
import shutil
import subprocess
import threading
import time
import functools
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    from openai import OpenAI, APITimeoutError, APIConnectionError
except ImportError:  # pragma: no cover - optional dependency for cloud-compatible gateways
    OpenAI = None
    APITimeoutError = Exception
    APIConnectionError = Exception

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
from modules.logger import log_error
from modules.audit import DecisionTrace, log_audit_event
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
from modules.preferences import format_preferences_for_prompt, set_preference as _set_preference_raw
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
    safe_write_file,
    schedule_social_post,
    search_long_term_memory,
    search_project_files,
    validate_code_syntax,
    web_search,
    write_local_file,
    generate_image,
    web_fetch_and_learn,
    set_system_volume,
    control_linux_app,
    manage_clipboard,
    capture_desktop_screenshot,
    get_all_dynamic_tool_schemas,
    get_dynamic_tool,
    get_relevant_dynamic_tool_schemas,
)
from modules.workspace_growth import (
    append_workspace_growth_log,
    build_project_memory_index,
    ensure_experiment_directory,
    search_project_memory_index,
    search_workspace_growth_log,
)
from modules.self_update import self_update_system, verify_python_syntax

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

AGY_PATH = shutil.which("agy")
if not AGY_PATH or not os.path.exists(AGY_PATH):
    AGY_PATH = ""

# Initialize clients dynamically
groq_client = Groq(api_key=GROQ_API_KEY, timeout=5.0) if GROQ_API_KEY and Groq else None
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY and genai else None

# Bound set_preference for AI-learned heuristics (source="ai", actor="autonomous_ai").
# User preferences always take strict precedence; AI only fills unconfigured gaps.
set_preference_ai = functools.partial(_set_preference_raw, source="ai", actor="autonomous_ai")
functools.update_wrapper(set_preference_ai, _set_preference_raw)


def _safe_tool_name(fn) -> str:
    """Safely resolve a callable's name, handling functools.partial and unknown objects.

    Gemini's ``GenerateContentConfig`` accesses ``__name__`` on each tool callable,
    which fails for ``functools.partial`` objects.  This helper walks the partial
    chain to find the underlying function's name, falling back to ``"unknown_tool"``.
    """
    return getattr(fn, "__name__", getattr(getattr(fn, "func", None), "__name__", "unknown_tool"))


def _build_gemini_tools(schemas: list[dict]):
    """Convert OpenAI-style tool schemas into Gemini ``types.Tool`` objects.

    Each schema dict must have ``function.name``, ``function.description``, and
    ``function.parameters``.  Returns a list of ``genai.types.Tool`` instances
    with proper ``function_declarations``, or an empty list if the Gemini SDK
    types are unavailable.
    """
    if genai is None or not hasattr(genai, "types"):
        return []

    declarations = []
    for schema in schemas:
        fn = schema.get("function", schema)
        name = fn.get("name", "unknown_tool")
        description = fn.get("description", "")
        parameters = fn.get("parameters", {"type": "object", "properties": {}})
        try:
            fd = genai.types.FunctionDeclaration(
                name=name,
                description=description,
                parameters=parameters,
            )
            declarations.append(fd)
        except Exception as e:
            log_error(f"Failed to build Gemini FunctionDeclaration for {name}: {e}")

    if not declarations:
        return []

    try:
        return [genai.types.Tool(function_declarations=declarations)]
    except Exception as e:
        log_error(f"Failed to build Gemini Tool wrapper: {e}")
        return []

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
    "self_update_system": self_update_system,
    "set_system_volume": set_system_volume,
    "control_linux_app": control_linux_app,
    "manage_clipboard": manage_clipboard,
    "capture_desktop_screenshot": capture_desktop_screenshot,
    "safe_write_file": safe_write_file,
    "set_preference_ai": set_preference_ai,
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
    {
        "type": "function",
        "function": {
            "name": "self_update_system",
            "description": "Safely self-updates JARVIS: verifies syntax, commits local changes, pulls from Git, optionally updates Ollama models, and restarts the process. Use this when the user asks to update, upgrade, or restart JARVIS.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pull_git": {"type": "boolean", "description": "Whether to pull updates from Git (default: true)"},
                    "update_ollama_models": {"type": "boolean", "description": "Whether to refresh Ollama models llama3.2:3b and qwen2.5:7b (default: false)"},
                    "restart_after": {"type": "boolean", "description": "Whether to restart JARVIS after updating (default: true)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_system_volume",
            "description": "Set Linux Mint system volume (0-100) or mute/unmute audio.",
            "parameters": {
                "type": "object",
                "properties": {
                    "level_percent": {"type": "integer", "description": "Volume percentage 0-100"},
                    "mute": {"type": "boolean", "description": "True to mute, False to unmute"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "control_linux_app",
            "description": "Open, focus, or close a Linux desktop application.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "'open', 'focus', or 'close'"},
                    "app_name": {"type": "string", "description": "Application command or window title"},
                },
                "required": ["action", "app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_clipboard",
            "description": "Read or write text on the Linux Mint system clipboard.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "'read' or 'write'"},
                    "text": {"type": "string", "description": "Text to copy when action is 'write'"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "capture_desktop_screenshot",
            "description": "Take a screenshot of the Linux Mint desktop.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "Optional output PNG filename"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "safe_write_file",
            "description": "Unified file write gateway. Protected source/test files route through the backup → compile → test → rollback pipeline; non-protected workspace files write directly. Every write is recorded in audit.jsonl.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Path to the file to write"},
                    "content": {"type": "string", "description": "The full text content to write"},
                    "actor": {"type": "string", "description": "Actor performing the write (user, autonomous_worker, self_repair, tool_synthesis)"},
                    "reason": {"type": "string", "description": "Reason for the file modification"},
                },
                "required": ["filepath", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_preference_ai",
            "description": "Record an AI-learned preference/heuristic (source='ai'). AI preferences only fill gaps where no explicit user preference exists for the same key; user preferences always take strict precedence.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Preference key (e.g., 'response.max_bullets', 'autonomous.allowed_hours')"},
                    "value": {"type": "string", "description": "Preference value (string or JSON-serializable)"},
                    "weight": {"type": "number", "description": "Confidence weight for this AI preference (default 1.0)"},
                },
                "required": ["key", "value"],
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

# Thread-safe lock for CONVERSATION_HISTORY mutations and snapshots
CONVERSATION_LOCK = threading.Lock()
SESSION_HISTORY_PATH = os.path.join(BASE_DIR, "memory", "session_history.json")


def _save_session_history_to_disk() -> None:
    """Persist non-system conversation turns to memory/session_history.json."""
    try:
        os.makedirs(os.path.dirname(SESSION_HISTORY_PATH), exist_ok=True)
        serializable = []
        for msg in CONVERSATION_HISTORY:
            if not isinstance(msg, dict) or msg.get("role") == "system":
                continue
            role = msg.get("role")
            content = msg.get("content", "")
            if role in ("user", "assistant", "tool") and isinstance(content, str) and content.strip():
                entry = {"role": role, "content": content}
                if msg.get("name"):
                    entry["name"] = msg["name"]
                serializable.append(entry)
        with open(SESSION_HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(serializable[-16:], f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[MEMORY] Failed to persist session history: {e}")


def _load_session_history_from_disk() -> None:
    """Reload previous session turns into CONVERSATION_HISTORY on startup."""
    if not os.path.exists(SESSION_HISTORY_PATH):
        return
    try:
        with open(SESSION_HISTORY_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, list) and saved:
            CONVERSATION_HISTORY.extend(saved[-12:])
    except Exception as e:
        print(f"[MEMORY] Failed to load session history: {e}")


_load_session_history_from_disk()


def _snapshot_history() -> list[dict]:
    """Return a shallow copy of CONVERSATION_HISTORY under lock."""
    with CONVERSATION_LOCK:
        return list(CONVERSATION_HISTORY)


def _append_history(message: dict) -> None:
    """Append a single message to CONVERSATION_HISTORY under lock and persist to disk."""
    with CONVERSATION_LOCK:
        CONVERSATION_HISTORY.append(message)
        _save_session_history_to_disk()


def _replace_history(new_history: list[dict]) -> None:
    """Replace CONVERSATION_HISTORY entirely under lock and persist to disk."""
    with CONVERSATION_LOCK:
        CONVERSATION_HISTORY.clear()
        CONVERSATION_HISTORY.extend(new_history)
        _save_session_history_to_disk()


def clear_conversation_history() -> None:
    """Clear all conversation history and reset to the default system prompt."""
    default_system = {
        "role": "system",
        "content": "You are J.A.R.V.I.S., a highly advanced autonomous AI assistant.",
    }
    _replace_history([default_system])


def _clean_message_text(message: dict) -> str:
    """Return a plain text view of a message payload."""
    content = message.get("content", "") if isinstance(message, dict) else str(message)
    return str(content).strip()


def extract_structured_preferences(text: str | list[str]) -> dict:
    """Turn unstructured user facts or summary points into an actionable learned preferences object."""
    if isinstance(text, list):
        combined = " ".join(str(item).strip() for item in text if item)
    else:
        combined = str(text or "").strip()

    if not combined:
        return {}

    lower = combined.lower()
    prefs = {
        "communication": [],
        "technical": [],
        "design": [],
        "goals": [],
        "constraints": [],
        "explicit": [],
    }

    if any(k in lower for k in ["concise", "brief", "short", "bullet", "bullet point"]):
        prefs["communication"].append("concise and direct answers")
    if any(k in lower for k in ["spoken", "voice", "audio", "natural", "conversational"]):
        prefs["communication"].append("natural spoken pacing")
    if any(k in lower for k in ["detailed", "thorough", "in-depth", "step-by-step", "explain"]):
        prefs["communication"].append("structured step-by-step detail")
    if any(k in lower for k in ["sir", "formal"]):
        prefs["communication"].append("address user respectfully as Sir")

    tech_patterns = [
        "python", "javascript", "typescript", "fastapi", "react", "sqlite",
        "customtkinter", "docker", "git", "ollama", "groq", "gemini"
    ]
    for tech in tech_patterns:
        if re.search(rf"\b{tech}\b", lower):
            prefs["technical"].append(tech)

    styles = ["premium", "luxury", "minimal", "clean", "modern", "bold", "calm", "elegant"]
    found_styles = [s for s in styles if re.search(rf"\b{s}\b", lower)]
    if found_styles:
        prefs["design"].append(f"style: {', '.join(found_styles)}")

    colors = ["blue", "gold", "cyan", "green", "red", "black", "white", "purple"]
    found_colors = [c for c in colors if re.search(rf"\b{c}\b", lower)]
    if found_colors:
        prefs["design"].append(f"palette: {', '.join(found_colors)}")

    if any(k in lower for k in ["growth", "learn", "self-improve", "evolve", "autonomous"]):
        prefs["goals"].append("autonomous self-learning and self-improvement")
    if any(k in lower for k in ["automate", "automation", "scheduled", "background"]):
        prefs["goals"].append("workflow automation and background tasks")
    if any(k in lower for k in ["marketing", "social media", "brand", "campaign"]):
        prefs["goals"].append("brand development and high-converting marketing")

    if any(k in lower for k in ["safe", "safety", "local-only", "workspace-only", "project-scoped", "workspace", "boundaries", "boundary"]):
        prefs["constraints"].append("preserve local project workspace boundaries")
    if any(k in lower for k in ["no fluff", "straight to the point", "don't ask"]):
        prefs["constraints"].append("avoid conversational filler")

    matches = re.findall(r"(?:prefer|like|love|want|need|always|never)\s+(?:to\s+)?([^.;!?]+)", combined, re.IGNORECASE)
    for m in matches:
        clean = " ".join(m.strip().split())
        if 4 < len(clean) < 80:
            prefs["explicit"].append(clean.lower())

    structured = {}
    for key, items in prefs.items():
        deduped = sorted(set(items))
        if deduped:
            structured[key] = deduped

    return structured


def extract_user_preferences_from_message(message: str) -> dict:
    """Extract important user preferences from natural-language requests into structured guidance."""
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

    guidance = extract_structured_preferences(text)
    if guidance:
        preferences["structured_guidance"] = json.dumps(guidance, ensure_ascii=False)

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

    learned_preferences_raw = get_memory_entry("learned_preferences", "")
    if learned_preferences_raw:
        try:
            preferences = json.loads(learned_preferences_raw)
            if preferences:
                memory_lines.append("Learned preferences: " + json.dumps(preferences, ensure_ascii=False))
        except Exception:
            pass
    else:
        try:
            from modules.memory import build_learned_preferences_from_reflections
            prefs = build_learned_preferences_from_reflections(limit=8)
            if prefs:
                memory_lines.append("Learned preferences: " + json.dumps(prefs, ensure_ascii=False))
        except Exception:
            pass

    reflection_summary = get_memory_entry("reflection_summary", "")
    if reflection_summary:
        memory_lines.append("Reflection summary: " + reflection_summary)
    else:
        try:
            from modules.memory import summarize_recent_reflections
            generated = summarize_recent_reflections(limit=8)
            if generated:
                memory_lines.append("Reflection summary: " + generated)
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
    """Create a structured learned preferences guidance object and summary of recent user facts."""
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
        structured_prefs = extract_structured_preferences(recent_facts)
        
        prefs_payload = {
            "recent_facts": recent_facts,
            "guidance": structured_prefs,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        
        remember("profile", "conversation_preferences", json.dumps(prefs_payload, ensure_ascii=False))
        
        if structured_prefs:
            existing_learned_raw = get_memory_entry("learned_preferences", "")
            merged_learned = {}
            if existing_learned_raw:
                try:
                    merged_learned = json.loads(existing_learned_raw)
                except Exception:
                    merged_learned = {}
            for k, vals in structured_prefs.items():
                current_vals = merged_learned.get(k, [])
                if isinstance(current_vals, list):
                    merged_learned[k] = sorted(set(current_vals + vals))[:6]
                else:
                    merged_learned[k] = vals
            remember("profile", "learned_preferences", json.dumps(merged_learned, ensure_ascii=False))

        guidance_sections = []
        if structured_prefs.get("communication"):
            guidance_sections.append(f"Tone/Pacing: {', '.join(structured_prefs['communication'])}")
        if structured_prefs.get("technical"):
            guidance_sections.append(f"Tech: {', '.join(structured_prefs['technical'])}")
        if structured_prefs.get("design"):
            guidance_sections.append(f"Design: {', '.join(structured_prefs['design'])}")
        if structured_prefs.get("goals"):
            guidance_sections.append(f"Goals: {', '.join(structured_prefs['goals'])}")
        if structured_prefs.get("constraints"):
            guidance_sections.append(f"Rules: {', '.join(structured_prefs['constraints'])}")
        if structured_prefs.get("explicit"):
            guidance_sections.append(f"Directives: {', '.join(structured_prefs['explicit'][:3])}")

        guidance_text = " | ".join(guidance_sections) if guidance_sections else "; ".join(recent_facts)
        summary = f"Learned User Preferences: [{guidance_text}]"
        remember("profile", "conversation_summary", summary)
        return summary

    return saved_summary or "Learned User Preferences: [status: active]"


def trim_and_summarize_conversation(history: list[dict]) -> list[dict]:
    """Preserve the core JARVIS system prompt while keeping a rolling summary and the last 16 dialogue turns."""
    base_system = CONVERSATION_HISTORY[0] if CONVERSATION_HISTORY else {
        "role": "system",
        "content": "You are JARVIS, an advanced autonomous AI assistant. Always address the user as Sir."
    }
    if not history:
        return [base_system]

    summary = _build_conversation_summary(history)
    recent_messages = []
    for message in history:
        if not isinstance(message, dict) or message.get("role") == "system":
            continue
        content = _clean_message_text(message)
        if content or message.get("tool_calls"):
            recent_messages.append(message)

    # Keep the last 16 messages (8 full user/assistant exchanges) so multi-step context never drops
    kept_recent = recent_messages[-16:]
    merged_system = {
        "role": "system",
        "content": f"{base_system['content']}\n\n--- ACTIVE SESSION STATE ---\n{summary}"
    }
    return [merged_system] + kept_recent


def ask_local_brain(query: str, force_provider: str | None = None, is_urgent: bool = True) -> str:
    """Standard text generation using local Ollama with a strict timeout.

    Args:
        query: The user query to process.
        force_provider: Accepted for API consistency. This function is always
            local-only, so cloud providers are never invoked regardless of the value.

    """
    if ollama is None:
        return "Local Ollama is not installed or unavailable in this environment."
    try:
        response = ollama.chat(model=MODEL_NAME, messages=sanitize_fallback_messages(CONVERSATION_HISTORY))
        return response["message"]["content"]
    except Exception as e:
        return f"Local brain error: {e}"


def stream_local_brain(prompt: str, history: list[dict] | None = None, force_provider: str | None = None):
    """Yield text chunks from a streaming provider, falling back to a single full response.

    Uses Ollama ``/api/chat`` with ``stream=True`` when available, otherwise
    yields the full ``ask_local_brain(...)`` response as one chunk.
    """
    import json as _json
    import urllib.request

    messages = list(history) if history else [{"role": "user", "content": prompt}]

    # Try Ollama streaming first
    if ollama is not None:
        try:
            payload = _json.dumps(
                {"model": MODEL_NAME, "messages": messages, "stream": True}
            ).encode("utf-8")
            req = urllib.request.Request(
                "http://localhost:11434/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8").strip()
                    if not line:
                        continue
                    try:
                        data = _json.loads(line)
                    except ValueError:
                        continue
                    chunk = data.get("message", {}).get("content", "")
                    if chunk:
                        yield chunk
                    if data.get("done"):
                        break
            return
        except Exception:
            pass

    # Fallback: single full response
    yield ask_local_brain(prompt, force_provider=force_provider)


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
                timeout=12,
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
    """Routes chat completions through Cloudflare Workers AI using the OpenAI compatibility layer with timeout."""
    if OpenAI is None:
        raise RuntimeError("openai package is not installed; Cloudflare gateway cannot be used.")

    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID")
    api_token = os.getenv("CLOUDFLARE_API_TOKEN")
    if not account_id or not api_token:
        raise RuntimeError("Cloudflare credentials missing in .env")

    client = OpenAI(
        base_url=f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1",
        api_key=api_token,
        timeout=5.0,
    )

    response = client.chat.completions.create(
        model=CLOUDFLARE_MODEL,
        messages=sanitize_fallback_messages(messages),
        timeout=5.0,
    )

    content = response.choices[0].message.content
    _set_last_usage(response)
    return (content or "").strip()


def call_kilo_gateway(messages: list) -> str:
    """Routes chat completions through Kilo AI Gateway's free model pool with a strict 12s timeout."""
    if OpenAI is None:
        raise RuntimeError("openai package is not installed; Kilo gateway cannot be used.")

    api_key = os.getenv("KILO_API_KEY") or "anonymous"
    client = OpenAI(
        base_url="https://ai-gateway.kilo.ai/api/v1",
        api_key=api_key,
        timeout=5.0,
    )
    response = client.chat.completions.create(
        model="kilo-auto/free",
        messages=sanitize_fallback_messages(messages),
        timeout=5.0,
    )
    content = response.choices[0].message.content
    _set_last_usage(response)
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

    # Explicit model name mentions boost ollama routing
    if any(kw in q for kw in ("llama3.2", "llama 3.2", "qwen2.5", "qwen 2.5")):
        scores["ollama"] = scores.get("ollama", 0) + 15

    fallback_order = {"groq": 0, "gemini": 1, "kilo": 2, "cloudflare": 3, "agy": 4, "ollama": 5}
    return sorted(available, key=lambda provider: (-scores.get(provider, 0), fallback_order.get(provider, 99)))


def _select_ollama_model(query: str, history: list[dict] | None = None) -> str:
    """Select the Ollama model based on query complexity and conversation context.

    qwen2.5:7b is preferred for multi-turn tool/context tracking, self-update,
    and complex reasoning. llama3.2:3b handles lightweight/fast tasks.
    """
    complex_keywords = (
        "code", "coding", "debug", "debugging", "bug", "fix", "error", "python",
        "javascript", "typescript", "refactor", "syntax", "function", "class",
        "program", "script", "build", "test", "patch", "exception",
        "analyze", "analysis", "research", "complex", "reasoning", "architecture",
        "design", "implement", "optimize", "performance", "security",
        "write a", "create a", "generate", "explain how", "step by step",
        "update", "upgrade", "restart", "git", "pull", "commit", "rollback",
        "self", "improve", "evolve", "tool", "autonomous",
    )
    q = (query or "").lower()

    # Multi-turn context: qwen2.5:7b holds tool-calling instructions better
    turn_count = len(history) if history else 0
    is_multi_turn = turn_count > 4

    if any(kw in q for kw in complex_keywords) or is_multi_turn:
        return "qwen2.5:7b"
    return "llama3.2:3b"


_PROVIDER_COOLDOWN: dict[str, float] = {}
_COOLDOWN_SECONDS = 120.0


def _is_provider_cooling_down(provider: str) -> bool:
    until = _PROVIDER_COOLDOWN.get(provider, 0.0)
    if time.time() < until:
        return True
    _PROVIDER_COOLDOWN.pop(provider, None)
    return False


def _mark_provider_failed(provider: str) -> None:
    _PROVIDER_COOLDOWN[provider] = time.time() + _COOLDOWN_SECONDS


ACTION_KEYWORDS = (
    "file", "folder", "directory", "run", "execute", "script", "code", "git",
    "search", "find", "list", "read", "write", "edit", "delete", "create",
    "system", "disk", "ram", "cpu", "process", "goal", "schedule", "test", "log",
)


def should_attach_tools(prompt: str) -> bool:
    """Fast-path router: decide whether a prompt needs tool schemas attached.

    Short conversational prompts with no action keywords skip tool mode entirely.
    """
    cleaned = (prompt or "").strip().lower()
    if not cleaned:
        return False
    if len(cleaned.split()) <= 3 and not any(k in cleaned for k in ACTION_KEYWORDS):
        return False
    return any(k in cleaned for k in ACTION_KEYWORDS) or "/" in cleaned or "." in cleaned


def _query_needs_tools(query: str) -> bool:
    """Only attach the 35 JSON tool schemas when the request likely needs an external action."""
    q = (query or "").lower()
    # Pure greetings should never trigger tool mode
    q_clean = q.strip().rstrip("!.,?")
    greeting_patterns = {
        "hi", "hello", "hey", "howdy", "yo", "sup",
        "good morning", "good evening", "good afternoon",
        "hi there", "hello there", "hey there",
    }
    if q_clean in greeting_patterns:
        return False
    action_triggers = (
        "file", "read", "write", "edit", "save", "folder", "directory", "search",
        "find", "code", "bug", "fix", "test", "syntax", "log", "update", "upgrade",
        "restart", "rollback", "reload", "cpu", "ram", "battery", "diagnostic",
        "system", "time", "date", "open", "website", "url", "web", "browser",
        "volume", "mute", "unmute", "sound", "audio", "app", "close", "focus",
        "clipboard", "copy", "paste", "screenshot", "screen", "image", "flyer",
        "post", "instagram", "brand", "analytics", "memory", "remember", "experiment",
    )
    return any(trigger in q for trigger in action_triggers)


_LATEST_DECISION_TRACE: Optional[DecisionTrace] = None


def get_latest_decision_trace() -> Optional[dict]:
    """Return the latest DecisionTrace as a dict for HUD rendering."""
    if _LATEST_DECISION_TRACE is None:
        return None
    return _LATEST_DECISION_TRACE.to_dict()


_LAST_PROVIDER_USAGE: Dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}


def _set_last_usage(response: Any) -> None:
    """Record token usage from an OpenAI-compatible response for later trace capture."""
    try:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        _LAST_PROVIDER_USAGE["prompt_tokens"] = int(getattr(usage, "prompt_tokens", 0) or 0)
        _LAST_PROVIDER_USAGE["completion_tokens"] = int(getattr(usage, "completion_tokens", 0) or 0)
    except Exception:
        pass


def _pop_last_usage(trace: DecisionTrace) -> None:
    """Copy and reset the module-level last-usage accumulator onto a trace."""
    if _LAST_PROVIDER_USAGE["prompt_tokens"] or _LAST_PROVIDER_USAGE["completion_tokens"]:
        trace.prompt_tokens = _LAST_PROVIDER_USAGE["prompt_tokens"]
        trace.completion_tokens = _LAST_PROVIDER_USAGE["completion_tokens"]
    _LAST_PROVIDER_USAGE["prompt_tokens"] = 0
    _LAST_PROVIDER_USAGE["completion_tokens"] = 0


def _set_gemini_usage(trace: DecisionTrace, response: Any) -> None:
    """Read token counts from a google-genai response usage_metadata onto a trace."""
    try:
        meta = getattr(response, "usage_metadata", None)
        if meta is None:
            return
        prompt = getattr(meta, "prompt_token_count", None)
        if prompt is None and isinstance(meta, dict):
            prompt = meta.get("prompt_token_count", 0)
        completion = getattr(meta, "candidates_token_count", None)
        if completion is None and isinstance(meta, dict):
            completion = meta.get("candidates_token_count", 0)
        trace.prompt_tokens = int(prompt or 0)
        trace.completion_tokens = int(completion or 0)
    except Exception:
        pass


def _set_ollama_usage(trace: DecisionTrace, response: Any) -> None:
    """Read token counts from an Ollama chat response dict onto a trace."""
    try:
        if not isinstance(response, dict):
            return
        trace.prompt_tokens = int(response.get("prompt_eval_count", 0) or 0)
        trace.completion_tokens = int(response.get("eval_count", 0) or 0)
    except Exception:
        pass


def _extract_groq_tokens(response: Any, kind: str) -> int:
    """Safely read prompt/completion token counts from an OpenAI-compatible response."""
    try:
        usage = getattr(response, "usage", None)
        if usage is None:
            return 0
        value = getattr(usage, kind + "_tokens", 0) if kind else 0
        if isinstance(value, dict):
            value = value.get(kind + "_tokens", 0)
        return int(value or 0)
    except Exception:
        return 0


def _finalize_ai_turn(trace: DecisionTrace, reply: str, outcome: str = "success") -> str:
    """Log the ai_response audit event and store the DecisionTrace for HUD access."""
    global _LATEST_DECISION_TRACE
    if trace.start_perf:
        trace.latency_ms = round((time.perf_counter() - trace.start_perf) * 1000.0, 2)
    if not trace.prompt_tokens and not trace.completion_tokens:
        _pop_last_usage(trace)
    _LATEST_DECISION_TRACE = trace
    try:
        provider_info = {
            "provider": trace.provider,
            "model": trace.model,
            "version": "local",
        }
        log_audit_event(
            actor=trace.actor,
            action="ai_response",
            outcome=outcome,
            memory_ids=trace.memory_ids_retrieved,
            preference_keys=trace.preference_keys_used,
            provider_info=provider_info,
            metadata={
                "trace_id": trace.trace_id,
                "tools_invoked": trace.tools_invoked,
                "audit_event_ids": trace.audit_event_ids,
                "reply_length": len(reply or ""),
                "latency_ms": trace.latency_ms,
                "prompt_tokens": trace.prompt_tokens,
                "completion_tokens": trace.completion_tokens,
            },
        )
    except Exception:
        pass
    # Persist per-provider token/latency telemetry (skipped for the no-provider error path).
    if trace.provider and trace.provider != "unknown":
        try:
            from modules.budget import record_provider_usage

            record_provider_usage(
                provider=trace.provider,
                model=trace.model or "",
                prompt_tokens=trace.prompt_tokens,
                completion_tokens=trace.completion_tokens,
                latency_ms=trace.latency_ms,
                success=(outcome == "success"),
                is_urgent=trace.is_urgent,
            )
        except Exception:
            pass
    return reply


def _dispatch_tool(func_name: str, func_args: dict) -> str:
    """Execute a tool by name, checking static tools first then dynamic tools.

    Returns a structured error string if the tool is unknown or execution fails.
    """
    # MCP-namespaced tools route to the MCP bridge
    try:
        from modules.mcp_bridge import call_mcp_tool, is_mcp_tool

        if is_mcp_tool(func_name):
            result = call_mcp_tool(func_name, func_args, timeout_sec=15)
            log_audit_event(
                actor="autonomous_worker",
                action="mcp_tool_call",
                outcome="success",
                metadata={"tool": func_name},
            )
            return result
    except Exception:
        pass

    handler = AVAILABLE_TOOLS.get(func_name)
    if handler is None:
        handler = get_dynamic_tool(func_name)
    if handler is None:
        return f"Unknown tool: {func_name}"
    try:
        return handler(**func_args) if func_args else handler()
    except Exception as tool_err:
        return f"Tool error in {func_name}: {tool_err}"


_XML_TOOL_CALL_RE = re.compile(
    r"<(?:dots_function_call|function_call)>(.*?)</(?:dots_function_call|function_call)>",
    re.DOTALL,
)


def _parse_xml_tool_calls(text: str) -> list[tuple[str, dict]]:
    """Parse XML-style tool calls from assistant message text.

    Detects ``<dots_function_call>...</dots_function_call>`` and
    ``<function_call>...</function_call>`` blocks, extracting the tool name
    and arguments.  Returns a list of ``(tool_name, args_dict)`` tuples.
    """
    results: list[tuple[str, dict]] = []
    if not text:
        return results
    for match in _XML_TOOL_CALL_RE.finditer(text):
        inner = match.group(1).strip()
        if not inner:
            continue
        tool_name, args = _extract_tool_from_xml_inner(inner)
        if tool_name:
            results.append((tool_name, args))
    return results


def _extract_tool_from_xml_inner(inner: str) -> tuple[str | None, dict]:
    """Extract tool name and arguments from the inner content of an XML tool call block."""
    # Try JSON first
    try:
        data = json.loads(inner)
        if isinstance(data, dict):
            name = data.get("name") or data.get("function") or data.get("tool")
            args = data.get("arguments") or data.get("args") or data.get("parameters") or {}
            if isinstance(name, str) and name:
                return name, (args if isinstance(args, dict) else {})
    except (json.JSONDecodeError, ValueError):
        pass

    # Try explicit <name>...</name><arguments>...</arguments> tags
    name_match = re.search(r"<name>(.*?)</name>", inner, re.DOTALL)
    args_match = re.search(r"<arguments>(.*?)</arguments>", inner, re.DOTALL)
    if name_match:
        name = name_match.group(1).strip()
        args = {}
        if args_match:
            try:
                args = json.loads(args_match.group(1).strip())
            except (json.JSONDecodeError, ValueError):
                args = {}
        return name, (args if isinstance(args, dict) else {})

    # Try first XML tag as tool name with attributes as args
    tag_match = re.match(r"<(\w+)([^>]*)>", inner)
    if tag_match:
        tool_name = tag_match.group(1)
        attr_str = tag_match.group(2).strip()
        args = {}
        if attr_str:
            attr_pairs = re.findall(r'(\w+)="([^"]*)"', attr_str)
            args = dict(attr_pairs)
        inner_tags = re.findall(r"<(\w+)>(.*?)</\1>", inner, re.DOTALL)
        for tag, val in inner_tags:
            if tag not in args:
                args[tag] = val.strip()
        return tool_name, args

    # Fallback: treat the whole inner as a tool name
    name = inner.strip().split()[0] if inner.strip() else None
    return (name, {}) if name else (None, {})


def ask_local_brain_with_tools(query: str, force_provider: str | None = None, is_urgent: bool = True) -> str:
    """Choose the best available provider for the current request, executing a strict timeout and automatic failover loop.

    Args:
        query: The user query to process.
        force_provider: If set (e.g. ``"ollama"``), restrict the failover loop to only
            that provider, skipping cloud providers entirely. Used by autonomous
            background routines that must stay local.

    """
    save_user_profile_memory(query)

    decision_trace = DecisionTrace(actor="autonomous_worker")
    decision_trace.start_perf = time.perf_counter()
    decision_trace.is_urgent = bool(is_urgent)

    # Inject active user/AI preferences into the system prompt and record them in the trace.
    pref_block, traced_pref_keys = format_preferences_for_prompt()
    decision_trace.preference_keys_used.extend(traced_pref_keys)

    # Take a thread-safe snapshot of conversation history for this request
    history = _snapshot_history()
    history.append({"role": "user", "content": query})

    trimmed = trim_and_summarize_conversation(history)
    relevant_context = get_relevant_memory_context(query)
    system_suffix = relevant_context
    if pref_block:
        system_suffix = (system_suffix + "\n\n" if system_suffix else "") + pref_block
    if system_suffix and trimmed:
        trimmed[0] = {
            "role": "system",
            "content": f"{trimmed[0]['content']}\n\n--- RELEVANT LONG-TERM MEMORY / PREFERENCES ---\n{system_suffix}"
        }
    history = trimmed

    # Persist the trimmed history back under lock
    _replace_history(history)

    provider_order = choose_provider_order(query, get_available_providers())
    if force_provider:
        provider_order = [p for p in provider_order if p == force_provider]
        if not provider_order and force_provider in get_available_providers():
            provider_order = [force_provider]
    else:
        # Runtime routing mode + automatic 80% cloud-cap offload to Ollama
        try:
            from modules.preferences import get_runtime_settings

            routing_mode = get_runtime_settings().get("routing_mode", "auto")
            daily_cap = int(get_runtime_settings().get("daily_cloud_token_cap", 250000))
        except Exception:
            routing_mode, daily_cap = "auto", 250000
        if routing_mode == "ollama_only":
            provider_order = ["ollama"] if "ollama" in get_available_providers() else provider_order
        else:
            try:
                from modules.budget import should_offload_to_ollama

                if should_offload_to_ollama(is_urgent=is_urgent, daily_cap=daily_cap):
                    print("[ROUTER] Daily cloud quota >= 80% cap; shifting non-urgent request to local Ollama.")
                    if "ollama" in get_available_providers():
                        provider_order = ["ollama"]
            except Exception:
                pass
    if not provider_order:
        return _finalize_ai_turn(decision_trace, "No AI providers are available in this environment. Configure a model key or install Ollama.", "error")

    explicit_override = get_explicit_provider_override(query)
    if explicit_override:
        print(f"[ROUTER] Manual provider override active: {explicit_override}")

    print(f"[ROUTER] Request-aware provider order (Timeout: 12s per tier): {provider_order}")

    use_tools = should_attach_tools(query)
    active_tools = (TOOL_SCHEMAS + get_relevant_dynamic_tool_schemas(query, top_k=5)) if use_tools else None
    if use_tools:
        try:
            from modules.mcp_bridge import get_mcp_tool_schemas

            mcp_schemas = get_mcp_tool_schemas()
            if mcp_schemas:
                active_tools = (active_tools or []) + mcp_schemas[:5]
        except Exception:
            pass

    for provider in provider_order:
        if _is_provider_cooling_down(provider):
            continue
        set_active_provider(provider)
        decision_trace.provider = provider
        decision_trace.model = provider
        try:
            if provider == "groq":
                print("[ROUTER] Attempting Groq Cloud (5-step agent loop)...")
                called_tools: set[tuple[str, str]] = set()
                duplicate_detected = False
                for step in range(5):
                    response = groq_client.chat.completions.create(
                        model=CLOUD_MODEL_GROQ,
                        messages=history,
                        tools=active_tools,
                        tool_choice="auto" if active_tools else None,
                        timeout=6.0,
                    )
                    message = response.choices[0].message

                    if not message.tool_calls:
                        reply = message.content or "Completed, Sir."
                        reply = cognitive_review_loop(query, reply)
                        decision_trace.prompt_tokens = _extract_groq_tokens(response, "prompt")
                        decision_trace.completion_tokens = _extract_groq_tokens(response, "completion")
                        _append_history({"role": "assistant", "content": reply})
                        set_active_provider(provider)
                        return _finalize_ai_turn(decision_trace, reply, "success")

                    tool_messages = [{"role": "assistant", "content": message.content or "", "tool_calls": message.tool_calls}]
                    for tool_call in message.tool_calls:
                        func_name = tool_call.function.name
                        func_args = json.loads(tool_call.function.arguments or "{}")
                        call_key = (func_name, json.dumps(func_args, sort_keys=True))
                        if call_key in called_tools:
                            print(f"[GROQ AGENT] Duplicate tool call detected: {func_name}. Breaking loop.")
                            duplicate_detected = True
                            break
                        called_tools.add(call_key)
                        print(f"[GROQ AGENT Step {step + 1}/5] Tool: {func_name}")

                        tool_result = _dispatch_tool(func_name, func_args)
                        decision_trace.tools_invoked.append(func_name)
                        tool_messages.append({"role": "tool", "content": str(tool_result), "tool_call_id": tool_call.id})

                    if duplicate_detected:
                        break
                    with CONVERSATION_LOCK:
                        CONVERSATION_HISTORY.extend(tool_messages)
                        history = list(CONVERSATION_HISTORY)

                # Final synthesis after 5 tool steps or duplicate break
                final_response = groq_client.chat.completions.create(
                    model=CLOUD_MODEL_GROQ,
                    messages=history,
                    timeout=6.0,
                )
                reply = final_response.choices[0].message.content or "Completed multi-step execution, Sir."
                reply = cognitive_review_loop(query, reply)
                decision_trace.prompt_tokens = _extract_groq_tokens(final_response, "prompt")
                decision_trace.completion_tokens = _extract_groq_tokens(final_response, "completion")
                _append_history({"role": "assistant", "content": reply})
                set_active_provider(provider)
                return _finalize_ai_turn(decision_trace, reply, "success")

            if provider == "cloudflare":
                print("[ROUTER] Attempting Cloudflare Workers AI...")
                reply = call_cloudflare_workers_ai(history)
                if reply:
                    reply = cognitive_review_loop(query, reply)
                    _append_history({"role": "assistant", "content": reply})
                    set_active_provider(provider)
                    return _finalize_ai_turn(decision_trace, reply, "success")

            if provider == "kilo":
                print("[ROUTER] Attempting Kilo AI Gateway...")
                called_tools: set[tuple[str, str]] = set()
                duplicate_detected = False
                for step in range(5):
                    reply = call_kilo_gateway(history)
                    if not reply:
                        break
                    xml_calls = _parse_xml_tool_calls(reply)
                    if not xml_calls:
                        reply = cognitive_review_loop(query, reply)
                        _append_history({"role": "assistant", "content": reply})
                        set_active_provider(provider)
                        return _finalize_ai_turn(decision_trace, reply, "success")
                    _append_history({"role": "assistant", "content": reply})
                    for tool_name, func_args in xml_calls:
                        call_key = (tool_name, json.dumps(func_args, sort_keys=True))
                        if call_key in called_tools:
                            print(f"[KILO AGENT] Duplicate tool call detected: {tool_name}. Breaking loop.")
                            duplicate_detected = True
                            break
                        called_tools.add(call_key)
                        print(f"[KILO AGENT Step {step + 1}/5] Tool: {tool_name}")
                        tool_result = _dispatch_tool(tool_name, func_args)
                        decision_trace.tools_invoked.append(tool_name)
                        _append_history({"role": "tool", "content": str(tool_result), "name": tool_name})
                    if duplicate_detected:
                        break
                    history = _snapshot_history()
                reply = call_kilo_gateway(history) if history else ""
                if reply:
                    reply = cognitive_review_loop(query, reply)
                    _append_history({"role": "assistant", "content": reply})
                set_active_provider(provider)
                return _finalize_ai_turn(decision_trace, reply or "Completed, Sir.", "success")

            if provider == "agy":
                print("[ROUTER] Attempting Google Subscription (Antigravity CLI)...")
                reply = call_antigravity_cli(history)
                if reply:
                    reply = cognitive_review_loop(query, reply)
                    _append_history({"role": "assistant", "content": reply})
                    set_active_provider(provider)
                    return _finalize_ai_turn(decision_trace, reply, "success")

            if provider == "gemini":
                print("[ROUTER] Attempting Google Gemini Cloud...")
                gemini_prompt = "\n".join([f"{m['role']}: {m['content']}" for m in history if isinstance(m.get('content'), str)])

                all_schemas = TOOL_SCHEMAS + get_all_dynamic_tool_schemas()
                gemini_tools = _build_gemini_tools(all_schemas)
                for fn in AVAILABLE_TOOLS.values():
                    if not getattr(fn, "__name__", None):
                        safe = _safe_tool_name(fn)
                        if safe == "unknown_tool":
                            log_error(f"Gemini tool callable has no resolvable name: {fn!r}")
                config = None
                if hasattr(genai, "types") and hasattr(genai.types, "GenerateContentConfig"):
                    config = genai.types.GenerateContentConfig(
                        tools=gemini_tools,
                        system_instruction=history[0]["content"] if history else None,
                    )

                response = gemini_client.models.generate_content(
                    model=CLOUD_MODEL_GEMINI,
                    contents=gemini_prompt,
                    config=config,
                )

                if getattr(response, "function_calls", None):
                    # Execute all function calls, not just the first one
                    tool_messages = [{"role": "assistant", "content": f"Invoked tools: {', '.join(fc.name for fc in response.function_calls)}"}]
                    for tool_call in response.function_calls:
                        fn_name = tool_call.name
                        fn_args = dict(tool_call.args) if tool_call.args else {}
                        print(f"[GEMINI AGENT] Tool selected: {fn_name}")

                        if fn_name in AVAILABLE_TOOLS:
                            tool_result = AVAILABLE_TOOLS[fn_name](**fn_args) if fn_args else AVAILABLE_TOOLS[fn_name]()
                            tool_messages.append({"role": "tool", "content": str(tool_result), "name": fn_name})

                    with CONVERSATION_LOCK:
                        CONVERSATION_HISTORY.extend(tool_messages)
                        history = list(CONVERSATION_HISTORY)

                    fn_response_parts = [
                        genai.types.Part.from_function_response(
                            name=tm["name"],
                            response={"result": str(tm["content"])},
                        )
                        for tm in tool_messages
                        if tm.get("role") == "tool"
                    ]
                    follow_up = gemini_client.models.generate_content(
                        model=CLOUD_MODEL_GEMINI,
                        contents=[
                            gemini_prompt,
                            response.candidates[0].content,
                            *fn_response_parts,
                        ],
                        config=config,
                    )
                    reply = follow_up.text or str(tool_messages[-1]["content"]) if tool_messages else "Completed, Sir."
                    reply = cognitive_review_loop(query, reply)
                    _set_gemini_usage(decision_trace, follow_up)
                    _append_history({"role": "assistant", "content": reply})
                    return _finalize_ai_turn(decision_trace, reply, "success")

                reply = response.text or "Completed, Sir."
                reply = cognitive_review_loop(query, reply)
                _set_gemini_usage(decision_trace, response)
                _append_history({"role": "assistant", "content": reply})
                set_active_provider(provider)
                return _finalize_ai_turn(decision_trace, reply, "success")

            if provider == "ollama":
                print("[ROUTER] Attempting Local Ollama (5-step agent loop)...")
                if ollama is None:
                    continue

                ollama_model = _select_ollama_model(query, history)
                print(f"[ROUTER] Selected Ollama model: {ollama_model}")

                called_tools: set[tuple[str, str]] = set()
                duplicate_detected = False
                for step in range(5):
                    response = ollama.chat(
                        model=ollama_model,
                        messages=sanitize_fallback_messages(history),
                        tools=active_tools,
                        keep_alive="30m",
                        options={"num_ctx": 2048},
                    )

                    message = response.get("message", {})
                    tool_calls = message.get("tool_calls")
                    if not tool_calls:
                        reply = message.get("content", "") or "Completed, Sir."
                        reply = cognitive_review_loop(query, reply)
                        _set_ollama_usage(decision_trace, response)
                        _append_history({"role": "assistant", "content": reply})
                        set_active_provider(provider)
                        return _finalize_ai_turn(decision_trace, reply, "success")

                    tool_messages = [dict(message)]
                    for tool_call in tool_calls:
                        func_name = tool_call["function"]["name"]
                        raw_args = tool_call["function"].get("arguments", {})
                        func_args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                        call_key = (func_name, json.dumps(func_args, sort_keys=True))
                        if call_key in called_tools:
                            print(f"[LOCAL AGENT] Duplicate tool call detected: {func_name}. Breaking loop.")
                            duplicate_detected = True
                            break
                        called_tools.add(call_key)
                        print(f"[LOCAL AGENT Step {step + 1}/5] Tool: {func_name}")

                        tool_result = _dispatch_tool(func_name, func_args)
                        tool_messages.append({"role": "tool", "content": str(tool_result), "name": func_name})

                    if duplicate_detected:
                        break
                    with CONVERSATION_LOCK:
                        CONVERSATION_HISTORY.extend(tool_messages)
                        history = list(CONVERSATION_HISTORY)

                final_response = ollama.chat(
                    model=ollama_model,
                    messages=sanitize_fallback_messages(history),
                    keep_alive="30m",
                    options={"num_ctx": 2048},
                )
                reply = final_response["message"]["content"]
                reply = cognitive_review_loop(query, reply)
                _set_ollama_usage(decision_trace, final_response)
                _append_history({"role": "assistant", "content": reply})
                set_active_provider(provider)
                return _finalize_ai_turn(decision_trace, reply, "success")

        except Exception as e:
            log_error(f"Provider {provider} failed: {e}")
            if provider != "ollama":
                _mark_provider_failed(provider)
            continue

    return _finalize_ai_turn(decision_trace, "No AI provider returned a valid response, Sir.", "error")