"""事件总线：每客户端 asyncio queue 的 EventHub（SSE 推送）。

事件：
- {"type": "tree_changed"}                    普通目录树变化（外部创建/删除/移动）
- {"type": "file_changed", "path": rel}       普通文件内容变化（外部修改）
- {"type": "templates_changed"}               模板目录变化（不进入普通目录树/索引）

应用内写入产生的重复事件由 Watcher 的自身写入窗口抑制。
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

_MAX_QUEUE = 64


class EventHub:
    def __init__(self):
        self._subscribers: list[asyncio.Queue] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    def subscribe(self) -> asyncio.Queue:
        """在事件循环上下文内订阅（SSE 端点调用）。"""
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        q: asyncio.Queue = asyncio.Queue(maxsize=_MAX_QUEUE)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    def publish(self, event: dict) -> None:
        """向所有订阅者广播（线程安全）。

        慢消费者丢最旧事件（SSE 场景可接受）。
        """
        for q in list(self._subscribers):
            try:
                if q.full():
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                if self._loop is not None and self._loop.is_running():
                    # 跨线程投递（如测试/后台线程 publish）
                    self._loop.call_soon_threadsafe(q.put_nowait, event)
                else:
                    q.put_nowait(event)
            except Exception:
                logger.debug("事件推送失败", exc_info=True)

    def subscriber_count(self) -> int:
        return len(self._subscribers)
