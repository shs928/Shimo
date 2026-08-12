"""Vault 文件 API：目录树、读取、保存、移动、回收站、WikiLink 解析。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, Field

from ..deps import csrf_guard, get_indexer, get_vault, require_auth
from ..services.doc_parser import is_document, parse_document
from ..services.indexer import Indexer
from ..services.links import resolve_wiki_target
from ..services.vault import FileContent, MovePlan, NodeInfo, Vault

router = APIRouter(prefix="/api/v1", tags=["files"], dependencies=[Depends(require_auth)])

_write_deps = [Depends(csrf_guard)]


def _rag(request: Request):
    return request.app.state.rag


def _reindex_ai(request: Request, rel: str) -> None:
    """重建单个文件的 AI 索引：.md 取原文，文档解析文本。"""
    vault = get_vault(request)
    rag = _rag(request)
    if rel.lower().endswith(".md"):
        fc = vault.read_markdown(rel)
        rag.reindex_file(rel, fc.content)
    elif is_document(rel):
        text = parse_document(rel, (vault.root / rel).read_bytes())
        if text:
            rag.reindex_file(rel, text)


# ---------- 请求 / 响应模型 ----------


class ContentIn(BaseModel):
    content: str


class CreateIn(BaseModel):
    path: str = Field(min_length=1)
    type: str = Field(default="file", pattern="^(file|dir)$")
    initial_content: str = ""


class MoveIn(BaseModel):
    src: str
    dst: str


class RestoreIn(BaseModel):
    path: str
    target: str | None = None


class TrashEntryOut(BaseModel):
    name: str
    path: str
    type: str
    size: int
    mtime_ns: int


# ---------- 读取 ----------


@router.get("/tree")
def tree(request: Request, vault: Vault = Depends(get_vault), path: str = Query(default="")) -> dict:
    entries = vault.list_children(path)
    return {"entries": [e.__dict__ for e in entries]}


@router.get("/files/content")
def read_file(request: Request, vault: Vault = Depends(get_vault), path: str = Query(min_length=1)) -> FileContent:
    return vault.read_markdown(path)


@router.get("/trash")
def trash(request: Request, vault: Vault = Depends(get_vault)) -> dict:
    entries = vault.list_trash()
    return {"entries": [TrashEntryOut(**e.__dict__).model_dump() for e in entries]}


# ---------- 写入 ----------


@router.put("/files/content", dependencies=_write_deps)
def save_file(
    request: Request,
    vault: Vault = Depends(get_vault),
    indexer: Indexer = Depends(get_indexer),
    path: str = Query(min_length=1),
    payload: ContentIn = None,
    if_match: str | None = Header(default=None),
) -> FileContent:
    result = vault.write_markdown(path, payload.content, if_match)
    try:
        indexer.index_file(path)
        _rag(request).reindex_file(path, result.content)
    except Exception:
        # 索引失败不撤销已成功的写入；诊断 API 可观测。
        pass
    return result


@router.post("/files", dependencies=_write_deps)
def create(
    request: Request,
    vault: Vault = Depends(get_vault),
    indexer: Indexer = Depends(get_indexer),
    payload: CreateIn = None,
) -> NodeInfo:
    node = vault.create(payload.path, payload.type, payload.initial_content)
    if node.type == "file":
        try:
            indexer.index_file(node.path)
            _rag(request).reindex_file(node.path, payload.initial_content)
        except Exception:
            pass
    return node


@router.post("/files/move/preview", dependencies=_write_deps)
def move_preview(request: Request, vault: Vault = Depends(get_vault), payload: MoveIn = None) -> MovePlan:
    return vault.preview_move(payload.src, payload.dst)


@router.post("/files/move", dependencies=_write_deps)
def move(
    request: Request,
    vault: Vault = Depends(get_vault),
    indexer: Indexer = Depends(get_indexer),
    payload: MoveIn = None,
) -> NodeInfo:
    node = vault.move(payload.src, payload.dst)
    try:
        indexer.move_path(payload.src, payload.dst)
        rag = _rag(request)
        rag.delete_file(payload.src)
        if node.type == "file":
            _reindex_ai(request, node.path)
    except Exception:
        pass
    return node


@router.delete("/files", dependencies=_write_deps)
def delete(
    request: Request,
    vault: Vault = Depends(get_vault),
    indexer: Indexer = Depends(get_indexer),
    path: str = Query(min_length=1),
) -> dict:
    vault.delete(path)
    try:
        indexer.delete_path(path)
        _rag(request).delete_file(path)
    except Exception:
        pass
    return {"ok": True}


@router.post("/trash/restore", dependencies=_write_deps)
def restore(
    request: Request,
    vault: Vault = Depends(get_vault),
    indexer: Indexer = Depends(get_indexer),
    payload: RestoreIn = None,
) -> NodeInfo:
    node = vault.restore(payload.path, payload.target)
    if node.type == "file":
        try:
            indexer.index_file(node.path)
            _reindex_ai(request, node.path)
        except Exception:
            pass
    return node


@router.post("/trash/purge", dependencies=_write_deps)
def purge(request: Request, vault: Vault = Depends(get_vault)) -> dict:
    count = vault.purge_trash()
    return {"purged": count}


# ---------- WikiLink 解析 ----------


@router.get("/wiki/resolve")
def resolve_wiki(
    request: Request,
    vault: Vault = Depends(get_vault),
    link: str = Query(min_length=1),
    dir: str = Query(default=""),
) -> dict:
    """将 WikiLink 目标解析为 Vault 内真实路径。

    解析顺序：当前文档目录 → Vault 根目录 → 全库唯一文件名。
    歧义（多个同名文件且无显式路径）时返回 null，由前端提示用户。
    """
    return {"path": resolve_wiki_target(vault.root, dir, link)}
