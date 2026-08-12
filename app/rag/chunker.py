"""文档分块：按标题与段落切分，目标约 800 tokens。

中文按字符估算（约 1 字符 ≈ 0.6 token），以行数/字符数双重约束。
超长单行先按滑动窗口切分，相邻块带少量重叠，避免语义断点。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_TARGET_CHARS = 1500  # ≈ 900 tokens
_OVERLAP_CHARS = 200
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


@dataclass(frozen=True)
class Chunk:
    idx: int
    heading: str
    line_start: int
    line_end: int
    text: str


def chunk_markdown(text: str) -> list[Chunk]:
    lines = text.split("\n")
    if not lines:
        return []

    # 跳过 front matter
    start = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                start = i + 1
                break

    # 超长单行按滑动窗口切分（保留原行号），避免单行永远整段落块
    step = _TARGET_CHARS - _OVERLAP_CHARS
    prepared: list[tuple[str, int]] = []
    for i, line in enumerate(lines[start:], start=start):
        if len(line) > _TARGET_CHARS and not line.lstrip().startswith(("```", "~~~", "#")):
            pos = 0
            while pos < len(line):
                prepared.append((line[pos : pos + _TARGET_CHARS], i))
                pos += step
        else:
            prepared.append((line, i))

    chunks: list[Chunk] = []
    current_heading = ""
    buf: list[str] = []
    buf_start = -1

    def flush() -> None:
        nonlocal buf, buf_start
        body = "\n".join(buf).strip()
        if body:
            chunks.append(Chunk(len(chunks), current_heading, buf_start + 1, buf_start + len(buf), body))
        buf = []
        buf_start = -1

    in_fence = False
    for line, line_no in prepared:
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence

        if not in_fence:
            m = _HEADING_RE.match(line)
            if m:
                flush()
                current_heading = m.group(2).strip()
                buf_start = line_no
                continue

        if buf_start < 0:
            buf_start = line_no
        buf.append(line)
        if sum(len(x) for x in buf) >= _TARGET_CHARS:
            flush()
            # 重叠：保留上一块尾部若干行，避免语义断点
            if chunks:
                prev_lines = chunks[-1].text.split("\n")
                tail = "\n".join(prev_lines[-6:])
                buf = [tail, ""] if tail else []
                buf_start = line_no - len(prev_lines[-6:])

    flush()
    return chunks
