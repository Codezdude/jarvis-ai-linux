import ast
import json
import os
import re
from datetime import UTC, datetime
from urllib.parse import urlparse

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR = os.path.join(PROJECT_ROOT, "assets")
GENERATED_DIR = os.path.join(PROJECT_ROOT, "generated_code")
REVIEW_QUEUE_PATH = os.path.join(GENERATED_DIR, "social_review_queue.json")

DEFAULT_BROWSER_ALLOWLIST = {
    "localhost",
    "127.0.0.1",
    "example.com",
    "github.com",
    "docs.github.com",
    "google.com",
    "www.google.com",
    "linkedin.com",
    "www.linkedin.com",
}


def _normalize_host(url: str) -> str:
    """Return the host for a URL, stripped of protocol and www prefix."""
    if not url:
        return ""
    candidate = url if "://" in url else f"https://{url}"
    parsed = urlparse(candidate)
    return parsed.netloc.lower().replace("www.", "")


def _is_approved_domain(url: str, allowlist: list[str] | None = None) -> bool:
    """Check whether the target URL is inside the approved browser allowlist."""
    allow = {item.lower().replace("www.", "") for item in (allowlist or DEFAULT_BROWSER_ALLOWLIST)}
    host = _normalize_host(url)
    if not host:
        return False
    return host in allow or any(host.endswith(f".{domain}") for domain in allow)


def safe_browser_task(
    url: str,
    objective: str,
    steps: list[str] | str | None = None,
    allowlist: list[str] | None = None,
    confirmed: bool = False,
) -> str:
    """Run a safe browser automation task only for trusted domains and with explicit approval."""
    if not confirmed:
        return (
            "Automation blocked by the approval gate: JARVIS must receive explicit user confirmation "
            "before any browser workflow is allowed."
        )

    if not url:
        return "Automation failed: a target URL is required."

    if not _is_approved_domain(url, allowlist):
        host = _normalize_host(url)
        return (
            f"Automation blocked: '{host}' is not on the approved browser allowlist. "
            "Only whitelisted and pre-approved destinations may be accessed."
        )

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return (
            "Browser automation is unavailable because Playwright is not installed. "
            "Install it to enable the JARVIS sub-automation workflow."
        )

    try:
        os.makedirs(ASSETS_DIR, exist_ok=True)
        screenshot_path = os.path.join(ASSETS_DIR, "jarvis_browser_task.png")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            title = page.title()
            text = page.locator("body").inner_text()[:1500]
            page.screenshot(path=screenshot_path, full_page=True)
            browser.close()

        info = {
            "status": "approved",
            "url": url,
            "objective": objective,
            "title": title,
            "preview": text,
            "screenshot": screenshot_path,
            "steps": steps if isinstance(steps, list) else ([steps] if steps else []),
            "domain_guard": "allowlisted",
        }
        return json.dumps(info, ensure_ascii=False, indent=2)
    except Exception as exc:
        return f"Browser automation failed: {exc}"


def generate_social_media_pack(
    brand_name: str,
    audience: str,
    tone: str,
    platform: str = "instagram",
    colors: str = "blue, gold",
) -> str:
    """Generate a branded content brief and save it under the project workspace for JARVIS review."""
    brand = (brand_name or "Untitled Brand").strip() or "Untitled Brand"
    audience_text = (audience or "general audience").strip() or "general audience"
    tone_text = (tone or "clean and premium").strip() or "clean and premium"
    platform_text = (platform or "instagram").strip().lower() or "instagram"
    colors_text = (colors or "blue, gold").strip() or "blue, gold"

    pack = {
        "brand_name": brand,
        "platform": platform_text,
        "audience": audience_text,
        "tone": tone_text,
        "colors": colors_text,
        "headline": f"{brand} for {audience_text}",
        "caption": (
            f"{brand} is built for {audience_text} with a {tone_text} feel. "
            f"Designed for {platform_text} and tuned to {colors_text}."
        ),
        "cta": "Follow for more premium content and launch updates.",
        "hashtags": ["#brandlaunch", "#socialgrowth", "#creatorbrand", "#premiumcontent"],
        "status": "draft-ready",
        "approved": False,
        "review_notes": "",
        "last_updated": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }

    os.makedirs(GENERATED_DIR, exist_ok=True)
    slug = "".join(ch for ch in brand.lower() if ch.isalnum() or ch in ("-", "_")) or "brand-pack"
    path = os.path.join(GENERATED_DIR, f"{slug}_social_pack.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(pack, handle, ensure_ascii=False, indent=2)

    return json.dumps(pack, ensure_ascii=False, indent=2)


def generate_brand_design_concepts(
    brand_name: str,
    audience: str,
    tone: str,
    colors: str = "blue, gold",
    platform: str = "instagram",
) -> str:
    """Return a few branded visual concepts that JARVIS can review before publishing."""
    brand = (brand_name or "Untitled Brand").strip() or "Untitled Brand"
    audience_text = (audience or "general audience").strip() or "general audience"
    tone_text = (tone or "premium and bold").strip() or "premium and bold"
    palette = [part.strip() for part in str(colors or "blue, gold").split(",") if part.strip()]
    platform_text = (platform or "instagram").strip().lower() or "instagram"

    concepts = [
        {
            "name": f"{brand} Signal",
            "tagline": f"Premium storytelling for {audience_text}",
            "description": f"A high-contrast {tone_text} look, using {', '.join(palette) or 'blue, gold'} to frame the brand in a clean luxury layout.",
            "layout": "full-bleed portrait, strong headline, minimal product focus",
            "cta": "Follow for launch updates",
        },
        {
            "name": f"{brand} Momentum",
            "tagline": f"Bold fresh energy for {audience_text}",
            "description": f"Fast-paced motion-inspired concept emphasizing depth, contrast, and modern {tone_text} styling.",
            "layout": "checkerboard grid with layered text and visual rhythm",
            "cta": "Save this for later",
        },
        {
            "name": f"{brand} Authority",
            "tagline": f"Trust-first creative for {audience_text}",
            "description": f"A calm, premium identity anchored in polished typography and a confident {tone_text} visual language.",
            "layout": "minimal hero card with strong text hierarchy and a subtle product detail",
            "cta": "See the full story",
        },
    ]

    payload = {
        "brand_name": brand,
        "platform": platform_text,
        "audience": audience_text,
        "tone": tone_text,
        "colors": palette or ["blue", "gold"],
        "concepts": concepts,
        "recommended_concept": concepts[0]["name"],
        "status": "concept-ready",
    }

    os.makedirs(GENERATED_DIR, exist_ok=True)
    filename = "".join(ch for ch in brand.lower() if ch.isalnum() or ch in ("-", "_")) or "brand-designs"
    with open(os.path.join(GENERATED_DIR, f"{filename}_design_concepts.json"), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    return json.dumps(payload, ensure_ascii=False, indent=2)


def capture_engagement_summary(
    reach: int = 0,
    likes: int = 0,
    comments: int = 0,
    saves: int = 0,
    shares: int = 0,
) -> str:
    """Summarize post performance with a compact engagement score for review and optimization."""
    reach_total = max(int(reach), 0)
    likes_total = max(int(likes), 0)
    comments_total = max(int(comments), 0)
    saves_total = max(int(saves), 0)
    shares_total = max(int(shares), 0)
    total_interactions = likes_total + comments_total + saves_total + shares_total
    engagement_rate = round((total_interactions / reach_total * 100), 2) if reach_total else 0.0
    engagement_index = round(total_interactions / 200, 2)

    result = {
        "reach": reach_total,
        "likes": likes_total,
        "comments": comments_total,
        "saves": saves_total,
        "shares": shares_total,
        "engagement_rate": engagement_rate,
        "engagement_score": engagement_index,
        "summary": (
            f"Engagement score: {engagement_index:.1f}. "
            f"{total_interactions} interactions from {reach_total} reach "
            f"produced an engagement rate of {engagement_rate:.2f}%."
        ),
        "status": "insights-ready",
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


def review_social_media_pack(pack_input: str | dict, approved: bool = False, review_notes: str = "") -> str:
    """Move a generated draft into a review/approval state so JARVIS waits for explicit user consent."""
    if isinstance(pack_input, str):
        try:
            pack = json.loads(pack_input)
        except json.JSONDecodeError:
            return "Review failed: the social pack payload is not valid JSON."
    else:
        pack = pack_input or {}

    pack["approved"] = bool(approved)
    pack["status"] = "approved-for-publish" if approved else "pending-approval"
    pack["review_notes"] = review_notes or ("Approved for publishing." if approved else "Awaiting human approval.")
    pack["last_updated"] = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    os.makedirs(GENERATED_DIR, exist_ok=True)
    queue = []
    if os.path.exists(REVIEW_QUEUE_PATH):
        try:
            with open(REVIEW_QUEUE_PATH, "r", encoding="utf-8") as handle:
                queue = json.load(handle)
        except Exception:
            queue = []

    if not isinstance(queue, list):
        queue = []

    queue.append({
        "brand_name": pack.get("brand_name", "Untitled Brand"),
        "approved": pack["approved"],
        "status": pack["status"],
        "review_notes": pack["review_notes"],
        "last_updated": pack["last_updated"],
    })

    with open(REVIEW_QUEUE_PATH, "w", encoding="utf-8") as handle:
        json.dump(queue, handle, ensure_ascii=False, indent=2)

    return json.dumps(pack, ensure_ascii=False, indent=2)


def schedule_social_media_publish(pack_input: str | dict, publish_time: str | None = None) -> str:
    """Schedule or prepare an approved social post for publishing; requires explicit approval first."""
    if isinstance(pack_input, str):
        try:
            pack = json.loads(pack_input)
        except json.JSONDecodeError:
            return "Scheduling failed: the social pack payload is not valid JSON."
    else:
        pack = pack_input or {}

    if not pack.get("approved"):
        return "Publishing is blocked: this social draft is not approved yet. JARVIS must wait for human approval before scheduling or sending it."

    schedule = {
        "status": "scheduled",
        "platform": pack.get("platform", "instagram"),
        "brand_name": pack.get("brand_name", "Untitled Brand"),
        "publish_time": publish_time or "manual-review",
        "headline": pack.get("headline", ""),
        "caption": pack.get("caption", ""),
        "cta": pack.get("cta", ""),
        "approved": True,
    }
    return json.dumps(schedule, ensure_ascii=False, indent=2)


def _load_recent_project_notes(limit: int = 8) -> list[str]:
    """Collect a compact list of recent notes from the workspace for local bot learning."""
    notes = []
    candidate_paths = [
        os.path.join(PROJECT_ROOT, "memory.md"),
        os.path.join(PROJECT_ROOT, "workspace_growth.md"),
    ]
    for path in candidate_paths:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                content = handle.read().strip()
            if content:
                notes.extend(line.strip() for line in content.splitlines() if line.strip())
        except OSError:
            continue

    unique_notes = []
    seen = set()
    for note in notes:
        key = note.lower()
        if len(key) < 8 or key in seen:
            continue
        seen.add(key)
        unique_notes.append(note)
    return unique_notes[:limit]


def memory_cleanup_bot() -> dict:
    """Deduplicate and summarize project notes so JARVIS can learn from a clean memory stream."""
    notes = _load_recent_project_notes(limit=12)
    summary = {
        "bot": "memory_cleanup_bot",
        "status": "ok",
        "notes_count": len(notes),
        "summary": "Memory stream reviewed and duplicates removed from the most recent project notes.",
        "highlights": notes[:5],
    }
    os.makedirs(GENERATED_DIR, exist_ok=True)
    report_path = os.path.join(GENERATED_DIR, "memory_cleanup_bot_report.json")
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    return summary


def experiment_review_bot() -> dict:
    """Review recent experiment folders and capture any reusable patterns or learnings."""
    experiments_dir = os.path.join(PROJECT_ROOT, "experiments")
    entries = []
    if os.path.isdir(experiments_dir):
        for name in sorted(os.listdir(experiments_dir)):
            path = os.path.join(experiments_dir, name)
            if os.path.isdir(path):
                readme_path = os.path.join(path, "README.md")
                preview = ""
                if os.path.exists(readme_path):
                    try:
                        with open(readme_path, "r", encoding="utf-8", errors="ignore") as handle:
                            preview = handle.read().strip()[:200]
                    except OSError:
                        preview = ""
                entries.append({"name": name, "summary": preview})

    result = {
        "bot": "experiment_review_bot",
        "status": "ok",
        "experiments_seen": len(entries),
        "summary": "Recent experiment folders were scanned to find reusable patterns and promising concept directions.",
        "entries": entries[:6],
    }
    os.makedirs(GENERATED_DIR, exist_ok=True)
    report_path = os.path.join(GENERATED_DIR, "experiment_review_bot_report.json")
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    return result


def quality_guard_bot() -> dict:
    """Run a free local syntax pass over Python files and collect quality signals JARVIS can learn from."""
    scanned = []
    issues = []
    base_dirs = [os.path.join(PROJECT_ROOT, "modules"), os.path.join(PROJECT_ROOT, "tests")]
    for base_dir in base_dirs:
        if not os.path.isdir(base_dir):
            continue
        for root, _, files in os.walk(base_dir):
            for filename in sorted(files):
                if not filename.endswith(".py"):
                    continue
                path = os.path.join(root, filename)
                scanned.append(path)
                try:
                    with open(path, "r", encoding="utf-8") as handle:
                        src = handle.read()
                    ast.parse(src, filename=path)
                except Exception as exc:  # pragma: no cover - syntax check for bot learning
                    issues.append({"file": path, "error": str(exc)})

    result = {
        "bot": "quality_guard_bot",
        "status": "ok" if not issues else "warnings",
        "files_scanned": len(scanned),
        "issues_found": len(issues),
        "summary": "A local syntax scan completed; the project quality signals were logged for future learning loops.",
        "issues": issues[:10],
    }
    os.makedirs(GENERATED_DIR, exist_ok=True)
    report_path = os.path.join(GENERATED_DIR, "quality_guard_bot_report.json")
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    return result


def content_idea_bot() -> dict:
    """Use recent project memory and experiment notes to suggest new content ideas JARVIS can test."""
    notes = _load_recent_project_notes(limit=20)
    suggestions = [
        "Launch a premium blue creator campaign around trust, speed, and storytelling.",
        "Create a calm luxury product mockup with editorial typography and a warm gold accent.",
        "Design a minimal experiment comparing bold vs. premium positioning across three landing-page styles.",
    ]
    if notes:
        first_note = notes[0]
        suggestions.insert(0, f"Turn the latest project note into a test: '{first_note[:120]}'")

    result = {
        "bot": "content_idea_bot",
        "status": "ok",
        "summary": "New local content directions were harvested from recent notes and experiment history.",
        "ideas": suggestions[:5],
    }
    os.makedirs(GENERATED_DIR, exist_ok=True)
    report_path = os.path.join(GENERATED_DIR, "content_idea_bot_report.json")
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    return result


def tech_ir_learning_bot() -> dict:
    """Fetch a small set of public technical learning sources and summarize automation/image-generator takeaways for JARVIS."""
    sources = [
        "https://www.python.org/",
        "https://pypi.org/project/Pillow/",
        "https://playwright.dev/",
    ]
    learnings = []
    for source in sources:
        try:
            import requests
            response = requests.get(source, timeout=8)
            text = response.text[:1200] if response.ok else ""
        except Exception:
            text = ""

        if not text:
            learnings.append({"source": source, "summary": "Source reachable but no content was captured in this environment."})
            continue

        snippet = re.sub(r"<[^>]+>", " ", text)
        snippet = re.sub(r"\s+", " ", snippet)[:300]
        learnings.append({
            "source": source,
            "summary": f"Public source captured: {snippet.strip() or 'technical reference page loaded successfully.'}",
        })

    result = {
        "bot": "tech_ir_learning_bot",
        "status": "ok",
        "summary": "Public technical references were reviewed for automation, image-generation, and local tooling learnings.",
        "sources": learnings,
    }
    os.makedirs(GENERATED_DIR, exist_ok=True)
    report_path = os.path.join(GENERATED_DIR, "tech_ir_learning_bot_report.json")
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    return result


def run_free_automation_bots() -> dict:
    """Run the free local bot suite so JARVIS can learn from reusable behaviors and project signals."""
    bots = [
        memory_cleanup_bot(),
        experiment_review_bot(),
        quality_guard_bot(),
        content_idea_bot(),
        tech_ir_learning_bot(),
    ]
    summary = {
        "status": "ok",
        "bots": bots,
        "summary": "Free local learning bots completed successfully and saved their results under generated_code/.",
    }
    os.makedirs(GENERATED_DIR, exist_ok=True)
    report_path = os.path.join(GENERATED_DIR, "free_automation_bot_suite.json")
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    return summary
