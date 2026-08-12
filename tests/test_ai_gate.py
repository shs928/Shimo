"""2.2 AI 全局外呼闸门测试：AI 关闭时 Embedding/Rerank 不得外呼。"""
from __future__ import annotations

import pytest


def _save_config(client, enabled: bool, with_rerank: bool = False) -> None:
    payload = {
        "enabled": enabled,
        "providers": [
            {"id": "p1", "name": "甲", "base_url": "https://a.com/v1", "api_key": "k",
             "models": ["chat-m", "embed-m", "rerank-m"]},
        ],
        "chat": {"provider_id": "p1", "model": "chat-m"},
        "embedding": {"provider_id": "p1", "model": "embed-m", "batch": 8},
        "rerank": {"enabled": with_rerank, "provider_id": "p1", "model": "rerank-m"},
    }
    r = client.post("/api/v1/ai/config", json=payload)
    assert r.status_code == 200


def test_disabled_ai_does_not_call_embedding_or_rerank(client, monkeypatch):
    """AI 关闭时：semantic-search 不得调用 embedding / rerank（FTS-only）。"""
    _save_config(client, enabled=False, with_rerank=True)

    calls = {"embed": 0, "rerank": 0}
    import app.rag.provider as provider

    def fake_embed(cfg, texts):
        calls["embed"] += 1
        return [[0.1] * 8 for _ in texts]

    def fake_rerank(cfg, query, docs, top_n=None):
        calls["rerank"] += 1
        return [(i, 0.9 - i * 0.1) for i in range(len(docs))]

    monkeypatch.setattr(provider, "embed_texts", fake_embed)
    monkeypatch.setattr(provider, "rerank", fake_rerank)

    r = client.post("/api/v1/ai/semantic-search", json={"query": "测试", "k": 5})
    assert r.status_code == 200
    assert calls == {"embed": 0, "rerank": 0}


def test_enabled_ai_calls_embedding(client, monkeypatch):
    """AI 启用时：embedding 正常调用；rerank 未启用则不调用。"""
    _save_config(client, enabled=True, with_rerank=False)

    calls = {"embed": 0, "rerank": 0}
    import app.rag.provider as provider

    def fake_embed(cfg, texts):
        calls["embed"] += 1
        return [[0.1] * 8 for _ in texts]

    monkeypatch.setattr(provider, "embed_texts", fake_embed)

    r = client.post("/api/v1/ai/semantic-search", json={"query": "测试", "k": 5})
    assert r.status_code == 200
    assert calls["embed"] == 1
    assert calls["rerank"] == 0


def test_disabled_ai_agent_search_uses_fts_only(client, monkeypatch):
    """Agent knowledge_search：AI 关闭时不得调用 embedding。"""
    _save_config(client, enabled=False)
    from app.agent.engine import stream_agent
    from app.agent.tools import ToolContext

    import app.rag.provider as provider

    calls = {"embed": 0}

    def fake_embed(cfg, texts):
        calls["embed"] += 1
        return [[0.1] * 8 for _ in texts]

    monkeypatch.setattr(provider, "embed_texts", fake_embed)

    # 构造直接调用工具层的场景（knowledge_search 内部）
    ctx = ToolContext(
        vault=client.app.state.vault,
        indexer=client.app.state.indexer,
        rag=client.app.state.rag,
        settings_provider=lambda: client.app.state.ai_store.load(),
        registry=client.app.state.agent_registry,
        mcp_manager=client.app.state.mcp_manager,
        ai_store=client.app.state.ai_store,
    )
    from app.agent.tools import execute

    status, _ = execute(ctx, "knowledge_search", {"query": "笔记"})
    assert status == "ok"  # FTS-only 仍可检索
    assert calls["embed"] == 0


def test_active_config_respects_enabled(client):
    """active_embedding_config / active_rerank_config 随 enabled 切换。"""
    _save_config(client, enabled=False, with_rerank=True)
    s = client.app.state.ai_store.load()
    assert s.embedding_config() is not None  # 已配置（状态展示用）
    assert s.active_embedding_config() is None  # 但不可外呼
    assert s.active_rerank_config() is None

    _save_config(client, enabled=True, with_rerank=True)
    s = client.app.state.ai_store.load()
    assert s.active_embedding_config() is not None
    assert s.active_rerank_config() is not None


def test_status_splits_configured_active_worker(client):
    """/ai/status 状态拆分：configured / active / worker_alive。"""
    _save_config(client, enabled=False)
    r = client.get("/api/v1/ai/status").json()
    assert r["embed_configured"] is True
    assert r["embedding_active"] is False
    assert "worker_alive" in r
    assert "embedding_running" not in r  # 旧字段移除

    _save_config(client, enabled=True)
    r = client.get("/api/v1/ai/status").json()
    assert r["embedding_active"] is True
