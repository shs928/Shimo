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
    return indexer.stats()


@router.post("/index/rebuild", dependencies=_write_deps)
def rebuild(request: Request, indexer: Indexer = Depends(get_indexer)) -> dict:
    return indexer.rebuild()
