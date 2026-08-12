"""MCP Manager 测试：SDK 2.x in-process server（真实 Tool 对象，不依赖真实外网）。

- 用 mcp.server.lowlevel.Server + InMemoryTransport 建立进程内 server；
- McpManager 的网络层（_discover）替换为内存连接，其余（配置/生命周期/
  工具组装/call 处理/输出限制）走真实 SDK 2.x 客户端。
"""
from __future__ import annotations

import time

import pytest
from mcp.client._memory import InMemoryTransport
from mcp.client._transport import TransportStreams
from mcp.server.lowlevel import Server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ImageContent,
    ListToolsResult,
    TextContent,
    Tool,
)

from app.agent.mcp import McpConfigError, McpManager


def _echo_server(long_text: str = "", image: bool = False) -> Server:
    """in-process server：echo 工具 + 可选超长输出 / 图片块。"""

    async def on_list_tools(ctx, params):
        return ListToolsResult(tools=[
            Tool(name="echo", description="回声", input_schema={
                "type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"],
            }),
        ])

    async def on_call_tool(ctx, params: CallToolRequestParams):
        if params.name == "echo":
            if image:
                return CallToolResult(content=[ImageContent(type="image", data="AA==", mimeType="image/png")], is_error=False)
            if long_text:
                return CallToolResult(content=[TextContent(type="text", text=long_text)], is_error=False)
            return CallToolResult(content=[TextContent(type="text", text=f"echo:{params.arguments.get('text', '')}")], is_error=False)
        return CallToolResult(content=[TextContent(type="text", text="unknown")], is_error=True)

    return Server("echo-server", on_list_tools=on_list_tools, on_call_tool=on_call_tool)


def _attach_memory_discovery(manager: McpManager, server: Server) -> None:
    """把 manager 的发现逻辑替换为内存连接（真实 Client + 真实 Tool 对象）。"""

    async def fake_discover(self, conn) -> None:
        from mcp import Client

        transport = InMemoryTransport(server)
        client = Client(transport)
        await client.__aenter__()
        conn.client = client
        conn._gen = None
        tools = await client.list_tools()
        conn._tools = [
            {"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
            for t in (tools.tools or [])
        ]
        conn._discovered_at = time.monotonic()

    import app.agent.mcp as mcp_mod

    mcp_mod.McpManager._discover = fake_discover  # type: ignore[method-assign]


class FakeServerCfg:
    def __init__(self, name: str, url: str, transport: str = "streamable_http"):
        self.name = name
        self.url = url
        self.transport = transport


@pytest.fixture
def manager():
    m = McpManager()
    m.start()
    yield m
    m.stop()


def test_full_flow_with_inprocess_server(manager):
    """完整链路：configure → 发现 → tool_entries → call（真实 Tool 对象）。"""
    _attach_memory_discovery(manager, _echo_server())
    manager.configure([FakeServerCfg("echo", "https://mcp.local")])
    manager.ensure_connected("echo")

    entries = manager.tool_entries()
    assert entries == [{
        "name": "mcp__echo__echo", "server": "echo", "description": "回声",
        "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    }]

    status, result = manager.call("mcp__echo__echo", {"text": "你好"})
    assert status == "ok"
    assert result == "echo:你好"


def test_call_is_error_reported(manager):
    server = _echo_server()
    manager = McpManager()
    manager.start()
    _attach_memory_discovery(manager, server)
    manager.configure([FakeServerCfg("echo", "https://mcp.local")])
    manager.ensure_connected("echo")
    # 未知工具 → server 返回 is_error=True
    status, result = manager.call("mcp__echo__nope", {})
    assert status == "error"
    manager.stop()


def test_call_output_truncated(manager):
    long_text = "长" * 20000
    _attach_memory_discovery(manager, _echo_server(long_text=long_text))
    manager.configure([FakeServerCfg("echo", "https://mcp.local")])
    manager.ensure_connected("echo")
    status, result = manager.call("mcp__echo__echo", {"text": "x"})
    assert status == "ok"
    assert len(result) <= 8000 + 100
    assert "截断" in result


def test_call_non_text_block_summary(manager):
    _attach_memory_discovery(manager, _echo_server(image=True))
    manager.configure([FakeServerCfg("echo", "https://mcp.local")])
    manager.ensure_connected("echo")
    status, result = manager.call("mcp__echo__echo", {"text": "x"})
    assert status == "ok"
    assert "图片" in result
    assert "image/png" in result


def test_configure_validation_rejects_bad_url(manager):
    with pytest.raises(McpConfigError, match="http"):
        manager.configure([FakeServerCfg("bad", "ftp://x")])
    with pytest.raises(McpConfigError, match="http"):
        manager.configure([FakeServerCfg("bad", "not-a-url")])


def test_configure_validation_rejects_duplicate_and_bad_name(manager):
    with pytest.raises(McpConfigError, match="重复"):
        manager.configure([
            FakeServerCfg("dup", "https://a.local"),
            FakeServerCfg("dup", "https://b.local"),
        ])
    with pytest.raises(McpConfigError, match="名称非法"):
        manager.configure([FakeServerCfg("bad name!", "https://a.local")])
    with pytest.raises(McpConfigError, match="transport"):
        manager.configure([FakeServerCfg("x", "https://a.local", transport="udp")])


def test_configure_refreshes_changed_servers(manager):
    _attach_memory_discovery(manager, _echo_server())
    manager.configure([FakeServerCfg("echo", "https://mcp.local")])
    manager.ensure_connected("echo")
    assert "echo" in manager._clients
    # URL 变更 → 断开重连
    manager.configure([FakeServerCfg("echo", "https://mcp2.local")])
    assert "echo" not in manager._clients
    manager.ensure_connected("echo")
    assert "echo" in manager._clients
    # 移除 → 断开
    manager.configure([])
    assert "echo" not in manager._clients
    assert manager.status() == []


def test_configure_legacy_transport_migration(manager):
    """无 transport 字段（旧配置）→ 默认 sse_legacy（由 AiStore 保证）。"""
    from app.rag.ai_store import McpServer

    s = McpServer(name="old", url="https://mcp.local/sse")
    assert s.transport == "sse_legacy"
    s2 = McpServer(name="new", url="https://mcp.local", transport="streamable_http")
    assert s2.transport == "streamable_http"


def test_status_reflects_connection(manager):
    manager.configure([FakeServerCfg("echo", "https://mcp.local")])
    st = manager.status()
    assert st[0]["name"] == "echo"
    assert st[0]["connected"] is False
    assert st[0]["transport"] == "streamable_http"


def test_call_invalid_and_unconnected(manager):
    status, result = manager.call("bad", {})
    assert status == "error"
    assert "非法" in result
    manager.configure([FakeServerCfg("echo", "https://mcp.local")])
    status, result = manager.call("mcp__echo__echo", {})
    assert status == "error"
    assert "未连接" in result


def test_manager_start_stop_idempotent():
    m = McpManager()
    m.start()
    m.start()  # 幂等
    assert m._thread is not None
    m.stop()
    m.stop()  # 幂等
    assert m._thread is None
