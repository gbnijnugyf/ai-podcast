"""
分块口播文稿生成模块

根据话题搜索资讯，调用 LLM 生成约 2 分钟的结构化口播文稿。
输出 JSON 格式，包含 chapters → blocks（每块有 keyword + narration）。
"""

import json
import random
import re

import yaml
from openai import OpenAI

from src.topic_searcher import TopicSearcher


ENDINGS = [
    "每天两分钟，关注我，我们下期见！",
    "关注我，每天两分钟了解热点大事，下期见！",
    "又是被新闻信息洪流挤爆的一天，两分钟给你讲明白。",
    "关注我，培养一个每天了解时事热点的微习惯。",
    "每天花两分钟跟我看看世界在发生什么，咱们下期不见不散。",
    "今天就聊到这，点个关注不迷路，我们下期接着唠。",
    "关注我，每天两分钟帮你把握时事脉搏，咱们下期见。",
]


SCRIPT_PROMPT = """你是一个专业的短视频口播文案编辑，擅长用生动有力的语言讲述新闻热点。
请根据以下资讯素材，撰写一篇关于「{topic}」的 2 分钟口播文稿。

严格要求：
1. 总时长约 2 分钟（中文约 500-600 字，包含 opening + 所有 block narration 的总字数）
2. 必须包含 opening（开场概括）：用 1-2 句话点出今天的核心话题，简洁有力，约 20-30 字（朗读不超过 8 秒）
3. 正文分为 2-3 个章节（chapter），每个章节聚焦一个子话题并有深度
4. 每个章节包含 2-4 个内容块（block）
5. 每个 block 的 narration 包含 2-3 个分句，朗读时长约 4-8 秒（约 30-60 字）
6. 每个 block 必须有一个 keyword（英文，用于搜索配图，描述该段核心画面）
7. keyword 要具体、可视化，能搜索到有意义的图片（如 "nvidia gpu server rack" 而非 "technology"）
8. 不需要生成结束语，程序会自动追加

写作风格要求：
- 不要浮于表面罗列信息，要对话题有一定深度的分析
- 善用修辞手法：类比、设问、对比、排比、数据冲击等
- 口语化但不随意，像一个专业主播在和观众对话
- 适当使用短句增强节奏感，关键数据要突出
- 每个章节之间有逻辑递进（现象→原因→影响 或 事件→分析→展望）

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


class ScriptGenerator:
    def __init__(self, config_path: str = "config.yaml"):
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        ds_cfg = config["deepseek"]
        self.client = OpenAI(api_key=ds_cfg["api_key"], base_url=ds_cfg["base_url"])
        self.model = ds_cfg.get("model", "deepseek-chat")
        self.config_path = config_path

    def generate(self, topic: str) -> dict:
        """搜索话题资讯并生成结构化口播文稿。"""
        print(f"\n{'=' * 60}")
        print(f"  生成分块口播文稿：{topic}")
        print(f"{'=' * 60}\n")

        searcher = TopicSearcher(self.config_path)
        results = searcher.search(topic, max_results=12)
        if not results:
            raise RuntimeError(f"未搜索到与「{topic}」相关的资讯")

        materials = ""
        for i, r in enumerate(results, 1):
            materials += f"[{i}] {r['title']}\n   {r['body']}\n\n"

        print("  调用 LLM 生成分块文稿...")
        prompt = SCRIPT_PROMPT.format(topic=topic, materials=materials)

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
        self._append_ending(script_data)
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
        ending = random.choice(ENDINGS)
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

        estimated_duration = total_chars / 4.5
        print(f"\n  标题: {data['title']}")
        print(f"  开场: {data.get('opening', '')[:40]}...")
        print(f"  章节: {len(data['chapters'])} 个")
        print(f"  内容块: {total_blocks} 个")
        print(f"  总字数: {total_chars} 字")
        print(f"  预估时长: {estimated_duration:.0f}s (~{estimated_duration/60:.1f}min)")
