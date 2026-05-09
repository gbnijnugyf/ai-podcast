"""
数字人口播视频生成工具 — 主入口

用法：
  python main.py                          # 使用内置示例文本
  python main.py --text "讲解文本内容"     # 指定文本
  python main.py --text-file input.txt    # 从文件读取文本
  python main.py --slides-dir asset/slides/  # 使用已有幻灯片图片
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


def generate_srt(slides_data: list[dict], slide_durations: list[float], output_path: str) -> str:
    """根据 slides_data 和每页时长生成 SRT 字幕文件。"""
    entries = []
    idx = 1
    time_offset = 0.0

    for i, sd in enumerate(slides_data):
        narration = sd.get("narration", "").strip()
        dur = slide_durations[i] if i < len(slide_durations) else 3.0

        if narration:
            chunks = _split_narration(narration)
            chunk_dur = dur / max(len(chunks), 1)

            for j, chunk in enumerate(chunks):
                start = time_offset + j * chunk_dur
                end = start + chunk_dur - 0.05
                entries.append(f"{idx}\n{_format_srt_time(start)} --> {_format_srt_time(end)}\n{chunk}\n")
                idx += 1

        time_offset += dur

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(entries))

    print(f"  字幕文件: {output_path} ({idx - 1} 条)")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="数字人口播视频生成工具")
    parser.add_argument("--text", type=str, help="讲解文本内容")
    parser.add_argument("--text-file", type=str, help="从文件读取讲解文本")
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

    if args.slides_dir:
        slide_paths = sorted([
            os.path.join(args.slides_dir, f)
            for f in os.listdir(args.slides_dir)
            if f.lower().endswith((".png", ".jpg", ".jpeg"))
        ])
        slides_data = [{"type": "content", "narration": text}]
        print(f"使用已有幻灯片: {len(slide_paths)} 页")
    else:
        generator = SlideGenerator(args.config)
        slides_data, slide_paths = generator.generate(text, slides_output_dir)

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
    generate_srt(slides_data, slide_durations, srt_path)

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
        )
    else:
        output_path = composer.compose(
            slide_paths=slide_paths,
            slide_durations=slide_durations,
            avatar_frame_dir=avatar_dir,
            audio_path=full_audio,
            output_path=args.output,
            srt_path=srt_path,
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
