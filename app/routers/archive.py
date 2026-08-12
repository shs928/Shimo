"""ZIP 导入导出路由：预览、导入（策略）、导出。"""
from __future__ import annotations

import io

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import StreamingResponse

from ..deps import csrf_guard, get_indexer, get_vault, require_auth
from ..services.archive import export_zip, extract_zip, preview_zip
from ..services.indexer import Indexer
from ..services.vault import Vault

router = APIRouter(prefix="/api/v1/archive", tags=["archive"], dependencies=[Depends(require_auth)])
_write_deps = [Depends(csrf_guard)]

_MAX_UPLOAD = 600 * 1024 * 1024  # zip 上传上限（略大于展开上限）


@router.post("/import/preview", dependencies=_write_deps)
async def import_preview(file: UploadFile = File(...)) -> dict:
    data = await file.read(_MAX_UPLOAD + 1)
    if len(data) > _MAX_UPLOAD:
        from ..services.vault import VaultError

        raise VaultError("压缩包超过 600MB 上限")
    return preview_zip(data)


@router.post("/import", dependencies=_write_deps)
async def import_zip(
    request: Request,
    strategy: str = Query(default="skip", pattern="^(skip|rename|overwrite)$"),
    file: UploadFile = File(...),
) -> dict:
    data = await file.read(_MAX_UPLOAD + 1)
    if len(data) > _MAX_UPLOAD:
        from ..services.vault import VaultError

        raise VaultError("压缩包超过 600MB 上限")
    return extract_zip(
        data, strategy,
        get_vault(request), request.app.state.history,
        get_indexer(request), request.app.state.rag,
    )


@router.get("/export")
def export(request: Request, vault: Vault = Depends(get_vault), path: str = Query(default="")) -> StreamingResponse:
    buf: io.BytesIO = export_zip(vault, path)
    name = "shimo-vault.zip" if not path else path.strip("/").replace("/", "-") + ".zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
