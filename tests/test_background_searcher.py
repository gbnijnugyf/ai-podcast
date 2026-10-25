import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
import yaml
from PIL import Image

from src.background_searcher import BackgroundSearcher, MediaCandidate


class MediaPolicyTests(unittest.TestCase):
    def write_config(self, directory, *, pexels="", required=None, ratio=0.8):
        path = Path(directory) / "config.yaml"
        path.write_text(yaml.safe_dump({
            "pexels": {"api_key": pexels, "cache_dir": str(Path(directory) / "cache")},
            "pixabay": {},
            "slides": {"width": 1920, "height": 1080},
            "media": {"required_sources": required or [], "min_online_media_ratio": ratio},
        }), encoding="utf-8")
        return path

    def test_required_pexels_key_fails_before_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_config(directory, required=["pexels"])
            with self.assertRaisesRegex(RuntimeError, "pexels"):
                BackgroundSearcher(path)

    def test_optional_pixabay_does_not_block_pexels_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_config(directory, pexels="proxy-placeholder", required=["pexels"])
            searcher = BackgroundSearcher(path)
        self.assertEqual(searcher.min_online_media_ratio, 0.8)

    def test_online_coverage_threshold_is_enforced(self):
        searcher = BackgroundSearcher.__new__(BackgroundSearcher)
        searcher.min_online_media_ratio = 0.8
        searcher._enforce_online_coverage(4, 5)
        with self.assertRaisesRegex(RuntimeError, "below required 80%"):
            searcher._enforce_online_coverage(3, 5)

    def test_download_writes_online_media_report(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_config(directory, pexels="proxy-placeholder", required=["pexels"])
            source = Path(directory) / "source.jpg"
            output = Path(directory) / "output"
            Image.new("RGB", (32, 18), "blue").save(source)
            candidate = MediaCandidate("42", "pexels", "image", "https://images.pexels.com/42.jpg")
            searcher = BackgroundSearcher(path)
            script = {"chapters": [{"blocks": [{
                "keyword": "humanoid robot face", "narration": "仿生机器人面部技术",
            }]}]}
            with patch.object(searcher, "_gather_candidates", return_value=([candidate], [])), \
                    patch.object(searcher, "_download_candidate", return_value=str(source)):
                paths = searcher.download_for_script(script, str(output))

            report = json.loads((output / "media_sources.json").read_text(encoding="utf-8"))
            self.assertEqual(len(paths), 1)
            self.assertEqual(report["ratio"], 1.0)
            self.assertEqual(report["items"][0]["source"], "pexels")

    def test_request_errors_do_not_include_secret_urls(self):
        response = Mock(status_code=401)
        error = requests.HTTPError("https://pixabay.com/api/?key=must-not-leak", response=response)
        self.assertEqual(BackgroundSearcher._safe_request_error(error), "HTTP 401")


if __name__ == "__main__":
    unittest.main()
