"""Initialize the private output repository and upload explicit Release assets.

Uses Python's standard library. Credentials stay in memory and never enter files.
This script does not schedule jobs, generate videos, or select files automatically.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


ROOT = Path(__file__).resolve().parents[1]
API = "https://api.github.com"
MAX_ASSET_BYTES = 2 * 1024**3
README = """# ai-podcast-output

Private output repository for gbnijnugyf/ai-podcast.

## Storage

- Store podcast videos as Release assets, never as Git commits.
- Use a unique tag per job: podcast-YYYY-MM-DD-JOBID (Asia/Shanghai date).
- Upload into a draft Release; publish only after verifying all assets.
- Suggested assets: video.mp4, summary.txt, article.json, manifest.json,
  and a reviewed, redacted run.log.
- Add source repository and source commit to manifest.json. Tags in this output
  repository identify output jobs, not the source repository's commits.

## Recovery

Checkpoint archives must contain only the selected job's reusable media,
script, and sanitized state. Never include API keys, config.yaml, _config.yaml,
.env, browser profiles, cookies, or credentials.
Use versioned checkpoint names; verify a new upload before removing old copies.
No automatic cleanup or retention policy is enabled.

## Access

Keep this repository private. Downloads require repository access.
The uploader accepts PODCAST_GITHUB_TOKEN (a token limited to this repository
with Contents: read and write), GH_TOKEN, GITHUB_TOKEN, or an existing local
Git Credential Manager login.
Codex source repository access alone does not grant write access here.

## Scope

Repository initialization and Release uploads do not enable the daily schedule,
ChatGPT browser access, video generation, or automatic TTS retry.
"""
IGNORE = """# Media and credentials belong outside Git history.
*.mp4
*.mp3
*.wav
*.zip
*.tar
*.gz
.env
.env.*
config.yaml
_config.yaml
*cookie*
*credential*
*token*
"""


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlparse(newurl).scheme != "https":
            raise RuntimeError("Refusing a non-HTTPS redirect.")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected and urlparse(newurl).netloc != urlparse(req.full_url).netloc:
            redirected.remove_header("Authorization")
        return redirected


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("private_required") is not True:
        raise RuntimeError("Configuration must require a private output repository.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", config.get("repository", "")):
        raise RuntimeError("Invalid repository name.")
    key = config.get("token_environment_variable", "")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        raise RuntimeError("Invalid token environment variable name.")
    return config


def load_token(owner: str, key: str = "PODCAST_GITHUB_TOKEN", *, cloud: bool = False) -> str:
    # A Codex network-secret placeholder is valid here: the HTTPS proxy replaces
    # it at the destination. Never inspect, print, or persist the secret value.
    for name in ((key,) if cloud else (key, "GH_TOKEN", "GITHUB_TOKEN")):
        if os.environ.get(name):
            return os.environ[name]
    if cloud:
        raise RuntimeError(f"Missing {key}. Configure this key in the Codex cloud environment.")
    try:
        result = subprocess.run(
            ["git", "credential", "fill"],
            input=f"protocol=https\nhost=github.com\nusername={owner}\n\n",
            capture_output=True, text=True, timeout=20,
            env={**os.environ, "GCM_INTERACTIVE": "Never", "GIT_TERMINAL_PROMPT": "0"},
        )
        fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if result.returncode == 0 and fields.get("password"):
            return fields["password"]
    except (OSError, subprocess.TimeoutExpired):
        pass
    raise RuntimeError(f"No GitHub credential available. Configure {key} securely.")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_asset(path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"Asset must be a regular, non-symlink file: {path.name}")
    if not 0 < path.stat().st_size < MAX_ASSET_BYTES:
        raise RuntimeError(f"Asset must be nonempty and smaller than 2 GiB: {path.name}")
    lowered = path.name.lower()
    if lowered.endswith((".yaml", ".yml", ".env", ".pem", ".key")) or any(
        word in lowered for word in (".env", "cookie", "credential", "token", "secret")
    ):
        raise RuntimeError(f"Refusing a possible configuration/credential file: {path.name}")
    if path.suffix.lower() in (".zip", ".gz", ".tar", ".7z"):
        raise RuntimeError("Archive uploads are disabled until a sanitized checkpoint packer is configured.")


class GitHub:
    def __init__(self, repository: str, token: str):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise RuntimeError("Invalid repository name.")
        self.repository = repository
        self.prefix = f"/repos/{repository}"
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "ai-podcast-output",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self.opener = build_opener(SafeRedirect())

    def request(self, method: str, path: str, body=None, *, missing_ok=False):
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(
            API + path, data=payload, method=method,
            headers={**self.headers, "Content-Type": "application/json"},
        )
        try:
            with self.opener.open(request, timeout=90) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code == 404 and missing_ok:
                return None
            raise RuntimeError(f"GitHub returned HTTP {exc.code} for {method} {path}.") from None

    def private_repo(self):
        repo = self.request("GET", self.prefix)
        if repo.get("private") is not True:
            raise RuntimeError("Refusing to use a public output repository.")
        if repo.get("archived"):
            raise RuntimeError("Output repository is archived.")
        return repo

    def initialize(self):
        owner, name = self.repository.split("/")
        identity = self.request("GET", "/user")
        if identity["login"].lower() != owner.lower():
            raise RuntimeError("Authenticated account does not match the configured owner.")
        repo = self.request("GET", self.prefix, missing_ok=True)
        created = repo is None
        if created:
            repo = self.request("POST", "/user/repos", {
                "name": name, "private": True, "auto_init": False,
                "description": "Private podcast videos and job artifacts stored as GitHub Release assets.",
                "has_issues": False, "has_projects": False, "has_wiki": False,
            })
        self.private_repo()
        for filename, content in (("README.md", README), (".gitignore", IGNORE)):
            existing = self.request("GET", self.prefix + f"/contents/{filename}", missing_ok=True)
            if existing:
                if base64.b64decode(existing["content"]).decode("utf-8") != content:
                    raise RuntimeError(f"Existing {filename} differs; refusing to overwrite it.")
                continue
            self.request("PUT", self.prefix + f"/contents/{filename}", {
                "message": f"Initialize {filename}",
                "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            })
        return {"repository": repo["html_url"], "private": True, "created": created}

    def list_all(self, path: str):
        items = []
        for page in range(1, 1001):
            batch = self.request("GET", path + f"?per_page=100&page={page}")
            items.extend(batch)
            if len(batch) < 100:
                return items
        raise RuntimeError("Pagination limit exceeded.")

    def verify_asset(self, asset, path: Path):
        if asset.get("state") != "uploaded" or asset["size"] != path.stat().st_size:
            raise RuntimeError(f"Remote asset size/state mismatch: {path.name}")
        expected = "sha256:" + digest(path)
        if asset.get("digest"):
            if asset["digest"] != expected:
                raise RuntimeError(f"Remote asset digest mismatch: {path.name}")
            return
        # Older API responses may omit digest: verify the actual downloaded bytes.
        url = f"{API}{self.prefix}/releases/assets/{asset['id']}"
        req = Request(url, headers={**self.headers, "Accept": "application/octet-stream"})
        checksum = hashlib.sha256()
        with self.opener.open(req, timeout=300) as response:
            while chunk := response.read(1024 * 1024):
                checksum.update(chunk)
        if "sha256:" + checksum.hexdigest() != expected:
            raise RuntimeError(f"Downloaded asset digest mismatch: {path.name}")

    def upload(self, tag: str, paths: list[Path], publish: bool):
        if not re.fullmatch(r"podcast-\d{4}-\d{2}-\d{2}-[A-Za-z0-9_-]+", tag):
            raise RuntimeError("Tag must be podcast-YYYY-MM-DD-JOBID.")
        names = [path.name for path in paths]
        if len(names) != len(set(names)):
            raise RuntimeError("Asset filenames must be unique.")
        for path in paths:
            validate_asset(path)
        self.private_repo()
        releases = self.list_all(self.prefix + "/releases")
        release = next((item for item in releases if item["tag_name"] == tag), None)
        if release is None:
            release = self.request("POST", self.prefix + "/releases", {
                "tag_name": tag, "name": tag, "draft": True,
                "body": "Podcast output. Files are uploaded explicitly and verified before publication.",
                "make_latest": "false",
            })
        assets_path = self.prefix + f"/releases/{release['id']}/assets"
        assets = {item["name"]: item for item in self.list_all(assets_path)}
        for path in paths:
            if path.name in assets:
                self.verify_asset(assets[path.name], path)
                continue  # Idempotent retry; never overwrite an existing attachment.
            if not release["draft"]:
                raise RuntimeError("Refusing to add files to an already published Release.")
            url = f"https://uploads.github.com{assets_path}?{urlencode({'name': path.name})}"
            with path.open("rb") as stream:
                req = Request(url, data=stream, method="POST", headers={
                    **self.headers, "Content-Length": str(path.stat().st_size),
                    "Content-Type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                })
                try:
                    with self.opener.open(req, timeout=600) as response:
                        asset = json.load(response)
                except HTTPError as exc:
                    raise RuntimeError(f"Asset upload failed with HTTP {exc.code}: {path.name}") from None
            self.verify_asset(asset, path)
        if publish and release["draft"]:
            release = self.request("PATCH", self.prefix + f"/releases/{release['id']}", {
                "draft": False, "make_latest": "false",
            })
        return {"release": release["html_url"], "draft": release["draft"], "verified_assets": names}


def smoke_test(client: GitHub, tag: str) -> dict:
    """Exercise write access and checksum verification without uploading user data."""
    with tempfile.TemporaryDirectory(prefix="podcast-output-check-") as directory:
        fixture = Path(directory) / "cloud-verification.txt"
        fixture.write_bytes(
            b"ai-podcast-output cloud integration check\n"
            b"This is a storage test, not a podcast. No credentials or user content.\n"
        )
        result = client.upload(tag, [fixture], publish=False)
        repeat = client.upload(tag, [fixture], publish=False)
        if result["release"] != repeat["release"]:
            raise RuntimeError("Repeated upload returned a different Release.")
        result["repeat_verified"] = True
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "github-output.json")
    parser.add_argument("--cloud", action="store_true",
                        help="Require the configured token key; disable other-token and local-login fallback.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("status")
    check = commands.add_parser("smoke-test", help="Upload a harmless draft asset twice and verify checksums.")
    check.add_argument("--tag", required=True, help="Use a dedicated podcast-YYYY-MM-DD-cloud-check tag.")
    upload = commands.add_parser("upload")
    upload.add_argument("--tag", required=True)
    upload.add_argument("--file", type=Path, action="append", required=True,
                        help="Explicit reviewed file; repeat for multiple files. No directory scanning.")
    upload.add_argument("--publish", action="store_true",
                        help="Publish after verifying uploads; caller must validate video playback first.")
    args = parser.parse_args()
    config = load_config(args.config)
    repo = config["repository"]
    client = GitHub(repo, load_token(
        repo.split("/")[0], config["token_environment_variable"], cloud=args.cloud,
    ))
    if args.command == "init":
        result = client.initialize()
    elif args.command == "status":
        metadata = client.private_repo()
        result = {"repository": metadata["html_url"], "private": metadata["private"],
                  "default_branch": metadata["default_branch"]}
    elif args.command == "smoke-test":
        result = smoke_test(client, args.tag)
    else:
        result = client.upload(args.tag, args.file, args.publish)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, URLError, ValueError, KeyError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
