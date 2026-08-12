"""引用随重命名更新：把指向旧路径的链接改写为新路径。

规则：
- 只重写可唯一解析的 Markdown 链接 / WikiLink / 嵌入（图片嵌入不重写）。
- 跳过 fenced code block 内的链接。
- 歧义链接（目标无法唯一确定）跳过。
- 写入前先保存历史快照。
- 批量写入 + rollback 清单：任一步失败恢复已写文件，不留半完成状态。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"^```.*$", re.MULTILINE)


def _link_spans(text: str) -> list[tuple[int, int, str]]:
    """找出正文（非代码块）中的 Markdown 链接与 WikiLink 区间。

    返回 [(start, end, raw)]，raw 为完整匹配文本。
    """
    spans: list[tuple[int, int, str]] = []
    # 代码块区间（按行扫描，``` 切换状态）
    in_fence = False
    fences: list[tuple[int, int]] = []
    line_start = 0
    for m in _FENCE_RE.finditer(text):
        start = m.start()
        # 行首检测
        line_start = text.rfind("\n", 0, start) + 1
        if m.start() != line_start:
            continue
        if not in_fence:
            fences.append((start, 0))
        else:
            fences[-1] = (fences[-1][0], m.end())
        in_fence = not in_fence
    if in_fence and fences:
        fences[-1] = (fences[-1][0], len(text))

    def in_fence_at(pos: int) -> bool:
        return any(a <= pos < b for a, b in fences)

    # WikiLink [[target]] / ![[embed]]
    for m in re.finditer(r"!?\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]", text):
        if not in_fence_at(m.start()):
            spans.append((m.start(), m.end(), m.group(0)))
    # Markdown 链接 [text](target)
    for m in re.finditer(r"\[[^\]]*\]\(([^)\s]+)\)", text):
        if not in_fence_at(m.start()):
            spans.append((m.start(), m.end(), m.group(0)))
    return spans


def _resolve_target(text: str, raw: str, src: str, dst: str, root) -> str | None:
    """解析单个链接：指向 src（或其子路径）且可唯一解析 → 返回新 raw；否则 None。"""
    from .links import resolve_markdown_target

    # WikiLink：取目标名
    target = None
    if raw.startswith("![[") or raw.startswith("[["):
        inner = raw.strip("![]")
        target = inner.split("#")[0].split("|")[0].strip()
        resolved = resolve_markdown_target(root, "", target)
        if resolved is None:
            return None  # 歧义或不存在
        if resolved == src or resolved.startswith(src + "/"):
            # 保持原链接风格：目标不带 .md 时新目标也去掉扩展名
            is_embed = raw.startswith("!")
            new_target = dst
            if not target.lower().endswith(".md") and new_target.lower().endswith(".md"):
                new_target = new_target[:-3]
            suffix = inner[len(target):]  # #anchor / |alias
            prefix = "!" if is_embed else ""
            return f"{prefix}[[{new_target}{suffix}]]"
        return None
    # Markdown 链接：目标路径
    m = re.match(r"\[([^\]]*)\]\(([^)\s]+)\)", raw)
    if not m:
        return None
    label, href = m.group(1), m.group(2)
    if href.startswith(("http://", "https://", "/", "#")):
        return None
    base = href.split("#")[0]
    resolved = resolve_markdown_target(root, "", base)
    if resolved is None:
        return None
    if resolved == src or resolved.startswith(src + "/"):
        new_href = dst
        if "#" in href:
            new_href = dst + "#" + href.split("#", 1)[1]
        return f"[{label}]({new_href})"
    return None


def collect_affected(db, src: str) -> list[str]:
    """从 links 表找出所有引用 src（或其子路径）的源文件。"""
    conn = db.connect()
    rows = conn.execute(
        """SELECT DISTINCT source_path FROM links
           WHERE target_path = ? OR target_path LIKE ?""",
        (src, src.rstrip("/") + "/%"),
    ).fetchall()
    return [r[0] for r in rows]


def refactor_links(vault, db, history, indexer, rag, src: str, dst: str) -> dict:
    """重写所有引用旧路径的链接；返回统计。失败回滚，不留半完成状态。"""
    root = vault.root
    sources = collect_affected(db, src)
    updated_files: list[str] = []
    updated_links = 0
    # 批量写入 + 回滚清单
    written: list[tuple[str, bytes]] = []  # (rel, 原始字节)

    def rollback() -> None:
        for rel, data in reversed(written):
            try:
                (root / rel).write_bytes(data)
            except Exception:
                logger.warning("回滚失败：%s", rel)

    try:
        for rel in sources:
            if not rel.lower().endswith(".md"):
                continue
            fc = vault.read_markdown(rel)
            text = fc.content
            new_text = text
            changed = 0
            # 从后往前替换，避免偏移错乱
            spans = _link_spans(text)
            for start, end, raw in reversed(spans):
                new_raw = _resolve_target(text, raw, src, dst, root)
                if new_raw is not None and new_raw != raw:
                    new_text = new_text[:start] + new_raw + new_text[end:]
                    changed += 1
            if changed == 0:
                continue
            # 历史快照（可撤销）
            history.save_snapshot(rel, text)
            written.append((rel, (root / rel).read_bytes()))
            result = vault.write_markdown(rel, new_text, None)
            indexer.index_file(rel)
            rag.reindex_file(rel, result.content)
            updated_files.append(rel)
            updated_links += changed
    except Exception as exc:
        rollback()
        raise RuntimeError(f"引用更新失败已回滚：{exc}") from exc
    return {"updated_files": updated_files, "updated_links": updated_links}
