"""模板后端：API 契约、CRUD、应用、导入导出与索引隔离。"""
from __future__ import annotations

import io
import re
import zipfile

import pytest
from fastapi.testclient import TestClient


def _create_custom(client: TestClient, **overrides) -> dict:
    payload = {
        "name": "demo",
        "title": "演示模板",
        "description": "模板描述",
        "category": "测试",
        "tags": ["模板", "测试"],
        "icon": "file-text",
        "content": "# {{title}}\n\n创建于 {{date}} {{time}}\n\n{{unknown}}\n",
    }
    payload.update(overrides)
    response = client.post("/api/v1/templates/custom", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_builtin_list_detail_and_read_only(client: TestClient):
    response = client.get("/api/v1/templates")
    assert response.status_code == 200
    data = response.json()
    assert len([item for item in data["templates"] if item["source"] == "builtin"]) == 8
    assert {item["title"] for item in data["templates"] if item["source"] == "builtin"} == {
        "会议纪要", "工作周报", "项目立项", "需求文档",
        "任务清单", "复盘记录", "读书笔记", "研究记录",
    }
    assert data["custom_categories"] == []
    assert {"工作", "项目", "学习", "研究"} <= set(data["categories"])
    for item in data["templates"]:
        assert {"id", "source", "title", "description", "category", "tags", "icon", "updated_at"} <= item.keys()
        assert isinstance(item["updated_at"], str)
        assert "content" not in item

    detail = client.get("/api/v1/templates/detail", params={"id": "builtin:meeting-notes"})
    assert detail.status_code == 200
    assert detail.json()["title"] == "会议纪要"
    assert "{{date}}" in detail.json()["content"]

    update = client.put(
        "/api/v1/templates/custom",
        json={"id": "builtin:meeting-notes", "title": "不可修改"},
    )
    assert update.status_code == 400
    delete = client.delete("/api/v1/templates/custom", params={"id": "builtin:meeting-notes"})
    assert delete.status_code == 400


def test_custom_crud_categories_copy_move_delete_contract(client: TestClient):
    created_category = client.post("/api/v1/templates/categories", json={"name": "团队/例会"})
    assert created_category.status_code == 200, created_category.text
    assert "团队/例会" in created_category.json()["categories"]
    assert {"团队", "团队/例会"} <= set(created_category.json()["custom_categories"])

    template = _create_custom(client, category="团队/例会")
    assert template["id"] == "custom:templates/团队/例会/demo.md"
    assert template["path"] == "templates/团队/例会/demo.md"
    assert template["content"].startswith("# {{title}}")
    assert isinstance(template["updated_at"], str) and template["updated_at"]

    detail = client.get("/api/v1/templates/detail", params={"id": template["id"]})
    assert detail.status_code == 200
    assert detail.json()["tags"] == ["模板", "测试"]

    updated = client.put(
        "/api/v1/templates/custom",
        json={
            "id": template["id"],
            "title": "更新标题",
            "description": "更新描述",
            "tags": ["更新"],
            "icon": "notebook-tabs",
            "content": "# 新正文\n\n{{datetime}}\n",
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["title"] == "更新标题"
    assert updated.json()["content"] == "# 新正文\n\n{{datetime}}\n"

    copied = client.post(
        "/api/v1/templates/copy",
        json={"id": updated.json()["id"], "name": "副本", "category": "团队/例会"},
    )
    assert copied.status_code == 200, copied.text
    assert copied.json()["id"] == "custom:templates/团队/例会/副本.md"
    assert copied.json()["content"] == updated.json()["content"]

    moved = client.post(
        "/api/v1/templates/move",
        json={"id": copied.json()["id"], "name": "归档", "category": "归档"},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["id"] == "custom:templates/归档/归档.md"
    assert moved.json()["category"] == "归档"
    assert client.get("/api/v1/templates/detail", params={"id": copied.json()["id"]}).status_code == 404

    renamed_category = client.post(
        "/api/v1/templates/categories/move",
        json={"name": "归档", "new_name": "历史"},
    )
    assert renamed_category.status_code == 200, renamed_category.text
    category_data = renamed_category.json()
    assert "历史" in category_data["categories"]
    assert "历史" in category_data["custom_categories"]
    assert "归档" not in category_data["custom_categories"]

    nonempty = client.delete("/api/v1/templates/categories", params={"name": "历史"})
    assert nonempty.status_code == 400
    removed = client.delete(
        "/api/v1/templates/categories",
        params={"name": "历史", "force": "true"},
    )
    assert removed.status_code == 200, removed.text
    assert "历史" not in removed.json()["custom_categories"]
    assert "历史" not in removed.json()["categories"]

    deleted = client.delete("/api/v1/templates/custom", params={"id": template["id"]})
    assert deleted.status_code == 200
    assert client.get("/api/v1/templates/detail", params={"id": template["id"]}).status_code == 404


def test_apply_strips_template_metadata_replaces_known_variables_and_indexes(client: TestClient):
    template = _create_custom(
        client,
        name="变量模板.md",
        title="变量模板",
        content=(
            "---\n"
            "title: 普通元数据标题\n"
            "template_title: 输入中的模板标题\n"
            "template_private: 必须移除\n"
            "tags: [普通标签]\n"
            "---\n"
            "# {{title}}\n\n"
            "日期 {{date}}，时间 {{time}}，完整 {{datetime}}。\n\n"
            "未知 {{owner}} 保留。\n"
        ),
    )
    response = client.post(
        "/api/v1/templates/apply",
        json={"id": template["id"], "path": "笔记/生成.md", "title": "生成标题"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"path": "笔记/生成.md"}

    note = client.get("/api/v1/files/content", params={"path": "笔记/生成.md"}).json()["content"]
    assert "template_title" not in note
    assert "template_private" not in note
    assert "title: 普通元数据标题" in note
    assert "# 生成标题" in note
    assert "{{owner}}" in note
    assert re.search(r"日期 \d{4}-\d{2}-\d{2}，时间 \d{2}:\d{2}", note)
    assert "{{date}}" not in note and "{{time}}" not in note and "{{datetime}}" not in note

    conn = client.app.state.db.connect()
    assert conn.execute("SELECT 1 FROM files_meta WHERE path=?", ("笔记/生成.md",)).fetchone() is not None
    assert conn.execute("SELECT count(*) FROM chunks WHERE file_path=?", ("笔记/生成.md",)).fetchone()[0] >= 1
    results = client.get("/api/v1/search", params={"q": "生成标题"}).json()["results"]
    assert any(item["path"] == "笔记/生成.md" for item in results)


def test_import_export_and_overwrite_history_contract(client: TestClient):
    files = [
        (
            "files",
            (
                "第一.md",
                "---\ntemplate_title: 第一模板\ntemplate_tags: [导入]\n---\n# 第一\n".encode(),
                "text/markdown",
            ),
        ),
        ("files", ("第二.md", "# 第二模板\n\n正文。\n".encode(), "text/markdown")),
    ]
    imported = client.post(
        "/api/v1/templates/import",
        params={"category": "导入", "strategy": "skip"},
        files=files,
    )
    assert imported.status_code == 200, imported.text
    payload = imported.json()
    assert payload["imported"] == 2
    assert payload["skipped"] == 0
    assert payload["renamed"] == 0
    assert len(payload["templates"]) == 2
    assert all(item["source"] == "custom" for item in payload["templates"])

    skipped = client.post(
        "/api/v1/templates/import",
        params={"category": "导入", "strategy": "skip"},
        files=[("files", ("第一.md", b"# ignored", "text/markdown"))],
    ).json()
    assert skipped["imported"] == 0
    assert skipped["skipped"] == 1
    assert skipped["templates"] == []

    renamed = client.post(
        "/api/v1/templates/import",
        params={"category": "导入", "strategy": "rename"},
        files=[("files", ("第一.md", b"# renamed", "text/markdown"))],
    ).json()
    assert renamed["imported"] == 1
    assert renamed["skipped"] == 0
    assert renamed["renamed"] == 1
    assert renamed["templates"][0]["path"] == "templates/导入/第一 (1).md"

    overwritten = client.post(
        "/api/v1/templates/import",
        params={"category": "导入", "strategy": "overwrite"},
        files=[("files", ("第一.md", "# 覆盖后\n".encode(), "text/markdown"))],
    )
    assert overwritten.status_code == 200, overwritten.text
    assert overwritten.json()["imported"] == 1
    assert overwritten.json()["templates"][0]["content"] == "# 覆盖后\n"
    assert client.app.state.history.list_versions("templates/导入/第一.md")

    template_id = overwritten.json()["templates"][0]["id"]
    one = client.get("/api/v1/templates/export", params={"id": template_id})
    assert one.status_code == 200
    assert one.headers["content-type"].startswith("text/markdown")
    assert b"template_title" in one.content
    assert "# 覆盖后" in one.content.decode("utf-8")

    exported = client.get("/api/v1/templates/export-all")
    assert exported.status_code == 200
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        assert set(archive.namelist()) == {"导入/第一.md", "导入/第一 (1).md", "导入/第二.md"}
        assert "# 覆盖后" in archive.read("导入/第一.md").decode("utf-8")


def test_template_paths_are_guarded(client: TestClient):
    template = _create_custom(client)
    bad_ids = [
        "custom:templates/../../outside.md",
        "custom:outside.md",
        "custom:/templates/demo.md",
    ]
    for template_id in bad_ids:
        response = client.get("/api/v1/templates/detail", params={"id": template_id})
        assert response.status_code == 422, (template_id, response.text)

    category = client.post("/api/v1/templates/categories", json={"name": "../../outside"})
    assert category.status_code == 422
    apply_inside_templates = client.post(
        "/api/v1/templates/apply",
        json={"id": template["id"], "path": "templates/非法.md"},
    )
    assert apply_inside_templates.status_code == 422
    absolute = client.post(
        "/api/v1/templates/apply",
        json={"id": template["id"], "path": "C:/outside.md"},
    )
    assert absolute.status_code == 422


def test_apply_rolls_back_file_when_indexing_fails(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    template = _create_custom(client, name="回滚模板")

    def fail_rag(_rel: str, _content: str):
        raise RuntimeError("rag failed")

    monkeypatch.setattr(client.app.state.rag, "reindex_file", fail_rag)
    with pytest.raises(RuntimeError, match="rag failed"):
        client.app.state.templates.apply(template["id"], "应回滚.md", "应回滚")

    assert not (client.app.state.vault.root / "应回滚.md").exists()
    conn = client.app.state.db.connect()
    assert conn.execute("SELECT 1 FROM files_meta WHERE path='应回滚.md'").fetchone() is None
    assert conn.execute("SELECT 1 FROM chunks WHERE file_path='应回滚.md'").fetchone() is None


def test_files_api_operations_keep_template_paths_unindexed(client: TestClient):
    created = client.post(
        "/api/v1/files",
        json={"path": "templates/普通入口.md", "type": "file", "initial_content": "# 模板隐私词 files-secret-7878\n"},
    )
    assert created.status_code == 200, created.text
    conn = client.app.state.db.connect()
    assert conn.execute("SELECT 1 FROM files_meta WHERE path='templates/普通入口.md'").fetchone() is None
    assert conn.execute("SELECT 1 FROM chunks WHERE file_path='templates/普通入口.md'").fetchone() is None

    saved = client.put(
        "/api/v1/files/content",
        params={"path": "templates/普通入口.md"},
        json={"content": "# 更新后 files-secret-7878\n"},
    )
    assert saved.status_code == 200, saved.text
    assert client.get("/api/v1/search", params={"q": "files-secret-7878"}).json()["results"] == []

    outside = client.post(
        "/api/v1/files",
        json={"path": "临时.md", "type": "file", "initial_content": "# 临时\n\n正文 move-secret-6767。\n"},
    )
    assert outside.status_code == 200
    moved_in = client.post(
        "/api/v1/files/move",
        json={"src": "临时.md", "dst": "templates/移入.md"},
    )
    assert moved_in.status_code == 200, moved_in.text
    conn = client.app.state.db.connect()
    assert conn.execute("SELECT 1 FROM files_meta WHERE path='临时.md'").fetchone() is None
    assert conn.execute("SELECT 1 FROM files_meta WHERE path='templates/移入.md'").fetchone() is None
    assert client.get("/api/v1/search", params={"q": "move-secret-6767"}).json()["results"] == []

    moved_out = client.post(
        "/api/v1/files/move",
        json={"src": "templates/移入.md", "dst": "移出.md"},
    )
    assert moved_out.status_code == 200, moved_out.text
    conn = client.app.state.db.connect()
    assert conn.execute("SELECT 1 FROM files_meta WHERE path='移出.md'").fetchone() is not None
    assert conn.execute("SELECT count(*) FROM chunks WHERE file_path='移出.md'").fetchone()[0] >= 1

    client.delete("/api/v1/files", params={"path": "templates/普通入口.md"})
    restored = client.post(
        "/api/v1/trash/restore",
        json={"path": "templates/普通入口.md", "target": None},
    )
    assert restored.status_code == 200, restored.text
    conn = client.app.state.db.connect()
    assert conn.execute("SELECT 1 FROM files_meta WHERE path='templates/普通入口.md'").fetchone() is None
    assert conn.execute("SELECT 1 FROM chunks WHERE file_path='templates/普通入口.md'").fetchone() is None


def test_templates_hidden_from_tree_search_rag_and_wiki_and_rebuild_cleans_history(client: TestClient):
    template = _create_custom(
        client,
        name="Hidden.md",
        category="",
        title="隐藏模板",
        content="# Hidden\n\nonly-template-secret-9988\n",
    )
    assert template["path"] == "templates/Hidden.md"

    root_entries = client.get("/api/v1/tree").json()["entries"]
    assert all(item["name"].casefold() != "templates" for item in root_entries)
    assert client.get("/api/v1/search", params={"q": "only-template-secret-9988"}).json()["results"] == []
    assert client.app.state.rag.reindex_file(template["path"], template["content"]) == 0
    assert client.app.state.rag.search("only-template-secret-9988", k=5) == []
    assert client.get("/api/v1/wiki/resolve", params={"link": "Hidden"}).json()["path"] is None

    # 模拟升级前遗留的普通/RAG 索引，普通重建必须级联清理。
    conn = client.app.state.db.connect()
    conn.execute(
        """INSERT INTO files_meta(path,title,mtime_ns,size,sha1,tags,indexed_at)
           VALUES(?,?,?,?,?,?,?)""",
        (template["path"], "Hidden", 1, 1, "legacy", "", "legacy"),
    )
    conn.execute(
        "INSERT INTO files_fts(path,title,body,tags) VALUES(?,?,?,?)",
        (template["path"], "Hidden", "only-template-secret-9988", ""),
    )
    conn.execute(
        """INSERT INTO chunks(file_path,idx,heading,line_start,line_end,text,content_hash,ai_indexed)
           VALUES(?,?,?,?,?,?,?,0)""",
        (template["path"], 0, "Hidden", 1, 2, "only-template-secret-9988", "legacy"),
    )
    conn.commit()

    rebuilt = client.post("/api/v1/index/rebuild")
    assert rebuilt.status_code == 200, rebuilt.text
    conn = client.app.state.db.connect()
    assert conn.execute("SELECT 1 FROM files_meta WHERE path=?", (template["path"],)).fetchone() is None
    assert conn.execute("SELECT 1 FROM chunks WHERE file_path=?", (template["path"],)).fetchone() is None
    assert client.get("/api/v1/search", params={"q": "only-template-secret-9988"}).json()["results"] == []

    # 由模板生成的普通笔记仍进入两套索引。
    applied = client.post(
        "/api/v1/templates/apply",
        json={"id": template["id"], "path": "generated.md", "title": "生成笔记"},
    )
    assert applied.status_code == 200, applied.text
    conn = client.app.state.db.connect()
    assert conn.execute("SELECT 1 FROM files_meta WHERE path='generated.md'").fetchone() is not None
    assert conn.execute("SELECT count(*) FROM chunks WHERE file_path='generated.md'").fetchone()[0] >= 1
    assert any(
        item["path"] == "generated.md"
        for item in client.get("/api/v1/search", params={"q": "only-template-secret-9988"}).json()["results"]
    )
