"""Private Release checkpoints. Configs, browser sessions and logs are never packed."""
from contextlib import contextmanager
from datetime import datetime, timezone
import base64
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from urllib.request import Request
from uuid import uuid4

from github_output import API, GitHub, digest, load_config, load_token


def save_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def checkpoint_path(name: str) -> bool:
    """Only an explicit set of portable generation inputs can be restored."""
    if "\\" in name or ".." in PurePosixPath(name).parts:
        return False
    if name in ("script.json", "tts/tts_cache.json", "_pipeline.json"):
        return True
    return bool(re.fullmatch(
        r"(?:tts/slide_\d+\.mp3|slides/topic_bg/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp))",
        name,
    ))


class DailyStore:
    def __init__(self, root: Path):
        config = load_config(root / "github-output.json")
        self.client = GitHub(config["repository"], load_token(
            config["repository"].split("/")[0], config["token_environment_variable"], cloud=True,
        ))
        self.client.private_repo()

    @contextmanager
    def lock(self, day: str):
        # GitHub's create-file operation is atomic; no automatic stealing after a timeout.
        path = self.client.prefix + f"/contents/locks/{day}.json"
        if self.client.request("GET", path, missing_ok=True) is not None:
            raise RuntimeError("Daily job is locked. Stop other runs before removing its output-repository lock.")
        content = json.dumps({"run": uuid4().hex, "started": datetime.now(timezone.utc).isoformat()})
        result = self.client.request("PUT", path, {
            "message": f"Lock podcast job {day}",
            "content": base64.b64encode(content.encode()).decode(),
        })
        try:
            yield
        finally:
            self.client.request("DELETE", path, {
                "message": f"Unlock podcast job {day}", "sha": result["content"]["sha"],
            })

    def release(self, tag):
        return next((r for r in self.client.list_all(self.client.prefix + "/releases")
                     if r["tag_name"] == tag), None)

    def assets(self, tag):
        release = self.release(tag)
        if release is None:
            return {}
        return {a["name"]: a for a in self.client.list_all(
            self.client.prefix + f"/releases/{release['id']}/assets"
        )}

    def download(self, asset, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".download")
        request = Request(
            f"{API}{self.client.prefix}/releases/assets/{int(asset['id'])}",
            headers={**self.client.headers, "Accept": "application/octet-stream"},
        )
        with self.client.opener.open(request, timeout=600) as response, temporary.open("wb") as stream:
            shutil.copyfileobj(response, stream, 1024 * 1024)
        self.client.verify_asset(asset, temporary)
        temporary.replace(path)
        return path

    def json_asset(self, asset):
        request = Request(
            f"{API}{self.client.prefix}/releases/assets/{int(asset['id'])}",
            headers={**self.client.headers, "Accept": "application/octet-stream"},
        )
        with self.client.opener.open(request, timeout=90) as response:
            data = response.read(5 * 1024 * 1024 + 1)
        if len(data) > 5 * 1024 * 1024 or len(data) != asset["size"]:
            raise RuntimeError("Invalid JSON asset size")
        if asset.get("digest") and asset["digest"] != "sha256:" + hashlib.sha256(data).hexdigest():
            raise RuntimeError("JSON asset checksum mismatch")
        return json.loads(data)

    def used_urls(self):
        urls = set()
        for release in self.client.list_all(self.client.prefix + "/releases"):
            if not re.fullmatch(r"podcast-\d{4}-\d{2}-\d{2}-daily", release["tag_name"]):
                continue
            for asset in self.client.list_all(self.client.prefix + f"/releases/{release['id']}/assets"):
                if asset["name"] == "article.json":
                    urls.add(self.json_asset(asset)["url"])
        return urls

    def checkpoint(self, tag: str, work: Path, staging: Path):
        """Copy only permitted data; hash-address assets so later snapshots never overwrite."""
        staging.mkdir(parents=True, exist_ok=True)
        entries, uploads = [], {}
        for path in sorted(work.rglob("*")):
            relative = path.relative_to(work).as_posix()
            if not checkpoint_path(relative) or not path.is_file() or path.is_symlink():
                continue
            if path.stat().st_size == 0:
                continue
            # Cache paths are unnecessary when restoring on a different OS/checkout.
            data = path.read_bytes()
            if relative == "tts/tts_cache.json":
                cache = json.loads(data)
                fp = cache["fingerprint"]
                data = json.dumps({
                    "fingerprint": {k: fp[k] for k in ("voice", "rate", "narrations")},
                    "slides": [None if not isinstance(s, dict) else {
                        "audio": f"slide_{i + 1:03d}.mp3" if s.get("audio") else "",
                        "timestamps": [{k: t[k] for k in ("type", "text", "offset_ms", "duration_ms")}
                                       for t in s["timestamps"]],
                    } for i, s in enumerate(cache["slides"])],
                }, ensure_ascii=False).encode("utf-8")
            checksum = hashlib.sha256(data).hexdigest()
            name = f"cp-{checksum}{path.suffix.lower()}"
            copy = staging / name
            copy.write_bytes(data)
            uploads[name] = copy
            entries.append({"path": relative, "asset": name, "sha256": checksum, "size": len(data)})
        if not entries:
            return None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        manifest = staging / f"checkpoint-{stamp}-{uuid4().hex[:8]}.json"
        save_json(manifest, {"version": 1, "files": entries})
        # The snapshot becomes discoverable only after every referenced asset was verified.
        self.client.upload(tag, list(uploads.values()) + [manifest], False)
        return manifest.name

    def restore(self, tag: str, work: Path, staging: Path):
        assets = self.assets(tag)
        snapshots = sorted(n for n in assets if re.fullmatch(r"checkpoint-[0-9T]+-[a-f0-9]{8}\.json", n))
        if not snapshots:
            return False
        manifest = self.json_asset(assets[snapshots[-1]])
        if manifest.get("version") != 1:
            raise RuntimeError("Unsupported checkpoint version")
        if work.exists():
            raise RuntimeError("Refusing to restore over an existing work directory")
        staging.mkdir(parents=True, exist_ok=True)
        # Download/verify every file before exposing a resumable job directory.
        # A failed download must not leave a partial job that skips restoration.
        with tempfile.TemporaryDirectory(prefix="restore-", dir=staging) as temporary:
            restored = Path(temporary) / "job"
            restored.mkdir()
            seen = set()
            for item in manifest["files"]:
                name = item["path"]
                if not checkpoint_path(name) or name in seen:
                    raise RuntimeError("Unsafe or duplicate checkpoint path")
                seen.add(name)
                target = restored / name
                if not target.resolve().is_relative_to(restored.resolve()) or target.is_symlink():
                    raise RuntimeError("Checkpoint escapes work directory")
                asset_name = item["asset"]
                if not re.fullmatch(r"cp-[a-f0-9]{64}\.[a-z0-9]+", asset_name):
                    raise RuntimeError("Invalid checkpoint asset name")
                cache = staging / asset_name
                self.download(assets[asset_name], cache)
                if cache.stat().st_size != item["size"] or digest(cache) != item["sha256"]:
                    raise RuntimeError("Checkpoint integrity check failed")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(cache, target)
            work.parent.mkdir(parents=True, exist_ok=True)
            restored.replace(work)
        return True
