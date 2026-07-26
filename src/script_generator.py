"""
分块口播文稿生成模块

根据话题搜索资讯，调用 LLM 生成结构化口播文稿。
输出 JSON 格式，包含 chapters → blocks（每块有 keyword + narration）。
"""

import json
import math
import random
import re

import yaml
from openai import OpenAI

from src.genre import DEFAULT_GENRE, resolve_genre
from src.topic_searcher import TopicSearcher

# 中文口播语速粗估：字/秒（与摘要预估一致）
CHARS_PER_SEC = 4.5
DEFAULT_DURATION_MIN = 2.0
DEFAULT_OPENING_SEC = 8.0

# 每块字数区间固定；时长靠增加章节数/块数达成
BLOCK_CHAR_LO = 30
BLOCK_CHAR_HI = 60
BLOCK_CHAR_MID = (BLOCK_CHAR_LO + BLOCK_CHAR_HI) // 2
ENDING_CHAR_RESERVE = 40


SCRIPT_PROMPT = """你是一个专业的短视频口播文案编辑，擅长用生动有力的语言讲述新闻热点。
请根据以下资讯素材，撰写一篇关于「{topic}」的 {duration_min:g} 分钟口播文稿。

严格要求：
1. 总时长约 {duration_min:g} 分钟（中文约 {char_lo}-{char_hi} 字，包含 opening + 所有 block narration 的总字数）
2. 必须包含 opening（开场概括）：用 1-2 句话点出今天的核心话题，简洁有力，约 {opening_char_lo}-{opening_char_hi} 字（朗读不超过 {opening_sec:.0f} 秒）
3. 正文分为 {chapter_range} 个章节（chapter），每个章节聚焦一个子话题并有深度
4. 每个章节包含 {blocks_per_chapter} 个内容块（block）
5. 每个 block 的 narration 包含 2-3 个分句，朗读时长约 4-8 秒（约 30-60 字）
6. 每个 block 必须有一个 keyword（英文，用于搜索配图，描述该段核心画面）
7. keyword 要具体、可视化，能搜索到有意义的图片（如 "nvidia gpu server rack" 而非 "technology"）
8. keyword 禁止包含 openai、chatgpt、chat gpt 等字样；改用通用画面（如 "ai neural network abstract"）
9. 不需要生成结束语，程序会自动追加

写作风格要求：
- 不要浮于表面罗列信息，要对话题有一定深度的分析
- 善用修辞手法：类比、设问、对比、排比、数据冲击等
- 口语化但不随意，像一个专业主播在和观众对话
- 适当使用短句增强节奏感，关键数据要突出
- 每个章节之间有逻辑递进（现象→原因→影响 或 事件→分析→展望）
{style_extra}
资讯素材：
{materials}

请严格按以下 JSON 格式输出：
{{
  "title": "视频主题标题",
  "opening": "1-2句话点题，约20-30字",
  "chapters": [
    {{
      "chapter_title": "章节标题",
      "blocks": [
        {{
          "keyword": "english image search keyword",
          "narration": "2-3个分句的口播文本，约30-60字"
        }}
      ]
    }}
  ]
}}
"""


ARTICLE_SCRIPT_PROMPT = """你是一个专业的短视频口播文案编辑，擅长把书面文章改写成生动有力的口播文稿。
请根据以下整篇文稿，将其改写为一篇适合短视频口播的文稿。

严格要求：
1. 保留原文的核心信息和逻辑脉络，但用更口语化、更有节奏感的方式重新组织；总时长约 {duration_min:g} 分钟（中文约 {char_lo}-{char_hi} 字，包含 opening + 所有 block narration 的总字数）
2. 必须包含 opening（开场概括）：用 1-2 句话点出今天的核心话题，简洁有力，约 {opening_char_lo}-{opening_char_hi} 字（朗读不超过 {opening_sec:.0f} 秒）
3. 正文分为 {chapter_range} 个章节（chapter），每个章节聚焦一个子话题并有深度
4. 每个章节包含 {blocks_per_chapter} 个内容块（block）
5. 每个 block 的 narration 包含 2-3 个分句，朗读时长约 4-8 秒（约 30-60 字）
6. 每个 block 必须有一个 keyword（英文，用于搜索配图，描述该段核心画面）
7. keyword 要具体、可视化，能搜索到有意义的图片（如 "nvidia gpu server rack" 而非 "technology"）
8. keyword 禁止包含 openai、chatgpt、chat gpt 等字样；改用通用画面（如 "ai neural network abstract"）
9. 不需要生成结束语，程序会自动追加

写作风格要求：
- 不要浮于表面罗列信息，要对话题有一定深度的分析
- 善用修辞手法：类比、设问、对比、排比、数据冲击等
- 口语化但不随意，像一个专业主播在和观众对话
- 适当使用短句增强节奏感，关键数据要突出
- 每个章节之间有逻辑递进（现象→原因→影响 或 事件→分析→展望）
{style_extra}
原文文稿：
{article}

请严格按以下 JSON 格式输出：
{{
  "title": "视频主题标题",
  "opening": "1-2句话点题，约20-30字",
  "chapters": [
    {{
      "chapter_title": "章节标题",
      "blocks": [
        {{
          "keyword": "english image search keyword",
          "narration": "2-3个分句的口播文本，约30-60字"
        }}
      ]
    }}
  ]
}}
"""


TEXT_TO_SCRIPT_PROMPT = """你是一个专业的短视频文稿编辑。
请将以下整篇文稿**原封不动地**切分为适合口播的内容块，只需组织结构和生成英文关键词，**不要改写原文任何文字**。

要求：
1. 正文分为 2-3 个章节（chapter），每个章节聚焦一个子话题
2. 每个章节包含 2-4 个内容块（block）
3. 每个 block 的 narration **必须直接使用原文文本**，可适当截取原文中的连续片段，但**不得做任何改写、缩写或润色**
4. 每个 block 约 30-60 字（朗读约 4-8 秒）
5. 每个 block 必须有一个 keyword（英文，用于搜索配图，描述该段核心画面）
6. keyword 要具体、可视化，能搜索到有意义的图片（如 "nvidia gpu server rack" 而非 "technology"）
7. keyword 禁止包含 openai、chatgpt、chat gpt 等字样；改用通用画面（如 "ai neural network abstract"）
8. 不需要 opening，不需要结束语

原文文稿：
{article}

请严格按以下 JSON 格式输出：
{{
  "title": "视频主题标题",
  "opening": "",
  "chapters": [
    {{
      "chapter_title": "章节标题",
      "blocks": [
        {{
          "keyword": "english image search keyword",
          "narration": "原文文本片段，不得改写"
        }}
      ]
    }}
  ]
}}
"""


def build_duration_plan(
    duration_min: float,
    opening_sec: float = DEFAULT_OPENING_SEC,
) -> dict:
    """由目标分钟数与片头时长，生成写入 prompt 的结构/字数引导。

    每块字数固定为 BLOCK_CHAR_LO–BLOCK_CHAR_HI；通过增加章节数与每章块数，
    保证结构上限容量 >= char_hi，避免总字数目标与结构互相矛盾。
    """
    duration_min = max(float(duration_min), 0.5)
    opening_sec = max(float(opening_sec), 3.0)

    total_sec = duration_min * 60
    target_chars = int(total_sec * CHARS_PER_SEC)
    char_lo = max(80, int(target_chars * 0.9))
    char_hi = int(target_chars * 1.1)

    opening_chars = max(15, int(opening_sec * CHARS_PER_SEC))
    opening_char_lo = max(12, int(opening_chars * 0.85))
    opening_char_hi = max(opening_char_lo + 1, int(opening_chars * 1.1))

    body_hi = max(60, char_hi - opening_char_hi - ENDING_CHAR_RESERVE)
    body_lo = max(60, char_lo - opening_char_hi - ENDING_CHAR_RESERVE)
    # 按块上限凑满 char_hi；按块中位凑满 char_lo —— 取更大者作为目标块数
    blocks_for_hi = math.ceil(body_hi / BLOCK_CHAR_HI)
    blocks_for_lo = math.ceil(body_lo / BLOCK_CHAR_MID)
    target_blocks = max(blocks_for_hi, blocks_for_lo, 4)

    ideal_bpc = 4 if target_blocks <= 16 else 5
    max_chapters = 6 if duration_min <= 4 else 8
    chapters = max(1, min(max_chapters, math.ceil(target_blocks / ideal_bpc)))
    blocks_per = max(2, math.ceil(target_blocks / chapters))

    # 结构上限必须盖住 char_hi
    while chapters * blocks_per * BLOCK_CHAR_HI < body_hi:
        if blocks_per < 8:
            blocks_per += 1
        else:
            chapters += 1

    chapter_lo = max(1, chapters - 1) if chapters > 1 else 1
    chapter_hi = chapters
    blocks_lo = max(2, blocks_per - 1)
    blocks_hi = blocks_per
    while chapter_hi * blocks_hi * BLOCK_CHAR_HI < body_hi:
        blocks_hi += 1

    chapter_range = (
        str(chapter_hi) if chapter_lo == chapter_hi else f"{chapter_lo}-{chapter_hi}"
    )
    blocks_per_chapter = (
        str(blocks_hi) if blocks_lo == blocks_hi else f"{blocks_lo}-{blocks_hi}"
    )

    return {
        "duration_min": duration_min,
        "char_lo": char_lo,
        "char_hi": char_hi,
        "chapter_range": chapter_range,
        "blocks_per_chapter": blocks_per_chapter,
        "target_blocks": target_blocks,
        "max_capacity_chars": (
            opening_char_hi + chapter_hi * blocks_hi * BLOCK_CHAR_HI
        ),
        "opening_sec": opening_sec,
        "opening_char_lo": opening_char_lo,
        "opening_char_hi": opening_char_hi,
    }


class ScriptGenerator:
    def __init__(
        self,
        config_path: str = "config.yaml",
        genre: str | None = None,
        duration_min: float = DEFAULT_DURATION_MIN,
        opening_sec: float | None = None,
    ):
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        ds_cfg = config["deepseek"]
        self.client = OpenAI(api_key=ds_cfg["api_key"], base_url=ds_cfg["base_url"])
        self.model = ds_cfg.get("model", "deepseek-chat")
        self.config_path = config_path
        self.genre = resolve_genre(genre or DEFAULT_GENRE)
        self.duration_min = float(duration_min) if duration_min else DEFAULT_DURATION_MIN
        self.opening_sec = float(opening_sec) if opening_sec else DEFAULT_OPENING_SEC
        self.plan = build_duration_plan(self.duration_min, self.opening_sec)

    def _style_extra(self) -> str:
        tone = (self.genre.script_tone or "").strip()
        return f"{tone}\n" if tone else ""

    def _prompt_kwargs(self) -> dict:
        return {**self.plan, "style_extra": self._style_extra()}

    def generate(self, topic: str) -> dict:
        """搜索话题资讯并生成结构化口播文稿。"""
        print(f"\n{'=' * 60}")
        print(f"  生成分块口播文稿：{topic}")
        print(f"  节目形态: {self.genre.id} ({self.genre.display_name})")
        print(f"  目标时长: {self.duration_min:g} min（约 {self.plan['char_lo']}-{self.plan['char_hi']} 字）")
        print(f"  结构引导: {self.plan['chapter_range']} 章 × 每章 {self.plan['blocks_per_chapter']} 块"
              f"（每块 {BLOCK_CHAR_LO}-{BLOCK_CHAR_HI} 字，容量上限约 {self.plan['max_capacity_chars']} 字）")
        print(f"  开场白:   约 {self.opening_sec:.0f}s（对齐片头）")
        print(f"{'=' * 60}\n")

        searcher = TopicSearcher(self.config_path)
        results = searcher.search(topic, max_results=12)
        if not results:
            raise RuntimeError(f"未搜索到与「{topic}」相关的资讯")

        materials = ""
        for i, r in enumerate(results, 1):
            materials += f"[{i}] {r['title']}\n   {r['body']}\n\n"

        print("  调用 LLM 生成分块文稿...")
        prompt = SCRIPT_PROMPT.format(
            topic=topic, materials=materials, **self._prompt_kwargs()
        )

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "你是专业短视频文案编辑。严格按 JSON 格式输出，不要输出任何其他内容。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.6,
        )

        raw = response.choices[0].message.content.strip()
        script_data = self._parse_response(raw)
        script_data["genre"] = self.genre.id
        script_data["duration_min"] = self.duration_min
        self._append_ending(script_data)
        self._print_summary(script_data)
        return script_data

    def generate_from_article(self, article_text: str) -> dict:
        """根据整篇文稿文本生成结构化口播文稿（不搜索资讯）。"""
        print(f"\n{'=' * 60}")
        print(f"  根据文稿生成口播文稿")
        print(f"  节目形态: {self.genre.id} ({self.genre.display_name})")
        print(f"  目标时长: {self.duration_min:g} min（约 {self.plan['char_lo']}-{self.plan['char_hi']} 字）")
        print(f"  结构引导: {self.plan['chapter_range']} 章 × 每章 {self.plan['blocks_per_chapter']} 块"
              f"（每块 {BLOCK_CHAR_LO}-{BLOCK_CHAR_HI} 字，容量上限约 {self.plan['max_capacity_chars']} 字）")
        print(f"  开场白:   约 {self.opening_sec:.0f}s（对齐片头）")
        print(f"{'=' * 60}\n")

        prompt = ARTICLE_SCRIPT_PROMPT.format(
            article=article_text, **self._prompt_kwargs()
        )

        print("  调用 LLM 转换文稿为分块口播...")
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "你是专业短视频文案编辑。严格按 JSON 格式输出，不要输出任何其他内容。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.6,
        )

        raw = response.choices[0].message.content.strip()
        script_data = self._parse_response(raw)
        script_data["genre"] = self.genre.id
        script_data["duration_min"] = self.duration_min
        self._append_ending(script_data)
        self._print_summary(script_data)
        return script_data

    def text_to_script(self, article_text: str) -> dict:
        """将整篇文稿文本直接转换为口播文稿 JSON（保留原文，LLM 负责分块+生成 keyword）。

        用于 --not-convert 模式：LLM 仅对原文做分块和组织结构，
        不修改 narration 原文，同时为每个 block 生成英文 keyword。
        """
        print(f"\n{'=' * 60}")
        print(f"  原文分块 + 生成关键词（保留原文不改写）")
        print(f"  [提示] --not-convert 不改写原文，--duration 不生效")
        print(f"{'=' * 60}\n")

        prompt = TEXT_TO_SCRIPT_PROMPT.format(article=article_text.strip())

        print("  调用 LLM 进行分块...")
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "你是专业短视频文稿编辑。严格按 JSON 格式输出，不要输出任何其他内容。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,  # 低温确保原文不被改写
        )

        raw = response.choices[0].message.content.strip()
        script_data = self._parse_response(raw)
        # 不追加结束语（not-convert 模式保持原文完整）
        self._print_summary(script_data)
        return script_data

    def _parse_response(self, raw: str) -> dict:
        """从 LLM 响应中解析 JSON。"""
        json_match = re.search(r'\{.*\}', raw, re.DOTALL)
        if json_match:
            raw = json_match.group()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"LLM 输出格式错误，无法解析 JSON: {e}\n原文: {raw[:500]}")

        if "chapters" not in data or not data["chapters"]:
            raise RuntimeError(f"LLM 输出缺少 chapters 字段")

        return data

    def _append_ending(self, data: dict):
        """在最后一个章节末尾追加预设结束语 block。"""
        ending = random.choice(self.genre.endings)
        ending_block = {"keyword": "subscribe button", "narration": ending}
        data["chapters"][-1]["blocks"].append(ending_block)

    def _print_summary(self, data: dict):
        """打印文稿摘要。"""
        total_chars = len(data.get("opening", ""))
        total_blocks = 0
        for ch in data["chapters"]:
            for block in ch["blocks"]:
                total_chars += len(block["narration"])
                total_blocks += 1

        estimated_duration = total_chars / CHARS_PER_SEC
        opening_chars = len(data.get("opening", ""))
        opening_est = opening_chars / CHARS_PER_SEC
        print(f"\n  标题: {data['title']}")
        print(f"  开场: {data.get('opening', '')[:40]}...（{opening_chars} 字 / ~{opening_est:.0f}s）")
        print(f"  章节: {len(data['chapters'])} 个")
        print(f"  内容块: {total_blocks} 个")
        print(f"  总字数: {total_chars} 字")
        print(f"  预估时长: {estimated_duration:.0f}s (~{estimated_duration/60:.1f}min)")
