"""
背景图片搜索模块

根据关键词从 Pexels 和 Unsplash 搜索横版高清图片，
下载并缩放为 1920x1080 全屏背景。
"""

import hashlib
import os

import requests
import yaml
from PIL import Image


class BackgroundSearcher:
    def __init__(self, config_path: str = "config.yaml"):
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        pexels_cfg = config.get("pexels", {})
        self.pexels_api_key = pexels_cfg.get("api_key", "")
        self.cache_dir = pexels_cfg.get("cache_dir", "asset/images")
        os.makedirs(self.cache_dir, exist_ok=True)

        self.width = config.get("slides", {}).get("width", 1920)
        self.height = config.get("slides", {}).get("height", 1080)

    def search_and_download(self, keyword: str) -> str | None:
        """搜索关键词对应的横版图片，下载后缩放为全屏尺寸返回路径。

        优先 Pexels，失败时降级到 Unsplash。
        """
        if not keyword:
            return None

        cache_name = hashlib.md5(f"bg_{keyword}".encode()).hexdigest() + ".jpg"
        cache_path = os.path.join(self.cache_dir, cache_name)
        if os.path.exists(cache_path):
            return cache_path

        img_url = self._search_pexels(keyword)
        if not img_url:
            img_url = self._search_unsplash(keyword)

        if not img_url:
            return None

        try:
            resp = requests.get(img_url, timeout=30)
            resp.raise_for_status()

            raw_path = cache_path + ".raw"
            with open(raw_path, "wb") as f:
                f.write(resp.content)

            self._resize_to_fullscreen(raw_path, cache_path)
            os.remove(raw_path)
            return cache_path
        except Exception as e:
            print(f"    [下载失败] {keyword}: {e}")
            return None

    def download_for_script(self, script_data: dict, output_dir: str) -> list[str]:
        """为整个脚本的每个 block 下载背景图，返回图片路径列表（与 block 一一对应）。"""
        os.makedirs(output_dir, exist_ok=True)
        paths = []
        block_idx = 0

        for ch in script_data["chapters"]:
            for block in ch["blocks"]:
                block_idx += 1
                keyword = block.get("keyword", "")
                print(f"  [{block_idx}] 搜索背景图: {keyword}")

                img_path = self.search_and_download(keyword)
                if img_path:
                    dest = os.path.join(output_dir, f"bg_{block_idx:03d}.jpg")
                    if os.path.abspath(img_path) != os.path.abspath(dest):
                        Image.open(img_path).save(dest, quality=92)
                    paths.append(dest)
                    print(f"      → {dest}")
                else:
                    fallback = self._generate_fallback(keyword, output_dir, block_idx)
                    paths.append(fallback)
                    print(f"      → 使用纯色兜底: {fallback}")

        return paths

    def _search_pexels(self, keyword: str) -> str | None:
        """从 Pexels 搜索横版图片，返回图片 URL。"""
        if not self.pexels_api_key:
            return None

        try:
            resp = requests.get(
                "https://api.pexels.com/v1/search",
                headers={"Authorization": self.pexels_api_key},
                params={
                    "query": keyword,
                    "per_page": 5,
                    "orientation": "landscape",
                    "size": "large",
                },
                timeout=15,
            )
            resp.raise_for_status()
            photos = resp.json().get("photos", [])
            if photos:
                return photos[0]["src"]["large2x"]
        except Exception as e:
            print(f"    [Pexels 失败] {keyword}: {e}")
        return None

    def _search_unsplash(self, keyword: str) -> str | None:
        """从 Unsplash Source 获取图片（无需 API Key）。"""
        try:
            url = f"https://source.unsplash.com/1920x1080/?{keyword.replace(' ', ',')}"
            resp = requests.get(url, timeout=20, allow_redirects=True)
            if resp.status_code == 200 and len(resp.content) > 5000:
                return resp.url
        except Exception as e:
            print(f"    [Unsplash 失败] {keyword}: {e}")
        return None

    def _resize_to_fullscreen(self, src_path: str, dest_path: str):
        """将图片缩放/裁剪为 1920x1080 全屏（居中裁切）。"""
        img = Image.open(src_path).convert("RGB")
        target_ratio = self.width / self.height
        img_ratio = img.width / img.height

        if img_ratio > target_ratio:
            new_height = img.height
            new_width = int(new_height * target_ratio)
            left = (img.width - new_width) // 2
            img = img.crop((left, 0, left + new_width, new_height))
        else:
            new_width = img.width
            new_height = int(new_width / target_ratio)
            top = (img.height - new_height) // 2
            img = img.crop((0, top, new_width, top + new_height))

        img = img.resize((self.width, self.height), Image.LANCZOS)
        img.save(dest_path, "JPEG", quality=92)

    def _generate_fallback(self, keyword: str, output_dir: str, idx: int) -> str:
        """搜索全部失败时生成深色纯色背景作为兜底。"""
        img = Image.new("RGB", (self.width, self.height), (25, 25, 35))
        path = os.path.join(output_dir, f"bg_{idx:03d}.jpg")
        img.save(path, "JPEG", quality=92)
        return path
