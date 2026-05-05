"""
渲染控制模块

通过命令行调用 Blender 执行渲染脚本。
"""

import os
import subprocess
import sys
import threading
import time

import yaml


class Renderer:
    def __init__(self, config_path: str = "config.yaml"):
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

        self.blender_path = self.config.get("blender", {}).get(
            "path", "blender"
        )
        render_cfg = self.config.get("render", {})
        self.avatar_size = render_cfg.get("avatar_size", 480)
        self.fps = render_cfg.get("fps", 30)
        self.output_dir = render_cfg.get("output_dir", "output/avatar")

        os.makedirs(self.output_dir, exist_ok=True)

    def render_test_frame(
        self,
        fbx_path: str = "asset/swat.fbx",
        output_path: str | None = None,
    ) -> str:
        """渲染一帧测试截图。"""
        if output_path is None:
            output_path = os.path.join(self.output_dir, "test_frame.png")

        script_path = os.path.join(
            os.path.dirname(__file__), "blender_driver.py"
        )

        cmd = [
            self.blender_path,
            "--background",
            "--python", script_path,
            "--",
            fbx_path,
            output_path,
            str(self.avatar_size),
            str(self.avatar_size),
        ]

        print(f"执行: {' '.join(cmd)}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=os.getcwd(),
        )

        stdout = result.stdout or ""
        stderr = result.stderr or ""

        if result.returncode != 0:
            print(f"Blender 错误:\n{stderr[-1000:]}")
            raise RuntimeError(f"Blender 渲染失败 (exit code {result.returncode})")

        print(stdout[-500:] if len(stdout) > 500 else stdout)
        return output_path


    def render_animation(
        self,
        fbx_path: str = "asset/swat.fbx",
        duration_s: float = 3.0,
        anim_path: str = "asset/animations/Talking.fbx",
        output_dir: str | None = None,
    ) -> str:
        """渲染带肢体动画的帧序列。"""
        if output_dir is None:
            output_dir = self.output_dir
        os.makedirs(output_dir, exist_ok=True)

        script_path = os.path.join(
            os.path.dirname(__file__), "blender_driver.py"
        )
        output_placeholder = os.path.join(output_dir, "frame_")

        cmd = [
            self.blender_path,
            "--background",
            "--python", script_path,
            "--",
            fbx_path,
            output_placeholder,
            str(self.avatar_size),
            str(self.avatar_size),
            "animation",
            str(duration_s),
            anim_path,
        ]

        print(f"执行动画渲染 ({duration_s}s)...")

        stop_spinner = threading.Event()
        spinner_thread = threading.Thread(
            target=self._spinner, args=(stop_spinner,), daemon=True
        )
        spinner_thread.start()

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=os.getcwd(),
            )
        finally:
            stop_spinner.set()
            spinner_thread.join()
            sys.stdout.write("\r" + " " * 60 + "\r")
            sys.stdout.flush()

        stdout = result.stdout or ""
        stderr = result.stderr or ""

        if result.returncode != 0:
            print(f"Blender 错误:\n{stderr[-1000:]}")
            raise RuntimeError(f"Blender 渲染失败 (exit code {result.returncode})")

        print(stdout[-500:] if len(stdout) > 500 else stdout)
        return output_dir

    @staticmethod
    def _spinner(stop_event: threading.Event) -> None:
        chars = "|/-\\"
        idx = 0
        start = time.time()
        while not stop_event.is_set():
            elapsed = time.time() - start
            mins, secs = divmod(int(elapsed), 60)
            sys.stdout.write(f"\r  Blender 渲染中 {chars[idx % len(chars)]}  已耗时 {mins:02d}:{secs:02d}")
            sys.stdout.flush()
            idx += 1
            stop_event.wait(0.3)


if __name__ == "__main__":
    renderer = Renderer()

    print("=== 渲染单帧 ===")
    output = renderer.render_test_frame()
    print(f"渲染完成: {output}")

    print("\n=== 渲染 3 秒动画 ===")
    anim_dir = renderer.render_animation(duration_s=3.0)
    print(f"动画帧目录: {anim_dir}")
