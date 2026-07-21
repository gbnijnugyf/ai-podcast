"""批量生成脚本：从文件读取多个 topic，并行生成多个数字人口播视频。

核心机制：每个 topic 使用独立的工作目录（通过临时 config.yaml 隔离），
         防止并行时音频、背景图、临时视频等素材交叉污染。

用法：
  python batch_generate.py --topics-file topics.txt

  python batch_generate.py --topics-file topics.txt --voice zh-CN-XiaoxiaoNeural --rate "+10%"

  python batch_generate.py --topics-file topics.txt --no-avatar --max-workers 3

  python batch_generate.py --topics-file topics.txt --genre general

  python batch_generate.py --topics-file topics.txt --genre general --duration 1 --intro 通用片头

topics.txt 文件格式（每行一个 topic，空行和 # 开头的行会被忽略）：
  AI大模型最新进展
  量子计算突破
  区块链技术应用
  # 这是一条注释
  新能源汽车行业动态
"""
import os
import argparse
import shutil
import subprocess
import sys
import re
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import yaml

THIS_DIR = Path(__file__).resolve().parent
GENERATE_SCRIPT = THIS_DIR / "generate_video_from_report.py"

# 需要隔离的输出目录配置键
ISOLATED_DIR_KEYS = {
    "slides": "output_dir",
    "tts": "output_dir",
    "render": "output_dir",
    "video": "output_dir",
    "pexels": "cache_dir",
}


def sanitize_filename(name: str) -> str:
    """将 topic 转换为安全的文件名。"""
    name = re.sub(r'[\\/:*?"<>|]', '_', name)
    name = name.strip().rstrip('_')
    if len(name) > 40:
        name = name[:40].rstrip('_')
    return name if name else "topic"


def read_topics(file_path: str) -> list[str]:
    """从文件读取 topics，每行一个，忽略空行和 # 开头的注释行。"""
    path = Path(file_path)
    if not path.exists():
        print(f"[错误] 文件不存在: {path}")
        sys.exit(1)

    topics = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            topics.append(line)

    if not topics:
        print(f"[错误] 文件中未找到有效 topic: {path}")
        sys.exit(1)

    return topics


def create_isolated_config(base_config_path: str, topic_name: str,
                           timestamp: str) -> tuple[str, str]:
    """基于原配置创建副本，将所有输出目录重定向到 topic 专属工作目录。

    Returns:
        (临时 config 绝对路径, 工作目录相对路径)
    """
    with open(base_config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    safe_name = sanitize_filename(topic_name)
    work_dir = f"output/batch/{safe_name}_{timestamp}"

    # 重定向所有输出目录到隔离的工作目录
    for section, key in ISOLATED_DIR_KEYS.items():
        if section in config:
            config[section][key] = os.path.join(work_dir, section.split("_")[0] if "_" in section else section)

    # 写入临时配置文件
    abs_work_dir = THIS_DIR / work_dir
    abs_work_dir.mkdir(parents=True, exist_ok=True)
    temp_config_path = abs_work_dir / "_config.yaml"
    with open(temp_config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False)

    return str(temp_config_path), work_dir


def run_single_topic(topic: str, base_config: str, extra_args: list[str],
                     index: int, total: int) -> dict:
    """为单个 topic 运行完整的视频生成流程（隔离工作目录）。"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = sanitize_filename(topic)

    # ---- 每个 topic 用独立配置，隔离输出目录 ----
    topic_config, work_dir = create_isolated_config(base_config, topic, timestamp)

    # 最终视频输出到公共目录，方便查找
    output_path = (THIS_DIR / "output" / "video" /
                   f"{safe_name}_{timestamp}.mp4")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        str(GENERATE_SCRIPT),
        "--topic", topic,
        "--config", topic_config,
        "--output", str(output_path),
    ] + extra_args

    print(f"\n{'#' * 60}")
    print(f"  [{index}/{total}] 开始: {topic}")
    print(f"  工作目录: {work_dir}")
    print(f"  输出视频: {output_path}")
    print(f"{'#' * 60}\n")

    start = datetime.now()
    result = subprocess.run(cmd, encoding="utf-8", errors="replace")
    elapsed = (datetime.now() - start).total_seconds()

    # 可选：完成后清理中间文件，最终视频已保存到公共目录
    cleanup = not result.returncode  # 仅在成功时清理工作目录
    if cleanup:
        try:
            work_dir_path = THIS_DIR / work_dir
            if work_dir_path.exists():
                shutil.rmtree(work_dir_path, ignore_errors=True)
                print(f"  [清理] 已删除工作目录: {work_dir}")
        except Exception:
            pass

    return {
        "topic": topic,
        "success": result.returncode == 0,
        "output": str(output_path),
        "elapsed": elapsed,
    }


def build_pass_through_args(args) -> list[str]:
    """构建透传给 generate_video_from_report.py 的参数。

    注意：--config 不在此透传，因为每个 topic 使用独立的隔离配置。
    """
    extra = []
    if args.voice:
        extra += ["--voice", args.voice]
    if args.rate:
        extra += ["--rate", args.rate]
    if args.no_avatar:
        extra += ["--no-avatar"]
    if args.genre:
        extra += ["--genre", args.genre]
    if args.duration is not None:
        extra += ["--duration", str(args.duration)]
    if args.intro:
        extra += ["--intro", args.intro]
    return extra


def main() -> None:
    parser = argparse.ArgumentParser(
        description="批量生成数字人口播视频（从文件读取多个 topic）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例 topics.txt 内容：\n"
            "  AI大模型最新进展\n"
            "  量子计算突破\n"
            "  # 注释行会被忽略\n"
            "  区块链技术应用\n"
        ),
    )
    parser.add_argument("--topics-file", type=str, required=True,
                        help="topic 列表文件（每行一个 topic）")
    parser.add_argument("--max-workers", type=int, default=2,
                        help="最大并行数，默认 2")

    # ---------- 透传参数 ----------
    parser.add_argument("--voice", type=str,
                        help="TTS 音色（透传）")
    parser.add_argument("--rate", type=str,
                        help="TTS 语速，如 +10%%（透传）")
    parser.add_argument("--no-avatar", action="store_true",
                        help="跳过数字人渲染（透传）")
    parser.add_argument("--genre", type=str, default="daily_brief",
                        help="节目形态（透传）：daily_brief（默认）或 general")
    parser.add_argument("--duration", type=float, default=2.0,
                        help="口播目标时长（分钟，透传），默认 2")
    parser.add_argument("--intro", type=str, default=None,
                        help="片头文件名（透传，asset/templates/started/ 下）")
    parser.add_argument("--config", type=str, default="config.yaml",
                        help="基准配置文件路径，默认 config.yaml（LLM/Blender 等配置来源）")

    args = parser.parse_args()

    from src.genre import resolve_genre
    from src.intro import resolve_intro_path
    try:
        resolve_genre(args.genre)
    except ValueError as e:
        parser.error(str(e))
    if args.duration <= 0:
        parser.error("--duration 必须为正数（单位：分钟）")
    if args.intro:
        try:
            resolve_intro_path(args.intro, args.config)
        except ValueError as e:
            parser.error(str(e))

    topics = read_topics(args.topics_file)
    extra_args = build_pass_through_args(args)

    # 确保基准配置文件存在
    if not Path(args.config).exists():
        print(f"[错误] 配置文件不存在: {args.config}")
        sys.exit(1)

    print(f"\n{'=' * 60}")
    print(f"  批量视频生成启动")
    print(f"  文件:      {args.topics_file}")
    print(f"  Topics:    {len(topics)} 个")
    print(f"  并行数:    {args.max_workers}")
    print(f"  基准配置:  {args.config}")
    if args.voice:
        print(f"  音色:      {args.voice}")
    if args.rate:
        print(f"  语速:      {args.rate}")
    if args.no_avatar:
        print(f"  模式:      无数字人")
    print(f"  节目形态:  {args.genre}")
    print(f"  目标时长:  {args.duration:g} min")
    if args.intro:
        print(f"  片头:      {args.intro}")
    print(f"{'=' * 60}\n")

    for i, t in enumerate(topics, 1):
        print(f"  [{i:02d}] {t}")
    print()

    # ---------- 并行执行 ----------
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        future_map = {
            executor.submit(
                run_single_topic, topic, args.config, extra_args,
                idx, len(topics)
            ): topic
            for idx, topic in enumerate(topics, 1)
        }

        for future in as_completed(future_map):
            topic = future_map[future]
            try:
                results.append(future.result())
            except Exception as e:
                results.append({
                    "topic": topic,
                    "success": False,
                    "output": "N/A",
                    "elapsed": 0,
                })
                print(f"\n[错误] topic「{topic}」异常: {e}\n")

    # 按原始顺序排序
    topic_order = {t: i for i, t in enumerate(topics)}
    results.sort(key=lambda r: topic_order.get(r["topic"], 999))

    # ---------- 汇总 ----------
    print(f"\n\n{'=' * 60}")
    print(f"  批量生成完成")
    print(f"{'=' * 60}")

    success_count = 0
    for r in results:
        if r["success"]:
            success_count += 1
        status = "✅" if r["success"] else "❌"
        print(f"  {status} [{r['topic']}]")
        print(f"     输出: {r['output']}")
        print(f"     耗时: {r['elapsed']:.0f}s")

    print(f"\n  结果: {success_count}/{len(results)} 成功")
    if success_count < len(results):
        sys.exit(1)


if __name__ == "__main__":
    main()