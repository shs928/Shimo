"""版本历史路由：列表、读取、diff、恢复。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from ..deps import csrf_guard, require_auth
from ..services.path_guard import normalize_rel
from ..services.vault import VaultError

router = APIRouter(prefix="/api/v1/history", tags=["history"], dependencies=[Depends(require_auth)])
_write_deps = [Depends(csrf_guard)]


def _history(request: Request):
    return request.app.state.history


@router.get("")
def list_versions(request: Request, path: str = Query(min_length=1)) -> dict:
    try:
        rel = normalize_rel(path)
    except Exception as exc:
        raise VaultError(str(exc)) from exc
    return {"path": rel, "versions": _history(request).list_versions(rel)}


@router.get("/version")
def get_version(request: Request, path: str = Query(min_length=1), sha1: str = Query(min_length=1)) -> dict:
    rel = normalize_rel(path)
    content = _history(request).get_version(rel, sha1)
    if content is None:
        raise VaultError("版本不存在")
    return {"path": rel, "sha1": sha1, "content": content}


@router.get("/diff")
def diff(request: Request, path: str = Query(min_length=1), sha1: str = Query(min_length=1)) -> dict:
    """指定版本与当前内容的行级 diff（统一格式）。"""
    rel = normalize_rel(path)
    store = _history(request)
    old = store.get_version(rel, sha1)
    if old is None:
        raise VaultError("版本不存在")
    current = request.app.state.vault.read_markdown(rel).content
    return {"path": rel, "sha1": sha1, "diff": store.diff(old, current)}


@router.post("/restore", dependencies=_write_deps)
def restore(request: Request, path: str = Query(min_length=1), sha1: str = Query(min_length=1)) -> dict:
    """恢复指定版本：先保存当前版本快照（可撤销），再写回并重建索引。"""
    rel = normalize_rel(path)
    watcher = getattr(request.app.state, "watcher", None)
    mark = watcher.mark_self_write if watcher is not None else None
    try:
        content = _history(request).restore(
            rel, sha1,
            request.app.state.vault, request.app.state.indexer, request.app.state.rag,
            mark_self_write=mark,
        )
    except ValueError as exc:
        raise VaultError(str(exc)) from exc
    return {"ok": True, "path": rel, "content": content}
