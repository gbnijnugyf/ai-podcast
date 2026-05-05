"""
音素对齐模块

将中文文本转换为音素序列（声母+韵母），并基于音频时长按比例分配每个音素的时间。
演示级方案：pypinyin + 比例分配。后续可升级为 MFA 获得更精确对齐。
"""

import json
import os
import re

from pypinyin import lazy_pinyin, Style

# 声母列表
INITIALS = {
    "b", "p", "m", "f", "d", "t", "n", "l",
    "g", "k", "h", "j", "q", "x",
    "zh", "ch", "sh", "r", "z", "c", "s",
    "y", "w",
}

# 声母在音节中的时长占比（声母通常较短）
INITIAL_RATIO = 0.35
FINAL_RATIO = 0.65


def text_to_phonemes(text: str) -> list[dict]:
    """将中文文本转换为音素序列。

    每个中文字拆分为声母(initial) + 韵母(final)。
    非中文字符（标点、空格等）标记为 silence。

    Returns:
        [{"char": "你", "initial": "n", "final": "i", "weight": 2.0}, ...]
        weight 表示该字符占用的"时长单位"（中文字=2, 标点=0.5, etc.）
    """
    # lazy_pinyin 对每个输入字符返回一个条目（包括标点），保证索引对齐
    pinyin_list = lazy_pinyin(text, style=Style.TONE3, errors="default")

    phonemes = []
    for i, ch in enumerate(text):
        if ch in " \t\n":
            continue

        py = pinyin_list[i] if i < len(pinyin_list) else ch

        if re.match(r"[\u4e00-\u9fff]", ch):
            py_base = re.sub(r"[0-5]", "", py)
            initial, final = _split_pinyin(py_base)
            phonemes.append({
                "char": ch,
                "pinyin": py_base,
                "initial": initial,
                "final": final,
                "weight": 2.0,
            })
        elif re.match(r"[a-zA-Z0-9]", ch):
            phonemes.append({
                "char": ch,
                "pinyin": ch.lower(),
                "initial": "",
                "final": ch.lower(),
                "weight": 1.5,
            })
        elif ch in "，。！？、；：""''…—,.:;!?":
            phonemes.append({
                "char": ch,
                "pinyin": "",
                "initial": "sil",
                "final": "",
                "weight": 0.8,
            })
        else:
            phonemes.append({
                "char": ch,
                "pinyin": "",
                "initial": "sil",
                "final": "",
                "weight": 0.3,
            })

    return phonemes


def _split_pinyin(py: str) -> tuple[str, str]:
    """将拼音拆分为声母和韵母。"""
    if not py:
        return ("", "")

    for length in (2, 1):
        prefix = py[:length]
        if prefix in INITIALS and len(py) > length:
            return (prefix, py[length:])

    return ("", py)


def align_phonemes(
    phonemes: list[dict],
    start_ms: float,
    end_ms: float,
) -> list[dict]:
    """为音素序列分配时间戳。

    基于每个字符的 weight 按比例分配 [start_ms, end_ms] 的时间。
    每个中文字内部再按 INITIAL_RATIO / FINAL_RATIO 分配声母和韵母的时间。

    Returns:
        [{"phoneme": "n", "start_ms": 0, "end_ms": 50, "char": "你"}, ...]
    """
    total_weight = sum(p["weight"] for p in phonemes)
    if total_weight == 0:
        return []

    duration_ms = end_ms - start_ms
    ms_per_unit = duration_ms / total_weight

    timeline = []
    cursor = start_ms

    for p in phonemes:
        char_duration = p["weight"] * ms_per_unit

        if p["initial"] == "sil":
            timeline.append({
                "phoneme": "sil",
                "start_ms": round(cursor, 1),
                "end_ms": round(cursor + char_duration, 1),
                "char": p["char"],
            })
            cursor += char_duration
            continue

        if p["initial"]:
            init_dur = char_duration * INITIAL_RATIO
            timeline.append({
                "phoneme": p["initial"],
                "start_ms": round(cursor, 1),
                "end_ms": round(cursor + init_dur, 1),
                "char": p["char"],
            })
            cursor += init_dur

            final_dur = char_duration * FINAL_RATIO
        else:
            final_dur = char_duration

        if p["final"]:
            timeline.append({
                "phoneme": p["final"],
                "start_ms": round(cursor, 1),
                "end_ms": round(cursor + final_dur, 1),
                "char": p["char"],
            })
            cursor += final_dur

    return timeline


def align_text(text: str, duration_ms: float, start_ms: float = 0) -> list[dict]:
    """端到端接口：文本 + 时长 → 音素时间轴。"""
    phonemes = text_to_phonemes(text)
    return align_phonemes(phonemes, start_ms, start_ms + duration_ms)


# ------------------------------------------------------------------
# 独立运行测试
# ------------------------------------------------------------------

if __name__ == "__main__":
    test_text = "你好，欢迎学习人工智能。"
    duration = 4000  # 假设 4 秒

    print(f"文本: {test_text}")
    print(f"时长: {duration}ms")
    print()

    phonemes = text_to_phonemes(test_text)
    print("=== 音素序列 ===")
    for p in phonemes:
        print(f"  {p['char']} -> 声母:{p['initial'] or '-'} 韵母:{p['final'] or '-'} (权重:{p['weight']})")

    print()
    timeline = align_text(test_text, duration)
    print("=== 音素时间轴 ===")
    for t in timeline:
        print(f"  {t['start_ms']:7.1f}ms ~ {t['end_ms']:7.1f}ms  [{t['phoneme']:4s}]  {t['char']}")
