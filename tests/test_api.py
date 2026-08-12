"""端到端 API 测试：认证、文件树、保存冲突、移动、回收站。"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_init_login_logout_flow(client: TestClient):
    # 已通过 fixture init，直接登录
    r = client.post("/api/v1/auth/logout")
    assert r.status_code == 200
    assert "kb_session" not in client.cookies

    r = client.post("/api/v1/auth/login", json={"password": "test-password"})
    assert r.status_code == 200
    assert client.cookies.get("kb_session")


def test_requires_auth(client: TestClient):
    client.post("/api/v1/auth/logout")
    r = client.get("/api/v1/tree")
    assert r.status_code == 401


def test_create_read_save_roundtrip(client: TestClient):
    r = client.post("/api/v1/files", json={"path": "笔记/入门.md", "type": "file", "initial_content": "# 标题\n"})
    assert r.status_code == 200

    r = client.get("/api/v1/files/content", params={"path": "笔记/入门.md"})
    assert r.status_code == 200
    data = r.json()
    etag = data["etag"]

    r = client.put("/api/v1/files/content", params={"path": "笔记/入门.md"}, json={"content": "# 更新\n"}, headers={"If-Match": etag})
    assert r.status_code == 200

    # 旧 etag 再次保存 -> 412 冲突
    r = client.put("/api/v1/files/content", params={"path": "笔记/入门.md"}, json={"content": "stale"}, headers={"If-Match": etag})
    assert r.status_code == 412


def test_tree_and_move(client: TestClient):
    client.post("/api/v1/files", json={"path": "a.md", "type": "file"})
    client.post("/api/v1/files", json={"path": "folder", "type": "dir"})

    r = client.get("/api/v1/tree")
    assert {e["name"] for e in r.json()["entries"]} == {"a.md", "folder"}

    r = client.post("/api/v1/files/move/preview", json={"src": "a.md", "dst": "folder/a.md"})
    assert r.json()["valid"] is True

    r = client.post("/api/v1/files/move", json={"src": "a.md", "dst": "folder/a.md"})
    assert r.status_code == 200


def test_move_over_existing_returns_400(client: TestClient):
    client.post("/api/v1/files", json={"path": "a.md", "type": "file"})
    client.post("/api/v1/files", json={"path": "b.md", "type": "file"})
    r = client.post("/api/v1/files/move", json={"src": "a.md", "dst": "b.md"})
    assert r.status_code == 400


def test_path_traversal_rejected(client: TestClient):
    r = client.get("/api/v1/files/content", params={"path": "../../etc/passwd"})
    assert r.status_code == 422


def test_delete_restore_purge(client: TestClient):
    client.post("/api/v1/files", json={"path": "tmp.md", "type": "file"})
    r = client.delete("/api/v1/files", params={"path": "tmp.md"})
    assert r.status_code == 200

    r = client.get("/api/v1/trash")
    assert len(r.json()["entries"]) == 1

    r = client.post("/api/v1/trash/restore", json={"path": "tmp.md"})
    assert r.status_code == 200

    r = client.delete("/api/v1/files", params={"path": "tmp.md"})
    client.post("/api/v1/trash/purge")
    r = client.get("/api/v1/trash")
    assert r.json()["entries"] == []


def test_csrf_rejects_cross_origin_write(client: TestClient):
    r = client.post(
        "/api/v1/files",
        json={"path": "x.md", "type": "file"},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403


def test_binary_file_blocked(client: TestClient):
    import os

    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.config import Config

    # 直接构造二进制文件绕过 API 上传限制
    target = client.app.state.config.vault_path / "bin.md"
    target.write_bytes(b"\xff\xfe\x00\x01")
    r = client.get("/api/v1/files/content", params={"path": "bin.md"})
    assert r.status_code == 409


def test_wiki_resolve_priority_and_uniqueness(client: TestClient):
    client.post("/api/v1/files", json={"path": "笔记/A.md", "type": "file"})
    client.post("/api/v1/files", json={"path": "B.md", "type": "file"})

    # 当前目录优先
    r = client.get("/api/v1/wiki/resolve", params={"link": "A", "dir": "笔记"})
    assert r.json()["path"] == "笔记/A.md"

    # 根目录精确匹配
    r = client.get("/api/v1/wiki/resolve", params={"link": "B"})
    assert r.json()["path"] == "B.md"

    # 不存在 -> null
    r = client.get("/api/v1/wiki/resolve", params={"link": "不存在"})
    assert r.json()["path"] is None

    # 同名歧义：子目录也有 B.md，根目录已有 B.md，根目录仍精确命中
    client.post("/api/v1/files", json={"path": "笔记/B.md", "type": "file"})
    r = client.get("/api/v1/wiki/resolve", params={"link": "B"})
    assert r.json()["path"] == "B.md"


def test_wiki_resolve_ambiguous_without_root(client: TestClient):
    client.post("/api/v1/files", json={"path": "x/同名.md", "type": "file"})
    client.post("/api/v1/files", json={"path": "y/同名.md", "type": "file"})

    r = client.get("/api/v1/wiki/resolve", params={"link": "同名"})
    assert r.json()["path"] is None
