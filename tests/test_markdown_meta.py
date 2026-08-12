"""附件上传/预览与元信息/大纲 API 测试。"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_upload_attachment_returns_relative_path(client: TestClient):
    r = client.post(
        "/api/v1/attachments",
        files={"file": ("diagram.png", b"\x89PNG\r\n\x1a\nfakepng", "image/png")},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["relative_path"].startswith("assets/diagram")
    assert data["relative_path"].endswith(".png")
    assert data["url"].startswith("/api/v1/raw/")

    # 通过 raw 接口取回
    raw = client.get(data["url"])
    assert raw.status_code == 200
    assert raw.content == b"\x89PNG\r\n\x1a\nfakepng"
    assert raw.headers["X-Content-Type-Options"] == "nosniff"


def test_upload_rejects_bad_extension(client: TestClient):
    r = client.post(
        "/api/v1/attachments",
        files={"file": ("virus.exe", b"MZ", "application/octet-stream")},
    )
    assert r.status_code == 400


def test_upload_requires_auth(client: TestClient):
    client.post("/api/v1/auth/logout")
    r = client.post("/api/v1/attachments", files={"file": ("a.png", b"x", "image/png")})
    assert r.status_code == 401


def test_raw_rejects_traversal(client: TestClient):
    # 字面 ../ 会被客户端规范化为绝对路径；这里用 URL 编码模拟穿越
    r = client.get("/api/v1/raw/%2e%2e/%2e%2e/etc/passwd")
    assert r.status_code in (422, 404)
    r = client.get("/api/v1/raw/%2e%2e%5c%2e%2e%5cwindows%5cwin.ini")
    assert r.status_code in (422, 404)


def test_file_meta_and_outline(client: TestClient):
    content = "---\ntitle: 我的笔记\ntags: [ai, python]\n---\n\n# 第一章\n\n正文\n\n## 小节\n\n```\n# 代码里的标题不算\n```\n"
    client.post("/api/v1/files", json={"path": "meta.md", "type": "file", "initial_content": content})

    r = client.get("/api/v1/files/meta", params={"path": "meta.md"})
    assert r.status_code == 200
    meta = r.json()
    assert meta["title"] == "我的笔记"
    assert meta["frontmatter"]["tags"] == ["ai", "python"]
    assert meta["has_frontmatter"] is True

    r = client.get("/api/v1/files/outline", params={"path": "meta.md"})
    headings = r.json()["headings"]
    assert [h["text"] for h in headings] == ["第一章", "小节"]
    assert headings[0]["level"] == 1


def test_outline_non_markdown_rejected(client: TestClient):
    client.post("/api/v1/files", json={"path": "x.txt", "type": "file"})
    r = client.get("/api/v1/files/outline", params={"path": "x.txt"})
    assert r.status_code == 400
