"""Markdown 元信息解析：YAML front matter、标题大纲、显示标题。

知识内容始终是原文，本模块只做只读解析，不重写用户文件。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

try:
    import yaml  # PyYAML
    _HAS_YAML = True
except ImportError:  # pragma: no cover
    _HAS_YAML = False


@dataclass
class Heading:
    level: int
    text: str
    slug: str
    line: int


@dataclass
class FrontMatter:
    data: dict = field(default_factory=dict)
    raw: str = ""
    start: int = 0
    end: int = 0


@dataclass
class DocMeta:
    frontmatter: FrontMatter = field(default_factory=FrontMatter)
    headings: list[Heading] = field(default_factory=list)
    title: str = ""


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def parse_frontmatter(text: str) -> FrontMatter:
    """解析文件开头的 YAML front matter（--- 包裹）。

    解析失败时安全降级：返回空数据，不抛出异常，不修改原文。
    """
    fm = FrontMatter()
    if not text.startswith("---"):
        return fm
    lines = text.split("\n")
    if len(lines) < 3:
        return fm
    end_idx: int | None = None
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return fm
    body = "\n".join(lines[1:end_idx])
    fm.raw = body
    fm.start = 0
    fm.end = end_idx
    if _HAS_YAML:
        try:
            parsed = yaml.safe_load(body) or {}
            fm.data = parsed if isinstance(parsed, dict) else {}
        except yaml.YAMLError:
            fm.data = {}
    return fm


def parse_headings(text: str) -> list[Heading]:
    headings: list[Heading] = []
    slug_counts: dict[str, int] = {}
    in_fence = False
    for line_no, line in enumerate(text.split("\n"), start=1):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING_RE.match(line)
        if not m:
            continue
        level = len(m.group(1))
        text_raw = m.group(2).strip()
        headings.append(Heading(level=level, text=text_raw, slug=_unique_slug(text_raw, slug_counts), line=line_no))
    return headings


def doc_title(text: str, fallback: str) -> str:
    """显示标题优先级：front matter title > 第一个 H1 > 文件名。"""
    fm = parse_frontmatter(text)
    t = fm.data.get("title")
    if isinstance(t, str) and t.strip():
        return t.strip()
    for h in parse_headings(text):
        if h.level == 1:
            return h.text
    return fallback


def body_without_frontmatter(text: str) -> str:
    fm = parse_frontmatter(text)
    if fm.end == 0:
        return text
    lines = text.split("\n")
    return "\n".join(lines[fm.end + 1 :])


def _unique_slug(text: str, counts: dict[str, int]) -> str:
    slug = _slugify(text)
    counts[slug] = counts.get(slug, 0) + 1
    if counts[slug] > 1:
        return f"{slug}-{counts[slug]}"
    return slug


def _slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "section"
