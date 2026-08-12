"""Vault 外部变化监听：watchfiles 后台线程 + 500ms 去抖 + 批量增量索引。

- 监听 Vault 目录（排除 .trash/ 与隐藏目录）。
- 变化去抖后批量处理：.md → 普通索引 + RAG 重分块；文档 → RAG 重分块。
- 广播 tree_changed / file_changed 事件（供前端自动刷新）。
- 与应用内写入幂等：最近 2s 内应用写入过的路径跳过（避免重复风暴）。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_SELF_WRITE_WINDOW = 2.0  # 应用内写入后的抑制窗口（秒）


class VaultWatcher:
    def __init__(self, vault, indexer, rag, hub, poll_interval: int = 500):
        self.vault = vault
        self.indexer = indexer
        self.rag = rag
        self.hub = hub
        self._poll_interval = poll_interval
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._self_writes: dict[str, float] = {}  # path -> 应用内写入时间戳

    # ---------- 生命周期 ----------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="vault-watcher")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    # ---------- 应用内写入标记（幂等抑制） ----------

    def mark_self_write(self, rel: str) -> None:
        """应用内写入路径登记：watcher 在窗口期内跳过该路径。"""
        now = time.monotonic()
        self._self_writes[rel] = now
        # 顺带清理过期条目，避免无限增长
        if len(self._self_writes) > 512:
            self._self_writes = {k: v for k, v in self._self_writes.items() if now - v < _SELF_WRITE_WINDOW * 4}

    # ---------- 监听循环 ----------

    def _run(self) -> None:
        try:
            from watchfiles import watch

            root = str(self.vault.root)
            for changes in watch(
                root,
                stop_event=self._stop,
                debounce=500,
                step=self._poll_interval,
                watch_filter=None,
                rust_timeout=1000,
            ):
                if self._stop.is_set():
                    break
                try:
                    self._handle_changes(changes)
                except Exception:
                    logger.warning("watcher 处理变化失败", exc_info=True)
        except Exception:
            logger.warning("watchfiles 不可用或监听失败", exc_info=True)

    def _handle_changes(self, changes) -> None:
        """批量处理变化：去重路径 → 增量索引 → 广播事件。"""
        now = time.monotonic()
        paths: dict[str, str] = {}  # rel -> 事件类型（modified/added/deleted）
        for change, path_str in changes:
            rel = self._to_rel(path_str)
            if not rel:
                continue
            # 保留最新事件类型
            paths[rel] = {"modified": "modified", "added": "added", "deleted": "deleted"}.get(change, "modified")

        tree_changed = False
        for rel, kind in paths.items():
            if kind == "deleted":
                self._handle_deleted(rel)
                tree_changed = True
                continue
            # 应用内写入窗口：跳过（自身写入已由写路径索引）
            if now - self._self_writes.get(rel, 0.0) < _SELF_WRITE_WINDOW:
                continue
            if self._handle_upsert(rel):
                tree_changed = True

        if tree_changed:
            self.hub.publish({"type": "tree_changed"})

    def _to_rel(self, path_str: str) -> str | None:
        """绝对路径 → Vault 相对路径；越界/隐藏/回收站返回 None。"""
        try:
            rel = Path(path_str).resolve().relative_to(self.vault.root.resolve()).as_posix()
        except (ValueError, OSError):
            return None
        if not rel or rel.startswith(".trash"):
            return None
        if any(part.startswith(".") for part in Path(rel).parts):
            return None
        return rel

    def _handle_upsert(self, rel: str) -> bool:
        """文件新增/修改：增量索引（.md → 普通索引 + RAG；文档 → RAG）。"""
        from ..services.doc_parser import is_document, parse_document

        full = self.vault.root / rel
        if not full.is_file():
            return True  # 目录变化也算树变化
        if rel.lower().endswith(".md"):
            try:
                fc = self.vault.read_markdown(rel)
                self.indexer.index_file(rel)
                self.rag.reindex_file(rel, fc.content)
            except Exception as exc:
                logger.warning("watcher 索引失败 %s: %s", rel, exc)
                return True
        elif is_document(rel):
            try:
                text = parse_document(rel, full.read_bytes())
                if text:
                    self.rag.reindex_file(rel, text)
            except Exception:
                pass
        return True

    def _handle_deleted(self, rel: str) -> None:
        try:
            self.indexer.delete_path(rel)
            self.rag.delete_file(rel)
        except Exception as exc:
            logger.warning("watcher 删除索引失败 %s: %s", rel, exc)
