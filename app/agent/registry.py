"""Agent 写操作确认注册表（跨线程：SSE 生成器线程等待，确认端点唤醒）。"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class PendingConfirm:
    event: threading.Event = field(default_factory=threading.Event)
    decision: str | None = None  # "allow" | "deny"
    tool: str = ""
    summary: str = ""
    created_at: float = field(default_factory=time.time)


class AgentRegistry:
    def __init__(self):
        self._pending: dict[str, PendingConfirm] = {}
        self._lock = threading.Lock()

    def register(self, request_id: str, pending: PendingConfirm) -> None:
        with self._lock:
            self._pending[request_id] = pending

    def resolve(self, request_id: str, decision: str) -> bool:
        """确认端点调用：记录决策并唤醒等待中的生成器线程。"""
        with self._lock:
            pend = self._pending.get(request_id)
            if pend is None:
                return False
            pend.decision = decision
            pend.event.set()
            return True

    def wait(self, request_id: str, timeout: float = 60.0) -> str:
        """生成器线程阻塞等待确认；超时或已被消费返回 deny。"""
        with self._lock:
            pend = self._pending.get(request_id)
            if pend is None:
                return "deny"
        pend.event.wait(timeout)
        with self._lock:
            self._pending.pop(request_id, None)
        return pend.decision or "deny"
