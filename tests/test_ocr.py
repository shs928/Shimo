"""本地 OCR 链路测试：入队、后台处理、预览状态、重建入队（mock 识别引擎）。

真实识别引擎（rapidocr/pypdfium2 重依赖）不在测试中加载——
OcrService.ocr_pdf 被 monkeypatch 为固定文本，其余（队列/状态/索引/预览）
全部走真实代码路径。
"""
from __future__ import annotations

import io

from fastapi.testclient import TestClient


def _blank_pdf() -> bytes:
    """合法但无文字层的 PDF（模拟扫描件）。"""
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.showPage()
    c.save()
    return buf.getvalue()


def _import_pdf(client: TestClient, name: str = "扫描件.pdf", dir: str = "") -> dict:
    from tests.test_rag import _import

    return _import(client, dir, name, _blank_pdf(), "application/pdf")


def test_import_scanned_pdf_enqueues_ocr(client: TestClient, monkeypatch):
    # 识别引擎 mock：真实导入链路其余部分不受影响
    from app.services.ocr import OcrService

    monkeypatch.setattr(OcrService, "ocr_pdf", lambda self, rel, vault: "识别文本 ocrword888")

    r = _import_pdf(client)
    assert r["parsed_chars"] == 0
    assert r["ocr_status"] == "pending"

    ocr = client.app.state.ocr_service
    assert ocr.status("扫描件.pdf")["status"] == "pending"


def test_process_next_ocrs_and_indexes(client: TestClient, monkeypatch):
    from app.services.ocr import OcrService

    monkeypatch.setattr(OcrService, "ocr_pdf", lambda self, rel, vault: "识别文本 ocrword888")
    _import_pdf(client)

    ocr = client.app.state.ocr_service
    rag = client.app.state.rag
    assert ocr.process_next(client.app.state.vault, rag) is True

    st = ocr.status("扫描件.pdf")
    assert st["status"] == "done"
    assert st["chars"] > 0

    # OCR 文本已进入 RAG chunks
    hits = rag.search("ocrword888", k=5)
    assert any(h["file_path"] == "扫描件.pdf" for h in hits)

    # 预览返回 OCR 文本
    r = client.get("/api/v1/documents/preview", params={"path": "扫描件.pdf"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["has_text"] is True
    assert data["ocr"] is True
    assert "ocrword888" in data["text"]


def test_preview_scanned_pdf_returns_pending_status(client: TestClient):
    """识别完成前：预览返回 has_text=false + ocr_status（不报错）。"""
    _import_pdf(client)
    r = client.get("/api/v1/documents/preview", params={"path": "扫描件.pdf"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["has_text"] is False
    assert data["ocr_status"] in ("pending", "running")


def test_process_next_no_pending(client: TestClient):
    ocr = client.app.state.ocr_service
    assert ocr.process_next(client.app.state.vault, client.app.state.rag) is False


def test_ocr_failure_is_recorded(client: TestClient, monkeypatch):
    from app.services.ocr import OcrService

    def boom(self, rel, vault):
        raise RuntimeError("识别引擎崩溃")

    monkeypatch.setattr(OcrService, "ocr_pdf", boom)
    _import_pdf(client)

    ocr = client.app.state.ocr_service
    ocr.process_next(client.app.state.vault, client.app.state.rag)
    st = ocr.status("扫描件.pdf")
    assert st["status"] == "failed"
    assert "识别引擎崩溃" in st["error"]

    # 预览返回 failed 状态与错误（不自动重置）
    r = client.get("/api/v1/documents/preview", params={"path": "扫描件.pdf"})
    data = r.json()
    assert data["ocr_status"] == "failed"
    assert "识别引擎崩溃" in data["ocr_error"]

    # 手动重试端点：failed → pending 重新入队
    r2 = client.post("/api/v1/documents/ocr-retry", params={"path": "扫描件.pdf"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "pending"
    r3 = client.get("/api/v1/documents/preview", params={"path": "扫描件.pdf"})
    assert r3.json()["ocr_status"] == "pending"


def test_ai_rebuild_enqueues_ocr_for_scanned(client: TestClient, monkeypatch):
    from app.services.ocr import OcrService

    monkeypatch.setattr(OcrService, "ocr_pdf", lambda self, rel, vault: "重建识别 rebuildocr777")
    _import_pdf(client)

    r = client.post("/api/v1/ai/rebuild")
    assert r.status_code == 200
    data = r.json()
    assert data["ocr_queued"] >= 1

    ocr = client.app.state.ocr_service
    assert ocr.status("扫描件.pdf")["status"] == "pending"
    ocr.process_next(client.app.state.vault, client.app.state.rag)

    rag = client.app.state.rag
    hits = rag.search("rebuildocr777", k=5)
    assert any(h["file_path"] == "扫描件.pdf" for h in hits)


def test_ocr_enabled_toggle_controls_job(client: TestClient):
    """关闭 OCR 开关后不处理队列；开启后恢复。"""
    _import_pdf(client)
    client.post("/api/v1/ai/config", json={"ocr": {"enabled": True}})
    s = client.app.state.ai_store.load()
    assert s.ocr_enabled is True

    client.post("/api/v1/ai/config", json={"ocr": {"enabled": False}})
    s2 = client.app.state.ai_store.load()
    assert s2.ocr_enabled is False
    # 队列保留，但 job 循环会跳过（这里只验证配置切换语义）
    assert client.app.state.ocr_service.pending(1) == ["扫描件.pdf"]
