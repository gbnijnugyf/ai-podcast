"""
幻灯片自动生成模块

输入讲解文本 → DeepSeek 提取结构化大纲 → Pillow 合成幻灯片图片
"""

import hashlib
import json
import os
import textwrap

import requests
import yaml
from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont


OUTLINE_PROMPT = """你是一个短视频口播文案专家。请将以下素材分页，提取每页标题、要点，并撰写高吸引力的口播旁白。

核心原则：**少即是多**。不要复述所有信息，只挑最炸裂、最反直觉、最能吸引人的点来讲。

要求：
1. 每页对应一个主题/知识点
2. 每页包含：标题（简短，不超过15字）、要点（3-5条，每条15-30字，内容详实具体）
3. 要点需要包含具体的关键信息，不要过度简化
4. 第一页为封面页（type=cover），narration 以"两分钟看完一天热点。"开头，然后用一句话抛出今天最劲爆的看点，制造悬念
5. 最后一页为结尾页（type=ending），narration 为结束语，如"好了，今天就聊到这里，我们下期见！"
6. 中间如果有大的主题切换，插入章节分隔页（type=section，只有章节标题）
7. 正文内容页 type=content
8. **narration 字段是口播旁白脚本，是整个视频的灵魂**，必须满足：
   - **纯文本**：不要出现emoji、特殊符号、HTML标签等非文本内容
   - **精简**：每页只讲 1-2 个最核心的爆点，不要面面俱到
   - **钩子感**：每页开头要有钩子，用反问、惊叹、反转来抓住注意力。如"这什么概念？""离谱的是...""但更炸裂的来了..."
   - **口语化**：像一个顶级财经 UP 主在跟朋友聊八卦，而不是播新闻
   - **制造情绪**：让听众觉得"卧槽"、"这也行？"、"赶紧告诉朋友"
   - **留悬念**：部分页面结尾要埋钩子引导继续听，如"但更疯狂的还在后面"
   - 必须保留关键数据，不能编造
   - **每页 narration 严格控制在 40-80 字**，宁短勿长

请严格以如下JSON格式输出，不要输出其他内容：
{
  "slides": [
    {"type": "cover", "title": "主标题", "subtitle": "副标题", "narration": "大家好，我是斯沃特。[一句话钩子]"},
    {"type": "content", "title": "页面标题", "points": ["要点1", "要点2", "要点3"], "image_keyword": "英文图片搜索关键词", "narration": "[40-80字精简口播]"},
    {"type": "section", "title": "章节标题"},
    {"type": "ending", "title": "谢谢观看", "narration": "好了今天就聊到这里，我是斯沃特，我们下期见！"}
  ]
}

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
        self.bg_dir = sl_cfg["bg_dir"]
        self.output_dir = sl_cfg["output_dir"]
        self.width = sl_cfg.get("width", 1920)
        self.height = sl_cfg.get("height", 1080)
        self.font_path = sl_cfg.get("font_path", "C:/Windows/Fonts/msyh.ttc")
        self.font_index = sl_cfg.get("font_index", 0)

        pexels_cfg = self.config.get("pexels", {})
        self.pexels_api_key = pexels_cfg.get("api_key", "")
        self.image_cache_dir = pexels_cfg.get("cache_dir", "asset/images")
        os.makedirs(self.image_cache_dir, exist_ok=True)

        self._load_fonts()

    def _load_fonts(self):
        """预加载各级字体。"""
        idx = self.font_index
        self.font_cover_title = ImageFont.truetype(self.font_path, 96, index=idx)
        self.font_cover_subtitle = ImageFont.truetype(self.font_path, 44, index=idx)
        self.font_title = ImageFont.truetype(self.font_path, 84, index=idx)
        self.font_point = ImageFont.truetype(self.font_path, 51, index=idx)
        self.font_page = ImageFont.truetype(self.font_path, 33, index=idx)

    # ------------------------------------------------------------------
    # DeepSeek 大纲提取
    # ------------------------------------------------------------------

    def extract_outline(self, text: str) -> list[dict]:
        """调用 DeepSeek API，将讲解文本转换为结构化幻灯片大纲。"""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "你是PPT大纲提取专家，只输出JSON。",
                },
                {"role": "user", "content": OUTLINE_PROMPT + text},
            ],
            temperature=0.3,
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
    # 各类型页面渲染
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

    def generate(self, text: str, output_dir: str | None = None) -> tuple[list[dict], list[str]]:
        """完整流程：文本 → 大纲 → 幻灯片图片序列。

        Returns:
            (slides_data, slide_paths) — 大纲数据列表 和 生成的图片路径列表
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
        for i, sd in enumerate(slides_data):
            path = self.render_slide(sd, i + 1, len(slides_data))
            slide_paths.append(path)
            print(f"  [{i + 1}/{len(slides_data)}] {sd['type']}: {path}")

        outline_path = os.path.join(self.output_dir, "outline.json")
        with open(outline_path, "w", encoding="utf-8") as f:
            json.dump({"slides": slides_data}, f, ensure_ascii=False, indent=2)
        print(f"大纲已保存: {outline_path}")

        return slides_data, slide_paths


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
    slides_data, slide_paths = generator.generate(sample_text)
    print(f"\n完成！共生成 {len(slide_paths)} 张幻灯片")
