"""
视频合成模块

使用 FFmpeg 将幻灯片背景、数字人动画帧、音频合成为最终视频。
"""

import os
import subprocess
import time

import yaml


class VideoComposer:
    def __init__(self, config_path: str = "config.yaml"):
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

        ffmpeg_cfg = self.config.get("ffmpeg", {})
        self.ffmpeg = ffmpeg_cfg.get("path", "ffmpeg")
        self.ffprobe = ffmpeg_cfg.get("ffprobe_path", "ffprobe")

        video_cfg = self.config.get("video", {})
        self.output_dir = video_cfg.get("output_dir", "output/video")
        self.fps = self.config.get("render", {}).get("fps", 30)
        self.avatar_size = self.config.get("render", {}).get("avatar_size", 480)

        anim_cfg = self.config.get("animation", {})
        self.transition_type = anim_cfg.get("transition_type", "fade")
        self.transition_duration = anim_cfg.get("transition_duration", 0.6)
        self.element_animation = anim_cfg.get("element_animation", False)
        self.intro_duration = anim_cfg.get("intro_duration", 0.8)

        os.makedirs(self.output_dir, exist_ok=True)

    def _run_ffmpeg(self, cmd: list[str], desc: str = ""):
        """执行 FFmpeg 命令。"""
        print(f"  FFmpeg: {desc}...", end="", flush=True)
        t0 = time.time()
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        elapsed = time.time() - t0
        if result.returncode != 0:
            print(f" 失败 ({elapsed:.1f}s)")
            stderr = result.stderr or ""
            print(f"  FFmpeg 错误:\n{stderr[-500:]}")
            raise RuntimeError(f"FFmpeg 失败: {desc}")
        print(f" 完成 ({elapsed:.1f}s)")

    def concat_audio(self, audio_paths: list[str], output_path: str) -> str:
        """将多个音频文件拼接为一个。"""
        valid_paths = [p for p in audio_paths if p and os.path.exists(p)]
        if not valid_paths:
            raise ValueError("没有有效的音频文件")

        if len(valid_paths) == 1:
            return valid_paths[0]

        list_file = os.path.join(self.output_dir, "_audio_list.txt")
        with open(list_file, "w", encoding="utf-8") as f:
            for p in valid_paths:
                f.write(f"file '{os.path.abspath(p)}'\n")

        self._run_ffmpeg([
            self.ffmpeg, "-y",
            "-f", "concat", "-safe", "0",
            "-i", list_file,
            "-c", "copy",
            output_path,
        ], "拼接音频")

        os.remove(list_file)
        return output_path

    def get_audio_duration(self, audio_path: str) -> float:
        """获取音频时长（秒）。"""
        result = subprocess.run(
            [
                self.ffprobe,
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

    def compose(
        self,
        slide_paths: list[str],
        slide_durations: list[float],
        avatar_frame_dir: str,
        audio_path: str,
        output_path: str | None = None,
        srt_path: str | None = None,
        all_frame_dirs: list[list[str]] | None = None,
    ) -> str:
        """合成最终视频。

        Args:
            slide_paths: 幻灯片图片路径列表
            slide_durations: 每张幻灯片的持续时长（秒）
            avatar_frame_dir: 数字人动画帧目录（frame_0001.png, frame_0002.png, ...）
            audio_path: 音频文件路径
            output_path: 输出视频路径
            srt_path: SRT 字幕文件路径
            all_frame_dirs: 每页的入场动画帧路径列表（可选）
        """
        if output_path is None:
            output_path = os.path.join(self.output_dir, "output.mp4")

        total_duration = sum(slide_durations)
        print(f"  总时长: {total_duration:.1f}s, 幻灯片: {len(slide_paths)} 页")

        slide_video = os.path.join(self.output_dir, "_slides.mp4")
        self._make_slide_video(slide_paths, slide_durations, slide_video, all_frame_dirs)

        self._compose_final(slide_video, avatar_frame_dir, audio_path, output_path, total_duration, srt_path)

        if os.path.exists(slide_video):
            os.remove(slide_video)

        return output_path

    def _make_slide_video(
        self,
        slide_paths: list[str],
        slide_durations: list[float],
        output_path: str,
        all_frame_dirs: list[list[str]] | None = None,
    ):
        """将幻灯片图片序列生成为视频，支持入场动画帧和页间转场。"""
        if not slide_paths:
            raise ValueError("没有幻灯片可以生成视频")

        n = len(slide_paths)
        use_xfade = self.transition_duration > 0 and n > 1
        td = self.transition_duration if use_xfade else 0.0

        # xfade 转场会让相邻页重叠 td 秒，总共减少 (n-1)*td 秒。
        # 补偿策略：给除最后一页外的每页追加 td 秒静态停留时间。
        compensated_durations = list(slide_durations)
        if use_xfade:
            for i in range(n - 1):
                compensated_durations[i] += td

        page_videos = []
        temp_files = []

        for i, (slide_path, dur) in enumerate(zip(slide_paths, compensated_durations)):
            page_video = os.path.join(self.output_dir, f"_page_{i:03d}.mp4")
            page_videos.append(page_video)
            temp_files.append(page_video)

            has_anim = (
                all_frame_dirs is not None
                and i < len(all_frame_dirs)
                and all_frame_dirs[i]
            )

            if has_anim:
                self._make_page_video_with_anim(
                    all_frame_dirs[i], slide_path, dur, page_video,
                )
            else:
                self._make_page_video_static(slide_path, dur, page_video)

        if len(page_videos) == 1:
            os.rename(page_videos[0], output_path)
            return

        if use_xfade:
            self._concat_with_xfade(page_videos, compensated_durations, output_path)
        else:
            self._concat_simple(page_videos, output_path)

        for f in temp_files:
            if os.path.exists(f):
                os.remove(f)

    def _make_page_video_static(self, slide_path: str, duration: float, output_path: str):
        """将单张静态图片生成为指定时长的视频片段。"""
        self._run_ffmpeg([
            self.ffmpeg, "-y",
            "-loop", "1",
            "-i", os.path.abspath(slide_path),
            "-t", f"{duration:.3f}",
            "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
            "-r", str(self.fps),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            output_path,
        ], f"静态页 → 视频")

    def _make_page_video_with_anim(
        self, frame_paths: list[str], static_slide: str,
        total_duration: float, output_path: str,
    ):
        """将入场动画帧 + 静态停留帧合成为单页视频。"""
        anim_dur = len(frame_paths) / self.fps
        static_dur = max(total_duration - anim_dur, 0.1)

        anim_video = os.path.join(self.output_dir, "_anim_temp.mp4")
        static_video = os.path.join(self.output_dir, "_static_temp.mp4")

        frames_dir = os.path.dirname(frame_paths[0])
        pattern = os.path.join(os.path.abspath(frames_dir), "frame_%04d.png")

        self._run_ffmpeg([
            self.ffmpeg, "-y",
            "-framerate", str(self.fps),
            "-i", pattern,
            "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
            "-r", str(self.fps),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            anim_video,
        ], "动画帧 → 视频")

        # 用动画最后一帧作为静态停留画面，避免与动画末尾的视觉跳变
        last_frame = os.path.abspath(frame_paths[-1])

        self._run_ffmpeg([
            self.ffmpeg, "-y",
            "-loop", "1",
            "-i", last_frame,
            "-t", f"{static_dur:.3f}",
            "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
            "-r", str(self.fps),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            static_video,
        ], "静态停留 → 视频")

        list_file = os.path.join(self.output_dir, "_page_concat.txt")
        with open(list_file, "w", encoding="utf-8") as f:
            f.write(f"file '{os.path.abspath(anim_video)}'\n")
            f.write(f"file '{os.path.abspath(static_video)}'\n")

        self._run_ffmpeg([
            self.ffmpeg, "-y",
            "-f", "concat", "-safe", "0",
            "-i", list_file,
            "-c", "copy",
            output_path,
        ], "拼接入场+停留")

        for tmp in [anim_video, static_video, list_file]:
            if os.path.exists(tmp):
                os.remove(tmp)

    def _concat_with_xfade(
        self, page_videos: list[str], slide_durations: list[float], output_path: str,
    ):
        """用 xfade 滤镜链拼接所有页面视频。"""
        n = len(page_videos)
        td = self.transition_duration

        inputs = []
        for pv in page_videos:
            inputs += ["-i", os.path.abspath(pv)]

        offsets = []
        cumulative = 0.0
        for i in range(n - 1):
            page_dur = self._get_video_duration(page_videos[i])
            offset = cumulative + page_dur - td
            offsets.append(max(offset, cumulative + 0.1))
            cumulative = offset

        filter_parts = []
        current_label = "[0:v]"

        for i in range(n - 1):
            next_label = f"[{i + 1}:v]"
            out_label = f"[v{i}]" if i < n - 2 else "[vout]"
            filter_parts.append(
                f"{current_label}{next_label}xfade=transition={self.transition_type}"
                f":duration={td}:offset={offsets[i]:.3f}{out_label}"
            )
            current_label = out_label

        if not filter_parts:
            filter_chain = "[0:v]copy[vout]"
        else:
            filter_chain = ";".join(filter_parts)

        cmd = [self.ffmpeg, "-y"] + inputs + [
            "-filter_complex", filter_chain,
            "-map", "[vout]",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-r", str(self.fps),
            output_path,
        ]
        self._run_ffmpeg(cmd, f"xfade 转场合成 ({n} 页)")

    def _concat_simple(self, page_videos: list[str], output_path: str):
        """无转场简单拼接。"""
        list_file = os.path.join(self.output_dir, "_concat_list.txt")
        with open(list_file, "w", encoding="utf-8") as f:
            for pv in page_videos:
                f.write(f"file '{os.path.abspath(pv)}'\n")

        self._run_ffmpeg([
            self.ffmpeg, "-y",
            "-f", "concat", "-safe", "0",
            "-i", list_file,
            "-c", "copy",
            output_path,
        ], "简单拼接视频")

        os.remove(list_file)

    def _get_video_duration(self, video_path: str) -> float:
        """获取视频时长。"""
        result = subprocess.run(
            [
                self.ffprobe,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                video_path,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return float(result.stdout.strip())

    def _compose_final(
        self,
        slide_video: str,
        avatar_frame_dir: str,
        audio_path: str,
        output_path: str,
        total_duration: float,
        srt_path: str | None = None,
    ):
        """最终合成：幻灯片 + 数字人叠加 + 字幕 + 音频。"""
        avatar_pattern = os.path.join(os.path.abspath(avatar_frame_dir), "frame_%04d.png")

        avatar_w = self.avatar_size
        margin = 20
        bg_w = avatar_w + 20
        bg_h = avatar_w + 16
        bg_x = f"1920-{bg_w}-{margin}"
        bg_y = f"1080-{bg_h}-{margin}"
        overlay_x = f"1920-overlay_w-{margin + 10}"
        overlay_y = f"1080-overlay_h-{margin + 10}"

        filter_chain = (
            f"[0:v]drawbox=x={bg_x}:y={bg_y}:w={bg_w}:h={bg_h}:color=white@0.5:t=fill[bg];"
            f"[1:v]scale={avatar_w}:-1[avatar];"
            f"[bg][avatar]overlay={overlay_x}:{overlay_y}:shortest=1[composed]"
        )

        if srt_path and os.path.exists(srt_path):
            srt_rel = os.path.relpath(srt_path).replace("\\", "/")
            subtitle_style = "FontName=Microsoft YaHei,FontSize=14,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=2,Shadow=1,MarginV=50"
            filter_chain += f";[composed]subtitles='{srt_rel}':force_style='{subtitle_style}'[out]"
        else:
            filter_chain += ";[composed]copy[out]"

        self._run_ffmpeg([
            self.ffmpeg, "-y",
            "-i", slide_video,
            "-framerate", str(self.fps),
            "-i", avatar_pattern,
            "-i", audio_path,
            "-filter_complex", filter_chain,
            "-map", "[out]",
            "-map", "2:a",
            "-c:v", "libx264",
            "-c:a", "aac",
            "-pix_fmt", "yuv420p",
            "-t", str(total_duration),
            output_path,
        ], "合成最终视频")

        print(f"  视频已生成: {output_path}")


    def compose_slides_only(
        self,
        slide_paths: list[str],
        slide_durations: list[float],
        audio_path: str,
        output_path: str | None = None,
        srt_path: str | None = None,
        all_frame_dirs: list[list[str]] | None = None,
    ) -> str:
        """合成视频（无数字人叠加）：幻灯片 + 音频 + 字幕。"""
        if output_path is None:
            output_path = os.path.join(self.output_dir, "output.mp4")

        total_duration = sum(slide_durations)
        print(f"  总时长: {total_duration:.1f}s, 幻灯片: {len(slide_paths)} 页（无数字人模式）")

        slide_video = os.path.join(self.output_dir, "_slides.mp4")
        self._make_slide_video(slide_paths, slide_durations, slide_video, all_frame_dirs)

        cmd = [
            self.ffmpeg, "-y",
            "-i", slide_video,
            "-i", audio_path,
        ]

        if srt_path and os.path.exists(srt_path):
            srt_rel = os.path.relpath(srt_path).replace("\\", "/")
            subtitle_style = "FontName=Microsoft YaHei,FontSize=14,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=2,Shadow=1,MarginV=50"
            cmd += [
                "-vf", f"subtitles='{srt_rel}':force_style='{subtitle_style}'",
            ]

        cmd += [
            "-map", "0:v",
            "-map", "1:a",
            "-c:v", "libx264",
            "-c:a", "aac",
            "-pix_fmt", "yuv420p",
            "-t", str(total_duration),
            output_path,
        ]

        self._run_ffmpeg(cmd, "合成视频（无数字人）")
        print(f"  视频已生成: {output_path}")

        if os.path.exists(slide_video):
            os.remove(slide_video)

        return output_path

    def compose_topic_video(
        self,
        bg_paths: list[str],
        slide_durations: list[float],
        audio_path: str,
        output_path: str | None = None,
        srt_path: str | None = None,
        intro_video: str | None = None,
        intro_duration: float = 0.0,
        title_text: str | None = None,
    ) -> str:
        """话题模式合成：片头视频 + 全屏背景图硬切 + 音频 + 白色字体红色描边字幕。"""
        if output_path is None:
            output_path = os.path.join(self.output_dir, "output.mp4")

        total_duration = sum(slide_durations) + intro_duration
        print(f"  总时长: {total_duration:.1f}s（片头 {intro_duration:.1f}s + 正文 {sum(slide_durations):.1f}s）")

        slide_video = os.path.join(self.output_dir, "_topic_slides.mp4")
        self._make_topic_slide_video(bg_paths, slide_durations, slide_video, intro_video, intro_duration, title_text)

        cmd = [
            self.ffmpeg, "-y",
            "-i", slide_video,
            "-i", audio_path,
        ]

        if srt_path and os.path.exists(srt_path):
            srt_rel = os.path.relpath(srt_path).replace("\\", "/")
            subtitle_style = (
                "FontName=Microsoft YaHei,"
                "FontSize=18,"
                "PrimaryColour=&H00FFFFFF,"
                "OutlineColour=&H000000FF,"
                "Outline=3,"
                "Shadow=0,"
                "Bold=1,"
                "MarginV=40"
            )
            cmd += [
                "-vf", f"subtitles='{srt_rel}':force_style='{subtitle_style}'",
            ]

        cmd += [
            "-map", "0:v",
            "-map", "1:a",
            "-c:v", "libx264",
            "-c:a", "aac",
            "-pix_fmt", "yuv420p",
            "-t", str(total_duration),
            output_path,
        ]

        self._run_ffmpeg(cmd, "合成话题视频")
        print(f"  视频已生成: {output_path}")

        if os.path.exists(slide_video):
            os.remove(slide_video)

        return output_path

    def _make_topic_slide_video(
        self,
        bg_paths: list[str],
        slide_durations: list[float],
        output_path: str,
        intro_video: str | None = None,
        intro_duration: float = 0.0,
        title_text: str | None = None,
    ):
        """将片头视频 + 背景图序列生成视频（硬切，无转场）。"""
        if not bg_paths and not intro_video:
            raise ValueError("没有背景图片或片头视频")

        page_videos = []
        temp_files = []

        if intro_video and intro_duration > 0:
            intro_page = os.path.join(self.output_dir, "_topic_intro.mp4")
            self._make_intro_segment(intro_video, intro_duration, intro_page, title_text)
            page_videos.append(intro_page)
            temp_files.append(intro_page)

        for i, (bg_path, dur) in enumerate(zip(bg_paths, slide_durations)):
            page_video = os.path.join(self.output_dir, f"_topic_page_{i:03d}.mp4")
            page_videos.append(page_video)
            temp_files.append(page_video)
            self._make_page_video_static(bg_path, dur, page_video)

        if len(page_videos) == 1:
            os.rename(page_videos[0], output_path)
            return

        self._concat_simple(page_videos, output_path)

        for f in temp_files:
            if os.path.exists(f):
                os.remove(f)

    def _make_intro_segment(
        self, intro_video: str, duration: float, output_path: str,
        title_text: str | None = None,
    ):
        """从片头视频裁剪指定时长的片段，缩放到 1920x1080，叠加标题文字。

        不循环：duration 不应超过源视频时长（由调用方保证）。
        title_text 如果提供，前 5 秒在画面中央显示标题文字。
        分两步执行避免 Windows 下 FFmpeg 滤镜转义问题。
        """
        output_abs = output_path.replace("\\", "/")

        if title_text:
            scaled_tmp = output_abs.replace(".mp4", "_scaled.mp4")
        else:
            scaled_tmp = output_abs

        self._run_ffmpeg([
            self.ffmpeg, "-y",
            "-i", os.path.abspath(intro_video),
            "-t", f"{duration:.3f}",
            "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
            "-r", str(self.fps),
            "-c:v", "libx264",
            "-an",
            "-pix_fmt", "yuv420p",
            scaled_tmp,
        ], "片头视频缩放")

        if title_text:
            lines = title_text.split("\\n")
            font_path = "C\\\\:/Windows/Fonts/msyhbd.ttc"

            if len(lines) >= 2:
                line1 = lines[0]
                line2 = lines[1]

                dt1_tmp = output_abs.replace(".mp4", "_dt1.mp4")
                self._run_ffmpeg([
                    self.ffmpeg, "-y",
                    "-i", scaled_tmp,
                    "-vf",
                    f"drawtext=fontfile={font_path}:text={line1}:"
                    f"fontsize=128:fontcolor=white:borderw=4:bordercolor=black@0.6:"
                    f"x=(w-text_w)/2:y=(h/2-text_h-20):"
                    f"enable=between(t\\,0\\,5)",
                    "-c:v", "libx264", "-an", "-pix_fmt", "yuv420p",
                    dt1_tmp,
                ], "片头标题第一行")

                self._run_ffmpeg([
                    self.ffmpeg, "-y",
                    "-i", dt1_tmp,
                    "-vf",
                    f"drawtext=fontfile={font_path}:text={line2}:"
                    f"fontsize=104:fontcolor=white:borderw=3:bordercolor=black@0.6:"
                    f"x=(w-text_w)/2:y=(h/2+20):"
                    f"enable=between(t\\,0\\,5)",
                    "-c:v", "libx264", "-an", "-pix_fmt", "yuv420p",
                    output_abs,
                ], "片头标题第二行")

                for tmp_f in [scaled_tmp, dt1_tmp]:
                    if os.path.exists(tmp_f):
                        os.remove(tmp_f)
            else:
                line1 = lines[0]
                self._run_ffmpeg([
                    self.ffmpeg, "-y",
                    "-i", scaled_tmp,
                    "-vf",
                    f"drawtext=fontfile={font_path}:text={line1}:"
                    f"fontsize=128:fontcolor=white:borderw=4:bordercolor=black@0.6:"
                    f"x=(w-text_w)/2:y=(h-text_h)/2:"
                    f"enable=between(t\\,0\\,5)",
                    "-c:v", "libx264", "-an", "-pix_fmt", "yuv420p",
                    output_abs,
                ], "片头标题")

                if os.path.exists(scaled_tmp):
                    os.remove(scaled_tmp)


if __name__ == "__main__":
    print("composer.py 需要通过 main.py 调用")
