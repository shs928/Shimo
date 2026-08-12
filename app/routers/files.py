"""Vault 文件 API：目录树、读取、保存、移动、回收站、WikiLink 解析。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, Field

from ..deps import csrf_guard, get_indexer, get_vault, require_auth
from ..services.doc_parser import is_document, parse_document
from ..services.indexer import Indexer
from ..services.links import resolve_wiki_target
from ..services.link_refactor import collect_affected, refactor_links
from ..services.vault import FileContent, MovePlan, NodeInfo, Vault

router = APIRouter(prefix="/api/v1", tags=["files"], dependencies=[Depends(require_auth)])

_write_deps = [Depends(csrf_guard)]


def _rag(request: Request):
    return request.app.state.rag


def _health(request: Request):
    return request.app.state.index_health


def _mark_self_write(request: Request, rel: str) -> None:
    """登记应用内写入：watcher 在抑制窗口内跳过该路径（幂等）。"""
    watcher = getattr(request.app.state, "watcher", None)
    if watcher is not None:
        watcher.mark_self_write(rel)


def _safe_index(request: Request, rel: str, kind: str = "index") -> str | None:
    """执行索引并返回失败信息；失败时记录到 index_failures（不撤销写入）。"""
    try:
        if kind == "index":
            request.app.state.indexer.index_file(rel)
        else:
            _reindex_ai(request, rel)
        return None
    except Exception as exc:
        _health(request).record(rel, "index" if kind == "index" else "rag", str(exc))
        return str(exc)[:200]


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
    refactor_links: bool = False  # 是否同步更新引用


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
    path: str = Query(min_length=1),
    payload: ContentIn = None,
    if_match: str | None = Header(default=None),
) -> FileContent:
    result = vault.write_markdown(
        path, payload.content, if_match,
        on_before_write=lambda rel, text, etag: request.app.state.history.save_snapshot(rel, text),
    )
    _mark_self_write(request, path)
    warning = _safe_index(request, path, "index")
    rag_warning = _safe_index(request, path, "rag")
    if warning and not rag_warning:
        result.index_warning = f"知识索引失败：{warning}"
    elif rag_warning:
        result.index_warning = f"AI 索引失败：{rag_warning}"
    return result


@router.post("/files", dependencies=_write_deps)
def create(
    request: Request,
    vault: Vault = Depends(get_vault),
    indexer: Indexer = Depends(get_indexer),
    payload: CreateIn = None,
) -> NodeInfo:
    node = vault.create(payload.path, payload.type, payload.initial_content)
    _mark_self_write(request, node.path)
    if node.type == "file":
        _safe_index(request, node.path, "index")
        _safe_index(request, node.path, "rag")
    return node


@router.post("/files/move/preview", dependencies=_write_deps)
def move_preview(request: Request, vault: Vault = Depends(get_vault), payload: MoveIn = None) -> dict:
    plan = vault.preview_move(payload.src, payload.dst)
    affected = []
    if plan.valid:
        affected = collect_affected(request.app.state.db, payload.src)
    return {
        **plan.__dict__,
        "affected_links": len(affected),
        "affected_files": [a for a in affected if a.lower().endswith(".md")],
    }


@router.post("/files/move", dependencies=_write_deps)
def move(
    request: Request,
    vault: Vault = Depends(get_vault),
    indexer: Indexer = Depends(get_indexer),
    payload: MoveIn = None,
) -> NodeInfo:
    # 先更新引用（旧目标文件仍在，可唯一解析），再执行移动
    if payload.refactor_links:
        try:
            refactor_links(
                vault, request.app.state.db, request.app.state.history,
                request.app.state.indexer, _rag(request),
                payload.src, payload.dst,
            )
        except Exception as exc:
            # 引用更新失败已回滚；移动本身成功，记录警告
            request.app.state.index_health.record(payload.dst, "index", str(exc))
    node = vault.move(payload.src, payload.dst)
    try:
        request.app.state.indexer.move_path(payload.src, payload.dst)
        _rag(request).delete_file(payload.src)
        if node.type == "file":
            _reindex_ai(request, node.path)
        _health(request).clear(payload.src)
    except Exception as exc:
        _health(request).record(payload.dst, "index", str(exc))
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
        request.app.state.indexer.delete_path(path)
        _rag(request).delete_file(path)
        _health(request).clear(path)
    except Exception as exc:
        _health(request).record(path, "index", str(exc))
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
        _safe_index(request, node.path, "index")
        _safe_index(request, node.path, "rag")
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
