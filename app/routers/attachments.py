"""附件与静态资源 API。

- 上传：写入 vault/assets/，返回可移植相对路径。
- 预览：GET /api/v1/raw/{rel} 安全读取 Vault 内任意文件，
  供 Markdown 中的相对路径图片/附件在浏览器渲染。

安全：类型白名单、大小上限、文件名规范化、路径穿越校验、nosniff。
"""
from __future__ import annotations

import mimetypes
import secrets
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import Response

from ..deps import csrf_guard, get_vault, require_auth
from ..services.path_guard import PathError, normalize_rel, resolve_in_root, validate_name
from ..services.vault import Vault, VaultError

router = APIRouter(prefix="/api/v1", tags=["attachments"], dependencies=[Depends(require_auth)])

_write_deps = [Depends(csrf_guard)]

ASSETS_DIR = "assets"
_MAX_SIZE = 50 * 1024 * 1024  # 50MB
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".avif"}
_TEXT_EXT = {".md", ".txt"}
_SAFE_EXT = {
    *_IMAGE_EXT, *_TEXT_EXT,
    ".pdf", ".zip", ".docx", ".xlsx", ".pptx", ".csv", ".json", ".html",
}
# 允许但提示风险的扩展（默认放行，用于导入场景）
_ALLOWED_MIME_PREFIX = ("image/", "text/", "application/pdf", "application/zip",
                        "application/json", "application/octet-stream")


def _safe_extension(name: str) -> str:
    ext = PurePosixPath(name).suffix.lower()
    if ext not in _SAFE_EXT:
        raise VaultError(f"不允许的文件类型：{ext or '无扩展名'}")
    return ext


def _content_type(path: str, fallback: str) -> str:
    guessed, _ = mimetypes.guess_type(path)
    return guessed or fallback


@router.post("/attachments", dependencies=_write_deps)
async def upload(
    request: Request,
    file: UploadFile = File(...),
    vault: Vault = Depends(get_vault),
) -> dict:
    name = validate_name(file.filename or "attachment")
    ext = _safe_extension(name)

    data = await file.read(_MAX_SIZE + 1)
    if len(data) > _MAX_SIZE:
        raise VaultError("附件超过 50MB 上限")

    vault.ensure_initialized()
    rel = vault.create_unique_asset(name, data)
    return {
        "name": name,
        "relative_path": rel,
        "url": f"/api/v1/raw/{rel}",
    }


@router.get("/raw/{rel:path}")
def raw(request: Request, rel: str, vault: Vault = Depends(get_vault)) -> Response:
    rel = normalize_rel(rel)
    path = resolve_in_root(vault.root, rel)
    if not path.is_file():
        raise VaultError("资源不存在")
    if path.stat().st_size > _MAX_SIZE:
        raise VaultError("资源过大")

    data = path.read_bytes()
    ctype = _content_type(path.name, "application/octet-stream")
    return Response(
        content=data,
        media_type=ctype,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=3600",
        },
    )
