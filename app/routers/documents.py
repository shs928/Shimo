"""文档只读预览：PDF/Word/TXT/CSV 提取纯文本供前端展示。

- 只读：无保存、无 Markdown 大纲、无编辑入口。
- 路径安全：走 path_guard（拒绝绝对路径 / .. / 符号链接 / 隐藏路径 / 回收站）。
- 大小限制：单文件 30MB 上限；预览文本截断至 50 万字符。
- 扫描件（无文字层）不报错：返回 has_text=false + OCR 状态，前端引导
  等待识别或用浏览器原生阅读器打开原文件。
- POST /ocr-retry：识别失败后手动重试（failed → pending 重新入队）。
- 原始文件经 /api/v1/raw/ 在新窗口打开（不放宽 X-Frame-Options）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..deps import csrf_guard, get_vault, require_auth
from ..services.doc_parser import is_document, parse_document_strict
from ..services.path_guard import PathError, is_hidden_rel, normalize_rel, resolve_in_root
from ..services.vault import NotFoundError, VaultError

router = APIRouter(prefix="/api/v1/documents", tags=["documents"], dependencies=[Depends(require_auth)])

_write_deps = [Depends(csrf_guard)]

_MAX_DOC_SIZE = 30 * 1024 * 1024  # 30MB
_MAX_PREVIEW_CHARS = 500_000


def _resolve_pdf(rel: str, request: Request):
    """路径校验并确认是 Vault 内存在的 PDF；非法时抛 VaultError。"""
    vault = get_vault(request)
    if is_hidden_rel(rel) or rel.startswith(".trash"):
        raise VaultError("不允许操作隐藏路径或回收站中的文件")
    if not rel.lower().endswith(".pdf"):
        raise VaultError("仅支持 PDF 的 OCR 重试")
    full = resolve_in_root(vault.root, rel)
    if not full.is_file():
        raise NotFoundError(f"文件不存在：{rel}")
    return full


@router.post("/ocr-retry", dependencies=_write_deps)
def ocr_retry(path: str, request: Request) -> dict:
    """识别失败后重新入队（failed → pending）。"""
    try:
        rel = normalize_rel(path)
    except PathError as exc:
        raise VaultError(str(exc)) from exc
    _resolve_pdf(rel, request)
    ocr_service = request.app.state.ocr_service
    status = ocr_service.ensure_job(rel)
    return {"status": status}


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
        raise VaultError("文档过大（超过 30MB），请在本地打开")

    try:
        text = parse_document_strict(rel, full.read_bytes())
    except Exception as exc:
        raise VaultError(f"文档解析失败：{exc}") from exc

    ocr_status: str | None = None
    ocr_progress = 0
    ocr_error = ""
    ocr = False
    if not text.strip() and rel.lower().endswith(".pdf"):
        # 扫描件：优先返回已完成 OCR 文本；否则返回任务状态。
        # 失败状态不自动重置（前端展示错误并提供"重新识别"入口）。
        ocr_service = request.app.state.ocr_service
        stored = ocr_service.text(rel)
        if stored:
            text = stored
            ocr = True
        else:
            st = ocr_service.status(rel)
            if st is None:
                ocr_status = ocr_service.ensure_job(rel)
                st = ocr_service.status(rel) or {}
            else:
                ocr_status = st["status"]
            ocr_progress = int(st.get("progress") or 0)
            ocr_error = st.get("error") or ""

    truncated = len(text) > _MAX_PREVIEW_CHARS
    return {
        "path": rel,
        "name": full.name,
        "size": size,
        "chars": len(text),
        "truncated": truncated,
        # 扫描件/无文字层：解析成功但无可预览文本，前端引导打开原文件
        "has_text": bool(text.strip()),
        # OCR 相关：ocr=True 表示文本来自识别；ocr_status 为 pending/running/failed
        "ocr": ocr,
        "ocr_status": ocr_status,
        "ocr_progress": ocr_progress,
        "ocr_error": ocr_error,
        "raw_url": f"/api/v1/raw/{rel}",
        "text": text[:_MAX_PREVIEW_CHARS],
    }
