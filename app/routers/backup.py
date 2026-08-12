"""备份恢复路由：创建 / 预览 / 恢复。"""
from __future__ import annotations

import io

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import StreamingResponse

from ..deps import csrf_guard, get_indexer, get_vault, require_auth
from ..services.backup import create_backup, preview_backup, restore_backup

router = APIRouter(prefix="/api/v1/backup", tags=["backup"], dependencies=[Depends(require_auth)])
_write_deps = [Depends(csrf_guard)]


@router.post("/create", dependencies=_write_deps)
def create(request: Request, full: bool = Query(default=False)) -> StreamingResponse:
    config = request.app.state.config
    buf: io.BytesIO = create_backup(config.vault_path, config.data_path, full=full)
    name = "shimo-full-backup.zip" if full else "shimo-vault-backup.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.post("/preview", dependencies=_write_deps)
async def preview(file: UploadFile = File(...)) -> dict:
    data = await file.read(600 * 1024 * 1024 + 1)
    return preview_backup(data)


@router.post("/restore", dependencies=_write_deps)
async def restore(request: Request, file: UploadFile = File(...)) -> dict:
    data = await file.read(600 * 1024 * 1024 + 1)
    return restore_backup(
        data,
        get_vault(request),
        get_indexer(request),
        request.app.state.rag,
        request.app.state.history,
    )
