"""文档只读预览：PDF/Word/TXT/CSV 提取纯文本供前端展示。

- 只读：无保存、无 Markdown 大纲、无编辑入口。
- 路径安全：走 path_guard（拒绝绝对路径 / .. / 符号链接 / 隐藏路径 / 回收站）。
- 大小限制：单文件 20MB 上限；预览文本截断至 50 万字符。
- 原始文件经 /api/v1/raw/ 在新窗口打开（不放宽 X-Frame-Options）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..deps import get_vault, require_auth
from ..services.doc_parser import is_document, parse_document
from ..services.path_guard import PathError, is_hidden_rel, normalize_rel, resolve_in_root
from ..services.vault import NotFoundError, VaultError

router = APIRouter(prefix="/api/v1/documents", tags=["documents"], dependencies=[Depends(require_auth)])

_MAX_DOC_SIZE = 20 * 1024 * 1024  # 20MB
_MAX_PREVIEW_CHARS = 500_000


@router.get("/preview")
def preview(path: str, request: Request) -> dict:
    vault = get_vault(request)
    try:
        rel = normalize_rel(path)
    except PathError as exc:
        raise VaultError(str(exc)) from exc
    if is_hidden_rel(rel) or rel.startswith(".trash"):
        raise VaultError("不允许预览隐藏路径或回收站中的文件")
    if not is_document(rel):
        raise VaultError(f"不支持预览该类型：{path}（支持 PDF/Word/TXT/CSV）")

    try:
        full = resolve_in_root(vault.root, rel)
    except PathError as exc:
        raise VaultError(str(exc)) from exc
    if not full.is_file():
        raise NotFoundError(f"文件不存在：{path}")
    try:
        size = full.stat().st_size
    except OSError as exc:
        raise VaultError(f"无法访问文件：{exc}") from exc
    if size <= 0:
        raise VaultError("文件为空")
    if size > _MAX_DOC_SIZE:
        raise VaultError("文档过大（超过 20MB），请在本地打开")

    try:
        text = parse_document(rel, full.read_bytes())
    except Exception as exc:
        raise VaultError(f"文档解析失败：{exc}") from exc
    if not text.strip():
        raise VaultError("文档解析失败或无可预览内容")

    truncated = len(text) > _MAX_PREVIEW_CHARS
    return {
        "path": rel,
        "name": full.name,
        "size": size,
        "chars": len(text),
        "truncated": truncated,
        "text": text[:_MAX_PREVIEW_CHARS],
    }
