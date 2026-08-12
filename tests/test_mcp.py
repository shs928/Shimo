"""MCP Manager 测试：注册、工具包装、状态管理（mock，不依赖真实 mcp SDK）。"""
from __future__ import annotations

import pytest


@pytest.fixture
def manager():
    from app.agent.mcp import McpManager

    return McpManager()


def test_configure_and_status(manager):
    class FakeServer:
        name = "fs"
        url = "https://mcp.example.com/sse"

    manager.configure([FakeServer()])
    st = manager.status()
    assert len(st) == 1
    assert st[0]["name"] == "fs"
    assert st[0]["connected"] is False


def test_configure_removes_removed_servers(manager):
    class A:
        name = "a"
        url = "u1"

    class B:
        name = "b"
        url = "u2"

    manager.configure([A()])
    manager._clients["a"] = {"session": None, "ctx": None, "loop": None}
    manager.configure([B()])
    assert "a" not in manager._clients
    assert [s["name"] for s in manager.status()] == ["b"]


def test_call_invalid_name(manager):
    status, result = manager.call("bad", {})
    assert status == "error"


def test_call_unconnected_server(manager):
    class A:
        name = "a"
        url = "https://mcp.example.com/sse"

    manager.configure([A()])
    status, result = manager.call("mcp__a__list", {})
    assert status == "error"
    assert "未连接" in result


def test_tool_entries_empty_without_connection(manager):
    class A:
        name = "a"
        url = "https://mcp.example.com/sse"

    manager.configure([A()])
    assert manager.tool_entries() == []


def test_tool_entries_from_connected_tools(manager):
    manager._tools["a"] = [
        {"name": "list", "description": "d", "inputSchema": {"type": "object", "properties": {}}}
    ]
    entries = manager.tool_entries()
    assert entries == [{
        "name": "mcp__a__list", "server": "a", "description": "d",
        "input_schema": {"type": "object", "properties": {}},
    }]
