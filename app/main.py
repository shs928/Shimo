"""FastAPI 应用入口。

- 生命周期内初始化 Vault、SQLite 与会话。
- 托管前端构建产物（frontend/dist），不存在时给出提示。
- 统一映射 Vault 业务异常到 HTTP 状态码。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .agent.mcp import McpManager
from .agent.registry import AgentRegistry
from .agent.session import AgentSessionStore
from .auth import AuthError, AuthStore
from .config import Config, load_config
from .db import Database
from .rag.ai_store import AiStore
from .rag.indexer_job import EmbeddingJob
from .rag.retriever import RagIndexer
from .routers import agent as agent_router
from .routers import auth as auth_router
from .routers import attachments as attachments_router
from .routers import chat as chat_router
from .routers import files as files_router
from .routers import imports as imports_router
from .routers import knowledge as knowledge_router
from .routers import metadata as metadata_router
from .services.indexer import Indexer
from .services.path_guard import PathError
from .services.vault import (
    ConflictError,
    NotFoundError,
    UnsupportedEncodingError,
    Vault,
    VaultError,
)

_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


def _json_error(status: int):
    async def handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=status, content={"detail": str(exc)})

    return handler


def create_app(config: Config | None = None) -> FastAPI:
    config = config or load_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        vault = Vault(config.vault_path)
        vault.ensure_initialized()

        db = Database(config.index_db)
        db.initialize()

        auth = AuthStore(config.config_file, db)
        if config.initial_password and not auth.initialized:
            auth.set_password(config.initial_password)

        indexer = Indexer(vault, db)
        # 首次启动或 schema 版本变化时同步重建；小库足够快。
        if db.needs_rebuild:
            indexer.rebuild()

        ai_store = AiStore(config.data_path / "ai.json")
        rag = RagIndexer(config.vault_path, db)
        agent_sessions = AgentSessionStore(config.data_path / "ai_agent_sessions.json")
        agent_registry = AgentRegistry()
        mcp_manager = McpManager()
        embedding_job = EmbeddingJob(db, rag, lambda: ai_store.load().embedding_config())

        app.state.config = config
        app.state.vault = vault
        app.state.db = db
        app.state.auth = auth
        app.state.indexer = indexer
        app.state.ai_store = ai_store
        app.state.rag = rag
        app.state.agent_sessions = agent_sessions
        app.state.agent_registry = agent_registry
        app.state.mcp_manager = mcp_manager
        app.state.embedding_job = embedding_job
        app.state.chat_sessions = {}
        embedding_job.start()
        yield
        embedding_job.stop()

    app = FastAPI(title="KnowledgeBase", version="0.1.0", lifespan=lifespan)

    # 异常 -> HTTP 状态码
    app.add_exception_handler(PathError, _json_error(422))
    app.add_exception_handler(NotFoundError, _json_error(404))
    app.add_exception_handler(ConflictError, _json_error(412))
    app.add_exception_handler(UnsupportedEncodingError, _json_error(409))
    app.add_exception_handler(VaultError, _json_error(400))
    app.add_exception_handler(AuthError, _json_error(400))

    # 安全响应头
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; "
            "base-uri 'self'; frame-ancestors 'none'"
        )
        return response

    app.include_router(auth_router.router)
    app.include_router(files_router.router)
    app.include_router(imports_router.router)
    app.include_router(attachments_router.router)
    app.include_router(metadata_router.router)
    app.include_router(knowledge_router.router)
    app.include_router(chat_router.router)
    app.include_router(agent_router.router)

    # 健康检查
    @app.get("/health/live")
    def live() -> dict:
        return {"status": "ok"}

    # 前端静态托管（SPA fallback）
    if _FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=_FRONTEND_DIST, html=True), name="frontend")
    else:
        @app.get("/")
        def no_frontend() -> JSONResponse:
            return JSONResponse(
                status_code=200,
                content={
                    "message": "后端已启动，但尚未构建前端。请执行 npm run build（frontend/ 下）后重启。",
                },
            )

    return app
