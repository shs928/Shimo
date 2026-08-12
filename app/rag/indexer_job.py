"""后台嵌入任务：消费 ai_indexed=0 的 chunk 队列，增量生成向量。

- 在 lifespan 启动时创建并启动；写路径产生的新 chunk 自动进入待嵌入队列。
- 外呼闸门：AI 关闭时 get_job_config() 返回 None → 不发新批次；
  已发出的同步 HTTP 批次自然结束（不取消）。
- Embedding 模型签名变化（provider/base_url/model）时重置向量库重新嵌入。
- 批次严格校验：向量数与块数一致、维度一致才写入；失败状态可重试，
  绝不静默标完成。
- 暴露 last_error / backoff 供诊断接口观测。
"""
from __future__ import annotations

import asyncio
import logging
import time

from ..db import Database
from ..rag.provider import ProviderConfig, embed_texts
from ..rag.retriever import RagIndexer

logger = logging.getLogger(__name__)


class EmbeddingJob:
    def __init__(self, db: Database, rag: RagIndexer, get_job_config):
        self.db = db
        self.rag = rag
        self.get_job_config = get_job_config  # () -> (ProviderConfig, int) | None
        self._running = False
        self._task: asyncio.Task | None = None
        self.last_error: str | None = None
        self.backoff_seconds: float = 0.0
        self.embedded_total: int = 0

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
            job = self.get_job_config()
            if job is None:
                await asyncio.sleep(5)
                continue
            cfg, batch = job
            # 模型签名比对：变化 → 重置向量库（覆盖环境变量注入的变化）
            try:
                if self.rag.ensure_embedding_signature(cfg.provider_id, cfg.base_url, cfg.model):
                    logger.info("Embedding 模型变更，重置向量库，后台重新嵌入")
            except Exception as exc:
                logger.warning("Embedding 签名检查失败：%s", exc)

            pending = self.rag.pending_chunks(batch)
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
                self.last_error = None
                self.backoff_seconds = 0.0
                self.embedded_total += len(pending)
            except Exception as exc:
                # 单批失败不中断；指数退避后重试，避免打爆上游
                self.last_error = str(exc)
                self.backoff_seconds = backoff
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
            "last_error": self.last_error,
            "backoff_seconds": self.backoff_seconds,
        }
