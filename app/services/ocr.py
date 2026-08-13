"""本地 OCR 服务：扫描件 PDF（无文字层）→ 文本。

- 渲染：pypdfium2（pdfium 内嵌，免外部二进制），约 200 DPI 逐页渲染。
- 识别：rapidocr_onnxruntime（内置 PP-OCR 模型，离线、中文优先），懒加载单例。
- 结果存 doc_ocr 表（index.db），识别完成后文本同时进入 RAG chunks 索引。
- 队列由 app/rag/ocr_job.py 后台消费，一次一个文件（CPU 密集 + 引擎非线程安全）。
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_DPI = 200  # 渲染分辨率（识别质量与耗时平衡）


class OcrService:
    def __init__(self, db, rag):
        self.db = db
        self.rag = rag
        self._engine = None
        self._engine_lock = threading.Lock()

    # ---------- 引擎（懒加载单例） ----------

    def get_engine(self):
        if self._engine is None:
            with self._engine_lock:
                if self._engine is None:
                    from rapidocr_onnxruntime import RapidOCR

                    self._engine = RapidOCR()
        return self._engine

    # ---------- 队列 / 状态 ----------

    def ensure_job(self, rel: str) -> str:
        """确保存在 OCR 任务；failed 状态重置为 pending（用户再次打开可重试）。

        返回当前（重置后）状态。
        """
        now = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            self._ensure_placeholder(conn, rel)
            row = conn.execute("SELECT status FROM doc_ocr WHERE file_path=?", (rel,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO doc_ocr(file_path,status,updated_at) VALUES(?,?,?)",
                    (rel, "pending", now),
                )
                return "pending"
            if row["status"] == "failed":
                conn.execute(
                    "UPDATE doc_ocr SET status='pending',progress=0,error='',updated_at=? WHERE file_path=?",
                    (now, rel),
                )
                return "pending"
            return row["status"]

    def pending(self, limit: int = 1) -> list[str]:
        rows = self.db.connect().execute(
            "SELECT file_path FROM doc_ocr WHERE status='pending' ORDER BY updated_at LIMIT ?",
            (limit,),
        ).fetchall()
        return [r["file_path"] for r in rows]

    def status(self, rel: str) -> dict | None:
        row = self.db.connect().execute(
            "SELECT status,progress,chars,error,updated_at FROM doc_ocr WHERE file_path=?", (rel,)
        ).fetchone()
        return dict(row) if row else None

    def text(self, rel: str) -> str | None:
        """已完成的 OCR 文本；未完成/失败返回 None。"""
        row = self.db.connect().execute(
            "SELECT status,text FROM doc_ocr WHERE file_path=?", (rel,)
        ).fetchone()
        if row is None or row["status"] != "done":
            return None
        return row["text"] or ""

    def _update(self, rel: str, **fields) -> None:
        keys = list(fields)
        sql = "UPDATE doc_ocr SET " + ",".join(f"{k}=?" for k in keys) + " WHERE file_path=?"
        with self.db.connect() as conn:
            conn.execute(sql, [fields[k] for k in keys] + [rel])

    def mark_running(self, rel: str) -> None:
        self._update(rel, status="running", progress=0, error="",
                     updated_at=datetime.now(timezone.utc).isoformat())

    def mark_progress(self, rel: str, pct: int) -> None:
        self._update(rel, progress=max(0, min(100, pct)),
                     updated_at=datetime.now(timezone.utc).isoformat())

    def mark_done(self, rel: str, text: str) -> None:
        self._update(rel, status="done", progress=100, chars=len(text), text=text, error="",
                     updated_at=datetime.now(timezone.utc).isoformat())

    def mark_failed(self, rel: str, error: str) -> None:
        self._update(rel, status="failed", error=str(error)[:300],
                     updated_at=datetime.now(timezone.utc).isoformat())

    # ---------- 识别 ----------

    def ocr_pdf(self, rel: str, vault) -> str:
        """逐页渲染并识别，返回全文；页间以空行分隔。"""
        import numpy as np
        import pypdfium2 as pdfium

        full = vault.root / rel
        engine = self.get_engine()
        pdf = pdfium.PdfDocument(str(full))
        total = len(pdf)
        parts: list[str] = []
        try:
            for i, page in enumerate(pdf):
                bitmap = page.render(scale=_DPI / 72)
                arr = np.asarray(bitmap.to_pil())
                result, _ = engine(arr)
                if result:
                    for item in result:
                        # 结构：[[box], text(str), score(str)]
                        text = item[1] if len(item) >= 2 else ""
                        if text and str(text).strip():
                            parts.append(str(text).strip())
                if total > 1 and (i + 1) % 3 == 0:
                    self.mark_progress(rel, int((i + 1) * 100 / total))
        finally:
            pdf.close()
        return "\n".join(parts)

    # ---------- 索引集成 ----------

    def text_for_index(self, vault, rel: str) -> str | None:
        """文档可入索引文本：解析文本优先；扫描件 PDF 用 OCR 结果并确保任务存在。

        返回 None 表示当前没有可用文本（OCR 任务已入队，完成后会自行重建索引）。
        """
        from .doc_parser import parse_document

        try:
            data = (vault.root / rel).read_bytes()
            text = parse_document(rel, data)
            if text.strip():
                return text
        except Exception:
            pass
        if rel.lower().endswith(".pdf"):
            stored = self.text(rel)
            if stored:
                return stored
            self.ensure_job(rel)
        return None

    def process_next(self, vault, rag) -> bool:
        """处理一个待识别任务（后台循环与测试共用）；返回是否有任务被处理。"""
        rels = self.pending(1)
        if not rels:
            return False
        rel = rels[0]
        self.mark_running(rel)
        try:
            text = self.ocr_pdf(rel, vault)
            if not text.strip():
                self.mark_failed(rel, "未能识别出任何文字（可能是空白页或纯图片且无文本）")
                return True
            self.mark_done(rel, text)
            # OCR 文本进入 RAG 索引；嵌入由 EmbeddingJob 后续消费
            rag.reindex_file(rel, text)
        except Exception as exc:
            logger.warning("OCR 失败 %s: %s", rel, exc)
            self.mark_failed(rel, str(exc))
        return True

    # ---------- 工具 ----------

    def _ensure_placeholder(self, conn, rel: str) -> None:
        """doc_ocr 外键依赖 files_meta；文档占位行不存在时补齐（幂等）。"""
        row = conn.execute("SELECT path FROM files_meta WHERE path=?", (rel,)).fetchone()
        if row:
            return
        conn.execute(
            """INSERT INTO files_meta(path,title,mtime_ns,size,sha1,tags,indexed_at)
               VALUES(?,?,?,?,?,?,?)""",
            (rel, Path(rel).name, 0, 0, "doc", "", datetime.now(timezone.utc).isoformat()),
        )
