"""片头视频解析：从 asset/templates/started/ 按文件名选择。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

THIS_DIR = Path(__file__).resolve().parent.parent
STARTED_DIR = THIS_DIR / "asset" / "templates" / "started"

# 无法探测片头时长时，开场白沿用历史默认（约 8 秒）
DEFAULT_OPENING_SEC = 8.0


def list_intro_files() -> list[str]:
    """列出 started 目录下可用片头文件名。"""
    if not STARTED_DIR.is_dir():
        return []
    names = [
        p.name
        for p in sorted(STARTED_DIR.iterdir())
        if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"}
    ]
    return names


def resolve_intro_path(intro: str | None, config_path: str = "config.yaml") -> Path | None:
    """解析片头路径。

    - intro 为 None/空：使用 config.video.intro_video
    - intro 为 started 目录下文件名（可省略扩展名）：在 started/ 中匹配
    """
    if intro:
        name = Path(intro).name.strip()
        if not name:
            raise ValueError("片头文件名不能为空")

        candidates = [STARTED_DIR / name]
        if not Path(name).suffix:
            for ext in (".mp4", ".mov", ".mkv", ".webm"):
                candidates.append(STARTED_DIR / f"{name}{ext}")

        for path in candidates:
            if path.is_file():
                return path.resolve()

        available = list_intro_files()
        avail_text = "、".join(available) if available else "(目录为空)"
        raise ValueError(
            f"未找到片头「{intro}」。请使用 asset/templates/started/ 下的文件名。"
            f" 可用: {avail_text}"
        )

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    configured = (config.get("video") or {}).get("intro_video") or ""
    if not configured:
        return None

    path = Path(configured)
    if not path.is_absolute():
        path = (THIS_DIR / path).resolve()
    else:
        path = path.resolve()

    if not path.is_file():
        return None
    return path


def probe_media_duration(media_path: Path | str, config_path: str = "config.yaml") -> float:
    """用 ffprobe 获取音视频时长（秒）。"""
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    ffprobe = (config.get("ffmpeg") or {}).get("ffprobe_path") or "ffprobe"
    result = subprocess.run(
        [
            ffprobe,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(media_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"无法探测时长: {media_path}\n{result.stderr.strip()}"
        )
    return float(result.stdout.strip())


def opening_sec_for_intro(
    intro: str | None = None,
    config_path: str = "config.yaml",
) -> tuple[Path | None, float]:
    """解析片头并返回 (路径, 建议开场白秒数)。

    开场白时长对齐片头视频时长（与 main 中裁剪/溢出逻辑一致）：
    朗读目标不超过片头长度；探测失败则回退 DEFAULT_OPENING_SEC。
    """
    path = resolve_intro_path(intro, config_path)
    if path is None:
        return None, DEFAULT_OPENING_SEC
    try:
        dur = probe_media_duration(path, config_path)
        if dur <= 0:
            return path, DEFAULT_OPENING_SEC
        return path, dur
    except Exception as e:
        print(f"  [警告] 探测片头时长失败，开场白按 {DEFAULT_OPENING_SEC:.0f}s 引导: {e}")
        return path, DEFAULT_OPENING_SEC
