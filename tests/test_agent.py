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
    _configure_agent(client)
    client.post("/api/v1/ai/config", json={"agent": {"tools": {"sql": True}}})
    ctx = _make_ctx(client)
    status, result = agent_tools.execute(ctx, "sql", {"query": "DELETE FROM chunks"})
    assert status == "error"
    assert "SELECT" in result


def test_sql_tool_readonly_select(client: TestClient):
    _configure_agent(client)
    client.post("/api/v1/ai/config", json={"agent": {"tools": {"sql": True}}})
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


# ---------- 3.4 Agent 会话可靠持久化 ----------


def test_agent_session_saves_full_history(client: TestClient, monkeypatch):
    """会话持久化完整历史：工具调用与执行结果（不止可见文本）。"""
    from app.rag import provider as provider_mod

    _configure_agent(client)
    monkeypatch.setattr(provider_mod, "stream_chat_events", _fake_events([
        [("content", "我先搜索。"), ("tool_call", _tc(0, "knowledge_search", '{"query": "测试"}')), ("done", "tool_calls")],
        [("content", "找到答案。"), ("done", "stop")],
    ]))

    r = client.post("/api/v1/ai/agent/session", json={"messages": []})
    sid = r.json()["session"]["id"]

    r = client.post("/api/v1/ai/agent/chat", json={"message": "帮我查一下", "session_id": sid})
    assert r.status_code == 200
    assert "done" in r.text

    data = client.get(f"/api/v1/ai/agent/session/{sid}").json()["session"]
    msgs = data["messages"]
    # 包含用户消息、助手消息、tool_call、tool_result
    assert any(m["role"] == "user" and m["content"] == "帮我查一下" for m in msgs)
    assert any(m["role"] == "assistant" and m["content"] for m in msgs)
    assert any(m["role"] == "tool_call" and m["tool"] == "knowledge_search" for m in msgs)
    assert any(m.get("status") == "ok" for m in msgs if m["role"] == "tool_call")
    # 标题默认取首条用户问题
    assert data["title"] == "帮我查一下"


def test_agent_max_iterations_returns_error(client: TestClient, monkeypatch):
    """达到 max_iterations：返回明确错误，不发虚假 done。"""
    from app.rag import provider as provider_mod

    _configure_agent(client)
    client.post("/api/v1/ai/config", json={"agent": {"max_iterations": 2}})
    # 模型每轮都要求调用工具（2 轮用尽）
    monkeypatch.setattr(provider_mod, "stream_chat_events", _fake_events([
        [("tool_call", _tc(0, "knowledge_search", '{"query": "x"}')), ("done", "tool_calls")],
        [("tool_call", _tc(0, "knowledge_search", '{"query": "x"}')), ("done", "tool_calls")],
    ]))

    ctx = _make_ctx(client)
    s = client.app.state.ai_store.load()
    events = list(stream_agent(ctx, s, [{"role": "user", "content": "查"}], max_iterations=2))
    errors = [e for e in events if e["type"] == "error"]
    assert errors and "最大迭代轮数" in errors[0]["error"]


def test_agent_route_no_fake_done_on_exhaustion(client: TestClient, monkeypatch):
    """路由层：达到 max_iterations 时 error 事件替代虚假 done。"""
    from app.rag import provider as provider_mod

    _configure_agent(client)
    client.post("/api/v1/ai/config", json={"agent": {"max_iterations": 2}})
    monkeypatch.setattr(provider_mod, "stream_chat_events", _fake_events([
        [("tool_call", _tc(0, "knowledge_search", '{"query": "x"}')), ("done", "tool_calls")],
        [("tool_call", _tc(0, "knowledge_search", '{"query": "x"}')), ("done", "tool_calls")],
    ]))

    r = client.post("/api/v1/ai/agent/chat", json={"message": "查一下"})
    assert "最大迭代轮数" in r.text
    assert r.text.strip().count('"type": "done"') == 0


# ---------- 2.3 工具权限 fail-closed ----------


def test_authorize_disabled_write_tool_rejects(client: TestClient):
    """禁用写工具：authorize 返回 reject（不发确认、不执行）。"""
    _configure_agent(client)
    client.post("/api/v1/ai/config", json={"agent": {"tools": {"update_note": False}}})
    ctx = _make_ctx(client)
    assert agent_tools.authorize(ctx, "update_note") == "reject"
    status, result = agent_tools.execute(ctx, "update_note", {"path": "a.md", "content": "x"})
    assert status == "error"
    assert "已禁用" in result


def test_authorize_disabled_readonly_tool_rejects(client: TestClient):
    """禁用只读工具：拒绝执行。"""
    _configure_agent(client)
    client.post("/api/v1/ai/config", json={"agent": {"tools": {"read_note": False}}})
    ctx = _make_ctx(client)
    assert agent_tools.authorize(ctx, "read_note") == "reject"
    status, _ = agent_tools.execute(ctx, "read_note", {"path": "欢迎.md"})
    assert status == "error"


def test_authorize_unknown_tool_rejects(client: TestClient):
    """未知工具：拒绝；execute 二次校验也拒绝。"""
    _configure_agent(client)
    ctx = _make_ctx(client)
    assert agent_tools.authorize(ctx, "evil_tool") == "reject"
    status, result = agent_tools.execute(ctx, "evil_tool", {})
    assert status == "error"
    assert "未知工具" in result


def test_authorize_reads_latest_config(client: TestClient):
    """每次执行前重新读取最新配置：运行中禁用即时生效。"""
    _configure_agent(client)
    ctx = _make_ctx(client)
    assert agent_tools.authorize(ctx, "read_note") == "run"
    client.post("/api/v1/ai/config", json={"agent": {"tools": {"read_note": False}}})
    assert agent_tools.authorize(ctx, "read_note") == "reject"
    client.post("/api/v1/ai/config", json={"agent": {"tools": {"read_note": True}}})
    assert agent_tools.authorize(ctx, "read_note") == "run"


def test_mcp_tool_not_in_allowlist_rejected(client: TestClient):
    """MCP 工具：未配置 allowlist → reject；配置后 → confirm（一律需确认）。"""
    _configure_agent(client)
    ctx = _make_ctx(client)
    assert agent_tools.authorize(ctx, "mcp__fs__read_file") == "reject"
    status, result = agent_tools.execute(ctx, "mcp__fs__read_file", {"path": "/"})
    assert status == "error"
    assert "未启用" in result

    client.post("/api/v1/ai/config", json={
        "agent": {"tools": {"mcp__fs__read_file": True}},
    })
    assert agent_tools.authorize(ctx, "mcp__fs__read_file") == "confirm"
    status, _ = agent_tools.execute(ctx, "mcp__fs__read_file", {"path": "/"})
    assert status == "error"  # 未连接 server，但授权层已放行到执行层


def test_engine_rejects_disabled_tool_without_confirm(client: TestClient, monkeypatch):
    """引擎层：模型调用被禁用工具 → denied（不弹确认卡）。"""
    from app.rag import provider as provider_mod

    _configure_agent(client)
    client.post("/api/v1/ai/config", json={"agent": {"tools": {"update_note": False}}})
    monkeypatch.setattr(provider_mod, "stream_chat_events", _fake_events([
        [("tool_call", _tc(0, "update_note", '{"path": "c.md", "content": "x"}')), ("done", "tool_calls")],
        [("content", "好的。"), ("done", "stop")],
    ]))

    ctx = _make_ctx(client)
    s = client.app.state.ai_store.load()
    events = list(stream_agent(ctx, s, [{"role": "user", "content": "更新笔记"}], max_iterations=4))
    confirms = [e for e in events if e["type"] == "confirm"]
    results = [e for e in events if e["type"] == "tool_result"]
    assert not confirms  # 不弹确认卡
    assert results and results[0]["status"] == "denied"
    assert not client.app.state.vault.root.joinpath("c.md").exists()


# ---------- 2.4 image.analyze 路径越界 ----------


def _make_image(ctx, rel: str, content: bytes | None = None) -> None:
    full = ctx.vault.root.joinpath(rel)
    full.parent.mkdir(parents=True, exist_ok=True)
    if content is None:
        # 真实 PNG（Pillow 校验需要有效图片）
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (4, 4), "red").save(buf, format="PNG")
        content = buf.getvalue()
    full.write_bytes(content)


def test_image_analyze_rejects_escape_paths(client: TestClient):
    _configure_agent(client)
    ctx = _make_ctx(client)
    for bad in ["/etc/passwd", "../outside.png", "C:\\x.png", "a/../../b.png"]:
        status, result = agent_tools.execute(ctx, "image.analyze", {"path": bad})
        assert status == "error", bad
        assert "非法图片路径" in result or "不允许" in result


def test_image_analyze_rejects_hidden_and_trash(client: TestClient):
    _configure_agent(client)
    ctx = _make_ctx(client)
    _make_image(ctx, ".hidden/x.png")
    _make_image(ctx, ".trash/y.png")
    status, result = agent_tools.execute(ctx, "image.analyze", {"path": ".hidden/x.png"})
    assert status == "error"
    assert "不允许访问隐藏路径" in result
    status, result = agent_tools.execute(ctx, "image.analyze", {"path": ".trash/y.png"})
    assert status == "error"
    assert "不允许访问隐藏路径" in result


@pytest.mark.skipif(
    not __import__("tests.test_path_guard", fromlist=["_SYMLINK_OK"])._SYMLINK_OK,
    reason="当前环境无符号链接权限",
)
def test_image_analyze_rejects_symlink(client: TestClient, tmp_path):
    import os

    _configure_agent(client)
    ctx = _make_ctx(client)
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"\x89PNG-fake")
    os.symlink(outside, ctx.vault.root / "link.png")
    status, result = agent_tools.execute(ctx, "image.analyze", {"path": "link.png"})
    assert status == "error"
    assert "不允许通过符号链接" in result or "非法图片路径" in result


def test_image_analyze_rejects_oversize(client: TestClient):
    _configure_agent(client)
    ctx = _make_ctx(client)
    _make_image(ctx, "big.png", b"x" * (10 * 1024 * 1024 + 1))
    status, result = agent_tools.execute(ctx, "image.analyze", {"path": "big.png"})
    assert status == "error"
    assert "过大" in result


def test_image_analyze_rejects_non_image(client: TestClient):
    _configure_agent(client)
    ctx = _make_ctx(client)
    _make_image(ctx, "note.md", b"# hello")
    status, result = agent_tools.execute(ctx, "image.analyze", {"path": "note.md"})
    assert status == "error"
    assert "不是支持的图片类型" in result


def test_image_analyze_valid_path_reaches_model_call(client: TestClient, monkeypatch):
    """正常图片路径通过全部校验后，应到达模型调用层。"""
    _configure_agent(client)
    ctx = _make_ctx(client)
    _make_image(ctx, "assets/ok.png")

    from app.rag import provider as provider_mod

    calls = {"n": 0}

    def fake_chat_complete(cfg, messages, tools=None, tool_choice=None, temperature=0.3, max_tokens=1024):
        calls["n"] += 1
        from app.rag.provider import ChatResult

        return ChatResult(content="这是一张测试图片。")

    monkeypatch.setattr(provider_mod, "chat_complete", fake_chat_complete)
    status, result = agent_tools.execute(ctx, "image.analyze", {"path": "assets/ok.png"})
    assert status == "ok"
    assert result == "这是一张测试图片。"
    assert calls["n"] == 1
