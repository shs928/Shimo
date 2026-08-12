"""4.4 引用随重命名更新：preview 清单、WikiLink/Markdown 链接改写、代码块跳过、回滚。"""
from __future__ import annotations


def _create(client, path: str, content: str) -> None:
    r = client.post("/api/v1/files", json={"path": path, "type": "file", "initial_content": content})
    assert r.status_code == 200, r.text


def test_move_preview_reports_affected_links(client):
    _create(client, "a.md", "引用 [[b]] 和 [[b.md]]。\n")
    _create(client, "b.md", "# B\n")
    r = client.post("/api/v1/files/move/preview", json={"src": "b.md", "dst": "c.md"})
    assert r.status_code == 200
    data = r.json()
    assert data["valid"] is True
    assert data["affected_links"] >= 1
    assert "a.md" in data["affected_files"]


def test_move_refactors_wikilink_and_markdown_link(client):
    _create(client, "a.md", "看 [[b]] 和 [链接](b.md) 还有 [[b|别名]]。\n")
    _create(client, "b.md", "# B\n")
    r = client.post("/api/v1/files/move", json={"src": "b.md", "dst": "c.md", "refactor_links": True})
    assert r.status_code == 200, r.text

    fc = client.get("/api/v1/files/content", params={"path": "a.md"}).json()["content"]
    assert "[[c]]" in fc
    assert "[链接](c.md)" in fc
    assert "[[c|别名]]" in fc
    assert "[[b" not in fc


def test_move_skips_code_blocks_and_ambiguous(client):
    # 歧义：两个同名 b.md（根 + sub/）→ [[b]] 无法唯一解析 → 跳过
    _create(client, "a.md", "代码块内 [[b]]\n```\n[[b]]\n```\n")
    _create(client, "b.md", "# B1\n")
    _create(client, "sub/b.md", "# B2\n")
    # 先建索引让链接可解析
    client.post("/api/v1/index/rebuild")
    # 两个 b.md 都存在 → [[b]] 歧义；但根 b.md 移动时 [[b]] 无法唯一解析 → 保留
    r = client.post("/api/v1/files/move", json={"src": "b.md", "dst": "c.md", "refactor_links": True})
    assert r.status_code == 200, r.text
    fc = client.get("/api/v1/files/content", params={"path": "a.md"}).json()["content"]
    # 代码块内保留，歧义正文也保留（不可唯一解析）
    assert "[[b]]" in fc


def test_move_refactor_rolls_back_on_failure(client, monkeypatch):
    """批量写入中途失败：已写文件回滚，不留半完成状态。"""
    _create(client, "x.md", "指向 [[t]]。\n")
    _create(client, "y.md", "也指向 [[t]]。\n")
    _create(client, "t.md", "# T\n")

    from app.services import link_refactor as lr

    calls = {"n": 0}

    def fail_on_second(vault, rel, content, expected_etag, on_before_write=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("模拟写入失败")
        return vault.write_markdown(rel, content, expected_etag, on_before_write=on_before_write)

    monkeypatch.setattr(type(client.app.state.vault), "write_markdown", fail_on_second)
    import pytest

    with pytest.raises(RuntimeError, match="回滚"):
        lr.refactor_links(
            client.app.state.vault, client.app.state.db, client.app.state.history,
            client.app.state.indexer, client.app.state.rag,
            "t.md", "u.md",
        )
    # 第一个文件已回滚：内容保持原样
    fc = client.get("/api/v1/files/content", params={"path": "x.md"}).json()["content"]
    assert "[[t]]" in fc
