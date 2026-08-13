"""文档导入 API：上传 PDF / Word / TXT / CSV / MD 到知识库指定目录。

与附件接口（固定存 assets/）不同：import 写入用户指定的目录（文件树当前目录），
上传后立即解析文本并进入 AI 索引（与 .md 同等参与 RAG 检索）。
"""
from __future__ import annotations

from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from pydantic import BaseModel

from ..deps import csrf_guard, get_indexer, get_vault, require_auth
from ..services.doc_parser import SUPPORTED_EXT, is_document
from ..services.indexer import Indexer
from ..services.path_guard import is_templates_rel, validate_name
from ..services.vault import Vault, VaultError

router = APIRouter(prefix="/api/v1", tags=["import"], dependencies=[Depends(require_auth)])

_write_deps = [Depends(csrf_guard)]

_MAX_SIZE = 50 * 1024 * 1024  # 50MB


class ImportOut(BaseModel):
    path: str
    name: str
    size: int
    parsed_chars: int = 0


def _rag(request: Request):
    return request.app.state.rag


@router.post("/import", dependencies=_write_deps)
async def import_file(
    request: Request,
    file: UploadFile = File(...),
    dir: str = Query(default=""),
    vault: Vault = Depends(get_vault),
    indexer: Indexer = Depends(get_indexer),
) -> dict:
    name = validate_name(file.filename or "document")
    ext = PurePosixPath(name).suffix.lower()
    if ext not in SUPPORTED_EXT:
        raise VaultError(f"不支持的文档类型：{ext or '无扩展名'}（支持 pdf/docx/txt/csv/md）")

    data = await file.read(_MAX_SIZE + 1)
    if len(data) > _MAX_SIZE:
        raise VaultError("文件超过 50MB 上限")

    # 目标目录规范化（去掉首尾斜杠），仍交由 vault 校验路径安全
    rel_dir = dir.strip("/")
    rel = f"{rel_dir}/{name}" if rel_dir else name
    node = vault.import_file(rel, data)
    watcher = getattr(request.app.state, "watcher", None)
    if watcher is not None:
        watcher.mark_self_write(node.path)

    parsed_chars = 0
    ocr_status = None
    try:
        if is_templates_rel(node.path):
            request.app.state.event_hub.publish({"type": "templates_changed"})
        elif node.path.lower().endswith(".md"):
            fc = vault.read_markdown(node.path)
            indexer.index_file(node.path)
            _rag(request).reindex_file(node.path, fc.content)
            parsed_chars = len(fc.content)
        elif is_document(node.path):
            ocr_service = request.app.state.ocr_service
            text = ocr_service.text_for_index(vault, node.path)
            if text:
                _rag(request).reindex_file(node.path, text)
                parsed_chars = len(text)
            else:
                # 扫描件 PDF：OCR 任务已入队（后台识别完成后自动重建索引）
                st = ocr_service.status(node.path)
                ocr_status = (st or {}).get("status")
    except Exception as exc:
        # 索引失败不撤销已成功的导入；记录到 index_failures 供诊断与重试
        request.app.state.index_health.record(node.path, "index", str(exc))

    out = ImportOut(
        path=node.path,
        name=node.name,
        size=node.size,
        parsed_chars=parsed_chars,
    ).model_dump()
    if ocr_status:
        out["ocr_status"] = ocr_status
    return out
