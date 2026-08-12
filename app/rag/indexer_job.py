"""后台嵌入任务：消费 ai_indexed=0 的 chunk 队列，增量生成向量。

在 lifespan 启动时创建并启动；写路径（files.py 已调用 rag.reindex_file）
产生的新 chunk 自动进入待嵌入队列，无需额外挂钩。未配置 embedding 时静默等待。
"""
from __future__ import annotations

import asyncio

from ..db import Database
from ..rag.provider import ProviderConfig, embed_texts
from ..rag.retriever import RagIndexer


class EmbeddingJob:
    def __init__(self, db: Database, rag: RagIndexer, get_embedding_cfg):
        self.db = db
        self.rag = rag
        self.get_embedding_cfg = get_embedding_cfg  # () -> ProviderConfig | None
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
        backoff = 1
        while self._running:
            cfg = self.get_embedding_cfg()
            if cfg is None:
                await asyncio.sleep(5)
                continue
            pending = self.rag.pending_chunks(32)
            if not pending:
                backoff = 1
                await asyncio.sleep(5)
                continue
            try:
                vectors = await asyncio.to_thread(embed_texts, cfg, [t for _, t in pending])
                await asyncio.to_thread(
                    self.rag.store_embeddings, [cid for cid, _ in pending], vectors, cfg.model
                )
                backoff = 1
            except Exception:
                # 单批失败不中断；指数退避后重试，避免打爆上游
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def stats(self) -> dict:
        total = self.rag.count_chunks()
        embedded = self.rag.count_embedded()
        return {
            "running": self._running,
            "pending": max(0, total - embedded),
            "embedded": embedded,
            "total": total,
        }
