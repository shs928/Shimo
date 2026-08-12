"""M3 知识索引测试：搜索、链接、反链、图谱、重建与增量维护。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.services.indexer import Indexer


def _create(client: TestClient, path: str, content: str) -> None:
    r = client.post("/api/v1/files", json={"path": path, "type": "file", "initial_content": content})
    assert r.status_code == 200, r.text


def test_search_finds_title_body_tag_path(client: TestClient):
    _create(client, "技术/机器学习.md", "---\ntags: [ai]\n---\n\n# 机器学习入门\n\n这是关于神经网络的笔记。\n")
    _create(client, "随笔.md", "# 随笔\n\n今天天气不错。\n")

    r = client.get("/api/v1/search", params={"q": "神经网络"})
    assert [x["path"] for x in r.json()["results"]] == ["技术/机器学习.md"]

    r = client.get("/api/v1/search", params={"q": "机器学习入门"})
    assert [x["path"] for x in r.json()["results"]] == ["技术/机器学习.md"]

    r = client.get("/api/v1/search", params={"q": "ai"})
    assert any(x["path"] == "技术/机器学习.md" for x in r.json()["results"])

    r = client.get("/api/v1/search", params={"q": "机器学习"})
    assert any(x["path"] == "技术/机器学习.md" for x in r.json()["results"])


def test_short_chinese_query_uses_like_fallback(client: TestClient):
    _create(client, "a.md", "# 训练\n\n每天训练一小时。\n")
    r = client.get("/api/v1/search", params={"q": "训练"})
    assert any(x["path"] == "a.md" for x in r.json()["results"])


def test_backlinks_and_outgoing(client: TestClient):
    _create(client, "home.md", "# 主页\n\n参见 [[主题A]] 和 [[主题A#小节|别名]]\n")
    _create(client, "主题A.md", "# 主题A\n\n## 小节\n\n内容。\n")
    _create(client, "other.md", "# 其他\n\n与 [[主题A]] 无关。\n")

    r = client.get("/api/v1/backlinks", params={"path": "主题A.md"})
    bl = r.json()["backlinks"]
    assert {x["source_path"] for x in bl} == {"home.md", "other.md"}
    assert any(x["anchor"] == "小节" for x in bl)

    r = client.get("/api/v1/outgoing", params={"path": "home.md"})
    links = r.json()["links"]
    assert len(links) == 2
    assert all(x["resolved"] == 1 for x in links)


def test_code_block_links_ignored_and_unresolved_counted(client: TestClient):
    _create(client, "code.md", "# 代码\n\n```\n[[不是链接]]\n```\n\n引用 [[不存在]]\n")
    r = client.get("/api/v1/outgoing", params={"path": "code.md"})
    links = r.json()["links"]
    # 代码块内的 [[不是链接]] 不解析，正文中 [[不存在]] 解析为 unresolved
    assert len(links) == 1
    assert links[0]["resolved"] == 0


def test_relative_and_parent_links(client: TestClient):
    _create(client, "dir/a.md", "# A\n\n链接到 [同级](./b.md)\n")
    _create(client, "dir/b.md", "# B\n\n回到 [上级](../c.md)\n")
    _create(client, "c.md", "# C\n")

    r = client.get("/api/v1/outgoing", params={"path": "dir/a.md"})
    assert r.json()["links"][0]["target_path"] == "dir/b.md"
    r = client.get("/api/v1/outgoing", params={"path": "dir/b.md"})
    assert r.json()["links"][0]["target_path"] == "c.md"


def test_ambiguous_link_unresolved(client: TestClient):
    _create(client, "x/同名.md", "# 同名\n")
    _create(client, "y/同名.md", "# 同名\n")
    _create(client, "ref.md", "# 引用\n\n看 [[同名]]\n")
    r = client.get("/api/v1/outgoing", params={"path": "ref.md"})
    assert r.json()["links"][0]["resolved"] == 0


def test_graph_local(client: TestClient):
    _create(client, "中心.md", "# 中心\n\n指向 [[左]] 和 [[右]]\n")
    _create(client, "左.md", "# 左\n\n回链 [[中心]]\n")
    _create(client, "右.md", "# 右\n\n孤立。\n")

    r = client.get("/api/v1/graph", params={"path": "中心.md"})
    data = r.json()
    assert {n["id"] for n in data["nodes"]} == {"中心.md", "左.md", "右.md"}
    assert len(data["edges"]) >= 3


def test_index_stats_and_rebuild(client: TestClient):
    _create(client, "s1.md", "# 一号\n")
    _create(client, "s2.md", "# 二号\n\n[[s1]]\n")

    r = client.get("/api/v1/index/stats")
    stats = r.json()
    assert stats["files"] == 2
    assert stats["links"] >= 1

    r = client.post("/api/v1/index/rebuild")
    assert r.status_code == 200
    assert r.json()["indexed"] == 2

    r = client.get("/api/v1/index/stats")
    assert r.json()["files"] == 2


def test_save_delete_move_updates_index(client: TestClient):
    _create(client, "life.md", "# 生活\n\n今天学习了新的东西。\n")

    # 保存修改后搜索新词
    r = client.get("/api/v1/files/content", params={"path": "life.md"})
    etag = r.json()["etag"]
    r = client.put(
        "/api/v1/files/content",
        params={"path": "life.md"},
        headers={"If-Match": etag},
        json={"content": "# 生活\n\n现在包含量子力学。\n"},
    )
    assert r.status_code == 200
    r = client.get("/api/v1/search", params={"q": "量子力学"})
    assert any(x["path"] == "life.md" for x in r.json()["results"])

    # 移动后路径可搜索，旧路径失效
    client.post("/api/v1/files/move", json={"src": "life.md", "dst": "新目录/life.md"})
    r = client.get("/api/v1/search", params={"q": "量子力学"})
    assert any(x["path"] == "新目录/life.md" for x in r.json()["results"])
    assert not any(x["path"] == "life.md" for x in r.json()["results"])

    # 删除后索引移除
    client.delete("/api/v1/files", params={"path": "新目录/life.md"})
    r = client.get("/api/v1/search", params={"q": "量子力学"})
    assert r.json()["results"] == []


def test_trash_restore_reindexes(client: TestClient):
    _create(client, "gone.md", "# 消失\n\n关键词检索。\n")
    client.delete("/api/v1/files", params={"path": "gone.md"})
    r = client.get("/api/v1/search", params={"q": "关键词检索"})
    assert r.json()["results"] == []

    client.post("/api/v1/trash/restore", json={"path": "gone.md"})
    r = client.get("/api/v1/search", params={"q": "关键词检索"})
    assert any(x["path"] == "gone.md" for x in r.json()["results"])
