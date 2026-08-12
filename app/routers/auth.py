"""认证与初始化路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from ..auth import SESSION_COOKIE, AuthError, AuthStore
from ..deps import get_auth

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class PasswordIn(BaseModel):
    password: str = Field(min_length=1, max_length=256)


def _cookie(response: Response, token: str, secure: bool) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=30 * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def _secure(request: Request) -> bool:
    return request.url.scheme == "https"


@router.get("/status")
def status(request: Request, auth: AuthStore = Depends(get_auth)) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    return {
        "initialized": auth.initialized,
        "authenticated": auth.validate_session(token),
    }


@router.post("/init")
def init(
    payload: PasswordIn,
    request: Request,
    response: Response,
    auth: AuthStore = Depends(get_auth),
) -> dict:
    if auth.initialized:
        raise AuthError("已初始化，请直接登录")
    auth.set_password(payload.password)
    token = auth.create_session()
    _cookie(response, token, _secure(request))
    return {"ok": True}


@router.post("/login")
def login(
    payload: PasswordIn,
    request: Request,
    response: Response,
    auth: AuthStore = Depends(get_auth),
) -> dict:
    if not auth.initialized:
        raise AuthError("尚未初始化，请先设置访问密码")
    if not auth.verify_password(payload.password):
        raise AuthError("密码错误")
    token = auth.create_session()
    _cookie(response, token, _secure(request))
    return {"ok": True}


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    auth: AuthStore = Depends(get_auth),
) -> dict:
    auth.revoke_session(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}
