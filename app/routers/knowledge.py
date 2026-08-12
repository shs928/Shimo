"""知识索引 API：搜索、反链、出链、局部图谱、索引状态与重建。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from ..deps import csrf_guard, get_indexer, require_auth
from ..services.indexer import Indexer

router = APIRouter(prefix="/api/v1", tags=["knowledge"], dependencies=[Depends(require_auth)])

_write_deps = [Depends(csrf_guard)]


@router.get("/search")
def search(
    request: Request,
    indexer: Indexer = Depends(get_indexer),
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=30, ge=1, le=100),
) -> dict:
    return {"results": indexer.search(q, limit)}


@router.get("/backlinks")
def backlinks(
    request: Request,
    indexer: Indexer = Depends(get_indexer),
    path: str = Query(min_length=1, max_length=2048),
) -> dict:
    return {"backlinks": indexer.backlinks(path)}


@router.get("/outgoing")
def outgoing(
    request: Request,
    indexer: Indexer = Depends(get_indexer),
    path: str = Query(min_length=1, max_length=2048),
) -> dict:
    return {"links": indexer.outgoing(path)}


@router.get("/graph")
def graph(
    request: Request,
    indexer: Indexer = Depends(get_indexer),
    path: str = Query(min_length=1, max_length=2048),
    depth: int = Query(default=1, ge=1, le=1),
) -> dict:
    return indexer.graph(path, depth)


@router.get("/index/stats")
def index_stats(request: Request, indexer: Indexer = Depends(get_indexer)) -> dict:
    health = request.app.state.index_health
    return {
        **indexer.stats(),
        "failures": health.recent(limit=20),
        "failure_count": len(health.recent(limit=1000)),
    }


@router.post("/index/rebuild", dependencies=_write_deps)
def rebuild(request: Request, indexer: Indexer = Depends(get_indexer)) -> dict:
    result = indexer.rebuild()
    # 重建成功后清除失败记录（重建本身会重新索引全部文件）
    request.app.state.index_health.clear()
    return result


@router.post("/index/retry-failed", dependencies=_write_deps)
def retry_failed(request: Request) -> dict:
    """重试全部索引失败项；成功清除，仍失败的更新记录。"""
    return request.app.state.index_health.retry_failed(
        request.app.state.vault, request.app.state.indexer, request.app.state.rag
    )
