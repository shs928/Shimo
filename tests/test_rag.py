"""最小 RAG 测试：分块、排除规则、chunks 维护、SSE 降级、文档导入与检索。"""
from __future__ import annotations

import io

from fastapi.testclient import TestClient

from app.rag.chunker import chunk_markdown


def test_chunker_splits_by_heading_and_length():
    text = "# 第一节\n\n" + "内容内容" * 200 + "\n\n## 第二节\n\n短内容\n"
    chunks = chunk_markdown(text)
    assert len(chunks) >= 2
    assert chunks[0].heading == "第一节"
    assert chunks[1].heading == "第二节"


def test_chunker_overlap_on_long_paragraph():
    # 单段超过目标长度（>1500 字符）：应切分且相邻块共享尾部内容
    long_line = "这是一个非常长的段落用于测试分块重叠行为。" * 100
    chunks = chunk_markdown(f"# 标题\n\n{long_line}\n")
    assert len(chunks) >= 2
    assert chunks[0].text[-30:] in chunks[1].text


def test_chunker_skips_frontmatter():
    text = "---\ntitle: x\ntags: [a]\n---\n\n# 正文\n\n内容。\n"
    chunks = chunk_markdown(text)
    assert chunks and "title: x" not in chunks[0].text
    assert chunks[0].heading == "正文"


def test_rag_rebuild_and_fts_fallback(client: TestClient):
    _create(client, "note.md", "# 重要\n\nRAG 检索需要能命中这段独特内容。\n")
    _create(client, "excluded.md", "---\nai: false\n---\n\n# 私密\n\n这段不应该进入 AI 索引。\n")

    r = client.post("/api/v1/ai/rebuild")
    assert r.status_code == 200
    data = r.json()
    assert data["reindexed"] == 2
    assert data["chunks"] >= 1

    # ai:false 文件不产生 chunks
    r = client.get("/api/v1/ai/status")
    assert r.status_code == 200
    assert r.json()["chunks"] >= 1

    # FTS-only 检索命中（未配置 embedding）
    from app.rag.retriever import RagIndexer

    rag = client.app.state.rag
    hits = rag.search("独特内容", k=3)
    assert hits and hits[0]["file_path"] == "note.md"
    assert all(h["file_path"] != "excluded.md" for h in hits)


def test_ai_status_when_disabled(client: TestClient):
    r = client.get("/api/v1/ai/status")
    assert r.json()["enabled"] is False
    assert r.json()["chat_configured"] is False


def test_chat_returns_clear_error_when_disabled(client: TestClient):
    r = client.post("/api/v1/ai/chat", json={"message": "你好"})
    assert r.status_code == 200
    assert "AI 未启用" in r.text or "未配置 Chat 模型" in r.text


def test_ai_config_save_and_clear(client: TestClient):
    r = client.post(
        "/api/v1/ai/config",
        json={"enabled": True, "chat": {"base_url": "https://example.com/v1", "api_key": "sk-test", "model": "m"}},
    )
    assert r.status_code == 200
    assert r.json()["chat_configured"] is True

    r = client.get("/api/v1/ai/status")
    assert r.json()["enabled"] is True

    # 关闭后 chat 立即返回明确错误（不发起外部请求）
    r = client.post("/api/v1/ai/config", json={"enabled": False})
    assert r.json()["enabled"] is False
    r = client.post("/api/v1/ai/chat", json={"message": "hello"})
    assert r.status_code == 200
    assert "AI 未启用" in r.text


def test_delete_file_clears_chunks(client: TestClient):
    _create(client, "temp.md", "# 临时\n\n要被删除。\n")
    client.post("/api/v1/ai/rebuild")
    before = client.get("/api/v1/ai/status").json()["chunks"]

    client.delete("/api/v1/files", params={"path": "temp.md"})
    after = client.get("/api/v1/ai/status").json()["chunks"]
    assert after < before


def test_semantic_search_endpoint_fts_fallback(client: TestClient):
    _create(client, "note.md", "# 重要\n\n语义搜索需要能命中这段独特内容。\n")
    client.post("/api/v1/ai/rebuild")
    r = client.post("/api/v1/ai/semantic-search", json={"query": "独特内容", "k": 3})
    assert r.status_code == 200
    data = r.json()
    assert data["query"] == "独特内容"
    assert data["results"] and data["results"][0]["file_path"] == "note.md"


def test_embedding_stat_endpoint(client: TestClient):
    r = client.get("/api/v1/ai/embedding-stat")
    assert r.status_code == 200
    data = r.json()
    assert "pending" in data and "embedded" in data and "total" in data


def test_rebuild_now_returns_pending_not_embedded(client: TestClient):
    _create(client, "note.md", "# 内容\n\n重建返回待嵌入数量。\n")
    r = client.post("/api/v1/ai/rebuild")
    assert r.status_code == 200
    data = r.json()
    assert "reindexed" in data
    assert "pending" in data  # 嵌入交给后台任务，不再同步 embedded
    assert data["reindexed"] >= 1


def test_list_models_unknown_provider(client: TestClient):
    r = client.post("/api/v1/ai/list-models", json={"provider_id": "nope"})
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_free_chat_session_management(client: TestClient):
    r = client.post("/api/v1/ai/chat/session")
    assert r.status_code == 200
    sid = r.json()["session_id"]
    r = client.get("/api/v1/ai/chat/session")
    assert r.status_code == 200
    assert any(s["id"] == sid for s in r.json()["sessions"])

    # 未启用时 free 模式返回明确错误
    r = client.post("/api/v1/ai/chat", json={"message": "你好", "mode": "free", "session_id": sid})
    assert "AI 未启用" in r.text

    r = client.delete(f"/api/v1/ai/chat/session/{sid}")
    assert r.json()["ok"] is True
    assert client.get("/api/v1/ai/chat/session").json()["sessions"] == []


def test_free_chat_session_persisted(client: TestClient, monkeypatch):
    """自由对话持久化：SSE 返回 session 事件；会话落盘；标题取首条问题。"""
    import app.routers.chat as chat_mod

    client.post("/api/v1/ai/config", json={
        "enabled": True,
        "providers": [{"id": "p1", "name": "甲", "base_url": "https://a.com/v1", "api_key": "k", "models": []}],
        "chat": {"provider_id": "p1", "model": "m"},
    })

    def fake_stream(cfg, messages, temperature=0.3, max_tokens=1024):
        yield "你好！"

    monkeypatch.setattr(chat_mod, "stream_chat", fake_stream)

    r = client.post("/api/v1/ai/chat", json={"message": "介绍一下知识库", "mode": "free"})
    assert r.status_code == 200
    assert '"type": "session"' in r.text  # 未传 session_id 也自动创建并返回
    assert "你好！" in r.text

    from app.services.chat_sessions import ChatSessionStore

    store = client.app.state.chat_sessions
    assert isinstance(store, ChatSessionStore)
    sessions = client.get("/api/v1/ai/chat/session").json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["title"] == "介绍一下知识库"

    # 重启语义：从文件重新加载仍有会话
    fresh = ChatSessionStore(store.path)
    assert len(fresh.ls()) == 1


def test_action_endpoint_error_when_disabled(client: TestClient):
    r = client.post("/api/v1/ai/action", json={"text": "选中的文本", "action": "续写"})
    assert r.status_code == 200
    assert "AI 未启用" in r.text


def _import(client: TestClient, dir: str, name: str, content: bytes, ctype: str = "application/octet-stream") -> dict:
    files = {"file": (name, content, ctype)}
    r = client.post("/api/v1/import", params={"dir": dir}, files=files)
    assert r.status_code == 200, r.text
    return r.json()


def test_import_pdf_and_txt_into_dir(client: TestClient):
    # 生成一个含文本的 PDF（reportlab 默认字体不支持中文，用 ASCII 关键词）
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 500, "PDF document with keyword zhilian")
    c.save()
    data = buf.getvalue()

    r = _import(client, "docs", "手册.pdf", data, "application/pdf")
    assert r["path"] == "docs/手册.pdf"
    assert r["parsed_chars"] > 0

    # 目录列表出现该文件
    entries = client.get("/api/v1/tree", params={"path": "docs"}).json()["entries"]
    assert any(e["path"] == "docs/手册.pdf" for e in entries)

    # txt 导入并进入检索
    r2 = _import(client, "docs", "说明.txt", "txt 里的独特词 xyzsearch。".encode("utf-8"))
    assert r2["parsed_chars"] > 0

    # RAG 检索命中文档内容（FTS-only 降级路径）
    from app.rag.retriever import RagIndexer

    rag: RagIndexer = client.app.state.rag
    hits = rag.search("zhilian", k=5)
    assert any(h["file_path"] == "docs/手册.pdf" for h in hits)
    hits2 = rag.search("独特词", k=5)
    assert any(h["file_path"] == "docs/说明.txt" for h in hits2)


def test_import_unsupported_type_rejected(client: TestClient):
    files = {"file": ("evil.exe", b"MZ", "application/octet-stream")}
    r = client.post("/api/v1/import", files=files)
    assert r.status_code == 400
    assert "不支持" in r.text


def test_import_markdown_indexed(client: TestClient):
    r = _import(client, "", "note2.md", "# 导入的笔记\n\n导入内容检索词 abcxyz。\n".encode("utf-8"))
    assert r["path"] == "note2.md"
    from app.rag.retriever import RagIndexer

    rag: RagIndexer = client.app.state.rag
    hits = rag.search("abcxyz", k=3)
    assert any(h["file_path"] == "note2.md" for h in hits)


def test_rebuild_covers_documents(client: TestClient):
    _create(client, "note.md", "# 普通笔记\n\n正文内容也要占一个块。\n")
    _import(client, "", "说明.txt", "txt 文档内容 rebuildword123。".encode("utf-8"))
    r = client.post("/api/v1/ai/rebuild")
    assert r.status_code == 200
    data = r.json()
    assert data["reindexed"] >= 2  # .md + .txt 都进入索引
    assert data["chunks"] >= 2


def test_delete_document_clears_chunks(client: TestClient):
    _import(client, "", "临时.txt", "临时文档内容 tmpword456。".encode("utf-8"))
    client.post("/api/v1/ai/rebuild")
    before = client.get("/api/v1/ai/status").json()["chunks"]
    client.delete("/api/v1/files", params={"path": "临时.txt"})
    after = client.get("/api/v1/ai/status").json()["chunks"]
    assert after < before


def test_import_docx_indexed(client: TestClient):
    from docx import Document

    doc = Document()
    doc.add_paragraph("Word 文档里的检索词 docxword777。")
    buf = io.BytesIO()
    doc.save(buf)

    r = _import(client, "", "报告.docx", buf.getvalue(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert r["path"] == "报告.docx"
    assert r["parsed_chars"] > 0

    from app.rag.retriever import RagIndexer

    rag: RagIndexer = client.app.state.rag
    hits = rag.search("docxword777", k=5)
    assert any(h["file_path"] == "报告.docx" for h in hits)


def test_vault_import_file_renames_on_conflict(client: TestClient):
    from app.services.vault import Vault

    vault: Vault = client.app.state.vault
    vault.import_file("docs/a.txt", b"first")
    node = vault.import_file("docs/a.txt", b"second")
    assert node.path != "docs/a.txt"
    assert node.path.startswith("docs/a-")
    assert (vault.root / "docs" / "a.txt").read_bytes() == b"first"


def test_move_document_reindexes(client: TestClient):
    _import(client, "", "移动.txt", "移动文档内容 moveword999。".encode("utf-8"))
    client.post("/api/v1/ai/rebuild")
    r = client.post("/api/v1/files/move", json={"src": "移动.txt", "dst": "sub/移动.txt"})
    assert r.status_code == 200, r.text
    from app.rag.retriever import RagIndexer

    rag: RagIndexer = client.app.state.rag
    hits = rag.search("moveword999", k=5)
    assert any(h["file_path"] == "sub/移动.txt" for h in hits)


def _create(client: TestClient, path: str, content: str) -> None:
    r = client.post("/api/v1/files", json={"path": path, "type": "file", "initial_content": content})
    assert r.status_code == 200, r.text


# ---------- 3.1 Embedding 模型签名重建 ----------


def _configure_embedding(client: TestClient, model: str, enabled: bool = True) -> dict:
    r = client.post("/api/v1/ai/config", json={
        "enabled": enabled,
        "providers": [{"id": "p1", "name": "甲", "base_url": "https://a.com/v1", "api_key": "k", "models": []}],
        "embedding": {"provider_id": "p1", "model": model, "batch": 8},
    })
    assert r.status_code == 200, r.text
    return r.json()


def test_embedding_signature_first_save_no_reset(client: TestClient):
    """首次保存：写签名但不重置（无历史向量）。"""
    _create(client, "a.md", "# A\n\n内容。\n")
    client.post("/api/v1/ai/rebuild")
    data = _configure_embedding(client, "embed-v1")
    assert data["embedding_changed"] is False


def test_embedding_model_change_resets_vectors(client: TestClient):
    """模型变更：事务删除 embeddings、chunks 全部 ai_indexed=0。"""
    _create(client, "a.md", "# A\n\n需要嵌入的内容。\n")
    client.post("/api/v1/ai/rebuild")
    _configure_embedding(client, "embed-v1")

    db = client.app.state.db
    conn = db.connect()
    # 模拟已嵌入：所有 chunks 标记 indexed + 插入向量
    conn.execute("INSERT INTO embeddings(chunk_id,model,dims,vector) SELECT id,'embed-v1',2,x'0000000000000000' FROM chunks")
    conn.execute("UPDATE chunks SET ai_indexed=1")
    conn.commit()
    assert conn.execute("SELECT count(*) FROM embeddings").fetchone()[0] >= 1

    data = _configure_embedding(client, "embed-v2")
    assert data["embedding_changed"] is True
    conn = db.connect()
    assert conn.execute("SELECT count(*) FROM embeddings").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM chunks WHERE ai_indexed=0").fetchone()[0] >= 1


def test_embedding_key_change_does_not_rebuild(client: TestClient):
    """仅 api_key 变化：不重建（Key 不参与签名）。"""
    _create(client, "a.md", "# A\n\n内容。\n")
    client.post("/api/v1/ai/rebuild")
    _configure_embedding(client, "embed-v1")
    db = client.app.state.db
    conn = db.connect()
    conn.execute("UPDATE chunks SET ai_indexed=1")
    conn.commit()

    r = client.post("/api/v1/ai/config", json={
        "enabled": True,
        "providers": [{"id": "p1", "name": "甲", "base_url": "https://a.com/v1", "api_key": "new-key", "models": []}],
        "embedding": {"provider_id": "p1", "model": "embed-v1"},
    })
    assert r.status_code == 200
    assert r.json()["embedding_changed"] is False
    conn = db.connect()
    assert conn.execute("SELECT count(*) FROM chunks WHERE ai_indexed=1").fetchone()[0] >= 1


# ---------- 3.2 向量批次严格校验 ----------


def test_store_embeddings_strict_validation(client: TestClient):
    from app.rag.retriever import RagIndexer

    _create(client, "a.md", "# A\n\n" + "校验内容段落。" * 300 + "\n\n## 第二节\n\n更多内容。\n")
    client.post("/api/v1/ai/rebuild")
    rag: RagIndexer = client.app.state.rag
    pending = rag.pending_chunks(8)
    assert len(pending) >= 2  # 长内容产生多块，供混合维度构造

    # 数量不一致 → ValueError
    import pytest

    ids = [cid for cid, _ in pending]
    with pytest.raises(ValueError, match="不一致"):
        rag.store_embeddings(ids, [[0.1] * 4] * (len(ids) + 1), "m")
    # 维度不一致 → ValueError（混合维度）
    mixed = [[0.1] * 4] * (len(pending) - 1) + [[0.1] * 5]
    with pytest.raises(ValueError, match="维度"):
        rag.store_embeddings([cid for cid, _ in pending], mixed, "m")
    # 空向量 → ValueError
    with pytest.raises(ValueError, match="空向量"):
        rag.store_embeddings([cid for cid, _ in pending], [[]] * len(pending), "m")

    # 失败后 chunks 仍为未嵌入（可重试，不静默标完成）
    conn = rag.db.connect()
    assert conn.execute("SELECT count(*) FROM chunks WHERE ai_indexed=1").fetchone()[0] == 0

    # 合法批次：只标记写入的 chunk
    rag.store_embeddings([cid for cid, _ in pending], [[0.25] * 4] * len(pending), "m")
    conn = rag.db.connect()
    assert conn.execute("SELECT count(*) FROM chunks WHERE ai_indexed=1").fetchone()[0] == len(pending)
    assert conn.execute("SELECT count(*) FROM embeddings").fetchone()[0] == len(pending)


# ---------- 2.5 普通索引重建不得删除 RAG 数据 ----------


def test_normal_rebuild_preserves_embeddings(client: TestClient):
    """/index/rebuild（普通索引）后：chunks/embeddings 保留（id/model/vector 在）。"""
    _create(client, "rag-note.md", "# RAG 笔记\n\n嵌入向量要保留。\n")
    client.post("/api/v1/ai/rebuild")

    db = client.app.state.db
    conn = db.connect()
    before_chunks = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
    assert before_chunks >= 1

    # 手工插入一条向量，模拟已嵌入状态
    conn.execute(
        "INSERT INTO embeddings(chunk_id,model,dims,vector) SELECT id,'test-model',2,x'0000000000000000' FROM chunks WHERE file_path='rag-note.md' LIMIT 1"
    )
    conn.commit()
    emb_row = conn.execute("SELECT chunk_id,model FROM embeddings LIMIT 1").fetchone()
    assert emb_row is not None

    # 普通索引重建（不是 /ai/rebuild）
    r = client.post("/api/v1/index/rebuild")
    assert r.status_code == 200, r.text

    conn = db.connect()
    after_chunks = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
    assert after_chunks == before_chunks  # chunks 保留
    kept = conn.execute("SELECT chunk_id,model FROM embeddings LIMIT 1").fetchone()
    assert kept is not None
    assert kept["chunk_id"] == emb_row["chunk_id"]
    assert kept["model"] == "test-model"


def test_normal_rebuild_cleans_stale_markdown(client: TestClient):
    """普通重建清理 Vault 已不存在的 Markdown 行，但保留文档占位。"""
    _create(client, "stale.md", "# 将要删除\n")
    _import(client, "", "keep.txt", "文档内容 keepme99。".encode("utf-8"))

    db = client.app.state.db
    conn = db.connect()
    conn.execute("SELECT count(*) FROM files_meta WHERE path='stale.md'").fetchone()
    conn.execute("SELECT count(*) FROM files_meta WHERE path='keep.txt'").fetchone()

    # 删除 stale.md（走回收站 → files_meta 应已被清理），再直接向 db 插入一条 stale 记录
    import sqlite3

    from app.services.vault import Vault

    vault: Vault = client.app.state.vault
    vault.delete("stale.md")
    conn.execute(
        "INSERT INTO files_meta(path,title,mtime_ns,size,sha1,tags,indexed_at) VALUES('ghost.md','ghost',1,1,'x','','now')"
    )
    conn.commit()

    r = client.post("/api/v1/index/rebuild")
    assert r.status_code == 200, r.text

    conn = db.connect()
    assert conn.execute("SELECT count(*) FROM files_meta WHERE path='ghost.md'").fetchone()[0] == 0
    # 文档占位保留（非 .md）
    assert conn.execute("SELECT count(*) FROM files_meta WHERE path='keep.txt'").fetchone()[0] == 1
