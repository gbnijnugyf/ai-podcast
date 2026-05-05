"""
音素 → Viseme 映射模块

将中文拼音音素映射为标准 viseme（口型），用于驱动 3D 模型的口型动画。
"""

import json
import os

# 中文拼音音素 → Viseme 映射表
# Viseme 编码参考 Oculus/ARKit 标准，简化为以下几类
PHONEME_TO_VISEME = {
    # 静默
    "sil": "sil",

    # 声母
    "b": "PP",    # 双唇闭合
    "p": "PP",
    "m": "PP",
    "f": "FF",    # 下唇咬上齿
    "d": "DD",    # 舌尖抵上齿龈
    "t": "DD",
    "n": "DD",
    "l": "DD",
    "g": "kk",    # 舌根抬起
    "k": "kk",
    "h": "kk",
    "j": "SS",    # 齿缝送气
    "q": "SS",
    "x": "SS",
    "zh": "CH",   # 嘴唇微圆前突
    "ch": "CH",
    "sh": "CH",
    "r": "CH",
    "z": "SS",
    "c": "SS",
    "s": "SS",
    "y": "EE",    # 扁唇
    "w": "UU",    # 小圆唇

    # 韵母（简单元音）
    "a": "aa",    # 大开口
    "o": "OO",    # 嘴唇圆形
    "e": "II",    # 中开口
    "i": "EE",    # 扁唇展开
    "u": "UU",    # 小圆唇
    "v": "UU",    # ü

    # 韵母（复合）
    "ai": "aa",
    "ei": "EE",
    "ao": "aa",
    "ou": "OO",
    "an": "aa",
    "en": "II",
    "ang": "aa",
    "eng": "II",
    "ong": "OO",
    "er": "RR",   # 卷舌

    "ia": "aa",
    "ie": "EE",
    "iao": "aa",
    "iu": "OO",
    "iou": "OO",
    "ian": "aa",
    "in": "EE",
    "iang": "aa",
    "ing": "EE",
    "iong": "OO",

    "ua": "aa",
    "uo": "OO",
    "uai": "aa",
    "ui": "UU",
    "uei": "UU",
    "uan": "aa",
    "un": "UU",
    "uen": "UU",
    "uang": "aa",

    "ue": "UU",
    "ve": "UU",
    "van": "UU",
    "vn": "UU",

    # 补充常见韵母变体
    "uo": "OO",
    "ie": "EE",
    "üe": "UU",
    "ün": "UU",
}

# 每种 viseme 对应的口型描述（用于调试和可视化）
VISEME_DESCRIPTIONS = {
    "sil": "闭嘴/静默",
    "PP": "双唇闭合",
    "FF": "下唇咬上齿",
    "DD": "舌尖抵上齿龈",
    "kk": "舌根抬起",
    "CH": "嘴唇微圆前突",
    "SS": "齿缝送气",
    "aa": "大开口",
    "EE": "扁唇展开",
    "II": "中开口",
    "OO": "嘴唇圆形",
    "UU": "小圆唇",
    "RR": "卷舌",
    "nn": "鼻音",
}

# 每种 viseme 的"嘴巴张开程度"（0-1），用于骨骼驱动下颌
VISEME_JAW_WEIGHT = {
    "sil": 0.0,
    "PP": 0.0,
    "FF": 0.15,
    "DD": 0.25,
    "kk": 0.2,
    "CH": 0.3,
    "SS": 0.2,
    "aa": 1.0,
    "EE": 0.5,
    "II": 0.4,
    "OO": 0.6,
    "UU": 0.35,
    "RR": 0.3,
    "nn": 0.15,
}


def phoneme_to_viseme(phoneme: str) -> str:
    """将单个音素映射为 viseme 编码。"""
    return PHONEME_TO_VISEME.get(phoneme, "sil")


def timeline_to_visemes(phoneme_timeline: list[dict]) -> list[dict]:
    """将音素时间轴转换为 viseme 时间轴。

    输入: [{"phoneme": "n", "start_ms": 0, "end_ms": 50, "char": "你"}, ...]
    输出: [{"viseme": "DD", "start_ms": 0, "end_ms": 50, "jaw": 0.25, "phoneme": "n"}, ...]
    """
    viseme_timeline = []
    for entry in phoneme_timeline:
        vis = phoneme_to_viseme(entry["phoneme"])
        viseme_timeline.append({
            "viseme": vis,
            "start_ms": entry["start_ms"],
            "end_ms": entry["end_ms"],
            "jaw": VISEME_JAW_WEIGHT.get(vis, 0.0),
            "phoneme": entry["phoneme"],
            "char": entry.get("char", ""),
        })
    return viseme_timeline


def viseme_at_time(viseme_timeline: list[dict], time_ms: float) -> dict:
    """查询指定时间点的 viseme 状态。"""
    for entry in viseme_timeline:
        if entry["start_ms"] <= time_ms < entry["end_ms"]:
            return entry
    return {"viseme": "sil", "jaw": 0.0, "phoneme": "sil", "start_ms": time_ms, "end_ms": time_ms}


def save_viseme_map(output_path: str = "data/viseme_map.json"):
    """导出 viseme 映射表为 JSON 文件。"""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    data = {
        "phoneme_to_viseme": PHONEME_TO_VISEME,
        "viseme_descriptions": VISEME_DESCRIPTIONS,
        "viseme_jaw_weight": VISEME_JAW_WEIGHT,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return output_path


# ------------------------------------------------------------------
# 独立运行测试
# ------------------------------------------------------------------

if __name__ == "__main__":
    from aligner import align_text

    test_text = "你好，欢迎学习人工智能。"
    duration = 4000

    print(f"文本: {test_text}")
    print(f"时长: {duration}ms")
    print()

    phoneme_tl = align_text(test_text, duration)
    viseme_tl = timeline_to_visemes(phoneme_tl)

    print("=== Viseme 时间轴 ===")
    print(f"{'时间段':>20s}  {'音素':>5s}  {'Viseme':>6s}  {'张嘴':>5s}  {'描述':<12s}  {'字'}")
    print("-" * 75)
    for v in viseme_tl:
        desc = VISEME_DESCRIPTIONS.get(v["viseme"], "?")
        span = f"{v['start_ms']:7.1f} ~ {v['end_ms']:7.1f}ms"
        print(f"{span:>20s}  {v['phoneme']:>5s}  {v['viseme']:>6s}  {v['jaw']:>5.2f}  {desc:<12s}  {v['char']}")

    map_path = save_viseme_map()
    print(f"\nViseme 映射表已保存: {map_path}")
