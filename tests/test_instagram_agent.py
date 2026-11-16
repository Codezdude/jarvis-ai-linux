import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules import instagram_agent as ig


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class InstagramAgentTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db = self.tmp / "ig.db"
        self.media = self.tmp / "media"
        self.image = self.media / "card.png"

    def tearDown(self):
        self._tmp.cleanup()


class TestPillowCard(InstagramAgentTestBase):
    def test_render_pillow_card_creates_valid_png(self):
        out = ig._render_pillow_card("Hello JARVIS", "A short subtitle", self.image)
        self.assertTrue(out.exists())
        self.assertGreater(out.stat().st_size, 0)
        with out.open("rb") as fh:
            self.assertEqual(fh.read(8), b"\x89PNG\r\n\x1a\n")

    def test_render_pillow_card_creates_parent_dirs(self):
        nested = self.tmp / "a" / "b" / "card.png"
        out = ig._render_pillow_card("Nested", "", nested)
        self.assertTrue(out.exists())


class TestSchedulePost(InstagramAgentTestBase):
    def _make_image(self) -> Path:
        return ig._render_pillow_card("Topic", "Sub", self.image)

    def test_rejected_when_not_approved(self):
        img = self._make_image()
        res = ig.create_and_schedule_post(
            topic="Launch day",
            caption="Ready to ship.",
            image_path=str(img),
            approval_fn=lambda *_: False,
            db_path=self.db,
            media_dir=self.media,
        )
        self.assertEqual(res["status"], "rejected")
        self.assertFalse(res["approved"])

        rows = ig.list_scheduled_posts(db_path=self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "rejected")
        self.assertEqual(rows[0]["approved"], 0)

    def test_scheduled_when_approved(self):
        img = self._make_image()
        res = ig.create_and_schedule_post(
            topic="Launch day",
            caption="Ready to ship.",
            image_path=str(img),
            approval_fn=lambda *_: True,
            db_path=self.db,
            media_dir=self.media,
        )
        self.assertEqual(res["status"], "scheduled")
        self.assertTrue(res["approved"])

        rows = ig.list_scheduled_posts(status="scheduled", db_path=self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["approved"], 1)

    def test_auto_approve_bypasses_hook(self):
        img = self._make_image()
        res = ig.create_and_schedule_post(
            topic="Auto", caption="c", image_path=str(img),
            auto_approve=True, db_path=self.db, media_dir=self.media,
        )
        self.assertTrue(res["approved"])

    def test_caption_falls_back_to_ai_router(self):
        img = self._make_image()
        with patch("modules.ai.ask_local_brain", return_value="AI generated caption #test"):
            res = ig.create_and_schedule_post(
                topic="AI topic", image_path=str(img),
                auto_approve=True, db_path=self.db, media_dir=self.media,
            )
        self.assertEqual(res["caption"], "AI generated caption #test")

    def test_caption_fallback_when_ai_unavailable(self):
        img = self._make_image()
        with patch("modules.ai.ask_local_brain", side_effect=RuntimeError("offline")):
            caption = ig.generate_instagram_caption("My Topic")
        self.assertIn("My Topic", caption)
        self.assertIn("#", caption)


class TestProcessDuePosts(InstagramAgentTestBase):
    def _make_image(self) -> Path:
        return ig._render_pillow_card("Topic", "Sub", self.image)

    def _seed(self, when: datetime, approved: bool = True) -> dict:
        return ig.create_and_schedule_post(
            topic=f"Post {when.isoformat()}",
            caption="hello",
            image_path=str(self._make_image()),
            auto_approve=approved,
            scheduled_at_utc=_iso(when),
            db_path=self.db,
            media_dir=self.media,
        )

    def test_future_posts_are_ignored(self):
        now = datetime.now(timezone.utc)
        future = self._seed(now + timedelta(days=1))

        results = ig.process_due_instagram_posts(
            now_utc=now,
            db_path=self.db,
            publish_fn=lambda url, cap: "IG123",
            url_resolver_fn=lambda p, u: "https://cdn.example.com/x.png",
        )
        self.assertEqual(results, [])
        rows = ig.list_scheduled_posts(db_path=self.db)
        self.assertEqual(rows[0]["status"], "scheduled")
        self.assertEqual(rows[0]["id"], future["id"])

    def test_due_posts_publish_and_update_status(self):
        now = datetime.now(timezone.utc)
        past = self._seed(now - timedelta(hours=1))

        calls = {}

        def _publish(url, caption):
            calls["url"] = url
            calls["caption"] = caption
            return "IG-MEDIA-999"

        results = ig.process_due_instagram_posts(
            now_utc=now,
            db_path=self.db,
            publish_fn=_publish,
            url_resolver_fn=lambda p, u: "https://cdn.example.com/past.png",
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "published")
        self.assertEqual(results[0]["ig_media_id"], "IG-MEDIA-999")
        self.assertEqual(calls["url"], "https://cdn.example.com/past.png")

        rows = ig.list_scheduled_posts(db_path=self.db)
        self.assertEqual(rows[0]["status"], "published")
        self.assertEqual(rows[0]["ig_media_id"], "IG-MEDIA-999")
        self.assertEqual(rows[0]["public_image_url"], "https://cdn.example.com/past.png")
        self.assertTrue(rows[0]["published_at"])

    def test_failed_publish_records_error(self):
        now = datetime.now(timezone.utc)
        self._seed(now - timedelta(minutes=5))

        def _boom(url, caption):
            raise RuntimeError("Graph API rejected container")

        results = ig.process_due_instagram_posts(
            now_utc=now,
            db_path=self.db,
            publish_fn=_boom,
            url_resolver_fn=lambda p, u: "https://cdn.example.com/x.png",
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "failed")
        self.assertIn("Graph API rejected", results[0]["error"])

        rows = ig.list_scheduled_posts(db_path=self.db)
        self.assertEqual(rows[0]["status"], "failed")
        self.assertIn("Graph API rejected", rows[0]["error_message"])

    def test_unapproved_posts_are_never_published(self):
        now = datetime.now(timezone.utc)
        self._seed(now - timedelta(hours=2), approved=False)

        results = ig.process_due_instagram_posts(
            now_utc=now,
            db_path=self.db,
            publish_fn=lambda url, cap: "IG-SHOULD-NOT-HAPPEN",
            url_resolver_fn=lambda p, u: "https://cdn.example.com/x.png",
        )
        self.assertEqual(results, [])
        self.assertEqual(ig.list_scheduled_posts(db_path=self.db)[0]["status"], "rejected")

    def test_url_resolver_receives_existing_public_url(self):
        now = datetime.now(timezone.utc)
        ig.create_and_schedule_post(
            topic="With URL",
            caption="c",
            image_path=str(self._make_image()),
            auto_approve=True,
            scheduled_at_utc=_iso(now - timedelta(minutes=1)),
            public_image_url="https://already.example.com/x.png",
            db_path=self.db,
            media_dir=self.media,
        )

        seen = {}

        def _resolver(path, existing):
            seen["existing"] = existing
            return existing

        ig.process_due_instagram_posts(
            now_utc=now,
            db_path=self.db,
            publish_fn=lambda url, cap: "IG1",
            url_resolver_fn=_resolver,
        )
        self.assertEqual(seen["existing"], "https://already.example.com/x.png")


class TestEnvLoading(InstagramAgentTestBase):
    def test_env_delegates_to_project_dotenv_loader(self):
        values = {"IG_USER_ID": "17841400000000000", "IG_ACCESS_TOKEN": "tok"}
        with patch(
            "modules.ai.get_env_value",
            side_effect=lambda name, default="": values.get(name, default),
        ):
            self.assertEqual(ig._env("IG_USER_ID"), "17841400000000000")
            self.assertEqual(ig._env("IG_ACCESS_TOKEN"), "tok")
            self.assertEqual(ig._env("MISSING_KEY", "fallback"), "fallback")

    def test_env_falls_back_to_os_environ_when_ai_unavailable(self):
        with patch("modules.ai.get_env_value", side_effect=RuntimeError("ai import failed")), \
             patch.dict(os.environ, {"IG_USER_ID": "999"}):
            self.assertEqual(ig._env("IG_USER_ID"), "999")

    def test_publish_uses_env_credentials(self):
        sentinel = RuntimeError("reached network")

        def _fake_env(name, default=""):
            return {"IG_USER_ID": "uid-1", "IG_ACCESS_TOKEN": "tok-1"}.get(name, default)

        with patch("modules.instagram_agent._env", side_effect=_fake_env), \
             patch("modules.instagram_agent.urllib.request.urlopen", side_effect=sentinel):
            with self.assertRaises(RuntimeError) as ctx:
                ig.publish_to_instagram_graph("https://x/y.png", "cap")

        # Got past the credential check and attempted the Graph API call.
        self.assertIs(ctx.exception, sentinel)


class TestPollinationsImageTier(InstagramAgentTestBase):
    def test_try_pollinations_raises_on_tiny_payload(self):
        with patch("modules.instagram_agent.urllib.request.urlopen") as mock_open:
            mock_open.return_value.read.return_value = b"nope"
            with self.assertRaises(RuntimeError):
                ig._try_pollinations_image("a prompt")

    def test_try_pollinations_returns_bytes_on_success(self):
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 4000
        with patch("modules.instagram_agent.urllib.request.urlopen") as mock_open:
            mock_open.return_value.read.return_value = png_bytes
            data = ig._try_pollinations_image("a prompt")
        self.assertEqual(data, png_bytes)

    def test_generate_image_falls_back_to_pillow_when_pollinations_fails(self):
        out = self.tmp / "fallback.png"
        with patch("modules.instagram_agent._try_pollinations_image", side_effect=RuntimeError("offline")):
            result = ig.generate_instagram_image(
                prompt="test topic",
                headline="test topic",
                subtitle="",
                media_dir=self.tmp,
            )
        self.assertTrue(result.exists())
        self.assertGreater(result.stat().st_size, 0)
        with result.open("rb") as fh:
            self.assertEqual(fh.read(8), b"\x89PNG\r\n\x1a\n")

    def test_generate_image_uses_pollinations_bytes_when_available(self):
        out = self.tmp / "ai.png"
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 4000
        with patch("modules.instagram_agent._try_pollinations_image", return_value=png_bytes):
            result = ig.generate_instagram_image(prompt="ai topic", media_dir=self.tmp)
        self.assertEqual(result, out)
        self.assertEqual(result.read_bytes(), png_bytes)


class TestResolvePublicUrl(InstagramAgentTestBase):
    def test_existing_https_url_is_returned(self):
        self.assertEqual(
            ig.resolve_public_image_url(Path("/tmp/x.png"), "https://cdn.example.com/x.png"),
            "https://cdn.example.com/x.png",
        )

    def test_public_base_url_env_is_used(self):
        with patch.dict(os.environ, {"IG_PUBLIC_BASE_URL": "https://cdn.example.com/base/"}):
            url = ig.resolve_public_image_url(Path("/tmp/pic.png"))
        self.assertEqual(url, "https://cdn.example.com/base/pic.png")

    def test_raises_without_url_source(self):
        with patch.dict(os.environ, {"IG_PUBLIC_BASE_URL": "", "IMGBB_API_KEY": ""}):
            with self.assertRaises(RuntimeError):
                ig.resolve_public_image_url(Path("/tmp/missing.png"))


class TestPublishGraphApi(InstagramAgentTestBase):
    def test_missing_credentials_raise(self):
        with patch.dict(os.environ, {"IG_USER_ID": "", "IG_ACCESS_TOKEN": ""}):
            with self.assertRaises(RuntimeError):
                ig.publish_to_instagram_graph("https://x/y.png", "cap")

    def test_two_step_publish_flow(self):
        responses = [
            {"id": "container-1"},
            {"id": "media-1"},
        ]
        captured = []

        class _Resp:
            def __init__(self, payload):
                self._payload = payload

            def read(self):
                import json

                return json.dumps(self._payload).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def _fake_urlopen(req, timeout=None):
            captured.append(req.full_url)
            return _Resp(responses[len(captured) - 1])

        with patch.dict(os.environ, {"IG_USER_ID": "17841400000000000", "IG_ACCESS_TOKEN": "tok"}), \
             patch("modules.instagram_agent.urllib.request.urlopen", side_effect=_fake_urlopen):
            media_id = ig.publish_to_instagram_graph("https://cdn.example.com/x.png", "hello")

        self.assertEqual(media_id, "media-1")
        self.assertEqual(len(captured), 2)
        self.assertTrue(captured[0].endswith("/media"))
        self.assertTrue(captured[1].endswith("/media_publish"))


if __name__ == "__main__":
    unittest.main()