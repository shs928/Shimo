"""URL 导入测试：SSRF 防护、HTML 转 Markdown 入索引、PDF 链接、大小/协议限制。

FakeClient 仿 tests/test_safe_download.py：monkeypatch httpx.Client，
URL 主机用字面公网 IP（1.1.1.1）避免 DNS 依赖，SSRF 拦截走重定向跳转验证。
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from app.services.safe_download import _check_url

_HTML = """<!doctype html><html><head><title>测试文章标题</title></head>
<body><article><h1>AI 知识库实践</h1>
<p>这是 urlimporttest 正文段落，介绍如何把网页导入个人知识库。</p>
<p>第二段补充检索与分块说明。</p></article></body></html>"""


def _post(client: TestClient, url: str, dir: str = ""):
    q = f"?dir={dir}" if dir else ""
    return client.post(f"/api/v1/import-url{q}", json={"url": url})


def _patch_fetch(monkeypatch, responses: list):
    """responses: [(status_code, headers, body_bytes), ...] 依次返回。"""
    import httpx

    calls: list[str] = []

    class FakeResp:
        def __init__(self, status, headers, body):
            self.status_code = status
            self.headers = headers
            self._body = body

        def iter_bytes(self, *a, **k):
            return iter([self._body])

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            calls.append(url)
            idx = min(len(calls) - 1, len(responses) - 1)
            return FakeResp(*responses[idx])

    monkeypatch.setattr(httpx, "Client", FakeClient)
    return calls


def test_html_import_saves_markdown_and_indexes(client: TestClient, monkeypatch):
    _patch_fetch(
        monkeypatch,
        [(200, {"content-type": "text/html; charset=utf-8"}, _HTML.encode("utf-8"))],
    )
    r = _post(client, "https://1.1.1.1/blog/ai-kb")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["path"] == "ai-kb.md"
    # trafilatura metadata 优先取正文 h1 作为文章标题（比 <title> 更能代表文章）
    assert data["title"] == "AI 知识库实践"
    assert data["parsed_chars"] > 0
    assert data["source_url"] == "https://1.1.1.1/blog/ai-kb"

    # vault 落盘 .md，且带 source frontmatter 溯源
    fc = client.app.state.vault.read_markdown("ai-kb.md")
    assert "source: https://1.1.1.1/blog/ai-kb" in fc.content
    assert "urlimporttest" in fc.content

    # 分块进入 RAG，FTS 可检索
    hits = client.app.state.rag.search("urlimporttest", k=5)
    assert any(h["file_path"] == "ai-kb.md" for h in hits)


def test_html_import_into_dir(client: TestClient, monkeypatch):
    _patch_fetch(
        monkeypatch,
        [(200, {"content-type": "text/html"}, _HTML.encode("utf-8"))],
    )
    r = _post(client, "https://1.1.1.1/notes/intro", dir="docs")
    assert r.status_code == 200, r.text
    assert r.json()["path"] == "docs/intro.md"


def test_html_import_no_body_rejected(client: TestClient, monkeypatch):
    empty_html = "<html><head><title>x</title></head><body><script>console.log(1)</script></body></html>"
    _patch_fetch(
        monkeypatch, [(200, {"content-type": "text/html"}, empty_html.encode("utf-8"))]
    )
    r = _post(client, "https://1.1.1.1/empty")
    assert r.status_code == 400
    assert "无法" in r.text


def test_pdf_link_import_parses(client: TestClient, monkeypatch):
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 500, "PDF from url with keyword zhinengurl")
    c.save()
    pdf_bytes = buf.getvalue()

    _patch_fetch(monkeypatch, [(200, {"content-type": "application/pdf"}, pdf_bytes)])
    r = _post(client, "https://1.1.1.1/files/manual.pdf")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["path"] == "manual.pdf"
    assert data["parsed_chars"] > 0

    hits = client.app.state.rag.search("zhinengurl", k=5)
    assert any(h["file_path"] == "manual.pdf" for h in hits)


def test_url_import_rejects_private_redirect(client: TestClient, monkeypatch):
    _patch_fetch(
        monkeypatch,
        [(302, {"location": "http://127.0.0.1:8080/steal"}, b""), (200, {}, b"")],
    )
    r = _post(client, "https://1.1.1.1/redirect")
    assert r.status_code == 400


def test_url_import_rejects_non_http():
    with pytest.raises(Exception, match="仅支持 http"):
        _check_url("file:///etc/passwd")


def test_url_import_size_limit(client: TestClient, monkeypatch):
    big = b"<html><body><p>" + b"x" * (2 * 1024 * 1024) + b"</p></body></html>"
    _patch_fetch(monkeypatch, [(200, {"content-type": "text/html"}, big)])
    r = _post(client, "https://1.1.1.1/big")
    assert r.status_code == 400
    assert "上限" in r.text
