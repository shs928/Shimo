"""Markdown 链接解析与 Vault 内目标解析。

支持：
- WikiLink: [[页面]]、[[页面#标题]]、[[页面|别名]]、![[嵌入]]
- 标准 Markdown: [文字](path.md)、![图片](assets/a.png)

解析器跳过 fenced code block。目标解析顺序：当前目录 → Vault 根目录 →
全库唯一文件名；隐藏目录和回收站不参与。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from .path_guard import PathError, is_templates_rel, normalize_rel, resolve_in_root

_WIKI_RE = re.compile(r"(!)?\[\[([^\]|#]+)(?:#([^\]|]+))?(?:\|([^\]]+))?\]\]")
_MD_RE = re.compile(r"(!)?\[([^\]]*)\]\(([^)\s]+)(?:\s+[\"'][^\"']*[\"'])?\)")
_EXTERNAL_RE = re.compile(r"^(?:https?:|mailto:|data:|blob:|#)", re.I)


@dataclass(frozen=True)
class ParsedLink:
    target_raw: str
    anchor: str
    alias: str
    link_type: str  # wiki | embed | markdown | image
    line: int
    context: str


def parse_links(text: str) -> list[ParsedLink]:
    links: list[ParsedLink] = []
    in_fence = False
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        for m in _WIKI_RE.finditer(line):
            links.append(
                ParsedLink(
                    target_raw=m.group(2).strip(),
                    anchor=(m.group(3) or "").strip(),
                    alias=(m.group(4) or "").strip(),
                    link_type="embed" if m.group(1) else "wiki",
                    line=line_no,
                    context=line.strip()[:240],
                )
            )

        # 避免把 WikiLink 内部片段当成标准链接；正则本身通常不会命中，
        # 但保留显式过滤使规则更稳定。
        for m in _MD_RE.finditer(line):
            href = unquote(m.group(3).strip())
            if _EXTERNAL_RE.match(href):
                continue
            target, _, anchor = href.partition("#")
            if not target:
                continue
            links.append(
                ParsedLink(
                    target_raw=target,
                    anchor=anchor,
                    alias=(m.group(2) or "").strip(),
                    link_type="image" if m.group(1) else "markdown",
                    line=line_no,
                    context=line.strip()[:240],
                )
            )
    return links


def resolve_markdown_target(root: Path, source_path: str, target_raw: str) -> str | None:
    """解析目标 Markdown，返回 Vault 相对路径；不存在或歧义时返回 None。"""
    raw = unquote(target_raw).replace("\\", "/").strip()
    if not raw or _EXTERNAL_RE.match(raw):
        return None

    # 标准相对路径中的 ./ 和 Vault 内的 ../ 规范化后允许；
    # 归一化后仍逃逸 Vault 的由 normalize_rel 拒绝。
    segments: list[str] = []
    for seg in raw.split("/"):
        if seg == "." or seg == "":
            continue
        if seg == "..":
            if segments:
                segments.pop()
            continue
        segments.append(seg)
    raw = "/".join(segments)
    if not raw:
        return None
    if not PurePosixPath(raw).suffix:
        raw += ".md"
    if not raw.lower().endswith(".md"):
        return None

    source_dir = PurePosixPath(source_path).parent.as_posix()
    if source_dir == ".":
        source_dir = ""

    candidates: list[str] = []
    if source_dir:
        candidates.append(f"{source_dir}/{raw}")
    candidates.append(raw)

    seen: set[str] = set()
    for candidate in candidates:
        try:
            candidate = normalize_rel(candidate)
            if candidate in seen or is_templates_rel(candidate):
                continue
            seen.add(candidate)
            if resolve_in_root(root, candidate).is_file():
                return candidate
        except PathError:
            continue

    # 只对不含目录的目标做全库唯一文件名搜索。
    if "/" not in raw:
        matches = _find_by_basename(root, PurePosixPath(raw).name)
        if len(matches) == 1:
            return matches[0]
    return None


def resolve_wiki_target(root: Path, current_dir: str, link: str) -> str | None:
    """供 HTTP WikiLink 解析接口使用。"""
    synthetic_source = f"{current_dir}/_current.md" if current_dir else "_current.md"
    return resolve_markdown_target(root, synthetic_source, link)


def _find_by_basename(root: Path, basename: str) -> list[str]:
    matches: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and not (
                Path(dirpath) == root and d.casefold() == "templates"
            )
        ]
        if basename in filenames:
            full = Path(dirpath) / basename
            matches.append(full.relative_to(root).as_posix())
            if len(matches) > 1:
                break
    return matches
