"""3.3 索引失败可观测：index_failures 记录、warning 响应、统计、重试。"""
from __future__ import annotations


def test_save_warning_when_index_fails(client, monkeypatch):
    """保存成功但索引失败：响应带 index_warning，且记录到 index_failures。"""
    import app.services.indexer as indexer_mod

    r = client.post("/api/v1/files", json={"path": "a.md", "type": "file", "initial_content": "# A\n"})
    assert r.status_code == 200

    def boom(self, rel):
        raise RuntimeError("索引崩溃模拟")

    monkeypatch.setattr(indexer_mod.Indexer, "index_file", boom)

    r = client.put("/api/v1/files/content?path=a.md", json={"content": "# A 更新\n"})
    assert r.status_code == 200
    data = r.json()
    assert data["index_warning"] is not None
    assert "索引崩溃模拟" in data["index_warning"]
    # 文件内容确实已保存
    fc = client.get("/api/v1/files/content", params={"path": "a.md"}).json()
    assert "更新" in fc["content"]

    # 失败记录可观测
    stats = client.get("/api/v1/index/stats").json()
    assert stats["failure_count"] >= 1
    assert any(f["path"] == "a.md" for f in stats["failures"])
    failed = next(f for f in stats["failures"] if f["path"] == "a.md")
    assert failed["attempts"] >= 1


def test_retry_failed_clears_on_success(client, monkeypatch):
    """重试失败项：成功后清除记录。"""
    import app.services.indexer as indexer_mod

    client.post("/api/v1/files", json={"path": "b.md", "type": "file", "initial_content": "# B\n"})

    def boom(self, rel):
        raise RuntimeError("boom")

    monkeypatch.setattr(indexer_mod.Indexer, "index_file", boom)
    client.put("/api/v1/files/content?path=b.md", json={"content": "# B 更新\n"})
    stats = client.get("/api/v1/index/stats").json()
    assert stats["failure_count"] >= 1

    # 修复索引器后重试
    monkeypatch.undo()
    r = client.post("/api/v1/index/retry-failed")
    assert r.status_code == 200
    data = r.json()
    assert data["cleared"] >= 1
    assert data["still_failed"] == 0
    stats = client.get("/api/v1/index/stats").json()
    assert stats["failure_count"] == 0


def test_rebuild_clears_failures(client, monkeypatch):
    """全量重建成功后清除失败记录。"""
    import app.services.indexer as indexer_mod

    client.post("/api/v1/files", json={"path": "c.md", "type": "file", "initial_content": "# C\n"})

    def boom(self, rel):
        raise RuntimeError("boom")

    monkeypatch.setattr(indexer_mod.Indexer, "index_file", boom)
    client.put("/api/v1/files/content?path=c.md", json={"content": "# C 更新\n"})
    monkeypatch.undo()

    r = client.post("/api/v1/index/rebuild")
    assert r.status_code == 200
    assert client.get("/api/v1/index/stats").json()["failure_count"] == 0


def test_import_index_failure_recorded(client, monkeypatch):
    """导入成功但索引失败：记录失败项。"""
    import app.services.indexer as indexer_mod

    def boom(self, rel):
        raise RuntimeError("import boom")

    monkeypatch.setattr(indexer_mod.Indexer, "index_file", boom)
    files = {"file": ("note.md", "# 导入内容\n".encode("utf-8"), "text/markdown")}
    r = client.post("/api/v1/import", files=files)
    assert r.status_code == 200  # 导入成功（索引失败不撤销）
    monkeypatch.undo()
    stats = client.get("/api/v1/index/stats").json()
    assert any(f["path"] == "note.md" for f in stats["failures"])
