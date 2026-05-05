"""
视频合成模块

使用 FFmpeg 将幻灯片背景、数字人动画帧、音频合成为最终视频。
"""

import os
import subprocess

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

        os.makedirs(self.output_dir, exist_ok=True)

    def _run_ffmpeg(self, cmd: list[str], desc: str = ""):
        """执行 FFmpeg 命令。"""
        print(f"  FFmpeg: {desc}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            stderr = result.stderr or ""
            print(f"  FFmpeg 错误:\n{stderr[-500:]}")
            raise RuntimeError(f"FFmpeg 失败: {desc}")

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
    ) -> str:
        """合成最终视频。

        Args:
            slide_paths: 幻灯片图片路径列表
            slide_durations: 每张幻灯片的持续时长（秒）
            avatar_frame_dir: 数字人动画帧目录（frame_0001.png, frame_0002.png, ...）
            audio_path: 音频文件路径
            output_path: 输出视频路径
        """
        if output_path is None:
            output_path = os.path.join(self.output_dir, "output.mp4")

        total_duration = sum(slide_durations)
        print(f"  总时长: {total_duration:.1f}s, 幻灯片: {len(slide_paths)} 页")

        # 1. 生成幻灯片视频（每页持续对应时长）
        slide_video = os.path.join(self.output_dir, "_slides.mp4")
        self._make_slide_video(slide_paths, slide_durations, slide_video)

        # 2. 合成：幻灯片背景 + 数字人叠加 + 音频
        self._compose_final(slide_video, avatar_frame_dir, audio_path, output_path, total_duration)

        # 清理临时文件
        if os.path.exists(slide_video):
            os.remove(slide_video)

        return output_path

    def _make_slide_video(
        self,
        slide_paths: list[str],
        slide_durations: list[float],
        output_path: str,
    ):
        """将幻灯片图片序列生成为视频（每页显示指定时长）。"""
        # 使用 FFmpeg concat demuxer
        list_file = os.path.join(self.output_dir, "_slide_list.txt")
        with open(list_file, "w", encoding="utf-8") as f:
            for path, dur in zip(slide_paths, slide_durations):
                abs_path = os.path.abspath(path)
                f.write(f"file '{abs_path}'\n")
                f.write(f"duration {dur:.3f}\n")
            # 最后一帧需要再写一次 file（FFmpeg concat 的要求）
            if slide_paths:
                f.write(f"file '{os.path.abspath(slide_paths[-1])}'\n")

        self._run_ffmpeg([
            self.ffmpeg, "-y",
            "-f", "concat", "-safe", "0",
            "-i", list_file,
            "-vf", f"scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
            "-r", str(self.fps),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            output_path,
        ], "生成幻灯片视频")

        os.remove(list_file)

    def _compose_final(
        self,
        slide_video: str,
        avatar_frame_dir: str,
        audio_path: str,
        output_path: str,
        total_duration: float,
    ):
        """最终合成：幻灯片 + 数字人叠加 + 音频。"""
        avatar_pattern = os.path.join(os.path.abspath(avatar_frame_dir), "frame_%04d.png")

        avatar_w = self.avatar_size
        margin = 20
        # 背景矩形比数字人稍大，半透明深灰色
        bg_w = avatar_w + 20
        bg_h = avatar_w + 16
        bg_x = f"1920-{bg_w}-{margin}"
        bg_y = f"1080-{bg_h}-{margin}"
        overlay_x = f"1920-overlay_w-{margin + 10}"
        overlay_y = f"1080-overlay_h-{margin + 10}"

        self._run_ffmpeg([
            self.ffmpeg, "-y",
            "-i", slide_video,
            "-framerate", str(self.fps),
            "-i", avatar_pattern,
            "-i", audio_path,
            "-filter_complex",
            # 先在幻灯片上画一个半透明深色矩形作为数字人背景
            f"[0:v]drawbox=x={bg_x}:y={bg_y}:w={bg_w}:h={bg_h}:color=white@0.5:t=fill[bg];"
            # 缩放数字人
            f"[1:v]scale={avatar_w}:-1[avatar];"
            # 叠加数字人到背景框上
            f"[bg][avatar]overlay={overlay_x}:{overlay_y}:shortest=1[out]",
            "-map", "[out]",
            "-map", "2:a",
            "-c:v", "libx264",
            "-c:a", "aac",
            "-pix_fmt", "yuv420p",
            "-t", str(total_duration),
            output_path,
        ], "合成最终视频")

        print(f"  视频已生成: {output_path}")


if __name__ == "__main__":
    print("composer.py 需要通过 main.py 调用")
