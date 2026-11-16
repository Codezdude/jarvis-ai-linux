import os
import sys
import datetime
import fnmatch
import importlib

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import json
import re
import sqlite3
import subprocess
import urllib.request
import webbrowser
from config.settings import ASSETS_DIR, CODE_DIR, SAFE_MODE
from typing import Any, Callable, Dict, List, Optional

from modules.audit import log_audit_event
from modules.approval import check_approval, DEFAULT_DB_PATH

# Non-bypassable protected codebase patterns relative to PROJECT_ROOT
PROTECTED_GLOBS: list[str] = [
    "jarvis.py",
    "modules/*.py",
    "modules/**/*.py",
    "tests/*.py",
    "tests/**/*.py",
]


def _normalize_rel_path(filepath: str) -> Optional[str]:
    """
    Resolves symlinks and '..' traversal to determine path relative to PROJECT_ROOT.
    Returns None if the resolved path lies outside PROJECT_ROOT.
    """
    abs_root = os.path.realpath(PROJECT_ROOT)
    candidate = filepath if os.path.isabs(filepath) else os.path.join(abs_root, filepath)
    real_target = os.path.realpath(candidate)

    try:
        rel = os.path.relpath(real_target, abs_root)
    except ValueError:
        return None

    if rel.startswith("..") or os.path.isabs(rel):
        return None

    return rel.replace("\\", "/")


def is_protected_path(filepath: str) -> bool:
    """
    Returns True if filepath resolves to any protected source or test file
    inside PROJECT_ROOT. Immune to '..', './', and symlink indirection.
    """
    rel_path = _normalize_rel_path(filepath)
    if rel_path is None:
        return False

    for pattern in PROTECTED_GLOBS:
        if fnmatch.fnmatch(rel_path, pattern):
            return True
    return False

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover - optional image generation dependency
    Image = None
    ImageDraw = None
    ImageFont = None

try:
    import psutil
except ImportError:  # pragma: no cover - optional system diagnostics dependency
    psutil = None

try:
    import pyautogui
except ImportError:  # pragma: no cover - optional automation dependency
    pyautogui = None

from modules.automation import (
    capture_engagement_summary,
    generate_brand_design_concepts,
    generate_social_media_pack,
    review_social_media_pack,
    safe_browser_task,
    schedule_social_media_publish,
)
from modules.db import log_long_term_memory, search_long_term_memory
from modules.logger import log_error, log_info
from modules.safe_storage import atomic_write_text



def _resolve_project_path(filepath: str) -> str:
  """Return a project-scoped absolute path while allowing full access inside the JARVIS tree."""
  if not filepath or not filepath.strip():
    raise ValueError("A file path is required.")

  workspace_root = os.path.realpath(PROJECT_ROOT)
  candidate = os.path.realpath(
      os.path.normpath(filepath if os.path.isabs(filepath) else os.path.join(PROJECT_ROOT, filepath))
  )

  if os.path.commonpath([workspace_root, candidate]) != workspace_root:
    raise ValueError("Access denied: path escapes the project directory.")

  return candidate


def _require_explicit_confirmation(action_name: str, confirmed: bool = False) -> str | None:
  """Block project-mutating actions unless the user explicitly confirms them."""
  if SAFE_MODE and not confirmed:
    return (
        f"Action blocked by Safe Mode: {action_name} requires explicit confirmation before "
        "any project file write or edit is allowed."
    )
  return None


# Safety failsafe: slam mouse to any corner of screen to abort automation
if pyautogui is not None:
  pyautogui.FAILSAFE = True


def get_system_diagnostics() -> str:
  """Checks current CPU usage, RAM usage, and battery percentage."""
  if psutil is None:
    return "System diagnostics are unavailable because psutil is not installed."

  cpu = psutil.cpu_percent(interval=0.5)
  ram = psutil.virtual_memory().percent
  battery = psutil.sensors_battery()
  bat_str = f"{battery.percent}%" if battery else "Plugged in / Desktop"
  return f"CPU is at {cpu}%, RAM usage is {ram}%, and Battery is at {bat_str}."


def open_website(url: str) -> str:
  """Opens a website in the default browser."""
  if not url.startswith("http"):
    url = "https://" + url
  webbrowser.open(url)
  log_info(f"Opened website: {url}")
  return f"Opened {url} in the browser."


def web_search(query: str, max_results: int = 4) -> str:
  """Searches the live web via Tavily and returns concise, cited results."""
  api_key = os.getenv("TAVILY_API_KEY")
  if not api_key:
    return "Error: TAVILY_API_KEY missing in .env"

  payload = json.dumps({
      "query": query,
      "search_depth": "basic",
      "max_results": int(max_results) if max_results and max_results > 0 else 4,
  }).encode("utf-8")

  req = urllib.request.Request(
      "https://api.tavily.com/search",
      data=payload,
      headers={
          "Authorization": f"Bearer {api_key}",
          "Content-Type": "application/json",
      },
      method="POST",
  )

  try:
      with urllib.request.urlopen(req, timeout=20) as resp:
          data = json.loads(resp.read().decode("utf-8"))
      results = data.get("results", [])
      if not results:
          return f"No web results found for '{query}'."
      return "\n".join(
          f"- {r.get('title', 'Untitled')} ({r.get('url', '')}): {r.get('content', '')}"
          for r in results[: max_results if max_results and max_results > 0 else 4]
      )
  except Exception as e:
      return f"Web search error: {e}"


def get_current_time() -> str:
  """Returns current local date and time."""
  return datetime.datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")


def browser_automation_task(
    url: str,
    objective: str,
    steps: str | list[str] | None = None,
    allowlist: list[str] | None = None,
    confirmed: bool = False,
) -> str:
  """Run a controlled browser task for approved destinations under JARVIS."""
  return safe_browser_task(
      url=url,
      objective=objective,
      steps=steps,
      allowlist=allowlist,
      confirmed=confirmed,
  )


def generate_social_post_brief(
    brand_name: str,
    audience: str,
    tone: str,
    platform: str = "instagram",
    colors: str = "blue, gold",
) -> str:
  """Create a draft social media content brief for JARVIS-approved publishing workflows."""
  return generate_social_media_pack(
      brand_name=brand_name,
      audience=audience,
      tone=tone,
      platform=platform,
      colors=colors,
  )


def review_social_post_draft(
    pack_input: str | dict,
    approved: bool = False,
    review_notes: str = "",
) -> str:
  """Place a social draft into a review state and require user approval before any publishing action."""
  return review_social_media_pack(
      pack_input=pack_input,
      approved=approved,
      review_notes=review_notes,
  )


def generate_brand_concepts(
    brand_name: str,
    audience: str,
    tone: str,
    colors: str = "blue, gold",
    platform: str = "instagram",
) -> str:
  """Generate several reviewable design concepts based on brand identity and audience."""
  return generate_brand_design_concepts(
      brand_name=brand_name,
      audience=audience,
      tone=tone,
      colors=colors,
      platform=platform,
  )


def capture_post_analytics(
    reach: int = 0,
    likes: int = 0,
    comments: int = 0,
    saves: int = 0,
    shares: int = 0,
) -> str:
  """Summarize engagement metrics so JARVIS can improve future content choices."""
  return capture_engagement_summary(
      reach=reach,
      likes=likes,
      comments=comments,
      saves=saves,
      shares=shares,
  )


def schedule_social_post(
    pack_input: str | dict,
    publish_time: str | None = None,
) -> str:
  """Schedule a social post only after the draft has been approved by a human."""
  return schedule_social_media_publish(
      pack_input=pack_input,
      publish_time=publish_time,
  )


def save_code_file(filename: str, content: str) -> str:
  """Saves front-end, back-end code, or design files to generated_code/."""
  try:
    os.makedirs(CODE_DIR, exist_ok=True)
    filepath = os.path.join(CODE_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
      f.write(content)
    log_info(f"Saved code file to {filepath}")
    return f"Successfully wrote and saved file {filename} inside generated_code."
  except Exception as e:
    log_error(f"Failed to save code file: {e}")
    return f"Failed to save file: {str(e)}"


def run_terminal_command(command: str) -> str:
  """Runs a shell command inside the project workspace."""
  try:
    log_info(f"Executing shell command: {command}")
    result = subprocess.run(
        command, shell=True, capture_output=True, text=True, timeout=30,
        cwd=PROJECT_ROOT,
    )
    output = result.stdout if result.stdout else result.stderr
    return f"Command executed. Output: {output[:800]}"
  except Exception as e:
    log_error(f"Failed to execute command: {e}")
    return f"Failed to execute command: {str(e)}"


def _build_instagram_carousel_slides(headline: str, subtext: str) -> list[dict]:
  """Create a compact 3-slide carousel brief from a headline and supporting copy."""
  title = (headline or "Growth Pack").strip() or "Growth Pack"
  summary = (subtext or "Winning content ideas for your audience.").strip() or "Winning content ideas for your audience."
  return [
      {
          "kicker": "JARVIS content pack",
          "title": title,
          "subtitle": summary,
          "stats": "1080 × 1350 • carousel",
      },
      {
          "kicker": "Why it works",
          "title": "High-contrast, save-worthy slides",
          "subtitle": "Short value blocks, bold typography, and a clear CTA make it easy to stop, read, and share.",
          "stats": "higher saves • stronger shares",
      },
      {
          "kicker": "Action",
          "title": "Turn attention into trust",
          "subtitle": "Use the final caption, CTA, and offer to move viewers from save/share to DM or click.",
          "stats": "ready to publish",
      },
  ]


def _render_carousel_slide_html(slide: dict) -> str:
  """Return the HTML/CSS body for a single Instagram carousel slide."""
  kicker = slide.get("kicker", "JARVIS")
  title = slide.get("title", "Growth Pack")
  subtitle = slide.get("subtitle", "")
  stats = slide.get("stats", "")
  return f"""
  <html>
    <head>
      <meta charset="utf-8" />
      <style>
        * {{ box-sizing: border-box; }}
        html, body {{
          margin: 0;
          width: 100%;
          height: 100%;
          font-family: Arial, Helvetica, sans-serif;
          background: linear-gradient(135deg, #07141d 0%, #112b42 45%, #0f2535 100%);
          color: white;
        }}
        body {{
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 0;
        }}
        .frame {{
          width: 1080px;
          height: 1350px;
          background: radial-gradient(circle at top, rgba(0,243,255,0.18), transparent 35%), #07141d;
          position: relative;
          overflow: hidden;
          border: 4px solid rgba(255,255,255,0.1);
        }}
        .chip {{
          position: absolute;
          top: 68px;
          left: 72px;
          background: rgba(0,243,255,0.12);
          border: 1px solid rgba(0,243,255,0.55);
          color: #9aeaff;
          font-size: 24px;
          font-weight: 700;
          letter-spacing: 2px;
          padding: 10px 18px;
          border-radius: 999px;
          text-transform: uppercase;
        }}
        .title {{
          position: absolute;
          left: 72px;
          right: 72px;
          top: 180px;
          font-size: 84px;
          line-height: 1.05;
          font-weight: 800;
          letter-spacing: -2px;
          color: #f3fbff;
        }}
        .subtitle {{
          position: absolute;
          left: 72px;
          right: 72px;
          top: 620px;
          font-size: 34px;
          line-height: 1.5;
          color: rgba(240, 248, 255, 0.86);
        }}
        .badge {{
          position: absolute;
          left: 72px;
          bottom: 104px;
          font-size: 26px;
          color: #8dffde;
          background: rgba(141,255,222,0.08);
          border: 1px solid rgba(141,255,222,0.3);
          border-radius: 999px;
          padding: 12px 18px;
          font-weight: 700;
        }}
        .brand {{
          position: absolute;
          right: 72px;
          bottom: 104px;
          font-size: 26px;
          color: #d9f6ff;
          font-weight: 700;
          letter-spacing: 1px;
        }}
        .grid {{
          position: absolute;
          inset: 0;
          background-image: linear-gradient(rgba(255,255,255,0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.03) 1px, transparent 1px);
          background-size: 44px 44px;
          pointer-events: none;
        }}
      </style>
    </head>
    <body>
      <div class="frame">
        <div class="grid"></div>
        <div class="chip">{kicker}</div>
        <div class="title">{title}</div>
        <div class="subtitle">{subtitle}</div>
        <div class="badge">{stats}</div>
        <div class="brand">JARVIS</div>
      </div>
    </body>
  </html>
  """


def create_promotional_flyer(
    headline: str,
    subtext: str,
    filename: str = "flyer.jpg",
    slides: list[str | dict] | None = None,
) -> str:
  """Generate an Instagram carousel pack in 1080x1350 portrait format and save it in assets/."""
  base_name = os.path.splitext(filename or "flyer.jpg")[0] or "flyer"
  safe_name = base_name if base_name.lower().endswith((".jpg", ".jpeg")) else f"{base_name}.jpg"
  folder_name = re.sub(r"[^a-zA-Z0-9_-]+", "-", base_name).strip("-") or "instagram-carousel"
  output_dir = os.path.join(ASSETS_DIR, "instagram_carousels", folder_name)
  os.makedirs(output_dir, exist_ok=True)

  normalized_slides = []
  if slides:
      for item in slides:
          if isinstance(item, dict):
              normalized_slides.append(item)
          else:
              normalized_slides.append({
                  "kicker": "JARVIS",
                  "title": headline,
                  "subtitle": str(item),
                  "stats": "carousel slide",
              })
  if not normalized_slides:
      normalized_slides = _build_instagram_carousel_slides(headline, subtext)

  try:
      from playwright.sync_api import sync_playwright
      slide_paths = []
      with sync_playwright() as playwright:
          browser = playwright.chromium.launch(headless=True)
          page = browser.new_page(viewport={"width": 1080, "height": 1350}, device_scale_factor=1)
          for index, slide in enumerate(normalized_slides, start=1):
              page.set_content(_render_carousel_slide_html(slide), wait_until="load")
              path = os.path.join(output_dir, f"slide-{index}.jpg")
              page.screenshot(path=path, type="jpeg", quality=90, full_page=False)
              slide_paths.append(path)
          browser.close()

      manifest_path = os.path.join(output_dir, "manifest.json")
      with open(manifest_path, "w", encoding="utf-8") as handle:
          json.dump({
              "format": "1080x1350",
              "slides": slide_paths,
              "cover": slide_paths[0],
          }, handle, ensure_ascii=False, indent=2)

      log_info(f"Generated Instagram carousel pack at {output_dir}")
      return (
          "Successfully generated promotional flyer and saved as assets/instagram_carousels/"
          f"{folder_name}/slide-1.jpg. {len(slide_paths)} slides created."
      )
  except Exception as exc:
      log_info(f"Playwright carousel capture failed: {exc}; falling back to standard flyer generation.")

  if Image is None or ImageDraw is None or ImageFont is None:
      return "Image generation is unavailable because Pillow is not installed."

  try:
      filepath = os.path.join(ASSETS_DIR, safe_name)
      width, height = 1080, 1350
      image = Image.new("RGB", (width, height), color="#060d18")
      draw = ImageDraw.Draw(image)
      draw.rectangle([40, 40, width - 40, height - 40], outline="#00f3ff", width=4)

      try:
          title_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 72)
      except IOError:
          try:
              title_font = ImageFont.truetype("arial.ttf", 72)
          except IOError:
              title_font = ImageFont.load_default()
      try:
          sub_font = ImageFont.truetype("DejaVuSans.ttf", 38)
      except IOError:
          try:
              sub_font = ImageFont.truetype("arial.ttf", 38)
          except IOError:
              sub_font = ImageFont.load_default()

      draw.text((80, 260), (headline or "JARVIS Growth Pack")[:48], fill="#00f3ff", font=title_font)
      draw.text((80, 520), (subtext or "Carousels built for saves, shares, and DMs.")[:100], fill="#cceeff", font=sub_font)
      image.save(filepath, "JPEG")
      log_info(f"Generated promotional flyer at {filepath}")
      return (
          "Successfully generated promotional flyer and saved as assets/"
          f"{safe_name}."
      )
  except Exception as e:
      log_error(f"Failed to create flyer: {e}")
      return f"Failed to create flyer: {str(e)}"


def move_mouse(x: int, y: int) -> str:
  """Moves mouse cursor to pixel coordinates (x, y)."""
  if pyautogui is None:
    return "Mouse automation is unavailable because pyautogui is not installed."
  try:
    pyautogui.moveTo(x, y, duration=0.4)
    return f"Moved mouse cursor to coordinates ({x}, {y})."
  except Exception as e:
    return f"Failed to move mouse: {str(e)}"


def click_mouse(button: str = "left") -> str:
  """Clicks the mouse at current cursor location."""
  if pyautogui is None:
    return "Mouse automation is unavailable because pyautogui is not installed."
  try:
    pyautogui.click(button=button)
    return f"Performed {button} click."
  except Exception as e:
    return f"Failed to click mouse: {str(e)}"


def type_text(text: str) -> str:
  """Types text into active window."""
  if pyautogui is None:
    return "Keyboard automation is unavailable because pyautogui is not installed."
  try:
    pyautogui.write(text, interval=0.03)
    return f"Typed text: {text}"
  except Exception as e:
    return f"Failed to type text: {str(e)}"


def read_local_file(filepath: str) -> str:
  """Read the contents of a file inside the current project directory."""
  try:
    safe_path = _resolve_project_path(filepath)
    with open(safe_path, "r", encoding="utf-8") as f:
      return f.read()
  except Exception as e:
    return f"Error reading file: {e}"


def write_local_file(filepath: str, content: str, confirmed: bool = True) -> str:
  """Create or overwrite a file inside the project directory."""
  blocked = _require_explicit_confirmation("file writing", confirmed)
  if blocked:
    return blocked

  try:
    safe_path = _resolve_project_path(filepath)
    atomic_write_text(safe_path, content)
    return f"Successfully wrote data to {safe_path}"
  except Exception as e:
    return f"Error writing file: {e}"


def safe_write_file(
    filepath: str,
    content: str,
    actor: str = "user",
    source: str = "user",
    reason: str = "file modification",
    signed_token: Optional[str] = None,
    memory_ids: Optional[List[int]] = None,
    preference_keys: Optional[List[str]] = None,
    provider_info: Optional[Dict[str, Any]] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> Dict[str, Any]:
    """
    Unified file write gateway for all tools, plugins, and autonomous routines.
    - Every write is evaluated by the approval gate (check_approval) before execution.
    - Protected files (jarvis.py, modules/*.py, tests/*.py) route through
      learning.py's backup -> compile() -> test -> rollback pipeline.
    - Non-protected workspace/data files write directly.
    - Every operation is recorded in audit.jsonl.
    """
    abs_target = (
        os.path.realpath(filepath)
        if os.path.isabs(filepath)
        else os.path.realpath(os.path.join(PROJECT_ROOT, filepath))
    )
    rel_display = _normalize_rel_path(abs_target) or abs_target
    protected = is_protected_path(abs_target)

    gate = check_approval(
        action="protected_file_route" if protected else "file_write",
        target=abs_target,
        actor=actor,
        source=source,
        reason=reason,
        is_protected_file=protected,
        signed_token=signed_token,
        db_path=db_path,
    )
    if not gate["approved"]:
        return {
            "success": False,
            "blocked_by_approval_gate": True,
            "risk_tier": gate["risk_tier"],
            "method": gate["method"],
            "path": rel_display,
            "audit_event_id": gate["audit_event_id"],
        }

    if protected:
        # Lazy import prevents circular dependency between tools.py and learning.py
        from modules import learning

        pipeline_result = learning.apply_safe_modification(
            target_file=abs_target,
            new_content=content,
            reason=reason,
        )
        success = bool(pipeline_result.get("success", False))
        outcome = "success" if success else "rolled_back"

        event_id = log_audit_event(
            actor=actor,
            action="protected_file_route",
            outcome=outcome,
            paths_touched=[rel_display],
            memory_ids=memory_ids,
            preference_keys=preference_keys,
            provider_info=provider_info,
            metadata={
                "reason": reason,
                "backup_path": pipeline_result.get("backup_path"),
                "error": pipeline_result.get("error"),
            },
        )
        return {
            "success": success,
            "routed_through_pipeline": True,
            "path": rel_display,
            "audit_event_id": event_id,
            "details": pipeline_result,
        }

    # Direct write for non-protected workspace files (logs, memory DB artifacts, data)
    try:
        os.makedirs(os.path.dirname(abs_target), exist_ok=True)
        with open(abs_target, "w", encoding="utf-8") as f:
            f.write(content)

        event_id = log_audit_event(
            actor=actor,
            action="file_write",
            outcome="success",
            paths_touched=[rel_display],
            memory_ids=memory_ids,
            preference_keys=preference_keys,
            provider_info=provider_info,
            metadata={"reason": reason, "bytes_written": len(content.encode("utf-8"))},
        )
        return {
            "success": True,
            "routed_through_pipeline": False,
            "path": rel_display,
            "audit_event_id": event_id,
        }
    except Exception as exc:
        event_id = log_audit_event(
            actor=actor,
            action="file_write",
            outcome="error",
            paths_touched=[rel_display],
            memory_ids=memory_ids,
            preference_keys=preference_keys,
            provider_info=provider_info,
            metadata={"reason": reason, "error": str(exc)},
        )
        return {
            "success": False,
            "routed_through_pipeline": False,
            "path": rel_display,
            "audit_event_id": event_id,
            "error": str(exc),
        }


def edit_file_section(filepath: str, old_text: str, new_text: str, confirmed: bool = True) -> str:
  """Replace a specific snippet inside an existing file within the project directory."""
  blocked = _require_explicit_confirmation("file editing", confirmed)
  if blocked:
    return blocked

  try:
    safe_path = _resolve_project_path(filepath)
    with open(safe_path, "r", encoding="utf-8") as f:
      content = f.read()

    if old_text not in content:
      return f"Error: Could not find target text snippet to replace in {filepath}."

    updated_content = content.replace(old_text, new_text)
    atomic_write_text(safe_path, updated_content)
    return f"Successfully edited text block in {filepath}"
  except Exception as e:
    return f"Error editing file: {e}"


def list_directory(directory_path: str = ".") -> str:
  """List the files and folders inside a project-scoped directory."""
  try:
    safe_root = _resolve_project_path(directory_path)
    entries = sorted(os.listdir(safe_root))
    if not entries:
      return f"Directory is empty: {safe_root}"
    return "\n".join(entries)
  except Exception as e:
    return f"Error listing directory: {e}"


def generate_image(prompt: str, filename: str = "jarvis_generated.png", width: int = 1080, height: int = 1080) -> str:
  """Generate a simple local poster-style image using Pillow and a prompt-based layout."""
  if Image is None or ImageDraw is None or ImageFont is None:
    return "Image generation is unavailable because Pillow is not installed."

  try:
    os.makedirs(ASSETS_DIR, exist_ok=True)
    safe_name = (filename or "jarvis_generated.png").strip() or "jarvis_generated.png"
    if not safe_name.lower().endswith((".png", ".jpg", ".jpeg")):
      safe_name += ".png"
    filepath = os.path.join(ASSETS_DIR, safe_name)

    image = Image.new("RGB", (width, height), color="#07131d")
    draw = ImageDraw.Draw(image)
    for i in range(0, 400, 24):
      draw.rectangle([20 + i, 20 + i, width - 20 - i, height - 20 - i], outline=(40 + i // 3, 120 + i // 2, 180), width=2)

    title = (prompt or "JARVIS Creative Prompt").strip()[:62] or "JARVIS Creative Prompt"
    subtitle = "Local AI learning image"

    try:
      title_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 54)
    except IOError:
      try:
          title_font = ImageFont.truetype("arial.ttf", 54)
      except IOError:
          title_font = ImageFont.load_default()
    try:
      sub_font = ImageFont.truetype("DejaVuSans.ttf", 28)
    except IOError:
      try:
          sub_font = ImageFont.truetype("arial.ttf", 28)
      except IOError:
          sub_font = ImageFont.load_default()

    draw.text((60, 220), title, fill="#9fe7ff", font=title_font)
    draw.text((60, 420), subtitle, fill="#d8f3ff", font=sub_font)
    image.save(filepath, "PNG")
    log_info(f"Generated image at {filepath}")
    return f"Successfully generated image and saved it to assets/{safe_name}."
  except Exception as e:
    log_error(f"Failed to generate image: {e}")
    return f"Failed to generate image: {str(e)}"


def web_fetch_and_learn(url: str, max_chars: int = 2000) -> str:
    """Fetch a web page, truncate to `max_chars`, and store it in long‑term memory under the "web_learning" category.
    This allows JARVIS to ingest new knowledge from the internet.
    """
    if not url:
        return "A URL is required for web learning."
    try:
        try:
            import requests
            resp = requests.get(url, timeout=10)
            if resp.status_code != 200:
                return f"Failed to fetch {url}: status {resp.status_code}"
            content = resp.text[:max_chars]
        except ImportError:
            with urllib.request.urlopen(url, timeout=10) as resp:
                content = resp.read().decode("utf-8", errors="ignore")[:max_chars]
        from modules.memory import log_long_term_memory
        log_long_term_memory(category="web_learning", content=content)
        return f"Fetched and stored {len(content)} characters from {url}"
    except Exception as e:
        return f"Error fetching {url}: {e}"


def search_project_files(query: str, directory_path: str = ".", max_results: int = 20) -> str:
  """Search for filenames or content matches inside the project tree."""
  try:
    safe_root = _resolve_project_path(directory_path)
    matches = []
    query_text = query.lower()

    for root, dirnames, filenames in os.walk(safe_root):
      dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "__pycache__"]

      for name in sorted(dirnames + filenames):
        full_path = os.path.join(root, name)
        rel_path = os.path.relpath(full_path, PROJECT_ROOT)
        if query_text in name.lower():
          matches.append(rel_path)
          if len(matches) >= max_results:
            break

      if len(matches) >= max_results:
        break

      for filename in sorted(filenames):
        full_path = os.path.join(root, filename)
        if len(matches) >= max_results:
          break
        try:
          with open(full_path, "r", encoding="utf-8", errors="ignore") as handle:
            content = handle.read().lower()
          if query_text in content:
            rel_path = os.path.relpath(full_path, PROJECT_ROOT)
            matches.append(rel_path)
        except Exception:
          continue

    if not matches:
      return f"No files or content matching '{query}' were found under {directory_path}."

    return "Matches:\n" + "\n".join(matches[:max_results])
  except Exception as e:
    return f"Error searching project files: {e}"


# --- SELF-IMPROVEMENT & SAFE EVOLUTION TOOLS ---

def inspect_recent_logs(max_lines: int = 50) -> str:
  """Inspect the last operational and error log entries to diagnose system issues."""
  from modules.self_improvement import inspect_recent_logs as _inspect
  return _inspect(max_lines=max_lines)


def validate_code_syntax(code_string: str = "", filepath: str = "") -> str:
  """Validate Python syntax either from a code snippet or a file in the project."""
  from modules.self_improvement import validate_code_syntax as _validate
  valid, msg = _validate(code_string=code_string, filepath=filepath)
  return f"Syntax Valid: {valid}. Details: {msg}"


def run_project_tests(test_target: str = "tests") -> str:
  """Run the project unit test suite and return pass/fail status and output."""
  from modules.self_improvement import run_project_tests as _run_tests
  passed, output = _run_tests(test_target=test_target)
  status = "PASSED" if passed else "FAILED"
  return f"Test Suite {status}:\n{output}"


def apply_self_improvement(filepath: str, new_content: str, confirmed: bool = True) -> str:
  """Safely apply a code improvement with automatic backup, syntax check, test verification, and auto-rollback on failure."""
  from modules.self_improvement import apply_self_improvement as _apply
  return _apply(filepath=filepath, new_content=new_content, confirmed=confirmed, run_tests=True)


def rollback_file(filepath: str, backup_path: str = "") -> str:
  """Restore a file from its most recent backup or a specified backup copy."""
  from modules.self_improvement import rollback_file as _rollback
  return _rollback(filepath=filepath, backup_path=backup_path)


def reload_jarvis_module(module_name: str) -> str:
  """Hot-reload a modified Python module into the active runtime without restarting."""
  from modules.self_improvement import reload_jarvis_module as _reload
  return _reload(module_name=module_name)


# --- LINUX MINT NATIVE DESKTOP & SYSTEM CONTROL TOOLS ---
import subprocess as _sp
import os as _os
from datetime import datetime as _dt


def set_system_volume(level_percent: int = 50, mute: bool | None = None) -> str:
    """Control Linux Mint audio volume or mute state via pactl."""
    try:
        if mute is not None:
            flag = "1" if mute else "0"
            _sp.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", flag], check=True, timeout=10)
            return f"System audio {'muted' if mute else 'unmuted'}."
        clamped = max(0, min(100, int(level_percent)))
        _sp.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{clamped}%"], check=True, timeout=10)
        return f"System volume set to {clamped}%."
    except Exception as e:
        return f"Failed to adjust system volume: {e}"


def control_linux_app(action: str, app_name: str) -> str:
    """Launch, focus, or close an application on Linux Mint (action: 'open', 'focus', 'close')."""
    act = (action or "").strip().lower()
    target = (app_name or "").strip()
    if not target:
        return "Application name is required."
    try:
        if act == "open":
            _sp.Popen(target.split(), start_new_session=True)
            return f"Launched '{target}'."
        elif act == "focus":
            res = _sp.run(["wmctrl", "-a", target], capture_output=True, text=True, timeout=10)
            return f"Focused window matching '{target}'." if res.returncode == 0 else f"No open window matched '{target}'."
        elif act == "close":
            res = _sp.run(["wmctrl", "-c", target], capture_output=True, text=True, timeout=10)
            return f"Closed window matching '{target}'." if res.returncode == 0 else f"Could not close '{target}'."
        return "Invalid action. Use 'open', 'focus', or 'close'."
    except Exception as e:
        return f"App control error for '{target}': {e}"


def manage_clipboard(action: str = "read", text: str = "") -> str:
    """Read from or write text to the Linux X11 clipboard via xclip."""
    act = (action or "read").strip().lower()
    try:
        if act == "write":
            _sp.run(["xclip", "-selection", "clipboard"], input=text, text=True, check=True, timeout=10)
            return f"Copied {len(text)} characters to clipboard."
        res = _sp.run(["xclip", "-selection", "clipboard", "-o"], capture_output=True, text=True, timeout=10)
        return res.stdout if res.returncode == 0 else "Clipboard is empty."
    except Exception as e:
        return f"Clipboard error: {e}"


def capture_desktop_screenshot(filename: str = "") -> str:
    """Capture a screenshot of the Linux Mint desktop into assets/screenshots/."""
    try:
        out_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "assets", "screenshots")
        _os.makedirs(out_dir, exist_ok=True)
        fname = filename.strip() if filename.strip() else f"screen_{_dt.now().strftime('%Y%m%d_%H%M%S')}.png"
        if not fname.endswith(".png"):
            fname += ".png"
        full_path = _os.path.join(out_dir, fname)
        _sp.run(["scrot", full_path], check=True, timeout=15)
        return f"Screenshot saved to {full_path}"
    except Exception as e:
        return f"Screenshot failed: {e}"


# --- DYNAMIC TOOL REGISTRY (Tool Synthesis) ---

DYNAMIC_TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {}


def register_dynamic_tool(
    tool_name: str,
    func: Callable[..., Any],
    description: str,
    parameters_schema: Dict[str, Any],
) -> None:
    """Registers a validated, test-passing synthesized tool into the live runtime registry."""
    DYNAMIC_TOOL_REGISTRY[tool_name] = {
        "name": tool_name,
        "callable": func,
        "description": description,
        "parameters": parameters_schema,
    }


def get_dynamic_tool(tool_name: str) -> Optional[Callable[..., Any]]:
    entry = DYNAMIC_TOOL_REGISTRY.get(tool_name)
    return entry["callable"] if entry else None


DYNAMIC_TOOL_TIMEOUT_SEC = 15

_DYNAMIC_TOOL_RUNNER = (
    "import sys, json, importlib\n"
    "root, module_name, tool_name, args_json = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]\n"
    "sys.path.insert(0, root)\n"
    "try:\n"
    "    mod = importlib.import_module(module_name)\n"
    "    fn = getattr(mod, tool_name)\n"
    "    args = json.loads(args_json) if args_json else {}\n"
    "    result = fn(**args) if args else fn()\n"
    "    print(json.dumps({'ok': True, 'result': result}, default=str))\n"
    "except Exception as exc:\n"
    "    print(json.dumps({'ok': False, 'error': f'{type(exc).__name__}: {exc}'}))\n"
)


def run_dynamic_tool_isolated(
    tool_name: str,
    func_args: Optional[Dict[str, Any]] = None,
    timeout: int = DYNAMIC_TOOL_TIMEOUT_SEC,
    module_name: str = "modules.dynamic_tools",
) -> str:
    """Execute a synthesized dynamic tool in a separate process with a hard timeout.

    Isolation protects the HUD/runtime from a misbehaving or hanging synthesized
    tool: the call runs in a fresh ``modules.dynamic_tools`` interpreter, its
    result is serialized to JSON on stdout, and a timeout terminates the child.
    Returns a string suitable for feeding back to the model.
    """
    args_json = json.dumps(func_args or {})
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _DYNAMIC_TOOL_RUNNER,
             PROJECT_ROOT, module_name, tool_name, args_json],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=PROJECT_ROOT,
        )
    except subprocess.TimeoutExpired:
        log_error(f"Dynamic tool '{tool_name}' timed out after {timeout}s")
        return f"Dynamic tool '{tool_name}' timed out after {timeout}s."
    except Exception as e:
        log_error(f"Dynamic tool '{tool_name}' failed to launch: {e}")
        return f"Dynamic tool '{tool_name}' failed to launch: {e}"

    out = (proc.stdout or "").strip()
    if not out:
        err = (proc.stderr or "").strip()
        return f"Dynamic tool '{tool_name}' produced no output. {err[:400]}"
    try:
        payload = json.loads(out.splitlines()[-1])
    except Exception:
        return f"Dynamic tool '{tool_name}' returned unparseable output: {out[:400]}"
    if payload.get("ok"):
        return str(payload.get("result", ""))
    return f"Dynamic tool '{tool_name}' error: {payload.get('error', 'unknown')}"


def get_all_dynamic_tool_schemas() -> List[Dict[str, Any]]:
    """Returns OpenAI/Ollama-compatible tool schemas for all active synthesized tools."""
    return [
        {
            "type": "function",
            "function": {
                "name": meta["name"],
                "description": meta["description"],
                "parameters": meta["parameters"],
            },
        }
        for meta in DYNAMIC_TOOL_REGISTRY.values()
    ]


def rank_tool_schemas(query: str, schemas: List[Dict[str, Any]], top_k: int = 5) -> List[Dict[str, Any]]:
    """Return the top_k schemas most relevant to a query by token overlap.

    Generic ranker over already-built OpenAI-style tool schemas; used for MCP
    tools (and any pre-built schema list). Falls back to the first ``top_k`` when
    no tokens overlap or the query is empty.
    """
    if not schemas:
        return []
    if len(schemas) <= top_k:
        return list(schemas)
    q_tokens = set(re.findall(r"[a-z0-9_]+", (query or "").lower()))
    if not q_tokens:
        return list(schemas[:top_k])
    scored = []
    for idx, schema in enumerate(schemas):
        fn = schema.get("function", {}) if isinstance(schema, dict) else {}
        blob = f"{fn.get('name', '')} {fn.get('description', '')}".lower()
        t_tokens = set(re.findall(r"[a-z0-9_]+", blob))
        overlap = len(q_tokens & t_tokens)
        scored.append((overlap, -idx, schema))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in scored[:top_k]]


def get_relevant_dynamic_tool_schemas(query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """Return the top_k dynamic tool schemas most relevant to a query.

    Scores by token overlap + character trigram cosine similarity between the
    query and ``f"{name} {description}"``. Falls back to the most recently
    registered tools when all scores are zero.
    """
    import math
    from collections import Counter

    registry = list(DYNAMIC_TOOL_REGISTRY.values())
    if not registry:
        return []

    def _trigrams(text: str) -> Counter:
        padded = f"  {(text or '').lower()}  "
        return Counter(padded[i:i + 3] for i in range(len(padded) - 2))

    q_trigrams = _trigrams(query)
    q_tokens = set((query or "").lower().split())
    q_norm = math.sqrt(sum(v * v for v in q_trigrams.values())) or 1.0

    scored = []
    for meta in registry:
        doc = f"{meta['name']} {meta['description']}"
        d_trigrams = _trigrams(doc)
        # cosine over trigrams
        common = set(q_trigrams) & set(d_trigrams)
        dot = sum(q_trigrams[g] * d_trigrams[g] for g in common)
        d_norm = math.sqrt(sum(v * v for v in d_trigrams.values())) or 1.0
        cosine = dot / (q_norm * d_norm)
        # token overlap
        d_tokens = set(doc.lower().split())
        overlap = len(q_tokens & d_tokens)
        scored.append((cosine + overlap * 0.1, meta))

    if all(s == 0 for s, _ in scored):
        # Fallback: most recently registered
        selected = registry[-top_k:]
    else:
        scored.sort(key=lambda t: t[0], reverse=True)
        selected = [meta for _, meta in scored[:top_k]]

    return [
        {
            "type": "function",
            "function": {
                "name": meta["name"],
                "description": meta["description"],
                "parameters": meta["parameters"],
            },
        }
        for meta in selected
    ]


def hydrate_dynamic_tools(db_path: Optional[str] = None) -> int:
    """
    Re-loads all active synthesized tools from SQLite on startup so dynamic capabilities
    persist across restarts.
    """
    target_db = db_path or os.path.join(PROJECT_ROOT, "data", "jarvis_memory.db")
    if not os.path.exists(target_db):
        return 0

    conn = sqlite3.connect(target_db)
    conn.row_factory = sqlite3.Row
    try:
        table_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='dynamic_tools'"
        ).fetchone()
        if not table_exists:
            return 0
        rows = conn.execute(
            "SELECT tool_name, description, parameters_schema_json FROM dynamic_tools WHERE status = 'active'"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return 0

    try:
        dyn_mod = importlib.import_module("modules.dynamic_tools")
        dyn_mod = importlib.reload(dyn_mod)
    except Exception:
        return 0

    loaded = 0
    for row in rows:
        fn = getattr(dyn_mod, row["tool_name"], None)
        if callable(fn):
            register_dynamic_tool(
                tool_name=row["tool_name"],
                func=fn,
                description=row["description"],
                parameters_schema=json.loads(row["parameters_schema_json"]),
            )
            loaded += 1
    return loaded
