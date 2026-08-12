"""4.2 版本历史：快照、去重、列表、diff、恢复、Agent 更新链路。"""
from __future__ import annotations


def _create(client, path: str, content: str) -> None:
    r = client.post("/api/v1/files", json={"path": path, "type": "file", "initial_content": content})
    assert r.status_code == 200, r.text


def _save(client, path: str, content: str) -> None:
    r = client.put(f"/api/v1/files/content?path={path}", json={"content": content})
    assert r.status_code == 200, r.text


def test_save_creates_history_snapshots(client):
    _create(client, "h.md", "# v1\n\n第一版。\n")
    _save(client, "h.md", "# v1\n\n第二版。\n")
    _save(client, "h.md", "# v1\n\n第三版。\n")

    r = client.get("/api/v1/history", params={"path": "h.md"})
    versions = r.json()["versions"]
    # 每次覆盖保存旧版 → 2 个快照（v1、v2）；当前内容 v3 不在历史中
    assert len(versions) == 2
    assert versions[0]["saved_at"] >= versions[1]["saved_at"]


def test_history_dedup_identical_content(client):
    _create(client, "d.md", "# 相同内容\n")
    _save(client, "d.md", "# 相同内容\n")  # 内容未变 → 不产生快照
    versions = client.get("/api/v1/history", params={"path": "d.md"}).json()["versions"]
    assert versions == []


def test_history_get_and_diff(client):
    _create(client, "x.md", "# 标题\n\n第一行。\n")
    _save(client, "x.md", "# 标题\n\n第二行。\n")
    versions = client.get("/api/v1/history", params={"path": "x.md"}).json()["versions"]
    sha1 = versions[0]["sha1"]

    r = client.get("/api/v1/history/version", params={"path": "x.md", "sha1": sha1})
    assert "第一行" in r.json()["content"]

    r = client.get("/api/v1/history/diff", params={"path": "x.md", "sha1": sha1})
    diff = r.json()["diff"]
    assert "第一行" in diff and "第二行" in diff


def test_history_restore_roundtrip(client):
    _create(client, "r.md", "# 原始\n\n内容 A。\n")
    _save(client, "r.md", "# 原始\n\n内容 B。\n")
    versions = client.get("/api/v1/history", params={"path": "r.md"}).json()["versions"]
    old_sha1 = versions[0]["sha1"]

    r = client.post(f"/api/v1/history/restore?path=r.md&sha1={old_sha1}")
    assert r.status_code == 200
    fc = client.get("/api/v1/files/content", params={"path": "r.md"}).json()
    assert "内容 A" in fc["content"]

    # 恢复前保存了当前版本 → 可再次恢复到 B
    versions2 = client.get("/api/v1/history", params={"path": "r.md"}).json()["versions"]
    assert any(v["sha1"] == old_sha1 for v in versions2)
    # 恢复后搜索索引已更新（短词走 LIKE 降级）
    hits = client.get("/api/v1/search", params={"q": "内容"}).json()["results"]
    assert any(h["path"] == "r.md" for h in hits)


def test_history_restore_missing_version(client):
    _create(client, "r.md", "# 原始\n")
    r = client.post("/api/v1/history/restore?path=r.md&sha1=deadbeef")
    assert r.status_code == 400
    assert "版本不存在" in r.text


def test_agent_update_enters_history(client):
    """Agent update_note 同样进入历史链路。"""
    _create(client, "a.md", "# Agent 版\n\n旧内容。\n")
    _save(client, "a.md", "# Agent 版\n\n中间内容。\n")

    from app.agent.tools import ToolContext, execute

    ctx = ToolContext(
        vault=client.app.state.vault,
        indexer=client.app.state.indexer,
        rag=client.app.state.rag,
        settings_provider=lambda: client.app.state.ai_store.load(),
        registry=client.app.state.agent_registry,
        mcp_manager=client.app.state.mcp_manager,
        ai_store=client.app.state.ai_store,
        history=client.app.state.history,
    )
    status, _ = execute(ctx, "update_note", {"path": "a.md", "content": "# Agent 版\n\nAgent 新内容。\n"})
    assert status == "ok"

    versions = client.get("/api/v1/history", params={"path": "a.md"}).json()["versions"]
    assert len(versions) >= 2  # 中间内容 与 Agent 覆盖前的内容都在历史里
