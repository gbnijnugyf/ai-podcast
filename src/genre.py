"""节目形态（Genre）定义。

控制片头标题规则、结尾话术与文案口吻。
当前支持：
  - daily_brief：今日资讯（默认，行为与历史一致）
  - general：通用话题（片头用脚本/话题标题）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

TitleMode = Literal["date_label", "script_title"]

DEFAULT_GENRE = "daily_brief"
VALID_GENRES = ("daily_brief", "general")

# 片头标题单行过长时截断（字符数，含中文）
_MAX_TITLE_CHARS = 24


@dataclass(frozen=True)
class GenreConfig:
    id: str
    display_name: str
    title_mode: TitleMode
    title_label: str  # date_label 模式下第二行文案
    endings: tuple[str, ...]
    # 注入口播 prompt「写作风格要求」的补充说明；daily_brief 为空以保持原 prompt
    script_tone: str


DAILY_BRIEF_ENDINGS = (
    "每天两分钟，关注我，我们下期见！",
    "关注我，培养一个每天了解时事热点的微习惯。",
    "每天花两分钟跟我看看世界在发生什么，咱们下期不见不散。",
    "每天两分钟，帮助你了解一天大事，我们下期再见。",
    "关注我，养成每天快速了解时事的习惯，下期见。",
    "今天先到这，后续的事看后续，我们下期见。",
    "每天两分钟了解热点大事，觉得有帮助关注支持下吧，下期见！",
    "关注我，每天花两分钟就能跟上世界的节奏，咱们下期见。",
    "今天就聊到这，点个关注不迷路，我们下期接着唠。",
    "每天两分钟了解大事，关注我培养看新闻的好习惯，下期见！",
    "关注我，每天两分钟帮你把握时事脉搏，咱们下期见。",
    "干货都给了，关注我不错过下期精彩，我们下期见。",
)

GENERAL_ENDINGS = (
    "今天就先聊到这里，我们下期见！",
    "感兴趣的话点个关注，下期继续聊。",
    "以上就是本期内容，咱们下期再见。",
    "觉得有收获的话关注一下，下期见。",
    "先聊到这，有想听的话题可以留言，下期见。",
    "本期内容就这些，我们下期不见不散。",
)

GENRES: dict[str, GenreConfig] = {
    "daily_brief": GenreConfig(
        id="daily_brief",
        display_name="热点资讯",
        title_mode="date_label",
        title_label="热点资讯",
        endings=DAILY_BRIEF_ENDINGS,
        script_tone="",
    ),
    "general": GenreConfig(
        id="general",
        display_name="通用",
        title_mode="script_title",
        title_label="",
        endings=GENERAL_ENDINGS,
        script_tone=(
            "- 这是通用话题口播，不要绑定「今日资讯 / 两分钟时事 / 每天热点」的栏目感\n"
            "- 围绕给定主题清晰讲解即可，口吻自然专业，结尾不要强行时事订阅话术"
        ),
    ),
}


def resolve_genre(genre_id: str | None) -> GenreConfig:
    """解析 genre id，空值回退默认；非法值抛出 ValueError。"""
    if not genre_id:
        return GENRES[DEFAULT_GENRE]
    key = genre_id.strip().lower()
    if key not in GENRES:
        raise ValueError(
            f"未知 genre: {genre_id!r}，可选: {', '.join(VALID_GENRES)}"
        )
    return GENRES[key]


def format_intro_title(genre: GenreConfig, script_title: str | None = None) -> str:
    """生成片头叠加标题（FFmpeg drawtext 用，换行以 \\n 表示）。"""
    if genre.title_mode == "date_label":
        now = datetime.now()
        return f"{now.year}.{now.month}.{now.day}\\n{genre.title_label}"

    title = (script_title or "").strip() or genre.display_name
    title = _truncate_title(title)
    # 过长时拆成两行，便于片头排版
    if len(title) > 12:
        return _split_title_two_lines(title)
    return title


def _truncate_title(title: str) -> str:
    title = title.replace("\n", " ").strip()
    if len(title) <= _MAX_TITLE_CHARS:
        return title
    return title[: _MAX_TITLE_CHARS - 1].rstrip() + "…"


def _split_title_two_lines(title: str) -> str:
    """优先在空格处拆行，避免第二行前导空格。"""
    mid = (len(title) + 1) // 2
    # 在中点附近找空格
    for i in range(mid, max(mid - 6, 1), -1):
        if title[i] == " ":
            return f"{title[:i].rstrip()}\\n{title[i + 1:].lstrip()}"
    for i in range(mid, min(mid + 6, len(title))):
        if title[i] == " ":
            return f"{title[:i].rstrip()}\\n{title[i + 1:].lstrip()}"
    return f"{title[:mid]}\\n{title[mid:].lstrip()}"
