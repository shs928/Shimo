"""索引健康：记录文件保存成功但索引失败的项，支持诊断与重试。

- 失败项按 (path, subsystem) 去重累计 attempts；成功后清除。
- /index/stats 返回最近失败；前端展示非阻塞警告与"重试失败项"。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..db import Database

logger = logging.getLogger(__name__)

_SUBSYSTEMS = ("index", "rag", "embedding")


class IndexHealth:
    def __init__(self, db: Database):
        self.db = db

    def record(self, path: str, subsystem: str, error: str) -> None:
        """记录一次索引失败（幂等累计 attempts）。"""
        if subsystem not in _SUBSYSTEMS:
            subsystem = "index"
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self.db.connect() as conn:
                conn.execute(
                    """INSERT INTO index_failures(path,subsystem,error,attempts,updated_at)
                       VALUES(?,?,?,1,?)
                       ON CONFLICT(path,subsystem) DO UPDATE SET
                         error=excluded.error, attempts=attempts+1, updated_at=excluded.updated_at""",
                    (path, subsystem, str(error)[:500], now),
                )
        except Exception:
            logger.warning("记录索引失败项出错", exc_info=True)

    def clear(self, path: str | None = None, subsystem: str | None = None) -> None:
        """清除失败项：path/subsystem 为空表示全清。"""
        try:
            with self.db.connect() as conn:
                if path and subsystem:
                    conn.execute("DELETE FROM index_failures WHERE path=? AND subsystem=?", (path, subsystem))
                elif path:
                    conn.execute("DELETE FROM index_failures WHERE path=?", (path,))
                elif subsystem:
                    conn.execute("DELETE FROM index_failures WHERE subsystem=?", (subsystem,))
                else:
                    conn.execute("DELETE FROM index_failures")
        except Exception:
            logger.warning("清除索引失败项出错", exc_info=True)

    def recent(self, limit: int = 20) -> list[dict]:
        try:
            rows = self.db.connect().execute(
                "SELECT path,subsystem,error,attempts,updated_at FROM index_failures ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def retry_failed(self, vault, indexer, rag) -> dict:
        """重试全部失败项；成功则清除记录。返回统计。"""
        from ..services.doc_parser import is_document, parse_document

        retried = 0
        cleared = 0
        still_failed = 0
        for item in self.recent(limit=500):
            path, subsystem = item["path"], item["subsystem"]
            retried += 1
            try:
                if subsystem == "rag":
                    if path.lower().endswith(".md"):
                        rag.reindex_file(path, vault.read_markdown(path).content)
                    elif is_document(path):
                        text = parse_document(path, (vault.root / path).read_bytes())
                        if text:
                            rag.reindex_file(path, text)
                elif subsystem == "embedding":
                    rag.reindex_file(path, vault.read_markdown(path).content)
                else:
                    if path.lower().endswith(".md"):
                        indexer.index_file(path)
                        rag.reindex_file(path, vault.read_markdown(path).content)
                self.clear(path, subsystem)
                cleared += 1
            except Exception as exc:
                self.record(path, subsystem, str(exc))
                still_failed += 1
        return {"retried": retried, "cleared": cleared, "still_failed": still_failed}
