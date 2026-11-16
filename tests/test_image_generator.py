import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules import tools as T


class ImageGeneratorTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.assets = self.tmp / "assets"
        self.assets.mkdir()

    def tearDown(self):
        self._tmp.cleanup()


class TestPollinationsTier(ImageGeneratorTestBase):
    def _fake_response(self, payload):
        class _Resp:
            def __init__(self, data):
                self._data = data

            def read(self):
                return self._data

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return _Resp(payload)

    def test_try_pollinations_raises_on_tiny_payload(self):
        with patch("modules.tools.urllib.request.urlopen", return_value=self._fake_response(b"nope")):
            with self.assertRaises(RuntimeError):
                T._try_pollinations_image("a prompt")

    def test_try_pollinations_returns_bytes_on_success(self):
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 4000
        with patch("modules.tools.urllib.request.urlopen", return_value=self._fake_response(png_bytes)):
            data = T._try_pollinations_image("a prompt")
        self.assertEqual(data, png_bytes)

    def test_generate_image_uses_pollinations_when_available(self):
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 4000
        with patch("modules.tools.urllib.request.urlopen", return_value=self._fake_response(png_bytes)):
            with patch.object(T, "ASSETS_DIR", self.assets):
                result = T.generate_image(prompt="ai topic", filename="ai.png")
        self.assertTrue((self.assets / "ai.png").exists())
        self.assertIn("assets/ai.png", result)

    def test_generate_image_falls_back_to_pillow_when_pollinations_fails(self):
        with patch("modules.tools._try_pollinations_image", side_effect=RuntimeError("offline")):
            with patch.object(T, "ASSETS_DIR", self.assets):
                result = T.generate_image(prompt="fallback topic", filename="fb.png")
        out = self.assets / "fb.png"
        self.assertTrue(out.exists())
        self.assertGreater(out.stat().st_size, 0)
        with out.open("rb") as fh:
            self.assertEqual(fh.read(8), b"\x89PNG\r\n\x1a\n")
        self.assertIn("assets/fb.png", result)

    def test_generate_image_skips_gemini_when_no_key(self):
        with patch("modules.tools._try_pollinations_image", side_effect=RuntimeError("no net")):
            with patch.object(T, "ASSETS_DIR", self.assets):
                with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
                    result = T.generate_image(prompt="no key", filename="nokey.png")
        self.assertTrue((self.assets / "nokey.png").exists())


if __name__ == "__main__":
    unittest.main()