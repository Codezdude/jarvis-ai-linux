"""Instagram content creation, scheduling, and Graph API publishing.

Stdlib-only HTTP/SQLite plus Pillow (graceful fallback when PIL is missing).
Nothing is ever published without prior user approval: posts are stored with
``approved = 0`` / ``status = 'rejected'`` unless the approval hook (or an
explicit ``approval_fn`` / ``auto_approve``) grants it.
"""

import base64
import inspect
import json
import os
import sqlite3
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_IG_DB_PATH = PROJECT_ROOT / "data" / "jarvis_workspace.db"
DEFAULT_MEDIA_DIR = PROJECT_ROOT / "data" / "ig_media"
GRAPH_API_VERSION = os.getenv("IG_GRAPH_API_VERSION", "v21.0")


def _env(name: str, default: str = "") -> str:
    """Read a setting from the environment, loading the project ``.env`` first.

    ``modules.ai`` loads ``.env`` into ``os.environ`` at import time, but this
    module must not depend on import order, so load it explicitly (with a
    fallback if the AI router is unavailable).
    """
    try:
        from modules.ai import get_env_value

        return get_env_value(name, default)
    except Exception:
        return os.getenv(name, default)


def _ensure_ig_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS instagram_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT NOT NULL,
            caption TEXT NOT NULL,
            image_path TEXT DEFAULT '',
            public_image_url TEXT DEFAULT '',
            scheduled_at_utc TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'scheduled',
            approved INTEGER NOT NULL DEFAULT 0,
            ig_media_id TEXT DEFAULT '',
            error_message TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            published_at TEXT DEFAULT ''
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ig_posts_status_time ON instagram_posts(status, scheduled_at_utc)")
    conn.commit()


def generate_instagram_caption(
    topic: str,
    tone: str = "engaging, authentic, concise",
    hashtag_count: int = 8,
    call_to_action: str = "",
) -> str:
    """Use J.A.R.V.I.S.'s AI router to write an Instagram caption + hashtags."""
    cta_line = f"Include this call to action: {call_to_action}\n" if call_to_action else ""
    prompt = (
        "Write an Instagram post caption for the following topic.\n"
        f"Topic: {topic}\n"
        f"Tone: {tone}\n"
        f"{cta_line}"
        f"Rules: Start with a strong 1-line hook, keep body scannable with short line breaks, "
        f"and end with {hashtag_count} relevant hashtags. Return ONLY the ready-to-post caption text."
    )
    try:
        from modules.ai import ask_local_brain

        reply = ask_local_brain(prompt, is_urgent=True)
        if reply and reply.strip():
            return reply.strip()
    except Exception:
        pass
    base_tag = "".join(ch for ch in topic.title() if ch.isalnum())[:24] or "Update"
    return f"{topic.strip()}\n\n#{base_tag} #DailyUpdate #BuildInPublic"


def _render_pillow_card(headline: str, subtitle: str, out_path: Path, handle: str = "") -> Path:
    """Render a clean 1080x1080 square graphic card locally using Pillow."""
    from PIL import Image, ImageDraw, ImageFont

    out_path.parent.mkdir(parents=True, exist_ok=True)
    size = 1080
    img = Image.new("RGB", (size, size), color=(14, 18, 28))
    draw = ImageDraw.Draw(img)

    # Subtle accent border & top bar
    draw.rectangle([36, 36, size - 36, size - 36], outline=(56, 189, 248), width=4)
    draw.rectangle([72, 96, 220, 106], fill=(56, 189, 248))

    try:
        title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 54)
        body_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 34)
        footer_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
    except Exception:
        title_font = ImageFont.load_default()
        body_font = title_font
        footer_font = title_font

    wrapped_title = textwrap.fill(headline.strip()[:140], width=24)
    draw.multiline_text((76, 150), wrapped_title, fill=(248, 250, 252), font=title_font, spacing=16)

    if subtitle.strip():
        wrapped_sub = textwrap.fill(subtitle.strip()[:240], width=38)
        draw.multiline_text((76, 560), wrapped_sub, fill=(148, 163, 184), font=body_font, spacing=12)

    footer_text = handle or _env("IG_HANDLE", "@jarvis")
    draw.text((76, size - 115), footer_text, fill=(56, 189, 248), font=footer_font)
    img.save(out_path, format="PNG")
    return out_path


POLLINATIONS_BASE_URL = "https://image.pollinations.ai/prompt"


def _pollinations_model() -> str:
    """Pollinations model name (free tier default: flux-realism)."""
    return _env("IG_IMAGE_MODEL", "flux-realism").strip() or "flux-realism"


def _try_pollinations_image(prompt: str, width: int = 1080, height: int = 1080, timeout: int = 60) -> bytes:
    """Free, no-key AI image via Pollinations.ai REST endpoint. Returns PNG bytes or raises."""
    query = urllib.parse.urlencode({
        "width": width,
        "height": height,
        "nologo": "true",
        "model": _pollinations_model(),
    })
    url = f"{POLLINATIONS_BASE_URL}/{urllib.parse.quote(prompt)}?{query}"
    req = urllib.request.Request(url, headers={"User-Agent": "JARVIS/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    if len(data) < 2000:
        raise RuntimeError(f"Pollinations returned suspiciously small payload ({len(data)} bytes)")
    return data


def generate_instagram_image(
    prompt: str,
    headline: str = "",
    subtitle: str = "",
    media_dir: Path = DEFAULT_MEDIA_DIR,
    prefer_ai_image: bool = True,
) -> Path:
    """Generate a 1080x1080 post image via free AI tiers, falling back to local Pillow card.

    Tier order (all free, no key required for tier 1):
      1. Pollinations.ai (always available, no API key)
      2. Gemini Imagen (only when GEMINI_API_KEY is set in .env)
      3. Local Pillow card (offline template)
    """
    media_dir.mkdir(parents=True, exist_ok=True)
    out_path = media_dir / f"ig_{int(time.time() * 1000)}.png"

    if prefer_ai_image:
        # Tier 1 — free, no key
        try:
            out_path.write_bytes(_try_pollinations_image(prompt))
            print(f"[INSTAGRAM] AI image via Pollinations.ai -> {out_path}")
            return out_path
        except Exception as exc:
            print(f"[INSTAGRAM] Pollinations image failed: {exc}")

        # Tier 2 — Gemini Imagen, only if a key is configured
        gemini_key = _env("GEMINI_API_KEY", "").strip()
        if gemini_key:
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"imagen-3.0-generate-002:predict?key={urllib.parse.quote(gemini_key)}"
            )
            payload = json.dumps({
                "instances": [{"prompt": prompt}],
                "parameters": {"sampleCount": 1, "aspectRatio": "1:1"},
            }).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=45) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                preds = data.get("predictions") or []
                b64_img = preds[0].get("bytesBase64Encoded") if preds else None
                if b64_img:
                    out_path.write_bytes(base64.b64decode(b64_img))
                    print(f"[INSTAGRAM] AI image via Gemini Imagen -> {out_path}")
                    return out_path
            except Exception as exc:
                print(f"[INSTAGRAM] Gemini image failed: {exc}")

    return _render_pillow_card(headline or prompt, subtitle, out_path)


def resolve_public_image_url(image_path: Path, existing_url: str = "") -> str:
    """Return a public HTTPS image URL required by Meta's Instagram Graph API."""
    if existing_url.startswith("https://"):
        return existing_url

    base_url = _env("IG_PUBLIC_BASE_URL", "").strip()
    if base_url:
        return f"{base_url.rstrip('/')}/{image_path.name}"

    imgbb_key = _env("IMGBB_API_KEY", "").strip()
    if imgbb_key and image_path.is_file():
        b64_data = base64.b64encode(image_path.read_bytes()).decode("ascii")
        body = urllib.parse.urlencode({"key": imgbb_key, "image": b64_data}).encode("utf-8")
        req = urllib.request.Request("https://api.imgbb.com/1/upload", data=body)
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        url = payload.get("data", {}).get("url", "")
        if url.startswith("http"):
            return url.replace("http://", "https://", 1)

    raise RuntimeError(
        "No public HTTPS image URL available. Set IG_PUBLIC_BASE_URL or IMGBB_API_KEY in environment."
    )


def publish_to_instagram_graph(
    image_url: str,
    caption: str,
    ig_user_id: Optional[str] = None,
    access_token: Optional[str] = None,
) -> str:
    """Execute Meta's 2-step Instagram Graph API container creation and publish flow."""
    uid = (ig_user_id or _env("IG_USER_ID", "")).strip()
    token = (access_token or _env("IG_ACCESS_TOKEN", "")).strip()
    if not uid or not token:
        raise RuntimeError("Missing IG_USER_ID or IG_ACCESS_TOKEN environment variables.")

    base = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{urllib.parse.quote(uid)}"

    # Step 1: Create media container
    create_body = urllib.parse.urlencode({
        "image_url": image_url,
        "caption": caption,
        "access_token": token,
    }).encode("utf-8")
    create_req = urllib.request.Request(f"{base}/media", data=create_body)
    with urllib.request.urlopen(create_req, timeout=30) as resp:
        create_res = json.loads(resp.read().decode("utf-8"))
    creation_id = create_res.get("id")
    if not creation_id:
        raise RuntimeError(f"Instagram container creation failed: {create_res}")

    # Step 2: Publish container
    pub_body = urllib.parse.urlencode({
        "creation_id": creation_id,
        "access_token": token,
    }).encode("utf-8")
    pub_req = urllib.request.Request(f"{base}/media_publish", data=pub_body)
    with urllib.request.urlopen(pub_req, timeout=30) as resp:
        pub_res = json.loads(resp.read().decode("utf-8"))
    media_id = pub_res.get("id")
    if not media_id:
        raise RuntimeError(f"Instagram media_publish failed: {pub_res}")
    return str(media_id)


def _request_post_approval(summary: str, diff_preview: str = "") -> bool:
    from modules import approval

    for fn_name in ("request_approval", "ask_approval", "prompt_user", "request_user_approval"):
        fn = getattr(approval, fn_name, None)
        if not callable(fn):
            continue
        sig = inspect.signature(fn)
        params = sig.parameters
        kwargs = {}
        for k, v in (
            ("action", "instagram_post"),
            ("title", "Approve Instagram Post"),
            ("details", summary),
            ("message", summary),
            ("prompt", summary),
            ("diff_preview", diff_preview),
        ):
            if k in params:
                kwargs[k] = v
        if kwargs:
            return bool(fn(**kwargs))
        return bool(fn(summary))

    # Fallback: the real approval gate used by modules/ui.py.
    check_approval = getattr(approval, "check_approval", None)
    if callable(check_approval):
        try:
            result = check_approval(
                action="instagram_post",
                target="instagram",
                actor="system",
                source="self",
                reason=summary,
                diff=diff_preview,
            )
            if isinstance(result, dict) and "approved" in result:
                return bool(result["approved"])
        except Exception:
            pass

    callback = getattr(approval, "_UI_PROMPT_CALLBACK", None)
    if callable(callback):
        try:
            return bool(callback({
                "request_id": "instagram_post",
                "actor": "system",
                "source": "self",
                "action": "instagram_post",
                "risk_tier": "elevated",
                "target": "instagram",
                "reason": summary,
                "reasons": ["external_publish"],
                "diff": diff_preview,
            }))
        except Exception:
            return False
    return False


def create_and_schedule_post(
    topic: str,
    scheduled_at_utc: Optional[str] = None,
    caption: Optional[str] = None,
    image_prompt: Optional[str] = None,
    image_path: Optional[str] = None,
    public_image_url: str = "",
    auto_approve: bool = False,
    approval_fn: Optional[Callable[[str, str], bool]] = None,
    db_path: Path = DEFAULT_IG_DB_PATH,
    media_dir: Path = DEFAULT_MEDIA_DIR,
) -> dict:
    """Draft caption + image, request user approval, and store in the SQLite schedule queue."""
    final_caption = (caption or "").strip() or generate_instagram_caption(topic)
    if image_path:
        final_img = Path(image_path)
    else:
        final_img = generate_instagram_image(
            prompt=image_prompt or f"Minimalist modern square social media graphic about {topic}",
            headline=topic,
            subtitle=final_caption.splitlines()[0][:140],
            media_dir=media_dir,
        )

    when_utc = scheduled_at_utc or datetime.now(timezone.utc).isoformat()
    preview = f"Schedule (UTC): {when_utc}\nImage: {final_img}\n\nCaption:\n{final_caption}"

    if auto_approve:
        approved = True
    elif approval_fn is not None:
        approved = bool(approval_fn(preview, final_caption))
    else:
        approved = _request_post_approval(preview, diff_preview=final_caption)

    status = "scheduled" if approved else "rejected"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        _ensure_ig_schema(conn)
        cur = conn.execute(
            """
            INSERT INTO instagram_posts
            (topic, caption, image_path, public_image_url, scheduled_at_utc, status, approved, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                topic,
                final_caption,
                str(final_img),
                public_image_url,
                when_utc,
                status,
                1 if approved else 0,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        post_id = int(cur.lastrowid)
    finally:
        conn.close()

    return {
        "id": post_id,
        "status": status,
        "approved": approved,
        "scheduled_at_utc": when_utc,
        "image_path": str(final_img),
        "caption": final_caption,
    }


def list_scheduled_posts(status: Optional[str] = None, db_path: Path = DEFAULT_IG_DB_PATH) -> list[dict]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        _ensure_ig_schema(conn)
        if status:
            rows = conn.execute(
                "SELECT * FROM instagram_posts WHERE status = ? ORDER BY scheduled_at_utc ASC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM instagram_posts ORDER BY scheduled_at_utc DESC LIMIT 50").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def process_due_instagram_posts(
    now_utc: Optional[datetime] = None,
    db_path: Path = DEFAULT_IG_DB_PATH,
    publish_fn: Callable[[str, str], str] = publish_to_instagram_graph,
    url_resolver_fn: Callable[[Path, str], str] = resolve_public_image_url,
) -> list[dict]:
    """Publish all approved scheduled posts whose scheduled_at_utc <= now_utc."""
    now = now_utc or datetime.now(timezone.utc)
    now_iso = now.isoformat()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    results = []
    try:
        _ensure_ig_schema(conn)
        due = conn.execute(
            """
            SELECT * FROM instagram_posts
            WHERE status = 'scheduled' AND approved = 1 AND scheduled_at_utc <= ?
            ORDER BY scheduled_at_utc ASC
            """,
            (now_iso,),
        ).fetchall()

        for row in due:
            post_id = row["id"]
            try:
                img_url = url_resolver_fn(Path(row["image_path"]), row["public_image_url"] or "")
                media_id = publish_fn(img_url, row["caption"])
                conn.execute(
                    """
                    UPDATE instagram_posts
                    SET status = 'published', public_image_url = ?, ig_media_id = ?, published_at = ?, error_message = ''
                    WHERE id = ?
                    """,
                    (img_url, media_id, datetime.now(timezone.utc).isoformat(), post_id),
                )
                conn.commit()
                results.append({"id": post_id, "status": "published", "ig_media_id": media_id})
            except Exception as exc:
                conn.execute(
                    "UPDATE instagram_posts SET status = 'failed', error_message = ? WHERE id = ?",
                    (str(exc)[:500], post_id),
                )
                conn.commit()
                results.append({"id": post_id, "status": "failed", "error": str(exc)})
    finally:
        conn.close()
    return results