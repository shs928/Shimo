"""OCR 后台任务：消费 doc_ocr 待识别队列（一次一个，CPU 密集）。

- 由 lifespan 启动/停止；AI 全局开关不影响（OCR 为本地计算，不外呼）。
- 仅受 AiSettings.ocr.enabled 控制（默认开启）。
"""
from __future__ import annotations

import asyncio


class OcrJob:
    def __init__(self, ocr_service, vault, rag, settings_provider):
        self.ocr_service = ocr_service
        self.vault = vault
        self.rag = rag
        self.settings_provider = settings_provider  # () -> AiSettings
        self._running = False
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._task is not None:
            return
        loop = asyncio.get_event_loop()
        if not loop.is_running():
            return
        self._running = True
        self._task = loop.create_task(self._loop())

    def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        while self._running:
            settings = self.settings_provider()
            if not settings.ocr_enabled:
                await asyncio.sleep(5)
                continue
            try:
                processed = await asyncio.to_thread(
                    self.ocr_service.process_next, self.vault, self.rag
                )
                if not processed:
                    await asyncio.sleep(3)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(5)
