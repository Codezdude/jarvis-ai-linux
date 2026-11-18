import datetime
import json
import os
import re
import subprocess
import urllib.request
import webbrowser
from config.settings import ASSETS_DIR, CODE_DIR, SAFE_MODE

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

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
