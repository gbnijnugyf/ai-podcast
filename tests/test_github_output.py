import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request


SPEC = importlib.util.spec_from_file_location(
    "github_output", Path(__file__).resolve().parents[1] / "scripts" / "github_output.py"
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class OutputTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.video = Path(self.directory.name) / "video.mp4"
        self.video.write_bytes(b"test artifact; not a real video")
        self.client = module.GitHub("gbnijnugyf/ai-podcast-output", "test-only-token")

    def test_public_repository_is_rejected(self):
        self.client.request = Mock(return_value={"private": False})
        with self.assertRaisesRegex(RuntimeError, "public"):
            self.client.private_repo()

    def test_wrong_account_cannot_initialize_repository(self):
        self.client.request = Mock(return_value={"login": "someone-else"})
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            self.client.initialize()
        self.client.request.assert_called_once_with("GET", "/user")

    def test_config_enforces_private_storage(self):
        path = Path(self.directory.name) / "output-config.json"
        path.write_text(json.dumps({
            "repository": "gbnijnugyf/ai-podcast-output",
            "private_required": False,
            "token_environment_variable": "PODCAST_GITHUB_TOKEN",
        }), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "private"):
            module.load_config(path)

    def test_cloud_uses_configured_key_and_accepts_proxy_placeholder(self):
        with patch.dict(os.environ, {"CUSTOM_TOKEN": "proxy-placeholder"}, clear=True):
            with patch.object(module.subprocess, "run") as run:
                self.assertEqual(
                    module.load_token("gbnijnugyf", "CUSTOM_TOKEN", cloud=True), "proxy-placeholder"
                )
                run.assert_not_called()

    def test_cloud_missing_key_does_not_use_other_tokens_or_local_login(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "unrelated-token"}, clear=True):
            with patch.object(module.subprocess, "run") as run:
                with self.assertRaisesRegex(RuntimeError, "Missing PODCAST_GITHUB_TOKEN"):
                    module.load_token("gbnijnugyf", cloud=True)
                run.assert_not_called()

    def test_smoke_test_uploads_only_generated_fixture_and_keeps_draft(self):
        client = Mock()
        client.upload.return_value = {"release": "draft-url", "draft": True}
        contents = []

        def upload(tag, paths, publish):
            self.assertFalse(publish)
            self.assertEqual(len(paths), 1)
            self.assertEqual(paths[0].name, "cloud-verification.txt")
            contents.append(paths[0].read_bytes())
            return {"release": "draft-url", "draft": True}

        client.upload.side_effect = upload
        result = module.smoke_test(client, "podcast-2026-10-20-cloud-check")
        self.assertTrue(result["repeat_verified"])
        self.assertEqual(len(contents), 2)
        self.assertEqual(contents[0], contents[1])

    def test_upload_streams_file_and_verifies_before_publishing(self):
        tag = "podcast-2026-10-20-001"
        self.client.private_repo = Mock()
        self.client.list_all = Mock(side_effect=[[], []])
        self.client.request = Mock(side_effect=[
            {"id": 1, "tag_name": tag, "draft": True, "html_url": "test-release"},
            {"id": 1, "tag_name": tag, "draft": False, "html_url": "test-release"},
        ])
        asset = {"name": self.video.name, "state": "uploaded",
                 "size": self.video.stat().st_size,
                 "digest": "sha256:" + module.digest(self.video)}

        def open_request(request, timeout):
            self.assertTrue(request.full_url.startswith("https://uploads.github.com/"))
            self.assertEqual(request.data.read(), self.video.read_bytes())
            self.assertEqual(int(request.get_header("Content-length")), self.video.stat().st_size)
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read = Mock(return_value=json.dumps(asset).encode())
            return response

        self.client.opener = Mock()
        self.client.opener.open.side_effect = open_request
        result = self.client.upload(tag, [self.video], True)
        self.assertFalse(result["draft"])
        self.assertEqual(self.client.request.call_args.args[0], "PATCH")

    def test_corrupt_remote_asset_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "digest mismatch"):
            self.client.verify_asset(
                {"state": "uploaded", "size": self.video.stat().st_size,
                 "digest": "sha256:" + "0" * 64}, self.video
            )

    def test_repeat_upload_verifies_without_uploading_or_publishing(self):
        tag = "podcast-2026-10-20-001"
        self.client.private_repo = Mock()
        self.client.list_all = Mock(side_effect=[
            [{"id": 1, "tag_name": tag, "draft": True, "html_url": "test-release"}],
            [{"name": self.video.name, "state": "uploaded",
              "size": self.video.stat().st_size,
              "digest": "sha256:" + module.digest(self.video)}],
        ])
        self.client.request = Mock()
        self.client.opener = Mock()
        result = self.client.upload(tag, [self.video], False)
        self.assertTrue(result["draft"])
        self.client.request.assert_not_called()
        self.client.opener.open.assert_not_called()

    def test_failure_leaves_release_draft(self):
        tag = "podcast-2026-10-20-001"
        self.client.private_repo = Mock()
        self.client.list_all = Mock(side_effect=[
            [{"id": 1, "tag_name": tag, "draft": True, "html_url": "test-release"}],
            [{"name": self.video.name, "state": "uploaded", "size": 0}],
        ])
        self.client.request = Mock()
        with self.assertRaisesRegex(RuntimeError, "mismatch"):
            self.client.upload(tag, [self.video], True)
        self.client.request.assert_not_called()

    def test_credentials_and_unreviewed_archives_are_rejected(self):
        for name in ("_config.yaml", ".env", "cookies.json", "checkpoint.zip"):
            with self.subTest(name=name):
                path = Path(self.directory.name) / name
                path.write_bytes(b"sensitive")
                with self.assertRaises(RuntimeError):
                    module.validate_asset(path)

    def test_download_redirect_does_not_forward_github_token(self):
        request = Request(
            "https://api.github.com/repos/a/b/releases/assets/1",
            headers={"Authorization": "Bearer test-only"},
        )
        redirected = module.SafeRedirect().redirect_request(
            request, None, 302, "", {}, "https://release-assets.githubusercontent.com/file"
        )
        self.assertIsNone(redirected.get_header("Authorization"))
        with self.assertRaisesRegex(RuntimeError, "non-HTTPS"):
            module.SafeRedirect().redirect_request(request, None, 302, "", {}, "http://example.com/file")


if __name__ == "__main__":
    unittest.main()
