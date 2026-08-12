"""Agent 引擎测试：function calling 循环、写操作确认、工具约束、会话 CRUD。

沿用项目约定：不发起真实外部网络调用，mock provider 层（stream_chat_events）。
"""
from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.agent import tools as agent_tools
from app.agent.engine import stream_agent
from app.agent.registry import AgentRegistry
from app.agent.tools import ToolContext
from app.rag.provider import ToolCall


def _tc(index: int, name: str, arguments: str) -> ToolCall:
    return ToolCall(id=f"call-{index}", name=name, arguments=arguments, index=index)


def _configure_agent(client: TestClient) -> None:
    r = client.post("/api/v1/ai/config", json={
        "enabled": True,
        "providers": [{"id": "p1", "name": "test", "base_url": "https://example.com/v1", "api_key": "k", "models": []}],
        "chat": {"provider_id": "p1", "model": "m"},
        "agent": {"provider_id": "p1", "model": "m", "max_iterations": 6},
    })
    assert r.status_code == 200, r.text


def _make_ctx(client: TestClient) -> ToolContext:
    st = client.app.state
    return ToolContext(
        vault=st.vault,
        indexer=st.indexer,
        rag=st.rag,
        settings_provider=lambda: st.ai_store.load(),
        registry=st.agent_registry,
        mcp_manager=st.mcp_manager,
        ai_store=st.ai_store,
    )


def _fake_events(sequence: list[list[tuple]]):
    """将预设事件序列转成 stream_chat_events 的 mock 实现（按调用轮次返回）。"""
    calls = {"n": 0}

    def impl(cfg, messages, tools=None, tool_choice=None, temperature=0.3, max_tokens=1024):
        idx = calls["n"]
        calls["n"] += 1
        if idx >= len(sequence):
            return iter([("done", "")])
        return iter(sequence[idx])

    return impl


def test_engine_readonly_tool_loop(client: TestClient, monkeypatch):
    from app.rag import provider as provider_mod

    _configure_agent(client)
    monkeypatch.setattr(provider_mod, "stream_chat_events", _fake_events([
        [("content", "我先搜索。"), ("tool_call", _tc(0, "knowledge_search", '{"query": "测试"}')), ("done", "tool_calls")],
        [("content", "答案：测试内容。"), ("done", "stop")],
    ]))

    ctx = _make_ctx(client)
    s = client.app.state.ai_store.load()
    events = list(stream_agent(ctx, s, [{"role": "user", "content": "查一下"}], max_iterations=4))
    text = "".join(e["content"] for e in events if e["type"] == "delta")
    assert "答案：测试内容" in text
    tool_calls = [e for e in events if e["type"] == "tool_call"]
    assert tool_calls and tool_calls[0]["tool"] == "knowledge_search"
    results = [e for e in events if e["type"] == "tool_result"]
    assert results and results[0]["status"] == "ok"


def test_engine_write_tool_allows_after_confirm(client: TestClient, monkeypatch):
    from app.rag import provider as provider_mod

    _configure_agent(client)
    monkeypatch.setattr(provider_mod, "stream_chat_events", _fake_events([
        [("tool_call", _tc(0, "create_note", '{"path": "a.md", "content": "hello"}')), ("done", "tool_calls")],
        [("content", "已创建。"), ("done", "stop")],
    ]))

    ctx = _make_ctx(client)
    s = client.app.state.ai_store.load()
    results: list[dict] = []

    def consume():
        results.extend(list(stream_agent(ctx, s, [{"role": "user", "content": "创建笔记"}], max_iterations=4)))

    t = threading.Thread(target=consume, daemon=True)
    t.start()
    # 等待确认注册出现，然后允许
    for _ in range(200):
        with ctx.registry._lock:
            if ctx.registry._pending:
                break
        time.sleep(0.05)
    with ctx.registry._lock:
        assert ctx.registry._pending, "未出现确认注册"
        request_id = next(iter(ctx.registry._pending))
    assert ctx.registry.resolve(request_id, "allow")
    t.join(timeout=10)

    confirms = [e for e in results if e["type"] == "confirm"]
    assert confirms and confirms[0]["tool"] == "create_note"
    assert "已创建" in "".join(e["content"] for e in results if e["type"] == "delta")
    assert client.app.state.vault.root.joinpath("a.md").is_file()


def test_engine_write_tool_denied(client: TestClient, monkeypatch):
    from app.rag import provider as provider_mod

    _configure_agent(client)
    monkeypatch.setattr(provider_mod, "stream_chat_events", _fake_events([
        [("tool_call", _tc(0, "create_note", '{"path": "b.md", "content": "x"}')), ("done", "tool_calls")],
        [("content", "好的，不创建。"), ("done", "stop")],
    ]))

    ctx = _make_ctx(client)
    s = client.app.state.ai_store.load()
    results: list[dict] = []

    def consume():
        results.extend(list(stream_agent(ctx, s, [{"role": "user", "content": "创建笔记"}], max_iterations=4)))

    t = threading.Thread(target=consume, daemon=True)
    t.start()
    for _ in range(200):
        with ctx.registry._lock:
            if ctx.registry._pending:
                break
        time.sleep(0.05)
    with ctx.registry._lock:
        request_id = next(iter(ctx.registry._pending))
    assert ctx.registry.resolve(request_id, "deny")
    t.join(timeout=10)

    denied = [e for e in results if e["type"] == "confirm_denied"]
    assert denied
    assert not client.app.state.vault.root.joinpath("b.md").exists()


def test_sql_tool_rejects_non_select(client: TestClient):
    ctx = _make_ctx(client)
    status, result = agent_tools.execute(ctx, "sql", {"query": "DELETE FROM chunks"})
    assert status == "error"
    assert "SELECT" in result


def test_sql_tool_readonly_select(client: TestClient):
    ctx = _make_ctx(client)
    status, result = agent_tools.execute(ctx, "sql", {"query": "SELECT count(*) AS n FROM files_meta"})
    assert status == "ok"
    assert "n" in result


def test_create_tool_validates_path(client: TestClient):
    ctx = _make_ctx(client)
    status, _ = agent_tools.execute(ctx, "create_note", {"path": "../escape.md", "content": "x"})
    assert status == "error"
    status, _ = agent_tools.execute(ctx, "create_note", {"path": "ok.md", "content": "x"})
    assert status == "ok"


def test_agent_session_crud(client: TestClient):
    r = client.post("/api/v1/ai/agent/session", json={
        "messages": [{"role": "user", "content": "hi"}], "title": "t1",
    })
    assert r.status_code == 200
    sid = r.json()["session"]["id"]

    r = client.get(f"/api/v1/ai/agent/session/{sid}")
    assert r.json()["session"]["title"] == "t1"

    r = client.get("/api/v1/ai/agent/sessions")
    assert any(s["id"] == sid for s in r.json()["sessions"])

    r = client.delete(f"/api/v1/ai/agent/session/{sid}")
    assert r.json()["ok"] is True
    assert client.app.state.agent_sessions.get(sid) is None


def test_agent_chat_error_when_disabled(client: TestClient):
    r = client.post("/api/v1/ai/agent/chat", json={"message": "你好"})
    assert r.status_code == 200
    assert "AI 未启用" in r.text


def test_agent_confirm_unknown_request(client: TestClient):
    r = client.post("/api/v1/ai/agent/confirm", json={"request_id": "nope", "decision": "allow"})
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_agent_tools_endpoint(client: TestClient):
    r = client.get("/api/v1/ai/agent/lsTools")
    assert r.status_code == 200
    names = {t["name"] for t in r.json()["tools"]}
    assert "knowledge_search" in names
    assert "create_note" in names
