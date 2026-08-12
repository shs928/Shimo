"""SSE 事件路由：GET /api/v1/events（文件树/文件变化实时推送）。"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from ..deps import require_auth

router = APIRouter(prefix="/api/v1", tags=["events"], dependencies=[Depends(require_auth)])

_HEARTBEAT_SECONDS = 15


@router.get("/events")
async def events(request: Request) -> StreamingResponse:
    hub = request.app.state.event_hub
    queue = hub.subscribe()

    async def event_stream():
        try:
            # 先发送连接确认帧（SSE 头立即下发，客户端可感知连接建立）
            yield ": connected\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_SECONDS)
                    yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"  # 心跳注释行（SSE 规范）
        finally:
            hub.unsubscribe(queue)

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
