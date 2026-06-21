"""
主题搜索模块

通过 Bing 搜索指定主题的最新资讯（国内可直接访问），
然后调用 LLM 整理为适合口播的文稿。

配置项（config.yaml）：
  search:
    timeout: 20   # 搜索超时秒数
"""

import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

import requests
import yaml
from openai import OpenAI


AI_DAILY_ROOT = Path(r"D:\Study\aiproject\ai-daily\paper_daily")
AI_DAILY_OUTPUT_ROOT = AI_DAILY_ROOT / "output"
REPORT_FILENAME = "finance_daily_report.txt"
GENERATE_SCRIPT = AI_DAILY_ROOT / "generate_report_only.py"


SUMMARIZE_PROMPT = """你是一个专业的新闻编辑。请根据以下搜索结果，整理出一篇关于「{topic}」的资讯简报。

要求：
1. 从搜索结果中筛选出最有价值的 3-6 条信息
2. 每条信息用 2-4 句话概括关键内容，保留具体数据和事实
3. 各条之间用空行分隔
4. 不要编造搜索结果中没有的信息
5. 输出纯文本，不要使用 markdown 格式
6. 总字数控制在 4000-5000 字
7. 语言与搜索结果一致（中文结果输出中文，英文结果输出英文）

搜索结果：
{results}
"""

TOPIC_SELECTION_PROMPT = """你是一个资深新闻编辑。请根据以下今日资讯汇总，选出 2-3 个最适合做短视频口播的热门话题。

选题标准：
1. 时效性强（当天或近期的热点新闻）
2. 大众关注度高（科技、财经、社会、AI 等领域优先）
3. 内容有信息量和话题性，适合 2 分钟口播讲解
4. 2-3 个话题之间尽量覆盖不同领域，避免重复

今日资讯来源 1（金融/科技日报）：
{daily_report}

今日资讯来源 2（Bing 热搜新闻）：
{bing_news}

请严格按以下 JSON 格式输出，不要包含其他内容：
["话题1的简短标题", "话题2的简短标题", "话题3的简短标题"]
"""


class TopicSearcher:
    def __init__(self, config_path: str = "config.yaml"):
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        ds_cfg = config["deepseek"]
        self.client = OpenAI(api_key=ds_cfg["api_key"], base_url=ds_cfg["base_url"])
        self.model = ds_cfg.get("model", "deepseek-chat")

        search_cfg = config.get("search", {})
        self.timeout = search_cfg.get("timeout", 20)

    def search(self, topic: str, max_results: int = 10) -> list[dict]:
        """使用 Bing 搜索主题相关的最新资讯（国内可直接访问）。"""
        print(f"  正在搜索: {topic}")
        results = self._search_bing_news(topic, max_results)

        if not results:
            print("  新闻搜索无结果，尝试网页搜索...")
            results = self._search_bing_web(topic, max_results)

        print(f"  找到 {len(results)} 条结果")
        return results

    def _search_bing_news(self, topic: str, max_results: int) -> list[dict]:
        """从 Bing 新闻搜索获取结果。"""
        url = f"https://cn.bing.com/news/search?q={quote_plus(topic)}&FORM=HDRSC6"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

        try:
            resp = requests.get(url, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            return self._parse_bing_news(resp.text, max_results)
        except Exception as e:
            print(f"  [Bing 新闻搜索失败] {e}")
            return []

    def _search_bing_web(self, topic: str, max_results: int) -> list[dict]:
        """从 Bing 网页搜索获取结果。"""
        url = f"https://cn.bing.com/search?q={quote_plus(topic)}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

        try:
            resp = requests.get(url, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            return self._parse_bing_web(resp.text, max_results)
        except Exception as e:
            print(f"  [Bing 网页搜索失败] {e}")
            return []

    def _parse_bing_news(self, html: str, max_results: int) -> list[dict]:
        """解析 Bing 新闻搜索结果页面。"""
        results = []
        cards = re.findall(
            r'<a[^>]*class="[^"]*title[^"]*"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
            html, re.DOTALL,
        )
        snippets = re.findall(
            r'<div[^>]*class="[^"]*snippet[^"]*"[^>]*>(.*?)</div>',
            html, re.DOTALL,
        )

        for i, (url, title) in enumerate(cards[:max_results]):
            clean_title = re.sub(r'<[^>]+>', '', title).strip()
            body = ""
            if i < len(snippets):
                body = re.sub(r'<[^>]+>', '', snippets[i]).strip()
            if clean_title:
                results.append({
                    "title": clean_title,
                    "body": body,
                    "source": url,
                    "date": "",
                    "url": url,
                })

        return results

    def _parse_bing_web(self, html: str, max_results: int) -> list[dict]:
        """解析 Bing 网页搜索结果。"""
        results = []
        blocks = re.findall(
            r'<li class="b_algo"[^>]*>(.*?)</li>',
            html, re.DOTALL,
        )

        for block in blocks[:max_results]:
            title_match = re.search(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', block, re.DOTALL)
            snippet_match = re.search(r'<p[^>]*>(.*?)</p>', block, re.DOTALL)

            if title_match:
                url = title_match.group(1)
                title = re.sub(r'<[^>]+>', '', title_match.group(2)).strip()
                body = ""
                if snippet_match:
                    body = re.sub(r'<[^>]+>', '', snippet_match.group(1)).strip()
                if title:
                    results.append({
                        "title": title,
                        "body": body,
                        "source": url,
                        "date": "",
                        "url": url,
                    })

        return results

    def summarize(self, topic: str, search_results: list[dict]) -> str:
        """调用 LLM 将搜索结果整理为口播文稿。"""
        results_text = ""
        for i, r in enumerate(search_results, 1):
            results_text += f"\n[{i}] {r['title']}\n"
            if r['date']:
                results_text += f"   日期: {r['date']}\n"
            if r['source']:
                results_text += f"   来源: {r['source']}\n"
            results_text += f"   内容: {r['body']}\n"

        prompt = SUMMARIZE_PROMPT.format(topic=topic, results=results_text)

        print("  正在整理文稿...")
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "你是一个专业的新闻编辑，擅长将碎片化的信息整理为结构清晰的简报。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.5,
        )
        return response.choices[0].message.content.strip()

    def search_and_summarize(self, topic: str, max_results: int = 10) -> str:
        """一键搜索并整理：返回适合口播的文稿文本。"""
        results = self.search(topic, max_results)
        if not results:
            raise RuntimeError(f"未搜索到与「{topic}」相关的结果")
        return self.summarize(topic, results)

    # ------------------------------------------------------------------
    # 自动选题
    # ------------------------------------------------------------------

    def auto_select_topics(self, date_str: str | None = None) -> list[str]:
        """结合 ai-daily 输出和 Bing 热搜，自动选出 2-3 个热门话题。

        ai-daily 是必须流程：若当天无输出则先生成。
        """
        if date_str is None:
            date_str = datetime.now().strftime("%Y-%m-%d")

        print(f"{'=' * 60}")
        print(f"  阶段 1：自动选取热门话题")
        print(f"{'=' * 60}\n")

        daily_report = self._ensure_daily_report(date_str)
        bing_news = self._fetch_bing_trending()

        print("  调用 LLM 筛选热门话题...")
        prompt = TOPIC_SELECTION_PROMPT.format(
            daily_report=daily_report[:3000],
            bing_news=bing_news[:2000],
        )

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "你是一个资深新闻编辑，擅长发现热点话题。请严格按 JSON 数组格式输出。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
        )

        raw = response.choices[0].message.content.strip()
        import json
        try:
            match = re.search(r'\[.*\]', raw, re.DOTALL)
            topics = json.loads(match.group()) if match else json.loads(raw)
        except (json.JSONDecodeError, AttributeError):
            topics = [line.strip().strip('"').strip("'") for line in raw.splitlines() if line.strip()]

        topics = [t for t in topics if t][:3]
        if not topics:
            raise RuntimeError("LLM 未能选出有效话题")

        return topics

    def _ensure_daily_report(self, date_str: str) -> str:
        """确保 ai-daily 当天输出存在，若不存在则生成。返回日报文本。"""
        report_path = AI_DAILY_OUTPUT_ROOT / date_str / REPORT_FILENAME

        if not report_path.exists():
            print(f"  未找到 {date_str} 的日报，正在生成...")
            if not GENERATE_SCRIPT.exists():
                raise FileNotFoundError(f"ai-daily 脚本不存在: {GENERATE_SCRIPT}")

            result = subprocess.run(
                [sys.executable, str(GENERATE_SCRIPT), "--date", date_str],
                encoding="utf-8",
                errors="replace",
                cwd=str(AI_DAILY_ROOT),
            )
            if result.returncode != 0:
                raise RuntimeError(f"ai-daily 生成失败 (exit code {result.returncode})")

            if not report_path.exists():
                raise FileNotFoundError(f"日报生成后仍未找到文件: {report_path}")

        print(f"  日报文件: {report_path}")
        return report_path.read_text(encoding="utf-8")

    def _fetch_bing_trending(self) -> str:
        """获取 Bing 当日热搜新闻摘要。"""
        print("  获取 Bing 热搜新闻...")
        results = self._search_bing_news("今日热点新闻", 15)
        if not results:
            results = self._search_bing_web("今日热点 科技 财经", 10)

        text_parts = []
        for r in results[:15]:
            text_parts.append(f"- {r['title']}: {r['body'][:100]}")

        summary = "\n".join(text_parts)
        print(f"  获取到 {len(results)} 条热搜")
        return summary
