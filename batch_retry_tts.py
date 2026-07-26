"""重试批量流水线中未完成的 topic：从 TTS 阶段续跑（复用缓存）。

判定「未完成」：output/batch/*/ 仍存在（batch_generate 仅在成功时删除工作目录），
且含 _config.yaml 与 slides/topic_bg 媒资。

用法：
  python batch_retry_tts.py
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
MAIN_SCRIPT = THIS_DIR / "main.py"
BATCH_ROOT = THIS_DIR / "output" / "batch"
SCRIPT_ROOT = THIS_DIR / "output"
VIDEO_ROOT = THIS_DIR / "output" / "video"

MEDIA_EXTS = {".jpg", ".jpeg", ".png", ".mp4", ".mov", ".webm", ".mkv"}
TS_RE = re.compile(r"_(\d{8}_\d{6})$")
SCRIPT_TS_RE = re.compile(r"^script_(\d{8}_\d{6})\.json$")


def _media_count(bg_dir: Path) -> int:
    if not bg_dir.is_dir():
        return 0
    return sum(1 for p in bg_dir.iterdir() if p.suffix.lower() in MEDIA_EXTS)


def _parse_work_ts(name: str) -> str | None:
    m = TS_RE.search(name)
    return m.group(1) if m else None


def _topic_prefix(name: str) -> str:
    m = TS_RE.search(name)
    if not m:
        return name
    return name[: m.start()]


def _overlap_score(a: str, b: str) -> float:
    """粗粒度重叠分：共享长度>=2 的子串越多越高。"""
    a, b = (a or "").lower(), (b or "").lower()
    if not a or not b:
        return 0.0
    score = 0.0
    # 数字/英文词
    for tok in re.findall(r"[a-z0-9]{2,}", a):
        if tok in b:
            score += 3.0
    # 中文双字
    hans = re.findall(r"[\u4e00-\u9fff]{2,}", a)
    seen: set[str] = set()
    for chunk in hans:
        for i in range(len(chunk) - 1):
            bg = chunk[i : i + 2]
            if bg in seen:
                continue
            seen.add(bg)
            if bg in b:
                score += 1.0
    return score


def list_unfinished_work_dirs() -> list[Path]:
    """batch 成功时会删除工作目录；仍存在且尚无成品视频的视为未完成。"""
    if not BATCH_ROOT.is_dir():
        return []
    dirs: list[Path] = []
    for p in sorted(BATCH_ROOT.iterdir()):
        if not p.is_dir():
            continue
        if not (p / "_config.yaml").is_file():
            continue
        if _media_count(p / "slides" / "topic_bg") < 1:
            continue
        # 成品已在公共目录则视为已完成（仅工作目录未清干净）
        done_video = VIDEO_ROOT / f"{p.name}.mp4"
        if done_video.is_file() and done_video.stat().st_size > 100_000:
            continue
        dirs.append(p)
    return dirs


def _load_job(work_dir: Path) -> dict:
    job_path = work_dir / "_job.json"
    if job_path.is_file():
        try:
            return json.loads(job_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def _resolve_script(
    work_dir: Path, used_scripts: set[Path]
) -> Path | None:
    """优先 work_dir/script.json，否则启发式匹配 output/script_*.json。"""
    local = work_dir / "script.json"
    if local.is_file():
        return local

    job = _load_job(work_dir)
    job_script = job.get("script_json")
    if job_script:
        p = Path(job_script)
        if not p.is_absolute():
            p = THIS_DIR / p
        if p.is_file():
            return p

    work_ts = _parse_work_ts(work_dir.name)
    prefix = _topic_prefix(work_dir.name)
    candidates: list[tuple[float, str, Path]] = []

    for p in SCRIPT_ROOT.glob("script_*.json"):
        if p.resolve() in used_scripts:
            continue
        m = SCRIPT_TS_RE.match(p.name)
        if not m:
            continue
        script_ts = m.group(1)
        if work_ts and script_ts < work_ts:
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        text = f"{data.get('title', '')} {data.get('opening', '')}"
        score = _overlap_score(prefix, text)
        if work_ts:
            # 同一次批量内、时间接近的加一点分
            try:
                dt_w = datetime.strptime(work_ts, "%Y%m%d_%H%M%S")
                dt_s = datetime.strptime(script_ts, "%Y%m%d_%H%M%S")
                delta = abs((dt_s - dt_w).total_seconds())
                if delta <= 600:
                    score += max(0.0, 5.0 - delta / 120.0)
            except ValueError:
                pass
        candidates.append((score, script_ts, p))

    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    best_score, _, best = candidates[0]
    if best_score < 3.0:
        return None
    # 写入本地副本，方便下次直接命中
    try:
        shutil.copy2(best, local)
    except OSError:
        pass
    return best


def retry_one(work_dir: Path, script_path: Path) -> dict:
    config_path = work_dir / "_config.yaml"
    bg_dir = work_dir / "slides" / "topic_bg"
    job = _load_job(work_dir)

    try:
        script_data = json.loads(script_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        script_data = {}

    output = job.get("output") or str(VIDEO_ROOT / f"{work_dir.name}.mp4")
    genre = job.get("genre") or script_data.get("genre")
    intro = job.get("intro") or script_data.get("intro")

    cmd = [
        sys.executable,
        str(MAIN_SCRIPT),
        "--script-json",
        str(script_path),
        "--bg-dir",
        str(bg_dir),
        "--config",
        str(config_path),
        "--output",
        str(output),
    ]
    if genre:
        cmd += ["--genre", str(genre)]
    if intro:
        cmd += ["--intro", str(intro)]

    print(f"\n{'#' * 60}")
    print(f"  续跑: {work_dir.name}")
    print(f"  文稿: {script_path}")
    print(f"  背景: {bg_dir}")
    print(f"  配置: {config_path}")
    print(f"  输出: {output}")
    print(f"{'#' * 60}\n")

    start = datetime.now()
    result = subprocess.run(cmd, encoding="utf-8", errors="replace")
    elapsed = (datetime.now() - start).total_seconds()
    ok = result.returncode == 0

    if ok:
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
            print(f"  [清理] 已删除工作目录: {work_dir}")
        except Exception:
            pass

    return {
        "work_dir": str(work_dir),
        "success": ok,
        "output": output,
        "elapsed": elapsed,
    }


def main() -> None:
    work_dirs = list_unfinished_work_dirs()
    if not work_dirs:
        print("没有未完成的批量任务（output/batch 下无待续跑工作目录）。")
        return

    print(f"{'=' * 60}")
    print(f"  批量 TTS 续跑")
    print(f"  待处理: {len(work_dirs)} 个未完成工作目录")
    print(f"{'=' * 60}")
    for d in work_dirs:
        print(f"  - {d.name}")
    print()

    used_scripts: set[Path] = set()
    results: list[dict] = []

    for work_dir in work_dirs:
        script_path = _resolve_script(work_dir, used_scripts)
        if script_path is None:
            print(f"\n[跳过] 无法匹配文稿 JSON: {work_dir.name}")
            results.append({
                "work_dir": str(work_dir),
                "success": False,
                "output": "",
                "elapsed": 0,
                "error": "no_script",
            })
            continue
        used_scripts.add(script_path.resolve())
        results.append(retry_one(work_dir, script_path))

    ok_n = sum(1 for r in results if r.get("success"))
    print(f"\n{'=' * 60}")
    print(f"  批量 TTS 续跑完成")
    print(f"{'=' * 60}")
    for r in results:
        status = "✅" if r.get("success") else "❌"
        print(f"  {status} {Path(r['work_dir']).name}")
        if r.get("output"):
            print(f"     输出: {r['output']}")
        if r.get("error") == "no_script":
            print("     原因: 未找到匹配的 script JSON")
        print(f"     耗时: {r.get('elapsed', 0):.0f}s")
    print(f"\n  结果: {ok_n}/{len(results)} 成功")
    if ok_n < len(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
