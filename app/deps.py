"""FastAPI 依赖：认证、会话安全与应用状态。"""
from __future__ import annotations

from urllib.parse import urlparse

from fastapi import HTTPException, Request

from .auth import SESSION_COOKIE, AuthStore
from .services.indexer import Indexer
from .services.vault import Vault


def get_auth(request: Request) -> AuthStore:
    return request.app.state.auth


def get_vault(request: Request) -> Vault:
    return request.app.state.vault


def get_indexer(request: Request) -> Indexer:
    return request.app.state.indexer


def require_auth(request: Request) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if not get_auth(request).validate_session(token):
        raise HTTPException(status_code=401, detail="未登录或会话已过期")


def csrf_guard(request: Request) -> None:
    """写请求的同源校验：Origin 存在时必须与 Host 匹配。"""
    origin = request.headers.get("origin")
    if not origin:
        return
    host = request.headers.get("host") or ""
    if urlparse(origin).netloc != host:
        raise HTTPException(status_code=403, detail="跨站请求被拒绝")
