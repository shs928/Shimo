"""RAG 检索：FTS 文件级召回 + 可选向量余弦精排。

无 embedding 配置时退化为 FTS-only（取命中文件的前若干块）。
"""
from __future__ import annotations

import json
import math
import sqlite3
import struct
from datetime import datetime, timezone
from pathlib import Path

from ..db import Database
from ..services.indexer import Indexer
from ..services.metadata import parse_frontmatter

_EXCLUDE_PREFIXES = (".trash/",)
_EMBED_SIG_KEY = "embedding_signature"


def _pack(v: list[float]) -> bytes:
    return struct.pack(f"{len(v)}f", *v)


def _unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class RagIndexer:
    """chunks / embeddings 维护：分块、批量嵌入、排除规则、清理。"""

    def __init__(self, vault_root: Path, db: Database):
        self.root = vault_root
        self.db = db

    def is_excluded(self, rel: str, text: str) -> bool:
        if any(rel.startswith(p) for p in _EXCLUDE_PREFIXES):
            return True
        if any(part.startswith(".") for part in Path(rel).parts):
            return True
        fm = parse_frontmatter(text)
        if fm.data.get("ai") is False or str(fm.data.get("ai", "")).lower() in ("false", "no"):
            return True
        return False

    def reindex_file(self, rel: str, text: str) -> int:
        """删除并重建该文件的 chunks；返回块数。"""
        from .chunker import chunk_markdown

        with self.db.connect() as conn:
            conn.execute("DELETE FROM embeddings WHERE chunk_id IN (SELECT id FROM chunks WHERE file_path=?)", (rel,))
            conn.execute("DELETE FROM chunks WHERE file_path=?", (rel,))
            if self.is_excluded(rel, text):
                return 0
            chunks = chunk_markdown(text)
            # 非 .md 文档不在 files_meta（Indexer 只索引 .md），补占位记录满足外键
            if not rel.lower().endswith(".md"):
                self._ensure_file_meta(conn, rel)
            conn.executemany(
                """INSERT INTO chunks(file_path,idx,heading,line_start,line_end,text,content_hash,ai_indexed)
                   VALUES(?,?,?,?,?,?,?,0)""",
                [
                    (rel, c.idx, c.heading, c.line_start, c.line_end, c.text,
                     _content_hash(c.text))
                    for c in chunks
                ],
            )
            return len(chunks)

    @staticmethod
    def _ensure_file_meta(conn: sqlite3.Connection, rel: str) -> None:
        """为不在 files_meta 中的文档（PDF/Word 等）插入占位元数据记录。"""
        row = conn.execute("SELECT path FROM files_meta WHERE path=?", (rel,)).fetchone()
        if row:
            return
        conn.execute(
            """INSERT INTO files_meta(path,title,mtime_ns,size,sha1,tags,indexed_at)
               VALUES(?,?,?,?,?,?,?)""",
            (rel, Path(rel).name, 0, 0, "doc", "", datetime.now(timezone.utc).isoformat()),
        )

    def delete_file(self, rel: str) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM embeddings WHERE chunk_id IN (SELECT id FROM chunks WHERE file_path=?)", (rel,))
            conn.execute("DELETE FROM chunks WHERE file_path=?", (rel,))

    def pending_chunks(self, batch: int = 32) -> list[tuple[int, str]]:
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT id,text FROM chunks WHERE ai_indexed=0 ORDER BY id LIMIT ?", (batch,)
        ).fetchall()
        return [(row[0], row[1]) for row in rows]

    def ensure_embedding_signature(self, provider_id: str, base_url: str, model: str) -> bool:
        """Embedding 模型签名（provider_id + base_url + model）变化时重置向量库。

        - 变化：事务内删除 embeddings、全部 chunks ai_indexed=0（重新嵌入），写回新签名。
        - 首次（无签名记录）：只写签名不重置（无历史向量可清）。
        - Key 变化不参与签名（不重建）。
        - 事务失败完整回滚。
        返回是否发生了重置。
        """
        if not model:
            return False
        sig = f"{provider_id}|{base_url}|{model}"
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT value FROM schema_meta WHERE key=?", (_EMBED_SIG_KEY,)
            ).fetchone()
            if row is not None and row[0] == sig:
                return False
            if row is not None:
                conn.execute("DELETE FROM embeddings")
                conn.execute("UPDATE chunks SET ai_indexed=0")
            conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key,value) VALUES(?,?)",
                (_EMBED_SIG_KEY, sig),
            )
        return row is not None

    def store_embeddings(self, chunk_ids: list[int], vectors: list[list[float]], model: str) -> None:
        """严格校验后写入向量；只标记成功写入的 chunk 为 indexed。

        - len(vectors) == len(chunk_ids) 才写入
        - 每个向量非空、维度一致
        - 校验失败抛 ValueError（worker 进入退避重试，绝不静默标完成）
        """
        if not chunk_ids or not vectors:
            return
        if len(vectors) != len(chunk_ids):
            raise ValueError(f"向量数量 {len(vectors)} 与块数量 {len(chunk_ids)} 不一致")
        dims = len(vectors[0])
        if dims == 0:
            raise ValueError("空向量")
        for vec in vectors:
            if len(vec) != dims:
                raise ValueError("向量维度不一致")
        with self.db.connect() as conn:
            for cid, vec in zip(chunk_ids, vectors):
                conn.execute(
                    "INSERT OR REPLACE INTO embeddings(chunk_id,model,dims,vector) VALUES(?,?,?,?)",
                    (cid, model, dims, _pack(vec)),
                )
            conn.executemany("UPDATE chunks SET ai_indexed=1 WHERE id=?", [(cid,) for cid in chunk_ids])

    def has_vectors(self) -> bool:
        return self.db.connect().execute("SELECT 1 FROM embeddings LIMIT 1").fetchone() is not None

    def count_chunks(self) -> int:
        return self.db.connect().execute("SELECT count(*) FROM chunks").fetchone()[0]

    def count_embedded(self) -> int:
        return self.db.connect().execute(
            "SELECT count(*) FROM chunks WHERE ai_indexed=1"
        ).fetchone()[0]

    def search(self, query: str, k: int = 5, embedding_cfg=None) -> list[dict]:
        """检索 Top-K 片段。

        embedding_cfg 提供时走向量精排；否则 FTS-only（命中文件前几块）。
        """
        query_embedding = None
        if embedding_cfg is not None:
            from .provider import embed_texts

            try:
                query_embedding = embed_texts(embedding_cfg, [query])[0]
            except Exception:
                query_embedding = None

        candidates: list[dict] = []
        if query_embedding is not None:
            conn = self.db.connect()
            row = conn.execute(
                "SELECT model,dims,vector FROM embeddings LIMIT 1"
            ).fetchone()
            if row is None:
                query_embedding = None
            elif row["dims"] != len(query_embedding):
                query_embedding = None

        if query_embedding is not None:
            conn = self.db.connect()
            rows = conn.execute(
                """SELECT c.file_path,c.heading,c.line_start,c.line_end,c.text,e.vector
                   FROM chunks c JOIN embeddings e ON e.chunk_id=c.id"""
            ).fetchall()
            scored = [(dict(r) | {"score": _cosine(query_embedding, _unpack(r["vector"]))}) for r in rows]
            scored.sort(key=lambda x: x["score"], reverse=True)
            candidates = scored[:k]
        else:
            # FTS-only 降级：命中文件取前 3 块；非 .md 文档块按 LIKE 补充
            indexer = Indexer(self.root, self.db)
            hits = indexer.search(query, limit=8)
            conn = self.db.connect()
            for hit in hits:
                rows = conn.execute(
                    """SELECT file_path,heading,line_start,line_end,text
                       FROM chunks WHERE file_path=? ORDER BY idx LIMIT 3""",
                    (hit["path"],),
                ).fetchall()
                for r in rows:
                    candidates.append(dict(r) | {"score": 0.0})
                if len(candidates) >= k:
                    break
            if len(candidates) < k:
                like = f"%{query}%"
                rows = conn.execute(
                    """SELECT file_path,heading,line_start,line_end,text
                       FROM chunks
                       WHERE file_path NOT LIKE '%.md' AND text LIKE ? ESCAPE '\\'
                       ORDER BY id LIMIT ?""",
                    (like, k - len(candidates)),
                ).fetchall()
                seen = {c["file_path"] for c in candidates}
                for r in rows:
                    if r["file_path"] in seen:
                        continue
                    candidates.append(dict(r) | {"score": 0.0})
                    seen.add(r["file_path"])
            candidates = candidates[:k]
        return candidates


def _content_hash(text: str) -> str:
    import hashlib

    return hashlib.sha1(text.encode("utf-8")).hexdigest()
