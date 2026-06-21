"""
幻灯片自动生成模块

输入讲解文本 → DeepSeek 提取结构化大纲 → Pillow 合成幻灯片图片
支持逐帧元素入场动画（标题滑入、要点逐行淡入、配图缩放等）
支持随机渐变背景生成 + 半透明蒙版保障文字可读性
"""

import hashlib
import json
import math
import os
import random
import textwrap

import numpy as np
import requests
import yaml
from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont


# ------------------------------------------------------------------
# 预设渐变色板：每次生成锁定一组，不同页面类型在此基础上微调
# 每组包含 (起始色, 结束色) 的 RGB 元组
# ------------------------------------------------------------------
COLOR_PALETTES = [
    {
        "name": "深海蓝紫",
        "colors": [(15, 12, 41), (48, 43, 99), (36, 36, 62)],
    },
    {
        "name": "暗夜森林",
        "colors": [(15, 32, 39), (32, 58, 67), (44, 83, 100)],
    },
    {
        "name": "科技蓝黑",
        "colors": [(0, 4, 40), (0, 78, 146), (0, 26, 51)],
    },
    {
        "name": "星空靛紫",
        "colors": [(23, 11, 46), (58, 28, 113), (26, 5, 51)],
    },
    {
        "name": "深邃墨绿",
        "colors": [(5, 25, 20), (15, 60, 50), (8, 35, 30)],
    },
    {
        "name": "暗红酒韵",
        "colors": [(30, 5, 10), (80, 15, 25), (45, 8, 15)],
    },
    {
        "name": "午夜靛蓝",
        "colors": [(10, 15, 45), (25, 40, 90), (15, 20, 55)],
    },
    {
        "name": "钴蓝深空",
        "colors": [(5, 10, 35), (20, 50, 120), (10, 25, 60)],
    },
    {
        "name": "暗岩灰紫",
        "colors": [(25, 20, 35), (50, 40, 70), (35, 28, 48)],
    },
    {
        "name": "熔岩暗橙",
        "colors": [(25, 10, 0), (75, 30, 5), (40, 15, 2)],
    },
]


STYLE_VARIANTS = [
    {
        "persona": "你是一个贴吧味十足的毒舌博主，说话阴阳怪气又一针见血，喜欢用反讽和夸张类比让复杂事情秒懂，带着老哥特有的【乐子人】气质。",
        "openings": [
            "每天两分钟，帮你了解一天大事。",
            "又是被新闻信息洪流挤爆的一天，两分钟给你讲明白。",
            "今天这消息，我看完直接坐不住了。",
        ],
        "endings": [
            "每天两分钟，关注我，我们下期见！",
            "关注我，培养一个每天了解时事热点的微习惯。",
            "每天花两分钟跟我看看世界在发生什么，咱们下期不见不散。",
        ],
        "hooks": [
            "这什么概念？", "离谱的是...", "但更炸裂的来了...",
            "你敢信？", "关键来了...", "最骚的操作是...",
        ],
        "emotions": "卧槽、这也行？、赶紧告诉朋友",
        "tone": "像一个贴吧老哥在跟你吐槽今天的离谱新闻，尖锐毒舌但有料有趣",
    },
    {
        "persona": "你是一个知乎大V风格的知性主播，表达从容有深度，擅长把专业内容降维解释，让人听完有【涨知识了】的获得感，偶尔来一句冷幽默。",
        "openings": [
            "两分钟了解一天大事，今天信息量有点大，我帮你捋一捋。",
            "今天有几条值得关注的消息，两分钟帮你抓住重点。",
            "每天两分钟，不错过重要消息。先说一条让我眼前一亮的。",
        ],
        "endings": [
            "每天两分钟，帮助你了解一天大事，我们下期再见。",
            "关注我，养成每天快速了解时事的习惯，下期见。",
            "今天先到这，后续的事看后续，我们下期见。",
        ],
        "hooks": [
            "值得注意的是...", "换句话说...", "这意味着什么呢？",
            "重点来了...", "有意思的是...", "但别急，还有后续...",
        ],
        "emotions": "涨知识了、原来如此、这个角度新颖",
        "tone": "像一个靠谱的知乎答主在做深度解读，专业但不枯燥，让人有获得感",
    },
    {
        "persona": "你是一个B站味的邻家UP主，说话轻松随意接地气，喜欢用弹幕梗和日常比喻，让人听着特别舒服，有种【和朋友开语音】的感觉。",
        "openings": [
            "来了来了，每天两分钟帮你了解今天发生了啥。",
            "两分钟了解一天大事，今天这几条新闻挺有意思的。",
            "兄弟们来了，每天两分钟跟你唠唠今天的大事。",
        ],
        "endings": [
            "每天两分钟了解热点大事，觉得有用就关注一下，下期见！",
            "关注我，每天花两分钟就能跟上世界的节奏，咱们下期见。",
            "今天就聊到这，点个关注不迷路，我们下期接着唠。",
        ],
        "hooks": [
            "你猜怎么着？", "好家伙...", "等等，还没完...",
            "说出来你可能不信...", "但后面的事更绝...", "注意这个细节...",
        ],
        "emotions": "哈哈哈太真实了、长见识了、转给朋友看看",
        "tone": "像一个B站UP主在跟粉丝闲聊趣事，没有距离感，弹幕味拉满",
    },
    {
        "persona": "你是一个微博热搜味的资讯达人，擅长用脱口秀节奏包装信息，善于制造【热搜体】的戏剧感，让人又笑又涨知识。",
        "openings": [
            "各位，每天两分钟帮你看完今天大事件。",
            "两分钟了解一天大事。",
            "今日资讯，两分钟讲给你听。",
        ],
        "endings": [
            "每天两分钟了解大事，关注我培养看新闻的好习惯，下期见！",
            "关注我，每天两分钟帮你把握时事脉搏，咱们下期见。",
            "干货都给了，关注我不错过下期精彩，我们下期见。",
        ],
        "hooks": [
            "笑死，你听这个...", "精彩的来了...", "但反转来了...",
            "你以为这就完了？", "请注意前方高能...", "绷不住了...",
        ],
        "emotions": "笑死了、这也太离谱了、必须分享给朋友",
        "tone": "像一个脱口秀演员在讲今天的热搜，微博评论区味拉满，笑点密集但信息量足",
    },
]

OUTLINE_PROMPT_TEMPLATE = """你是一个短视频口播文案专家。{persona}

请将以下素材分页，提取每页标题、要点，并撰写高吸引力的口播旁白。

核心原则：**少即是多**。不要复述所有信息，只挑最炸裂、最反直觉、最能吸引人的点来讲。

要求：
1. 每页对应一个主题/知识点
2. 每页包含：标题（简短，不超过15字）、要点（3-5条，每条15-30字，内容详实具体）
3. 要点需要包含具体的关键信息，不要过度简化
4. 第一页为封面页（type=cover），narration 以"{opening}"开头，然后用一句话抛出今天最劲爆的看点，制造悬念
5. 最后一页为结尾页（type=ending），narration 为结束语，如"{ending}"
6. 中间如果有大的主题切换，插入章节分隔页（type=section，只有章节标题）
7. 正文内容页 type=content
8. **narration 字段是口播旁白脚本，是整个视频的灵魂**，必须满足：
   - **纯文本**：不要出现emoji、特殊符号、HTML标签等非文本内容
   - **精简**：每页只讲 1-2 个最核心的爆点，不要面面俱到
   - **钩子感**：每页开头要有钩子，用反问、惊叹、反转来抓住注意力。从以下话术中灵活选用或自由发挥：{hooks}
   - **口语化**：{tone}
   - **制造情绪**：让听众觉得"{emotions}"
   - **留悬念**：部分页面结尾要埋钩子引导继续听
   - 必须保留关键数据，不能编造
   - **每页 narration 严格控制在 40-80 字**，宁短勿长

请严格以如下JSON格式输出，不要输出其他内容：
{{
  "slides": [
    {{"type": "cover", "title": "主标题", "subtitle": "副标题", "narration": "{opening}[一句话钩子]"}},
    {{"type": "content", "title": "页面标题", "points": ["要点1", "要点2", "要点3"], "image_keyword": "英文图片搜索关键词", "narration": "[40-80字精简口播]"}},
    {{"type": "section", "title": "章节标题"}},
    {{"type": "ending", "title": "谢谢观看", "narration": "{ending}"}}
  ]
}}

注意：image_keyword 是用于搜索配图的英文关键词（1-3个词），要具体且有视觉表现力，例如"artificial intelligence brain"、"stock market chart"、"robot arm factory"。

素材原文：
"""


class SlideGenerator:
    def __init__(self, config_path="config.yaml"):
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

        ds_cfg = self.config["deepseek"]
        self.client = OpenAI(api_key=ds_cfg["api_key"], base_url=ds_cfg["base_url"])
        self.model = ds_cfg.get("model", "deepseek-chat")

        sl_cfg = self.config["slides"]
        self.bg_dir = sl_cfg.get("bg_dir", "asset/slide_bg")
        self.output_dir = sl_cfg["output_dir"]
        self.width = sl_cfg.get("width", 1920)
        self.height = sl_cfg.get("height", 1080)
        self.font_path = sl_cfg.get("font_path", "C:/Windows/Fonts/msyh.ttc")
        self.font_index = sl_cfg.get("font_index", 0)
        self.bg_mode = sl_cfg.get("bg_mode", "gradient")  # "gradient" | "static"

        pexels_cfg = self.config.get("pexels", {})
        self.pexels_api_key = pexels_cfg.get("api_key", "")
        self.image_cache_dir = pexels_cfg.get("cache_dir", "asset/images")
        os.makedirs(self.image_cache_dir, exist_ok=True)

        anim_cfg = self.config.get("animation", {})
        self.element_animation = anim_cfg.get("element_animation", False)
        self.intro_duration = anim_cfg.get("intro_duration", 0.8)
        self.title_effect = anim_cfg.get("title_effect", "fade_down")
        self.point_effect = anim_cfg.get("point_effect", "fade_left")
        self.image_effect = anim_cfg.get("image_effect", "scale_up")
        self.render_fps = self.config.get("render", {}).get("fps", 30)

        self._load_fonts()
        self._init_palette()

    def _load_fonts(self):
        """预加载各级字体。"""
        idx = self.font_index
        self.font_cover_title = ImageFont.truetype(self.font_path, 96, index=idx)
        self.font_cover_subtitle = ImageFont.truetype(self.font_path, 44, index=idx)
        self.font_title = ImageFont.truetype(self.font_path, 84, index=idx)
        self.font_point = ImageFont.truetype(self.font_path, 51, index=idx)
        self.font_page = ImageFont.truetype(self.font_path, 33, index=idx)

    def _init_palette(self):
        """每次运行时初始化背景策略：优先从 Pexels 搜索，降级使用渐变生成。"""
        self._bg_image_path = None
        self._current_palette = random.choice(COLOR_PALETTES)
        self._gradient_angle = random.uniform(0, 360)

        if self.bg_mode == "gradient":
            bg_path = self._search_bg_from_pexels()
            if bg_path:
                self._bg_image_path = bg_path
                print(f"[背景] 使用 Pexels 图片: {bg_path}")
            else:
                print(f"[背景] Pexels 搜索失败，降级使用渐变色板: {self._current_palette['name']}")
        else:
            print("[背景] 使用固定背景文件")

    # ------------------------------------------------------------------
    # Pexels 背景图片搜索
    # ------------------------------------------------------------------

    _BG_SEARCH_KEYWORDS = [
      # 科技风格
      "dark technology background",
      "dark cyberpunk city",
      "dark futuristic interface",
      "dark digital network",
      "dark ai technology",
  
      # 自然风格
      "dark forest moody",
      "dark mountain silhouette",
      "dark ocean night",
      "dark nature landscape night",
      "dark cave",
  
      # 宇宙风格
      "dark space nebula",
      "dark galaxy",
      "dark stars background",
  
      # 光影风格
      "dark bokeh background",
      "dark cinematic lighting",
      "dark spotlight texture",
    ]

    def _search_bg_from_pexels(self) -> str | None:
        """从 Pexels 搜索深色背景图片，下载缓存后返回路径。"""
        if not self.pexels_api_key:
            return None

        keyword = random.choice(self._BG_SEARCH_KEYWORDS)
        cache_name = "bg_" + hashlib.md5(keyword.encode()).hexdigest() + ".jpg"
        cache_path = os.path.join(self.image_cache_dir, cache_name)

        if os.path.exists(cache_path):
            return cache_path

        try:
            page = random.randint(1, 5)
            resp = requests.get(
                "https://api.pexels.com/v1/search",
                headers={"Authorization": self.pexels_api_key},
                params={
                    "query": keyword,
                    "per_page": 15,
                    "page": page,
                    "orientation": "landscape",
                    "size": "large",
                },
                timeout=15,
            )
            resp.raise_for_status()
            photos = resp.json().get("photos", [])
            if not photos:
                return None

            photo = random.choice(photos)
            img_url = photo["src"]["large2x"]
            img_resp = requests.get(img_url, timeout=30)
            img_resp.raise_for_status()
            with open(cache_path, "wb") as f:
                f.write(img_resp.content)
            return cache_path
        except Exception as e:
            print(f"  [背景搜索失败] {keyword}: {e}")
            return None

    # ------------------------------------------------------------------
    # 程序化渐变背景生成
    # ------------------------------------------------------------------

    def _generate_gradient_bg(self, slide_type: str) -> Image.Image:
        """根据当前色板和页面类型，程序化生成渐变背景。"""
        palette = self._current_palette
        colors = palette["colors"]

        type_params = {
            "cover": {"brightness": 0.9, "angle_offset": 0},
            "section": {"brightness": 0.85, "angle_offset": 45},
            "content": {"brightness": 0.7, "angle_offset": 15},
            "ending": {"brightness": 0.9, "angle_offset": -15},
        }
        params = type_params.get(slide_type, type_params["content"])
        brightness = params["brightness"]
        angle = self._gradient_angle + params["angle_offset"]

        angle_rad = math.radians(angle)
        cos_a = math.cos(angle_rad)
        sin_a = math.sin(angle_rad)

        w, h = self.width, self.height
        xs = np.arange(w, dtype=np.float32)
        ys = np.arange(h, dtype=np.float32)
        xx, yy = np.meshgrid(xs, ys)

        # 沿渐变方向归一化到 [0, 1]
        projected = (xx / w - 0.5) * cos_a + (yy / h - 0.5) * sin_a
        t = (projected - projected.min()) / (projected.max() - projected.min() + 1e-8)

        # 多色渐变插值
        n_colors = len(colors)
        img_array = np.zeros((h, w, 3), dtype=np.float32)

        for ch in range(3):
            color_values = [c[ch] * brightness for c in colors]
            # 将 t 映射到分段区间
            for seg in range(n_colors - 1):
                seg_start = seg / (n_colors - 1)
                seg_end = (seg + 1) / (n_colors - 1)
                mask = (t >= seg_start) & (t < seg_end)
                if seg == n_colors - 2:
                    mask = (t >= seg_start) & (t <= seg_end)
                local_t = (t - seg_start) / (seg_end - seg_start + 1e-8)
                local_t = np.clip(local_t, 0, 1)
                img_array[:, :, ch] += mask * (
                    color_values[seg] * (1 - local_t) + color_values[seg + 1] * local_t
                )

        img_array = np.clip(img_array, 0, 255).astype(np.uint8)
        img = Image.fromarray(img_array, "RGB").convert("RGBA")
        return img

    def _apply_text_overlay(self, bg: Image.Image, slide_type: str) -> Image.Image:
        """在文字区域叠加半透明深色渐变蒙版，保障文字可读性。"""
        overlay = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        if slide_type in ("cover", "section", "ending"):
            # 居中文字：全屏均匀半透明蒙版
            for y in range(self.height):
                # 中间区域更暗，边缘较轻
                center_dist = abs(y / self.height - 0.5) * 2  # 0 at center, 1 at edges
                alpha = int(100 * (1 - center_dist * 0.6))
                draw.line([(0, y), (self.width, y)], fill=(0, 0, 0, alpha))
        else:
            # 内容页：左侧和顶部文字区域加深
            for y in range(self.height):
                # 上半部分更暗（标题区），下半部分稍轻
                if y < self.height * 0.15:
                    alpha = 120
                elif y < self.height * 0.5:
                    alpha = 100
                else:
                    t = (y - self.height * 0.5) / (self.height * 0.5)
                    alpha = int(100 * (1 - t * 0.5))
                draw.line([(0, y), (self.width, y)], fill=(0, 0, 0, alpha))

        bg = Image.alpha_composite(bg, overlay)
        return bg

    # ------------------------------------------------------------------
    # DeepSeek 大纲提取
    # ------------------------------------------------------------------

    def _build_prompt(self) -> str:
        """随机选取一套风格参数，填充到提示词模板中。"""
        style = random.choice(STYLE_VARIANTS)
        return OUTLINE_PROMPT_TEMPLATE.format(
            persona=style["persona"],
            opening=random.choice(style["openings"]),
            ending=random.choice(style["endings"]),
            hooks="、".join(f'"{h}"' for h in random.sample(style["hooks"], min(4, len(style["hooks"])))),
            tone=style["tone"],
            emotions=style["emotions"],
        )

    def extract_outline(self, text: str) -> list[dict]:
        """调用 DeepSeek API，将讲解文本转换为结构化幻灯片大纲。"""
        prompt = self._build_prompt()
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "你是PPT大纲提取专家，只输出JSON。",
                },
                {"role": "user", "content": prompt + text},
            ],
            temperature=0.65,
            response_format={"type": "json_object"},
        )
        result = json.loads(response.choices[0].message.content)
        return result["slides"]

    # ------------------------------------------------------------------
    # 渲染单页
    # ------------------------------------------------------------------

    def _bg_path(self, slide_type: str) -> str:
        bg_map = {
            "cover": "封面.png",
            "section": "章节分隔.png",
            "content": "内容页.png",
            "ending": "结尾页.png",
        }
        return os.path.join(self.bg_dir, bg_map.get(slide_type, "内容页.png"))

    def _load_bg(self, slide_type: str) -> Image.Image:
        if self.bg_mode == "gradient":
            if self._bg_image_path and os.path.exists(self._bg_image_path):
                bg = Image.open(self._bg_image_path).convert("RGBA")
                bg = bg.resize((self.width, self.height), Image.LANCZOS)
            else:
                bg = self._generate_gradient_bg(slide_type)
            bg = self._apply_text_overlay(bg, slide_type)
            return bg

        bg = Image.open(self._bg_path(slide_type)).convert("RGBA")
        return bg.resize((self.width, self.height), Image.LANCZOS)

    def search_image(self, keyword: str) -> str | None:
        """通过 Pexels API 搜索图片，下载并缓存到本地。返回本地路径或 None。"""
        if not self.pexels_api_key or not keyword:
            return None

        cache_name = hashlib.md5(keyword.encode()).hexdigest() + ".jpg"
        cache_path = os.path.join(self.image_cache_dir, cache_name)
        if os.path.exists(cache_path):
            return cache_path

        try:
            resp = requests.get(
                "https://api.pexels.com/v1/search",
                headers={"Authorization": self.pexels_api_key},
                params={"query": keyword, "per_page": 1, "orientation": "landscape"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            photos = data.get("photos", [])
            if not photos:
                return None

            img_url = photos[0]["src"]["large"]
            img_resp = requests.get(img_url, timeout=30)
            img_resp.raise_for_status()
            with open(cache_path, "wb") as f:
                f.write(img_resp.content)
            return cache_path
        except Exception as e:
            print(f"  [图片搜索失败] {keyword}: {e}")
            return None

    def download_images(self, slides_data: list[dict]) -> dict[int, str]:
        """批量为 slides 下载配图，返回 {slide_index: local_path}。"""
        result = {}
        for i, sd in enumerate(slides_data):
            keyword = sd.get("image_keyword", "")
            if sd.get("type") == "content" and keyword:
                print(f"  [{i + 1}/{len(slides_data)}] 搜索配图: {keyword}")
                path = self.search_image(keyword)
                if path:
                    result[i] = path
                    print(f"    → {path}")
                else:
                    print(f"    → 未找到")
        return result

    @staticmethod
    def _center_x(draw: ImageDraw.Draw, text: str, font: ImageFont.FreeTypeFont, canvas_width: int) -> int:
        bbox = draw.textbbox((0, 0), text, font=font)
        return (canvas_width - (bbox[2] - bbox[0])) // 2

    def _wrap_text(self, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
        """按像素宽度自动换行。"""
        lines = []
        for raw_line in text.split("\n"):
            current = ""
            for ch in raw_line:
                test = current + ch
                bbox = font.getbbox(test)
                if bbox[2] - bbox[0] > max_width:
                    lines.append(current)
                    current = ch
                else:
                    current = test
            if current:
                lines.append(current)
        return lines

    def render_slide(self, slide_data: dict, index: int, total: int) -> str:
        slide_type = slide_data["type"]
        bg = self._load_bg(slide_type)
        draw = ImageDraw.Draw(bg)

        render_fn = {
            "cover": self._render_cover,
            "section": self._render_section,
            "content": self._render_content,
            "ending": self._render_ending,
        }.get(slide_type, self._render_content)

        render_fn(draw, slide_data, bg)

        if slide_type in ("content", "section"):
            page_text = f"{index} / {total}"
            draw.text(
                (self.width - 130, self.height - 55),
                page_text,
                fill="#888888",
                font=self.font_page,
            )

        os.makedirs(self.output_dir, exist_ok=True)
        output_path = os.path.join(self.output_dir, f"slide_{index:03d}.png")
        bg.save(output_path, "PNG")
        return output_path

    # ------------------------------------------------------------------
    # 逐帧元素入场动画
    # ------------------------------------------------------------------

    @staticmethod
    def _ease_out_cubic(t: float) -> float:
        """缓出曲线，让动画收尾更自然。"""
        return 1.0 - (1.0 - min(max(t, 0.0), 1.0)) ** 3

    def render_slide_frames(
        self, slide_data: dict, index: int, total: int, output_dir: str,
    ) -> list[str]:
        """为单页生成入场动画帧序列 + 最终静态帧。

        Returns:
            帧文件路径列表（按时间顺序）
        """
        slide_type = slide_data["type"]
        intro_frames = max(1, int(self.intro_duration * self.render_fps))

        os.makedirs(output_dir, exist_ok=True)
        frame_paths: list[str] = []

        for fi in range(intro_frames):
            progress = self._ease_out_cubic(fi / max(intro_frames - 1, 1))
            img = self._render_animated_frame(slide_data, index, total, progress)
            fp = os.path.join(output_dir, f"frame_{fi:04d}.png")
            img.save(fp, "PNG")
            frame_paths.append(fp)

        return frame_paths

    def _render_animated_frame(
        self, slide_data: dict, index: int, total: int, progress: float,
    ) -> Image.Image:
        """渲染动画中间帧。progress: 0.0(开始) → 1.0(完成)。"""
        slide_type = slide_data["type"]
        bg = self._load_bg(slide_type)
        draw = ImageDraw.Draw(bg)

        render_fn = {
            "cover": self._render_cover_animated,
            "section": self._render_section_animated,
            "content": self._render_content_animated,
            "ending": self._render_ending_animated,
        }.get(slide_type, self._render_content_animated)

        render_fn(draw, slide_data, bg, progress)

        if slide_type in ("content", "section"):
            page_text = f"{index} / {total}"
            alpha = min(max(progress * 2, 0.0), 1.0)
            color = f"#{int(0x88 * alpha):02x}{int(0x88 * alpha):02x}{int(0x88 * alpha):02x}"
            draw.text(
                (self.width - 130, self.height - 55),
                page_text,
                fill=color,
                font=self.font_page,
            )

        return bg

    # --- 封面动画 ---

    def _render_cover_animated(
        self, draw: ImageDraw.Draw, data: dict, bg: Image.Image, progress: float,
    ):
        title = data.get("title", "")
        subtitle = data.get("subtitle", "")

        title_p = min(progress * 1.5, 1.0)
        sub_p = max((progress - 0.3) / 0.7, 0.0)

        y_base = int(self.height * 0.48)
        x = self._center_x(draw, title, self.font_cover_title, self.width)

        if self.title_effect == "fade_down":
            offset_y = int((1.0 - title_p) * -60)
            self._draw_text_alpha(draw, (x, y_base + offset_y), title,
                                  self.font_cover_title, "white", title_p, bg)
        else:
            self._draw_text_alpha(draw, (x, y_base), title,
                                  self.font_cover_title, "white", title_p, bg)

        if subtitle:
            y_sub = y_base + 120
            x_sub = self._center_x(draw, subtitle, self.font_cover_subtitle, self.width)
            self._draw_text_alpha(draw, (x_sub, y_sub), subtitle,
                                  self.font_cover_subtitle, "#EEEEEE", sub_p, bg)

    # --- 章节分隔动画 ---

    def _render_section_animated(
        self, draw: ImageDraw.Draw, data: dict, bg: Image.Image, progress: float,
    ):
        title = data.get("title", "")
        y = int(self.height * 0.38)
        x = self._center_x(draw, title, self.font_cover_title, self.width)

        if self.title_effect == "fade_down":
            offset_y = int((1.0 - progress) * -50)
            self._draw_text_alpha(draw, (x, y + offset_y), title,
                                  self.font_cover_title, "white", progress, bg)
        else:
            self._draw_text_alpha(draw, (x, y), title,
                                  self.font_cover_title, "white", progress, bg)

    # --- 内容页动画 ---

    def _render_content_animated(
        self, draw: ImageDraw.Draw, data: dict, bg: Image.Image, progress: float,
    ):
        title = data.get("title", "")
        points = data.get("points", [])
        image_path = data.get("image_path")

        x_margin = 110
        safe_top = int(self.height * 0.06)
        safe_bottom = int(self.height * 0.94)

        has_image = image_path and os.path.exists(image_path)
        if has_image:
            text_right_edge = int(self.width * 0.55)
            max_text_width = text_right_edge - x_margin - 20
        else:
            max_text_width = self.width - x_margin * 2 - 40

        # 标题（前 30% 动画时间完成）
        title_p = min(progress / 0.3, 1.0)
        title_max_w = self.width - x_margin * 2 - 40
        y_title = safe_top
        title_lines = self._wrap_text(title, self.font_title, title_max_w)
        for line in title_lines:
            if self.title_effect == "fade_down":
                offset_y = int((1.0 - title_p) * -40)
                self._draw_text_alpha(draw, (x_margin, y_title + offset_y), line,
                                      self.font_title, "white", title_p, bg)
            else:
                self._draw_text_alpha(draw, (x_margin, y_title), line,
                                      self.font_title, "white", title_p, bg)
            y_title += 105

        # 要点（30% ~ 90% 动画时间，逐行出现）
        y_start = y_title + 67
        available_height = safe_bottom - y_start
        total_lines = sum(
            len(self._wrap_text(p, self.font_point, max_text_width - 60))
            for p in points
        )
        total_units = total_lines + len(points) * 0.35
        line_height = min(85, int(available_height / max(total_units, 1)))

        y = y_start
        point_start = 0.3
        point_end = 0.9
        point_range = point_end - point_start

        for pi, point in enumerate(points):
            if y > safe_bottom:
                break
            item_progress_start = point_start + point_range * pi / max(len(points), 1)
            item_progress = max((progress - item_progress_start) / (point_range / max(len(points), 1)), 0.0)
            item_progress = min(item_progress, 1.0)

            wrapped = self._wrap_text(point, self.font_point, max_text_width - 60)
            for j, wline in enumerate(wrapped):
                if y > safe_bottom:
                    break
                prefix = "●   " if j == 0 else "      "
                text = prefix + wline

                if self.point_effect == "fade_left":
                    offset_x = int((1.0 - item_progress) * -50)
                    self._draw_text_alpha(draw, (x_margin + 30 + offset_x, y), text,
                                          self.font_point, "#FFFFFF", item_progress, bg)
                else:
                    self._draw_text_alpha(draw, (x_margin + 30, y), text,
                                          self.font_point, "#FFFFFF", item_progress, bg)
                y += line_height
            y += int(line_height * 0.35)

        # 配图（50% ~ 100%）
        if has_image:
            img_p = max((progress - 0.5) / 0.5, 0.0)
            img_p = min(img_p, 1.0)
            self._draw_image_animated(bg, image_path, x_margin, safe_top, safe_bottom, img_p)

    def _draw_image_animated(
        self, bg: Image.Image, image_path: str,
        x_margin: int, safe_top: int, safe_bottom: int, progress: float,
    ):
        """绘制带动画的配图。"""
        img_area_left = int(self.width * 0.58)
        img_area_right = self.width - x_margin
        img_area_top = safe_top + 120
        img_area_bottom = safe_bottom - 20
        img_area_w = img_area_right - img_area_left
        img_area_h = img_area_bottom - img_area_top

        try:
            photo = Image.open(image_path).convert("RGBA")
            pw, ph = photo.size
            scale = min(img_area_w / pw, img_area_h / ph)

            if self.image_effect == "scale_up":
                anim_scale = 0.7 + 0.3 * progress
                scale *= anim_scale

            new_w = int(pw * scale)
            new_h = int(ph * scale)
            if new_w <= 0 or new_h <= 0:
                return
            photo = photo.resize((new_w, new_h), Image.LANCZOS)

            img_x = img_area_left + (img_area_w - new_w) // 2
            img_y = img_area_top + (img_area_h - new_h) // 2

            corner_radius = 16
            mask = Image.new("L", (new_w, new_h), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.rounded_rectangle(
                [(0, 0), (new_w, new_h)], radius=corner_radius, fill=255
            )

            from PIL import ImageChops
            alpha_val = int(255 * progress)
            alpha_mask = Image.new("L", (new_w, new_h), alpha_val)
            final_mask = ImageChops.darker(mask, alpha_mask)

            bg.paste(photo, (img_x, img_y), final_mask)
        except Exception as e:
            print(f"  [配图动画渲染失败] {e}")

    # --- 结尾页动画 ---

    def _render_ending_animated(
        self, draw: ImageDraw.Draw, data: dict, bg: Image.Image, progress: float,
    ):
        title = data.get("title", "谢谢观看")
        y = int(self.height * 0.48)
        x = self._center_x(draw, title, self.font_cover_title, self.width)
        self._draw_text_alpha(draw, (x, y), title,
                              self.font_cover_title, "white", progress, bg)

    # --- 工具：带透明度的文字绘制 ---

    def _draw_text_alpha(
        self, draw: ImageDraw.Draw, xy: tuple, text: str,
        font: ImageFont.FreeTypeFont, fill: str, alpha: float,
        bg: Image.Image,
    ):
        """在 RGBA 背景上绘制带透明度的文字（含阴影）。"""
        if alpha <= 0.01:
            return

        bbox = font.getbbox(text)
        shadow_dx, shadow_dy = 3, 2
        pad = 10

        # 层尺寸：文字 ink 区域 + 阴影偏移 + 四周留白
        layer_w = bbox[2] - bbox[0] + shadow_dx + pad * 2
        layer_h = bbox[3] - bbox[1] + shadow_dy + pad * 2
        layer = Image.new("RGBA", (layer_w, layer_h), (0, 0, 0, 0))
        layer_draw = ImageDraw.Draw(layer)

        # 让文字 ink 起始于 (pad, pad)，需要将绘制原点偏移 bbox 的 left/top
        ox = pad - bbox[0]
        oy = pad - bbox[1]

        shadow_a = int(0x55 * alpha)
        layer_draw.text((ox + shadow_dx, oy + shadow_dy), text,
                        fill=(0, 0, 0, shadow_a), font=font)

        if fill.startswith("#"):
            fill_hex = fill.lstrip("#")
            if len(fill_hex) == 6:
                r, g, b = int(fill_hex[0:2], 16), int(fill_hex[2:4], 16), int(fill_hex[4:6], 16)
            else:
                r, g, b = 255, 255, 255
        elif fill.lower() == "white":
            r, g, b = 255, 255, 255
        else:
            r, g, b = 255, 255, 255

        text_a = int(255 * alpha)
        layer_draw.text((ox, oy), text, fill=(r, g, b, text_a), font=font)

        # 粘贴到背景时偏移，使最终文字位置与 draw.text(xy) 一致
        bg.paste(layer, (xy[0] - pad + bbox[0], xy[1] - pad + bbox[1]), layer)

    # ------------------------------------------------------------------
    # 各类型页面渲染（静态，无动画）
    # ------------------------------------------------------------------

    def _draw_text_shadow(
        self, draw: ImageDraw.Draw, xy: tuple, text: str,
        font: ImageFont.FreeTypeFont, fill: str, shadow_color: str = "#00000088",
        offset: int = 3,
    ):
        """绘制带阴影的文字。"""
        sx, sy = xy[0] + offset, xy[1] + offset
        draw.text((sx, sy), text, fill=shadow_color, font=font)
        draw.text(xy, text, fill=fill, font=font)

    def _render_cover(self, draw: ImageDraw.Draw, data: dict, bg: Image.Image):
        title = data.get("title", "")
        subtitle = data.get("subtitle", "")

        y = int(self.height * 0.48)
        x = self._center_x(draw, title, self.font_cover_title, self.width)
        self._draw_text_shadow(draw, (x, y), title, self.font_cover_title, "white")

        if subtitle:
            y += 120
            x = self._center_x(draw, subtitle, self.font_cover_subtitle, self.width)
            self._draw_text_shadow(draw, (x, y), subtitle, self.font_cover_subtitle, "#EEEEEE")

    def _render_section(self, draw: ImageDraw.Draw, data: dict, bg: Image.Image):
        title = data.get("title", "")
        y = int(self.height * 0.38)
        x = self._center_x(draw, title, self.font_cover_title, self.width)
        self._draw_text_shadow(draw, (x, y), title, self.font_cover_title, "white")

    def _render_content(self, draw: ImageDraw.Draw, data: dict, bg: Image.Image):
        title = data.get("title", "")
        points = data.get("points", [])
        image_path = data.get("image_path")

        x_margin = 110
        safe_top = int(self.height * 0.06)
        safe_bottom = int(self.height * 0.94)

        # 有配图时：左侧文字占 55%，右侧配图占 40%（留 5% 间距）
        if image_path and os.path.exists(image_path):
            text_right_edge = int(self.width * 0.55)
            max_text_width = text_right_edge - x_margin - 20

            img_area_left = int(self.width * 0.58)
            img_area_right = self.width - x_margin
            img_area_top = safe_top + 120
            img_area_bottom = safe_bottom - 20
            img_area_w = img_area_right - img_area_left
            img_area_h = img_area_bottom - img_area_top

            try:
                photo = Image.open(image_path).convert("RGBA")
                pw, ph = photo.size
                scale = min(img_area_w / pw, img_area_h / ph)
                new_w = int(pw * scale)
                new_h = int(ph * scale)
                photo = photo.resize((new_w, new_h), Image.LANCZOS)

                img_x = img_area_left + (img_area_w - new_w) // 2
                img_y = img_area_top + (img_area_h - new_h) // 2

                corner_radius = 16
                mask = Image.new("L", (new_w, new_h), 0)
                mask_draw = ImageDraw.Draw(mask)
                mask_draw.rounded_rectangle(
                    [(0, 0), (new_w, new_h)], radius=corner_radius, fill=255
                )

                bg.paste(photo, (img_x, img_y), mask)
                draw = ImageDraw.Draw(bg)
            except Exception as e:
                print(f"  [配图渲染失败] {e}")
                max_text_width = self.width - x_margin * 2 - 40
        else:
            max_text_width = self.width - x_margin * 2 - 40

        # 标题（全宽）
        title_max_w = self.width - x_margin * 2 - 40
        y_title = safe_top
        title_lines = self._wrap_text(title, self.font_title, title_max_w)
        for line in title_lines:
            self._draw_text_shadow(
                draw, (x_margin, y_title), line, self.font_title,
                fill="white", shadow_color="#00000066", offset=2,
            )
            y_title += 105

        # 要点列表
        y_start = y_title + 67
        available_height = safe_bottom - y_start
        total_lines = sum(
            len(self._wrap_text(p, self.font_point, max_text_width - 60))
            for p in points
        )
        total_units = total_lines + len(points) * 0.35
        line_height = min(85, int(available_height / max(total_units, 1)))

        y = y_start
        for point in points:
            if y > safe_bottom:
                break
            wrapped = self._wrap_text(point, self.font_point, max_text_width - 60)
            for j, wline in enumerate(wrapped):
                if y > safe_bottom:
                    break
                prefix = "●   " if j == 0 else "      "
                self._draw_text_shadow(
                    draw, (x_margin + 30, y), prefix + wline, self.font_point,
                    fill="#FFFFFF", shadow_color="#00000055", offset=2,
                )
                y += line_height
            y += int(line_height * 0.35)

    def _render_ending(self, draw: ImageDraw.Draw, data: dict, bg: Image.Image):
        title = data.get("title", "谢谢观看")
        y = int(self.height * 0.48)
        x = self._center_x(draw, title, self.font_cover_title, self.width)
        self._draw_text_shadow(draw, (x, y), title, self.font_cover_title, "white")

    # ------------------------------------------------------------------
    # 完整流程
    # ------------------------------------------------------------------

    def generate(self, text: str, output_dir: str | None = None) -> tuple[list[dict], list[str], list[list[str]] | None]:
        """完整流程：文本 → 大纲 → 幻灯片图片序列。

        Returns:
            (slides_data, slide_paths, frame_dirs) —
              大纲数据列表、生成的静态图片路径列表、
              入场动画帧序列目录列表（未启用动画时为 None）
        """
        if output_dir:
            self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

        for f in os.listdir(self.output_dir):
            if f.startswith("slide_") and f.endswith(".png"):
                os.remove(os.path.join(self.output_dir, f))

        print("正在调用 DeepSeek 提取大纲...")
        slides_data = self.extract_outline(text)
        print(f"大纲提取完成，共 {len(slides_data)} 页")

        print("搜索配图...")
        image_map = self.download_images(slides_data)
        for idx, path in image_map.items():
            slides_data[idx]["image_path"] = path
        print(f"配图下载完成，共 {len(image_map)} 张")

        print("开始渲染幻灯片...")
        slide_paths = []
        all_frame_dirs: list[list[str]] | None = [] if self.element_animation else None

        for i, sd in enumerate(slides_data):
            path = self.render_slide(sd, i + 1, len(slides_data))
            slide_paths.append(path)
            print(f"  [{i + 1}/{len(slides_data)}] {sd['type']}: {path}")

            if self.element_animation:
                frames_dir = os.path.join(self.output_dir, f"anim_{i:03d}")
                frame_paths = self.render_slide_frames(sd, i + 1, len(slides_data), frames_dir)
                all_frame_dirs.append(frame_paths)
                print(f"    → 动画帧: {len(frame_paths)} 帧")

        outline_path = os.path.join(self.output_dir, "outline.json")
        with open(outline_path, "w", encoding="utf-8") as f:
            json.dump({"slides": slides_data}, f, ensure_ascii=False, indent=2)
        print(f"大纲已保存: {outline_path}")

        return slides_data, slide_paths, all_frame_dirs


# ------------------------------------------------------------------
# 独立运行测试
# ------------------------------------------------------------------

if __name__ == "__main__":
    sample_text = (
        "人工智能是计算机科学的一个分支，它试图理解智能的本质，"
        "并生产出一种新的能以人类智能相似的方式做出反应的智能机器。\n\n"
        "人工智能的发展历程可以追溯到1956年的达特茅斯会议。"
        "此后经历了多次起伏，包括两次AI寒冬。\n\n"
        "机器学习是人工智能的核心技术之一。"
        "它使计算机能够从数据中学习，而不需要被明确编程。"
        "深度学习是机器学习的一个子集，使用多层神经网络来处理复杂的模式。\n\n"
        "当前AI技术在多个领域得到广泛应用，包括：自然语言处理、"
        "计算机视觉、语音识别、推荐系统、自动驾驶等。"
    )

    generator = SlideGenerator()
    slides_data, slide_paths, all_frame_dirs = generator.generate(sample_text)
    print(f"\n完成！共生成 {len(slide_paths)} 张幻灯片")
    if all_frame_dirs:
        total_frames = sum(len(fd) for fd in all_frame_dirs)
        print(f"动画帧总数: {total_frames}")
