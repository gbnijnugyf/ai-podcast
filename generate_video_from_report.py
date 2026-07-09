"""桥接脚本：将金融日报或文稿转为数字人口播视频。

用法：
  # 自动选取热门话题并生成视频（全自动模式）
  python generate_video_from_report.py --topic

  # 指定主题搜索最新资讯并生成视频
  python generate_video_from_report.py --topic "AI大模型最新进展"
  python generate_video_from_report.py --topic "量子计算"

  # 根据整篇文稿生成口播视频（LLM 转换）
  python generate_video_from_report.py --topic-article "整篇文稿文本内容..."
  python generate_video_from_report.py --topic-article path/to/article.txt

  # 整篇文稿直接作为口播文稿（不经过 LLM 转换）
  python generate_video_from_report.py --topic-article path/to/article.txt --not-convert

  # 指定主题tts步骤重启
  python main.py --script-json output/script_20260621_xxxxxx.json --bg-dir output/slides/topic_bg

  # 一键：抓取新闻 → 生成日报 → 生成视频
  python generate_video_from_report.py --generate

  # 指定日期一键生成
  python generate_video_from_report.py --generate --date 2026-03-26

  # 使用已有日报文件生成视频
  python generate_video_from_report.py --report-path "D:/Study/aiproject/ai-daily/paper_daily/output/2026-03-26/finance_daily_report.txt"

  # 按日期自动查找已有日报
  python generate_video_from_report.py --date 2026-03-26

  # 附带 TTS 参数
  python generate_video_from_report.py --date 2026-03-26 --voice zh-CN-XiaoxiaoNeural --rate "+10%"

  # 跳过数字人渲染（无需 Blender，仅生成 PPT+语音视频）
  python generate_video_from_report.py --generate --no-avatar

  # 使用已有幻灯片图片
  python main.py --slides-dir output/slides --no-avatar

  # 指定输出路径
  python generate_video_from_report.py --generate --output output/video/daily.mp4
"""

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

AI_DAILY_ROOT = Path(r"D:\Study\aiproject\ai-daily\paper_daily")
AI_DAILY_OUTPUT_ROOT = AI_DAILY_ROOT / "output"
REPORT_FILENAME = "finance_daily_report.txt"
GENERATE_SCRIPT = AI_DAILY_ROOT / "generate_report_only.py"

MAIN_SCRIPT = Path(__file__).resolve().parent / "main.py"


def generate_report(date_str: str | None) -> Path:
    """调用 ai-daily 的 generate_report_only.py 生成日报，返回生成的 txt 路径。"""
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    cmd = [sys.executable, str(GENERATE_SCRIPT), "--date", date_str]

    print(f"{'=' * 60}")
    print(f"  阶段 1：生成金融日报（{date_str}）")
    print(f"{'=' * 60}\n")

    result = subprocess.run(
        cmd,
        encoding="utf-8",
        errors="replace",
        cwd=str(AI_DAILY_ROOT),
    )

    if result.returncode != 0:
        sys.exit(f"\n日报生成失败 (exit code {result.returncode})")

    report_path = AI_DAILY_OUTPUT_ROOT / date_str / REPORT_FILENAME
    if not report_path.exists():
        sys.exit(f"[错误] 日报文件未找到: {report_path}")
    return report_path


def resolve_report_path(date_str: str | None, explicit_path: str | None) -> Path:
    if explicit_path:
        p = Path(explicit_path)
        if not p.exists():
            sys.exit(f"[错误] 文件不存在: {p}")
        return p

    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    p = AI_DAILY_OUTPUT_ROOT / date_str / REPORT_FILENAME
    if not p.exists():
        sys.exit(f"[错误] 未找到 {date_str} 的日报文件: {p}")
    return p


def generate_from_topic(topic: str, config_path: str) -> Path:
    """搜索指定主题的最新资讯并整理为口播文稿，返回临时文件路径。"""
    from src.topic_searcher import TopicSearcher

    print(f"{'=' * 60}")
    print(f"  阶段 1：搜索主题资讯「{topic}」")
    print(f"{'=' * 60}\n")

    searcher = TopicSearcher(config_path)
    report_text = searcher.search_and_summarize(topic)

    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)
    report_path = output_dir / f"topic_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    report_path.write_text(report_text, encoding="utf-8")

    print(f"\n  文稿已保存: {report_path}")
    print(f"  字数: {len(report_text)}\n")
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(description="金融日报 → 数字人口播视频")
    parser.add_argument("--topic", nargs="?", const="__auto__", default=None,
                        help="指定主题生成视频；不带参数则自动选取热门话题")
    parser.add_argument("--topic-article", type=str, default=None,
                        help="整篇文稿文本或 txt 文件路径，LLM 将其转换为口播文稿")
    parser.add_argument("--not-convert", action="store_true",
                        help="配合 --topic-article 使用：直接将整篇文稿作为口播文稿，不经过 LLM 转换")
    parser.add_argument("--generate", action="store_true",
                        help="先调用 ai-daily 生成日报，再生成视频（一键模式）")
    parser.add_argument("--report-path", type=str, help="日报 txt 文件路径")
    parser.add_argument("--date", type=str, default=None,
                        help="日期 YYYY-MM-DD（默认当天）")
    parser.add_argument("--voice", type=str, help="TTS 音色")
    parser.add_argument("--rate", type=str, help="TTS 语速（如 +10%%）")
    parser.add_argument("--output", type=str, default=None, help="输出视频路径")
    parser.add_argument("--no-avatar", action="store_true", help="跳过数字人渲染（无需 Blender）")
    parser.add_argument("--config", type=str, default="config.yaml", help="配置文件路径")
    args = parser.parse_args()

    if args.topic:
        if args.topic == "__auto__":
            from src.topic_searcher import TopicSearcher
            searcher = TopicSearcher(args.config)
            topics = searcher.auto_select_topics(args.date)
            print(f"\n  自动选取热门话题: {topics}\n")
            topic = "、".join(topics)
        else:
            topic = args.topic

        from src.script_generator import ScriptGenerator
        gen = ScriptGenerator(args.config)
        script_data = gen.generate(topic)

        script_json_path = Path("output") / f"script_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        script_json_path.parent.mkdir(parents=True, exist_ok=True)
        import json
        script_json_path.write_text(json.dumps(script_data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  文稿 JSON: {script_json_path}")

        print(f"\n{'=' * 60}")
        print(f"  阶段 2：生成口播视频（话题模式）")
        print(f"{'=' * 60}\n")

        cmd = [sys.executable, str(MAIN_SCRIPT), "--script-json", str(script_json_path)]
        if args.voice:
            cmd += ["--voice", args.voice]
        if args.rate:
            cmd += ["--rate", args.rate]
        if args.output:
            cmd += ["--output", args.output]
        if args.config:
            cmd += ["--config", args.config]

        result = subprocess.run(cmd, encoding="utf-8", errors="replace")
        sys.exit(result.returncode)

    elif args.topic_article:
        # 读取文稿：判断是文件路径还是直接文本
        article_path = Path(args.topic_article)
        if article_path.exists() and article_path.is_file():
            print(f"  从文件读取文稿: {article_path}")
            article_text = article_path.read_text(encoding="utf-8")
        else:
            article_text = args.topic_article

        print(f"\n{'=' * 60}")
        print(f"  阶段 1：处理文稿（{len(article_text)} 字）")
        print(f"{'=' * 60}\n")
        print(f"  文稿前 200 字预览:\n  {article_text[:200].replace(chr(10), chr(10) + '  ')}\n")

        from src.script_generator import ScriptGenerator
        gen = ScriptGenerator(args.config)

        if args.not_convert:
            script_data = gen.text_to_script(article_text)
        else:
            script_data = gen.generate_from_article(article_text)

        script_json_path = Path("output") / f"script_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        script_json_path.parent.mkdir(parents=True, exist_ok=True)
        import json
        script_json_path.write_text(json.dumps(script_data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  文稿 JSON: {script_json_path}")

        print(f"\n{'=' * 60}")
        print(f"  阶段 2：生成口播视频（文稿模式）")
        print(f"{'=' * 60}\n")

        cmd = [sys.executable, str(MAIN_SCRIPT), "--script-json", str(script_json_path)]
        if args.voice:
            cmd += ["--voice", args.voice]
        if args.rate:
            cmd += ["--rate", args.rate]
        if args.output:
            cmd += ["--output", args.output]
        if args.config:
            cmd += ["--config", args.config]

        result = subprocess.run(cmd, encoding="utf-8", errors="replace")
        sys.exit(result.returncode)

    elif args.generate:
        report_path = generate_report(args.date)
    else:
        report_path = resolve_report_path(args.date, args.report_path)

    print(f"\n{'=' * 60}")
    print(f"  阶段 2：生成数字人口播视频")
    print(f"{'=' * 60}")
    print(f"日报文件: {report_path}")
    print(f"文件大小: {report_path.stat().st_size / 1024:.1f} KB\n")

    cmd = [sys.executable, str(MAIN_SCRIPT), "--text-file", str(report_path)]

    if args.voice:
        cmd += ["--voice", args.voice]
    if args.rate:
        cmd += ["--rate", args.rate]
    if args.output:
        cmd += ["--output", args.output]
    if args.no_avatar:
        cmd += ["--no-avatar"]
    if args.config:
        cmd += ["--config", args.config]

    result = subprocess.run(cmd, encoding="utf-8", errors="replace")
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
