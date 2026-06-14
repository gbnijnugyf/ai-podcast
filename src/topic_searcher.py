"""
主题搜索模块

通过 Bing 搜索指定主题的最新资讯（国内可直接访问），
然后调用 LLM 整理为适合口播的文稿。

配置项（config.yaml）：
  search:
    timeout: 20   # 搜索超时秒数
"""

import re
from datetime import datetime
from urllib.parse import quote_plus

import requests
import yaml
from openai import OpenAI


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
