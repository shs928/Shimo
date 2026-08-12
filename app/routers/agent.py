"""Agent 路由：SSE 对话、写操作确认、会话管理、工具与 MCP 状态。

- POST /ai/agent/chat：SSE 流式（delta / tool_call / confirm / tool_result / done / error）
- POST /ai/agent/confirm：确认卡片回执（allow | deny）
- 会话：GET/POST/DELETE /ai/agent/session[/{id}]，GET /ai/agent/sessions
- GET /ai/agent/lsTools、GET /ai/agent/system-prompt、GET /ai/mcp/status
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..agent.engine import stream_agent
from ..agent.registry import PendingConfirm
from ..agent.tools import TOOL_NAMES, TOOL_SCHEMAS, ToolContext, WRITE_TOOLS
from ..deps import csrf_guard, get_indexer, get_vault, require_auth

router = APIRouter(prefix="/api/v1/ai/agent", tags=["agent"], dependencies=[Depends(require_auth)])
_write_deps = [Depends(csrf_guard)]


class AgentChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    session_id: str | None = None


class ConfirmIn(BaseModel):
    request_id: str = Field(min_length=1)
    decision: str = Field(pattern="^(allow|deny)$")


class SessionSaveIn(BaseModel):
    session_id: str | None = None
    title: str | None = None
    messages: list = Field(default_factory=list)


def _ctx(request: Request) -> ToolContext:
    settings_provider = lambda: request.app.state.ai_store.load()
    return ToolContext(
        vault=request.app.state.vault,
        indexer=request.app.state.indexer,
        rag=request.app.state.rag,
        settings_provider=settings_provider,
        registry=request.app.state.agent_registry,
        mcp_manager=request.app.state.mcp_manager,
        ai_store=request.app.state.ai_store,
    )


def _sse(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


@router.post("/chat")
def agent_chat(payload: AgentChatIn, request: Request) -> StreamingResponse:
    s = request.app.state.ai_store.load()
    if not s.enabled:
        return StreamingResponse(iter([_sse({"type": "error", "error": "AI 未启用，请先配置并开启"})]),
                                 media_type="text/event-stream")
    if s.agent_config() is None:
        return StreamingResponse(iter([_sse({"type": "error", "error": "未配置 Agent 模型"})]),
                                 media_type="text/event-stream")

    ctx = _ctx(request)
    sessions = request.app.state.agent_sessions
    history = []
    if payload.session_id:
        data = sessions.get(payload.session_id)
        if data:
            history = list(data.get("messages") or [])

    messages = [{"role": "system", "content": s.agent_prompt()}, *history, {"role": "user", "content": payload.message}]

    def event_stream():
        seen_error = False
        full = []
        try:
            for ev in stream_agent(ctx, s, messages, max_iterations=s.agent.max_iterations,
                                   temperature=s.chat.temperature, max_tokens=s.chat.max_tokens):
                if ev["type"] == "delta":
                    full.append(ev["content"])
                elif ev["type"] == "error":
                    seen_error = True
                yield _sse(ev)
        finally:
            # 保存会话：用户消息 + 完整助手正文（跳过工具中间产物，模型上下文由历史保留）
            if payload.session_id and full:
                sessions.save(payload.session_id, [*history, {"role": "user", "content": payload.message},
                                                   {"role": "assistant", "content": "".join(full)}])
        if not seen_error:
            yield _sse({"type": "done"})

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/confirm", dependencies=_write_deps)
def confirm(payload: ConfirmIn, request: Request) -> dict:
    ok = request.app.state.agent_registry.resolve(payload.request_id, payload.decision)
    if not ok:
        return {"ok": False, "message": "确认请求不存在或已过期"}
    return {"ok": True}


@router.get("/sessions")
def ls_sessions(request: Request) -> dict:
    return {"sessions": request.app.state.agent_sessions.ls()}


@router.get("/session/{session_id}")
def get_session(session_id: str, request: Request) -> dict:
    data = request.app.state.agent_sessions.get(session_id)
    if data is None:
        return {"session": None}
    return {"session": data}


@router.post("/session", dependencies=_write_deps)
def save_session(payload: SessionSaveIn, request: Request) -> dict:
    saved = request.app.state.agent_sessions.save(payload.session_id or "", payload.messages, payload.title)
    return {"session": saved}


@router.delete("/session/{session_id}", dependencies=_write_deps)
def remove_session(session_id: str, request: Request) -> dict:
    ok = request.app.state.agent_sessions.remove(session_id)
    return {"ok": ok}


@router.get("/lsTools")
def ls_tools(request: Request) -> dict:
    s = request.app.state.ai_store.load()
    ctx = _ctx(request)
    tools = []
    for schema in TOOL_SCHEMAS:
        name = schema["function"]["name"]
        tools.append({
            "name": name,
            "description": schema["function"]["description"],
            "enabled": s.tool_enabled(name),
            "write": name in WRITE_TOOLS,
        })
    for entry in ctx.mcp_manager.tool_entries():
        tools.append({
            "name": entry["name"],
            "description": entry["description"],
            "enabled": True,
            "write": False,
        })
    return {"tools": tools}


@router.get("/system-prompt")
def get_system_prompt(request: Request) -> dict:
    s = request.app.state.ai_store.load()
    return {"system_prompt": s.agent_prompt()}


@router.get("/mcp/status")
def mcp_status(request: Request) -> dict:
    s = request.app.state.ai_store.load()
    request.app.state.mcp_manager.configure(s.mcp_servers)
    return {"servers": request.app.state.mcp_manager.status()}
