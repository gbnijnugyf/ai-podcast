"""
数字人口播视频生成工具 — 主入口

用法：
  python main.py                          # 使用内置示例文本
  python main.py --text "讲解文本内容"     # 指定文本
  python main.py --text-file input.txt    # 从文件读取文本
  python main.py --slides-dir asset/slides/  # 使用已有幻灯片图片
  python main.py --slides-dir output/slides  # 使用已有幻灯片图片
"""

import argparse
import json
import os
import shutil
import time

import yaml

from src.slide_generator import SlideGenerator
from src.tts import TTSEngine
from src.renderer import Renderer
from src.composer import VideoComposer


def load_config(config_path: str = "config.yaml") -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _split_narration(text: str, max_chars: int = 18) -> list[str]:
    """将 narration 文本按标点或字数拆分为字幕行。"""
    import re
    sentences = re.split(r'([，。！？；,!\?;])', text)

    merged = []
    buf = ""
    for i, seg in enumerate(sentences):
        if not seg:
            continue
        buf += seg
        is_punct = bool(re.match(r'^[，。！？；,!\?;]$', seg))
        if is_punct or i == len(sentences) - 1:
            if buf.strip():
                merged.append(buf.strip())
            buf = ""
    if buf.strip():
        merged.append(buf.strip())

    chunks = []
    for sent in merged:
        while len(sent) > max_chars:
            chunks.append(sent[:max_chars])
            sent = sent[max_chars:]
        if sent:
            chunks.append(sent)
    return chunks


def _format_srt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def generate_srt(
    slides_data: list[dict],
    slide_durations: list[float],
    output_path: str,
    all_timestamps: list[list[dict]] | None = None,
    audio_paths: list[str] | None = None,
) -> str:
    """根据 TTS 真实时间戳生成 SRT 字幕文件。

    time_offset 用每页音频文件的实际时长（ffprobe）累加，
    页内用 SentenceBoundary 时间戳做相对定位，确保无累积误差。
    """
    entries = []
    idx = 1
    time_offset = 0.0

    for i, sd in enumerate(slides_data):
        narration = sd.get("narration", "").strip()
        dur = slide_durations[i] if i < len(slide_durations) else 3.0

        if not narration:
            time_offset += dur
            continue

        ts_list = all_timestamps[i] if all_timestamps and i < len(all_timestamps) else []
        sentence_ts = [t for t in ts_list if t["type"] == "SentenceBoundary"]

        if sentence_ts:
            for st in sentence_ts:
                s_start = time_offset + st["offset_ms"] / 1000.0
                s_text = st["text"]
                s_dur = st["duration_ms"] / 1000.0

                chunks = _split_narration(s_text)
                s_total_chars = sum(len(c) for c in chunks)

                for j, chunk in enumerate(chunks):
                    c_char_offset = sum(len(chunks[k]) for k in range(j))
                    c_start = s_start + s_dur * c_char_offset / max(s_total_chars, 1)
                    c_end = s_start + s_dur * (c_char_offset + len(chunk)) / max(s_total_chars, 1) - 0.05
                    entries.append(f"{idx}\n{_format_srt_time(c_start)} --> {_format_srt_time(c_end)}\n{chunk}\n")
                    idx += 1
        else:
            chunks = _split_narration(narration)
            total_chars = sum(len(c) for c in chunks)
            for j, chunk in enumerate(chunks):
                char_offset = sum(len(chunks[k]) for k in range(j))
                start = time_offset + dur * char_offset / max(total_chars, 1)
                end = time_offset + dur * (char_offset + len(chunk)) / max(total_chars, 1) - 0.05
                entries.append(f"{idx}\n{_format_srt_time(start)} --> {_format_srt_time(end)}\n{chunk}\n")
                idx += 1

        time_offset += dur

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(entries))

    print(f"  字幕文件: {output_path} ({idx - 1} 条)")
    return output_path


def _run_topic_pipeline(args, config: dict):
    """话题模式流水线：片头 + 结构化文稿 → 背景图 → TTS → 合成视频。"""
    from src.background_searcher import BackgroundSearcher
    from src.tts import TTSEngine
    from src.composer import VideoComposer

    start_time = time.time()

    with open(args.script_json, "r", encoding="utf-8") as f:
        script_data = json.load(f)

    opening = script_data.get("opening", "")
    blocks = []
    for ch in script_data["chapters"]:
        for block in ch["blocks"]:
            blocks.append(block)

    print(f"话题: {script_data.get('title', '未知')}")
    print(f"开场: {opening[:50]}...")
    print(f"章节: {len(script_data['chapters'])} 个, 内容块: {len(blocks)} 个")
    print(f"TTS 音色: {config['tts']['voice']}")
    print(f"TTS 语速: {config['tts'].get('rate', '+0%')}")

    # -------------------------------------------------------
    # 1. 下载背景图（仅正文 blocks）
    # -------------------------------------------------------
    print("\n" + "=" * 50)
    print("【第 1 步】搜索下载背景图")
    print("=" * 50)

    bg_output_dir = os.path.join(config.get("slides", {}).get("output_dir", "output/slides"), "topic_bg")

    if getattr(args, "bg_dir", None) and os.path.isdir(args.bg_dir):
        bg_files = sorted([
            os.path.join(args.bg_dir, f)
            for f in os.listdir(args.bg_dir)
            if f.lower().endswith((".jpg", ".jpeg", ".png"))
        ])
        bg_paths = bg_files[:len(blocks)]
        print(f"  使用已有背景图目录: {args.bg_dir} ({len(bg_paths)} 张)")
    else:
        bg_searcher = BackgroundSearcher(args.config)
        bg_paths = bg_searcher.download_for_script(script_data, bg_output_dir)
        print(f"  背景图: {len(bg_paths)} 张")

    # -------------------------------------------------------
    # 2. TTS 语音合成（opening + 正文 blocks）
    # -------------------------------------------------------
    print("\n" + "=" * 50)
    print("【第 2 步】TTS 语音合成")
    print("=" * 50)

    all_slides_data = []
    if opening:
        all_slides_data.append({"narration": opening})
    all_slides_data += [{"narration": b["narration"]} for b in blocks]

    tts = TTSEngine(args.config)
    if args.voice:
        tts.voice = args.voice
    if args.rate:
        tts.rate = args.rate
    audio_paths, all_timestamps = tts.synthesize_slides(all_slides_data)

    composer = VideoComposer(args.config)
    audio_output_dir = config["tts"]["output_dir"]
    full_audio = os.path.join(audio_output_dir, "full.mp3")
    full_audio = composer.concat_audio(audio_paths, full_audio)
    total_audio_duration = composer.get_audio_duration(full_audio)
    print(f"  总音频时长: {total_audio_duration:.1f}s")

    # 计算每段时长（opening + blocks）
    all_durations = []
    for i, ap in enumerate(audio_paths):
        if ap and os.path.exists(ap):
            try:
                dur = subprocess_get_duration(ap, args.config)
                all_durations.append(dur)
            except Exception:
                all_durations.append(total_audio_duration / len(all_slides_data))
        else:
            all_durations.append(total_audio_duration / len(all_slides_data))

    current_total = sum(all_durations)
    if current_total > 0 and abs(current_total - total_audio_duration) > 0.5:
        scale = total_audio_duration / current_total
        all_durations = [d * scale for d in all_durations]

    # 分离 opening 时长和正文时长
    if opening:
        intro_duration = all_durations[0]
        slide_durations = all_durations[1:]
    else:
        intro_duration = 0.0
        slide_durations = all_durations

    print(f"  片头时长: {intro_duration:.1f}s")
    print(f"  正文每块时长: {[f'{d:.1f}s' for d in slide_durations]}")

    # -------------------------------------------------------
    # 3. 生成字幕（opening + blocks 全部生成字幕）
    # -------------------------------------------------------
    print("\n" + "=" * 50)
    print("【第 3 步】生成字幕")
    print("=" * 50)

    srt_path = os.path.join(config.get("video", {}).get("output_dir", "output/video"), "subtitles.srt")
    generate_srt(all_slides_data, all_durations, srt_path, all_timestamps, audio_paths)

    # -------------------------------------------------------
    # 4. 视频合成（片头视频 + 背景图硬切）
    # -------------------------------------------------------
    print("\n" + "=" * 50)
    print("【第 4 步】视频合成（话题模式）")
    print("=" * 50)

    intro_video = config.get("video", {}).get("intro_video", "")
    if intro_video and not os.path.isabs(intro_video):
        intro_video = os.path.abspath(intro_video)
    if not intro_video or not os.path.exists(intro_video):
        print(f"  [警告] 片头视频不存在: {intro_video}，跳过片头")
        intro_video = None
        intro_duration = 0.0

    if intro_video and intro_duration > 0:
        intro_video_duration = composer.get_audio_duration(intro_video)
        if intro_duration > intro_video_duration:
            overflow = intro_duration - intro_video_duration
            print(f"  片头口播 ({intro_duration:.1f}s) 超过片头视频 ({intro_video_duration:.1f}s)，"
                  f"溢出 {overflow:.1f}s 并入第一张背景图")
            intro_duration = intro_video_duration
            if slide_durations:
                slide_durations[0] += overflow

    from datetime import datetime
    now = datetime.now()
    title_text = f"{now.year}.{now.month}.{now.day}\\n热点资讯"

    output_path = composer.compose_topic_video(
        bg_paths=bg_paths,
        slide_durations=slide_durations,
        audio_path=full_audio,
        output_path=args.output,
        srt_path=srt_path,
        intro_video=intro_video,
        intro_duration=intro_duration,
        title_text=title_text,
    )

    elapsed = time.time() - start_time
    print("\n" + "=" * 50)
    print(f"完成！总用时: {elapsed:.1f}s")
    print(f"输出视频: {output_path}")
    print("=" * 50)


def main():
    parser = argparse.ArgumentParser(description="数字人口播视频生成工具")
    parser.add_argument("--text", type=str, help="讲解文本内容")
    parser.add_argument("--text-file", type=str, help="从文件读取讲解文本")
    parser.add_argument("--script-json", type=str, help="话题模式：结构化口播文稿 JSON 文件路径")
    parser.add_argument("--bg-dir", type=str, help="话题模式：已有背景图目录（跳过图片下载，从 TTS 阶段继续）")
    parser.add_argument("--slides-dir", type=str, help="已有幻灯片图片目录（跳过自动生成）")
    parser.add_argument("--model", type=str, default="asset/swat.fbx", help="3D 模型路径")
    parser.add_argument("--anim", type=str, default="asset/animations", help="动画 FBX 文件或目录路径")
    parser.add_argument("--output", type=str, default="output/video/output.mp4", help="输出视频路径")
    parser.add_argument("--voice", type=str, help="TTS 音色（如 zh-CN-XiaoxiaoNeural）")
    parser.add_argument("--rate", type=str, help="TTS 语速（如 +10%%, -10%%）")
    parser.add_argument("--no-avatar", action="store_true", help="跳过数字人渲染（无需 Blender）")
    parser.add_argument("--config", type=str, default="config.yaml", help="配置文件路径")
    args = parser.parse_args()

    config = load_config(args.config)

    if args.voice:
        config["tts"]["voice"] = args.voice
    if args.rate:
        config["tts"]["rate"] = args.rate

    if args.script_json:
        _run_topic_pipeline(args, config)
        return

    # -------------------------------------------------------
    # 1. 获取输入文本
    # -------------------------------------------------------
    if args.text:
        text = args.text
    elif args.text_file:
        with open(args.text_file, "r", encoding="utf-8") as f:
            text = f.read()
    else:
        text = (
            "人工智能是计算机科学的一个分支，它试图理解智能的本质，"
            "并生产出一种新的能以人类智能相似的方式做出反应的智能机器。\n\n"
            "人工智能的发展历程可以追溯到1956年的达特茅斯会议。"
            "此后经历了多次起伏，包括两次AI寒冬。\n\n"
            "机器学习是人工智能的核心技术之一。"
            "它使计算机能够从数据中学习，而不需要被明确编程。"
            "深度学习是机器学习的一个子集，使用多层神经网络来处理复杂的模式。\n\n"
            "当前AI技术在多个领域得到广泛应用，包括：自然语言处理、"
            "计算机视觉、语音识别、推荐系统、自动驾驶等。"
        )
        print("使用内置示例文本")

    print(f"文本长度: {len(text)} 字")
    print(f"TTS 音色: {config['tts']['voice']}")
    print(f"TTS 语速: {config['tts'].get('rate', '+0%')}")
    start_time = time.time()

    # -------------------------------------------------------
    # 2. 生成幻灯片
    # -------------------------------------------------------
    print("\n" + "=" * 50)
    print("【第 1 步】生成幻灯片")
    print("=" * 50)

    slides_output_dir = config["slides"]["output_dir"]

    all_frame_dirs = None

    if args.slides_dir:
        slide_paths = sorted([
            os.path.join(args.slides_dir, f)
            for f in os.listdir(args.slides_dir)
            if f.lower().endswith((".png", ".jpg", ".jpeg"))
        ])
        outline_path = os.path.join(args.slides_dir, "outline.json")
        if os.path.exists(outline_path):
            with open(outline_path, "r", encoding="utf-8") as f:
                slides_data = json.load(f)["slides"]
            print(f"从 {outline_path} 加载大纲，共 {len(slides_data)} 页")
        else:
            slides_data = [{"type": "content", "narration": text}]
        print(f"使用已有幻灯片: {len(slide_paths)} 页")
    else:
        generator = SlideGenerator(args.config)
        slides_data, slide_paths, all_frame_dirs = generator.generate(text, slides_output_dir)

    # -------------------------------------------------------
    # 3. TTS 语音合成
    # -------------------------------------------------------
    print("\n" + "=" * 50)
    print("【第 2 步】TTS 语音合成")
    print("=" * 50)

    tts = TTSEngine(args.config)
    if args.voice:
        tts.voice = args.voice
    if args.rate:
        tts.rate = args.rate
    audio_paths, all_timestamps = tts.synthesize_slides(slides_data)

    # 拼接所有音频
    audio_output_dir = config["tts"]["output_dir"]
    full_audio = os.path.join(audio_output_dir, "full.mp3")

    composer = VideoComposer(args.config)
    full_audio = composer.concat_audio(audio_paths, full_audio)
    total_audio_duration = composer.get_audio_duration(full_audio)
    print(f"总音频时长: {total_audio_duration:.1f}s")

    # 计算每页幻灯片的持续时长
    slide_durations = _calc_slide_durations(slides_data, audio_paths, all_timestamps, total_audio_duration, len(slide_paths))
    print(f"每页时长: {[f'{d:.1f}s' for d in slide_durations]}")

    # -------------------------------------------------------
    # 4. 渲染数字人动画（可跳过）
    # -------------------------------------------------------
    avatar_dir = config.get("render", {}).get("output_dir", "output/avatar")

    if not args.no_avatar:
        print("\n" + "=" * 50)
        print("【第 3 步】渲染数字人动画")
        print("=" * 50)

        renderer = Renderer(args.config)

        if os.path.exists(avatar_dir):
            for f in os.listdir(avatar_dir):
                if f.startswith("frame_") and f.endswith(".png"):
                    os.remove(os.path.join(avatar_dir, f))

        total_frames = int(total_audio_duration * config.get("render", {}).get("fps", 30))
        print(f"  渲染 {total_frames} 帧 ({total_audio_duration:.1f}s)，请耐心等待...")
        render_start = time.time()

        renderer.render_animation(
            fbx_path=args.model,
            duration_s=total_audio_duration,
            anim_path=args.anim,
            output_dir=avatar_dir,
        )

        render_elapsed = time.time() - render_start
        print(f"  渲染用时: {render_elapsed:.1f}s ({total_frames / max(render_elapsed, 0.1):.1f} fps)")
    else:
        print("\n" + "=" * 50)
        print("【第 3 步】跳过数字人渲染（--no-avatar 模式）")
        print("=" * 50)

    # -------------------------------------------------------
    # 5. 生成字幕
    # -------------------------------------------------------
    srt_path = os.path.join(config.get("video", {}).get("output_dir", "output/video"), "subtitles.srt")
    generate_srt(slides_data, slide_durations, srt_path, all_timestamps, audio_paths)

    # -------------------------------------------------------
    # 6. 视频合成
    # -------------------------------------------------------
    print("\n" + "=" * 50)
    print("【第 4 步】视频合成")
    print("=" * 50)

    if args.no_avatar:
        output_path = composer.compose_slides_only(
            slide_paths=slide_paths,
            slide_durations=slide_durations,
            audio_path=full_audio,
            output_path=args.output,
            srt_path=srt_path,
            all_frame_dirs=all_frame_dirs,
        )
    else:
        output_path = composer.compose(
            slide_paths=slide_paths,
            slide_durations=slide_durations,
            avatar_frame_dir=avatar_dir,
            audio_path=full_audio,
            output_path=args.output,
            srt_path=srt_path,
            all_frame_dirs=all_frame_dirs,
        )

    elapsed = time.time() - start_time
    print("\n" + "=" * 50)
    print(f"完成！总用时: {elapsed:.1f}s")
    print(f"输出视频: {output_path}")
    print("=" * 50)


def _calc_slide_durations(
    slides_data: list[dict],
    audio_paths: list[str],
    all_timestamps: list[list[dict]],
    total_duration: float,
    total_slides: int,
) -> list[float]:
    """计算每页幻灯片的持续时长。

    有 narration 的页面按其音频时长分配，
    无 narration 的页面（封面、章节、结尾）给固定时长。
    """
    STATIC_DURATION = 3.0
    durations = []
    remaining_duration = total_duration
    narration_count = 0

    for i, sd in enumerate(slides_data):
        has_narration = bool(sd.get("narration", "").strip())
        if has_narration and i < len(audio_paths) and audio_paths[i]:
            try:
                composer = VideoComposer.__new__(VideoComposer)
                dur = subprocess_get_duration(audio_paths[i])
                durations.append(dur)
                remaining_duration -= dur
            except Exception:
                durations.append(-1)
                narration_count += 1
        else:
            durations.append(STATIC_DURATION)
            remaining_duration -= STATIC_DURATION

    # 填补未能获取时长的页面
    unfilled = [i for i, d in enumerate(durations) if d < 0]
    if unfilled:
        per_page = max(remaining_duration / len(unfilled), 2.0)
        for i in unfilled:
            durations[i] = per_page

    # 如果幻灯片数量多于 slides_data（已有幻灯片模式）
    while len(durations) < total_slides:
        durations.append(total_duration / total_slides)

    durations = durations[:total_slides]

    # 确保总时长匹配音频
    current_total = sum(durations)
    if current_total > 0 and abs(current_total - total_duration) > 0.5:
        scale = total_duration / current_total
        durations = [d * scale for d in durations]

    return durations


def subprocess_get_duration(audio_path: str, config_path: str = "config.yaml") -> float:
    """获取音频时长。"""
    import subprocess
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    ffprobe = cfg.get("ffmpeg", {}).get("ffprobe_path", "ffprobe")

    result = subprocess.run(
        [
            ffprobe,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            audio_path,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return float(result.stdout.strip())


if __name__ == "__main__":
    main()
