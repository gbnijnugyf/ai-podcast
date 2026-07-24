"""
背景媒资搜索模块

多源检索横版图片/视频（Pexels + Pixabay），规则打分选优，
支持 --max-videos 限制全片使用视频背景的 block 数量（默认 0=全用图）。
"""

from __future__ import annotations

import hashlib
import os
import random
import re
import shutil
import time
from dataclasses import dataclass, field

import requests
import yaml
from PIL import Image

FALLBACK_BG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "asset", "topic_bg_default"
)

VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


@dataclass
class MediaCandidate:
    media_id: str
    source: str
    media_type: str  # image | video
    url: str
    title: str = ""
    tags: str = ""
    score: float = 0.0
    meta: dict = field(default_factory=dict)


class BackgroundSearcher:
    def __init__(self, config_path: str = "config.yaml"):
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

        pexels_cfg = config.get("pexels", {}) or {}
        pixabay_cfg = config.get("pixabay", {}) or {}
        media_cfg = config.get("media", {}) or {}

        self.pexels_api_key = pexels_cfg.get("api_key", "") or ""
        self.pixabay_api_key = pixabay_cfg.get("api_key", "") or ""
        self.cache_dir = pexels_cfg.get("cache_dir", "asset/images")
        os.makedirs(self.cache_dir, exist_ok=True)

        self.width = config.get("slides", {}).get("width", 1920)
        self.height = config.get("slides", {}).get("height", 1080)
        self.per_page = int(media_cfg.get("per_page", 8))
        self._used_fallback_images: set[str] = set()
        self._used_media_ids: set[str] = set()

    def search_and_download(self, keyword: str) -> str | None:
        """兼容旧接口：仅下载一张最优图片。"""
        if not keyword:
            return None
        images, _videos = self._gather_candidates(keyword, want_video=False)
        best = self._pick_best(images, exclude_ids=set())
        if not best:
            return None
        return self._download_candidate(best)

    def download_for_script(
        self,
        script_data: dict,
        output_dir: str,
        max_videos: int = 0,
    ) -> list[str]:
        """为每个 block 下载背景媒资，返回路径列表（jpg 或 mp4）。

        max_videos: 全片最多使用视频背景的 block 数；0 表示全部用图。
        视频名额按「视频相对图片的相关度优势」优先分配。
        """
        os.makedirs(output_dir, exist_ok=True)
        max_videos = max(0, int(max_videos or 0))
        self._used_media_ids.clear()

        blocks: list[dict] = []
        for ch in script_data.get("chapters", []):
            for block in ch.get("blocks", []):
                blocks.append(block)

        print(f"  媒资渠道: Pexels/Pixabay（图"
              f"{'+视频' if max_videos > 0 else ''}），max_videos={max_videos}")

        # 1) 每块收集候选
        block_candidates: list[dict] = []
        for i, block in enumerate(blocks, 1):
            keyword = (block.get("keyword") or "").strip()
            narration = (block.get("narration") or "").strip()
            print(f"  [{i}/{len(blocks)}] 搜索媒资: {keyword}")
            images, videos = self._gather_candidates(
                keyword, want_video=(max_videos > 0)
            )
            for c in images + videos:
                c.score = self._score_candidate(c, keyword, narration)
            images.sort(key=lambda c: c.score, reverse=True)
            videos.sort(key=lambda c: c.score, reverse=True)
            best_img = images[0] if images else None
            best_vid = videos[0] if videos else None
            advantage = 0.0
            if best_vid and best_img:
                advantage = best_vid.score - best_img.score
            elif best_vid:
                advantage = best_vid.score
            block_candidates.append({
                "index": i,
                "keyword": keyword,
                "images": images,
                "videos": videos,
                "best_img": best_img,
                "best_vid": best_vid,
                "advantage": advantage,
            })
            img_n, vid_n = len(images), len(videos)
            top = best_vid or best_img
            top_info = (
                f"{top.source}/{top.media_type} score={top.score:.1f}"
                if top else "无候选"
            )
            print(f"      候选: 图 {img_n} / 视频 {vid_n}，最优 {top_info}")

        # 2) 分配视频名额（相关度优势优先）
        video_slots: set[int] = set()
        if max_videos > 0:
            ranked = sorted(
                [b for b in block_candidates if b["best_vid"] is not None],
                key=lambda b: b["advantage"],
                reverse=True,
            )
            for b in ranked[:max_videos]:
                video_slots.add(b["index"])
            if video_slots:
                print(f"  视频名额分配给 block: {sorted(video_slots)}")

        # 3) 下载
        paths: list[str] = []
        for b in block_candidates:
            idx = b["index"]
            use_video = idx in video_slots
            chosen = None
            if use_video:
                chosen = self._pick_best(b["videos"], self._used_media_ids)
            if chosen is None:
                chosen = self._pick_best(b["images"], self._used_media_ids)

            if chosen is None:
                fallback = self._generate_fallback(b["keyword"], output_dir, idx)
                paths.append(fallback)
                print(f"      → 兜底图: {fallback}")
                continue

            local = self._download_candidate(chosen)
            if not local:
                fallback = self._generate_fallback(b["keyword"], output_dir, idx)
                paths.append(fallback)
                print(f"      → 下载失败，兜底图: {fallback}")
                continue

            self._used_media_ids.add(chosen.media_id)
            ext = ".mp4" if chosen.media_type == "video" else ".jpg"
            dest = os.path.join(output_dir, f"bg_{idx:03d}{ext}")
            if os.path.abspath(local) != os.path.abspath(dest):
                if chosen.media_type == "video":
                    shutil.copy2(local, dest)
                else:
                    Image.open(local).convert("RGB").save(dest, quality=92)
            paths.append(dest)
            print(f"      → {chosen.source}/{chosen.media_type} ({chosen.score:.1f}): {dest}")

        return paths

    # ------------------------------------------------------------------
    # 候选收集
    # ------------------------------------------------------------------

    def _gather_candidates(
        self, keyword: str, want_video: bool
    ) -> tuple[list[MediaCandidate], list[MediaCandidate]]:
        images: list[MediaCandidate] = []
        videos: list[MediaCandidate] = []
        if not keyword:
            return images, videos

        images.extend(self._search_pexels_photos(keyword))
        images.extend(self._search_pixabay_photos(keyword))
        if want_video:
            videos.extend(self._search_pexels_videos(keyword))
            videos.extend(self._search_pixabay_videos(keyword))

        # 无 API 结果时保留 Unsplash 作为图片弱兜底（单条、无元数据）
        if not images:
            url = self._search_unsplash(keyword)
            if url:
                images.append(MediaCandidate(
                    media_id=f"unsplash:image:{hashlib.md5(url.encode()).hexdigest()[:12]}",
                    source="unsplash",
                    media_type="image",
                    url=url,
                    title=keyword,
                    tags=keyword,
                ))
        return images, videos

    def _search_pexels_photos(self, keyword: str) -> list[MediaCandidate]:
        if not self.pexels_api_key:
            return []
        data = self._request_json(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": self.pexels_api_key},
            params={
                "query": keyword,
                "per_page": self.per_page,
                "orientation": "landscape",
                "size": "large",
            },
            label="Pexels图",
            keyword=keyword,
        )
        if not data:
            return []
        out = []
        for photo in data.get("photos", []):
            src = (photo.get("src") or {}).get("large2x") or (photo.get("src") or {}).get("large")
            if not src:
                continue
            out.append(MediaCandidate(
                media_id=f"pexels:image:{photo.get('id')}",
                source="pexels",
                media_type="image",
                url=src,
                title=photo.get("alt") or "",
                tags=photo.get("alt") or "",
                meta={"photographer": photo.get("photographer", "")},
            ))
        return out

    def _search_pexels_videos(self, keyword: str) -> list[MediaCandidate]:
        if not self.pexels_api_key:
            return []
        data = self._request_json(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": self.pexels_api_key},
            params={
                "query": keyword,
                "per_page": self.per_page,
                "orientation": "landscape",
                "size": "medium",
            },
            label="Pexels视频",
            keyword=keyword,
        )
        if not data:
            return []
        out = []
        for video in data.get("videos", []):
            url = self._pick_pexels_video_url(video.get("video_files") or [])
            if not url:
                continue
            user = (video.get("user") or {}).get("name", "")
            out.append(MediaCandidate(
                media_id=f"pexels:video:{video.get('id')}",
                source="pexels",
                media_type="video",
                url=url,
                title=user,
                tags=" ".join(
                    t.get("name", "") for t in (video.get("tags") or []) if isinstance(t, dict)
                ) or keyword,
                meta={"duration": video.get("duration")},
            ))
        return out

    def _pick_pexels_video_url(self, files: list[dict]) -> str | None:
        """优先选接近 1920 宽的 mp4。"""
        mp4s = [
            f for f in files
            if str(f.get("file_type", "")).endswith("mp4") or f.get("file_type") == "video/mp4"
        ]
        if not mp4s:
            mp4s = files
        if not mp4s:
            return None

        def quality(f: dict) -> tuple:
            w = int(f.get("width") or 0)
            return (-abs(w - 1920), -(w))

        mp4s.sort(key=quality)
        return mp4s[0].get("link")

    def _search_pixabay_photos(self, keyword: str) -> list[MediaCandidate]:
        if not self.pixabay_api_key:
            return []
        data = self._request_json(
            "https://pixabay.com/api/",
            params={
                "key": self.pixabay_api_key,
                "q": keyword,
                "image_type": "photo",
                "orientation": "horizontal",
                "per_page": min(self.per_page, 20),
                "safesearch": "true",
            },
            label="Pixabay图",
            keyword=keyword,
        )
        if not data:
            return []
        out = []
        for hit in data.get("hits", []):
            url = hit.get("largeImageURL") or hit.get("webformatURL")
            if not url:
                continue
            tags = hit.get("tags") or ""
            out.append(MediaCandidate(
                media_id=f"pixabay:image:{hit.get('id')}",
                source="pixabay",
                media_type="image",
                url=url,
                title=tags,
                tags=tags,
            ))
        return out

    def _search_pixabay_videos(self, keyword: str) -> list[MediaCandidate]:
        if not self.pixabay_api_key:
            return []
        data = self._request_json(
            "https://pixabay.com/api/videos/",
            params={
                "key": self.pixabay_api_key,
                "q": keyword,
                "per_page": min(self.per_page, 20),
                "safesearch": "true",
            },
            label="Pixabay视频",
            keyword=keyword,
        )
        if not data:
            return []
        out = []
        for hit in data.get("hits", []):
            videos = hit.get("videos") or {}
            # 优先 medium / large
            file_info = videos.get("medium") or videos.get("large") or videos.get("small")
            if not file_info:
                continue
            url = file_info.get("url")
            if not url:
                continue
            tags = hit.get("tags") or ""
            out.append(MediaCandidate(
                media_id=f"pixabay:video:{hit.get('id')}",
                source="pixabay",
                media_type="video",
                url=url,
                title=tags,
                tags=tags,
                meta={"duration": hit.get("duration")},
            ))
        return out

    def _search_unsplash(self, keyword: str) -> str | None:
        """Unsplash Source 弱兜底（无稳定元数据）。"""
        max_retries = 3
        delay = 1
        for attempt in range(1, max_retries + 1):
            try:
                url = f"https://source.unsplash.com/1920x1080/?{keyword.replace(' ', ',')}"
                resp = requests.get(url, timeout=20, allow_redirects=True)
                if resp.status_code == 200 and len(resp.content) > 5000:
                    return resp.url
                return None
            except Exception as e:
                if attempt < max_retries:
                    time.sleep(delay)
                    delay *= 2
                else:
                    print(f"    [Unsplash 失败] {keyword}: {e}")
        return None

    def _request_json(
        self,
        url: str,
        *,
        headers: dict | None = None,
        params: dict | None = None,
        label: str = "",
        keyword: str = "",
        max_retries: int = 3,
    ) -> dict | None:
        delay = 1
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                resp = requests.get(url, headers=headers, params=params, timeout=20)
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    print(f"    [{label} 重试 {attempt}/{max_retries}] {keyword}: {e}")
                    time.sleep(delay)
                    delay *= 2
        print(f"    [{label} 失败] {keyword}: {last_error}")
        return None

    # ------------------------------------------------------------------
    # 打分 / 选择 / 下载
    # ------------------------------------------------------------------

    def _tokenize(self, text: str) -> list[str]:
        text = (text or "").lower()
        parts = re.split(r"[^a-z0-9\u4e00-\u9fff]+", text)
        return [p for p in parts if len(p) >= 2]

    def _score_candidate(
        self, cand: MediaCandidate, keyword: str, narration: str = ""
    ) -> float:
        """基于标题/标签与 keyword（及口播文本）的词重叠打分。"""
        hay = f"{cand.title} {cand.tags}".lower()
        keys = self._tokenize(keyword)
        if not keys:
            return 0.0
        score = 0.0
        for tok in keys:
            if tok in hay:
                score += 3.0
            # 部分匹配
            elif any(tok in w or w in tok for w in self._tokenize(hay)):
                score += 1.0
        # 完整短语
        kw = keyword.lower().strip()
        if kw and kw in hay:
            score += 5.0
        # 口播里的英文片段轻微加分
        for tok in self._tokenize(narration):
            if tok.isascii() and tok in hay:
                score += 0.5
        # 视频略加一点多样性权重（最终是否采用仍受 max_videos 约束）
        if cand.media_type == "video":
            score += 0.2
        return score

    def _pick_best(
        self, candidates: list[MediaCandidate], exclude_ids: set[str]
    ) -> MediaCandidate | None:
        for c in sorted(candidates, key=lambda x: x.score, reverse=True):
            if c.media_id not in exclude_ids:
                return c
        return None

    def _download_candidate(self, cand: MediaCandidate) -> str | None:
        ext = ".mp4" if cand.media_type == "video" else ".jpg"
        cache_name = hashlib.md5(cand.media_id.encode()).hexdigest() + ext
        cache_path = os.path.join(self.cache_dir, cache_name)
        if os.path.exists(cache_path) and os.path.getsize(cache_path) > 1000:
            return cache_path

        try:
            resp = requests.get(cand.url, timeout=60, stream=True)
            resp.raise_for_status()
            raw_path = cache_path + ".raw"
            with open(raw_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 16):
                    if chunk:
                        f.write(chunk)

            if cand.media_type == "video":
                os.replace(raw_path, cache_path)
            else:
                self._resize_to_fullscreen(raw_path, cache_path)
                os.remove(raw_path)
            return cache_path
        except Exception as e:
            print(f"    [下载失败] {cand.media_id}: {e}")
            return None

    def _resize_to_fullscreen(self, src_path: str, dest_path: str):
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
        dest = os.path.join(output_dir, f"bg_{idx:03d}.jpg")

        if not os.path.isdir(FALLBACK_BG_DIR):
            img = Image.new("RGB", (self.width, self.height), (25, 25, 35))
            img.save(dest, "JPEG", quality=92)
            return dest

        all_images = sorted([
            os.path.join(FALLBACK_BG_DIR, f)
            for f in os.listdir(FALLBACK_BG_DIR)
            if f.lower().endswith((".jpg", ".jpeg", ".png"))
        ])
        if not all_images:
            img = Image.new("RGB", (self.width, self.height), (25, 25, 35))
            img.save(dest, "JPEG", quality=92)
            return dest

        available = [p for p in all_images if p not in self._used_fallback_images]
        if not available:
            self._used_fallback_images.clear()
            available = all_images

        chosen = random.choice(available)
        self._used_fallback_images.add(chosen)
        img = Image.open(chosen).convert("RGB")
        img = img.resize((self.width, self.height), Image.LANCZOS)
        img.save(dest, "JPEG", quality=92)
        return dest
