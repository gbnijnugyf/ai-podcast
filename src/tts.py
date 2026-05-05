"""
TTS 语音合成模块

使用 Edge-TTS 将文本转换为语音，同时收集 word-level 时间戳。
支持按幻灯片分页分别合成，以便后续精确匹配每页的音频时长。
"""

import asyncio
import json
import os

import edge_tts
import yaml


class TTSEngine:
    def __init__(self, config_path: str = "config.yaml"):
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

        tts_cfg = self.config["tts"]
        self.voice = tts_cfg.get("voice", "zh-CN-YunxiNeural")
        self.output_dir = tts_cfg.get("output_dir", "output/audio")
        os.makedirs(self.output_dir, exist_ok=True)

    async def _synthesize(
        self, text: str, audio_path: str
    ) -> list[dict]:
        """合成单段文本，返回时间戳列表（句子级或词级，取决于 edge-tts 版本）。"""
        communicate = edge_tts.Communicate(text, self.voice)
        timestamps: list[dict] = []

        with open(audio_path, "wb") as f:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                    timestamps.append({
                        "type": chunk["type"],
                        "text": chunk["text"],
                        "offset_ms": chunk["offset"] // 10000,
                        "duration_ms": chunk["duration"] // 10000,
                    })

        return timestamps

    def synthesize_text(self, text: str, filename: str = "full") -> tuple[str, list[dict]]:
        """合成一整段文本。

        Returns:
            (audio_path, timestamps)
        """
        audio_path = os.path.join(self.output_dir, f"{filename}.mp3")
        timestamps = asyncio.run(self._synthesize(text, audio_path))
        return audio_path, timestamps

    def synthesize_slides(self, slides_data: list[dict]) -> tuple[list[str], list[list[dict]]]:
        """按幻灯片分页逐页合成语音。

        只对有 narration 字段的页面合成。
        Returns:
            (audio_paths, all_timestamps) — 每页一个音频文件和对应的时间戳
        """
        audio_paths: list[str] = []
        all_timestamps: list[list[dict]] = []

        for i, slide in enumerate(slides_data):
            narration = slide.get("narration", "").strip()
            if not narration:
                audio_paths.append("")
                all_timestamps.append([])
                continue

            audio_path = os.path.join(self.output_dir, f"slide_{i + 1:03d}.mp3")
            timestamps = asyncio.run(self._synthesize(narration, audio_path))
            audio_paths.append(audio_path)
            all_timestamps.append(timestamps)
            print(f"  [{i + 1}/{len(slides_data)}] {audio_path}  ({len(timestamps)} words)")

        return audio_paths, all_timestamps

    def save_timestamps(self, timestamps, filename: str = "timestamps"):
        """保存时间戳到 JSON 文件。"""
        path = os.path.join(self.output_dir, f"{filename}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(timestamps, f, ensure_ascii=False, indent=2)
        return path


# ------------------------------------------------------------------
# 独立运行测试
# ------------------------------------------------------------------

if __name__ == "__main__":
    engine = TTSEngine()

    # 测试 1：合成一整段文本
    sample_text = (
        "人工智能是计算机科学的一个分支，"
        "它试图理解智能的本质，"
        "并生产出一种新的能以人类智能相似的方式做出反应的智能机器。"
    )

    print("=== 测试 1：单段文本合成 ===")
    audio_path, timestamps = engine.synthesize_text(sample_text, "test_single")
    ts_path = engine.save_timestamps(timestamps, "test_single_timestamps")
    print(f"音频: {audio_path}")
    print(f"时间戳: {ts_path}")
    print(f"时间戳条数: {len(timestamps)}")
    for ts in timestamps:
        end_ms = ts["offset_ms"] + ts["duration_ms"]
        print(f"  [{ts['type']}] {ts['offset_ms']}ms ~ {end_ms}ms: {ts['text']}")

    # 测试 2：按幻灯片分页合成
    print("\n=== 测试 2：分页合成 ===")
    sample_slides = [
        {"type": "cover", "title": "测试封面"},
        {
            "type": "content",
            "title": "第一页",
            "narration": "机器学习是人工智能的核心技术之一，它使计算机能够从数据中学习。",
        },
        {
            "type": "content",
            "title": "第二页",
            "narration": "深度学习使用多层神经网络来处理复杂的模式识别任务。",
        },
        {"type": "ending", "title": "谢谢观看"},
    ]

    audio_paths, all_timestamps = engine.synthesize_slides(sample_slides)
    ts_path = engine.save_timestamps(
        {"slides": [{"audio": p, "timestamps": ts} for p, ts in zip(audio_paths, all_timestamps)]},
        "test_slides_timestamps",
    )
    print(f"时间戳: {ts_path}")

    for i, (path, ts) in enumerate(zip(audio_paths, all_timestamps)):
        if path:
            total_ms = ts[-1]["offset_ms"] + ts[-1]["duration_ms"] if ts else 0
            print(f"  第 {i + 1} 页: {path} ({total_ms / 1000:.1f}s)")
        else:
            print(f"  第 {i + 1} 页: (无语音)")
