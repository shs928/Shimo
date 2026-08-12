"""MCP client：外部 MCP Server（SSE 传输）工具接入 Agent。

按需连接：Agent 请求时懒建立会话，工具调用前确保已连接。
mcp SDK 仅在本模块导入，mock 测试不依赖真实 SDK。

实现说明：每条 SSE 连接使用独立 asyncio 事件循环（会话创建与调用复用同一
循环），避免与请求线程/uvicorn 主循环纠缠。
"""
from __future__ import annotations

import asyncio
import logging
import threading

logger = logging.getLogger(__name__)


class McpManager:
    def __init__(self):
        self._clients: dict[str, dict] = {}  # name -> {"session", "ctx", "loop"}
        self._tools: dict[str, list] = {}
        self._urls: dict[str, str] = {}
        self._lock = threading.RLock()

    # ---------- 配置 ----------

    def configure(self, servers: list) -> None:
        """同步当前配置：不在列表中的 server 断开并移除。"""
        with self._lock:
            self._urls = {s.name: s.url for s in servers}
            for name in list(self._clients.keys()):
                if name not in self._urls:
                    self._disconnect(name)

    def _disconnect(self, name: str) -> None:
        entry = self._clients.pop(name, None)
        self._tools.pop(name, None)
        if entry is None:
            return
        loop, ctx = entry.get("loop"), entry.get("ctx")
        if ctx is not None and loop is not None:
            try:
                loop.run_until_complete(ctx.__aexit__(None, None, None))
                loop.close()
            except Exception:
                logger.warning("MCP disconnect failed: %s", name)

    # ---------- 工具 ----------

    def tool_entries(self) -> list[dict]:
        """当前已连接 server 的可用工具列表（用于拼 Agent 的 tools 参数）。"""
        with self._lock:
            entries = []
            for name, server_tools in self._tools.items():
                for tool in server_tools:
                    entries.append({
                        "name": f"mcp__{name}__{tool['name']}",
                        "server": name,
                        "description": tool.get("description") or "",
                        "input_schema": tool.get("inputSchema"),
                    })
            return entries

    def call(self, name: str, args: dict) -> tuple[str, str]:
        """执行 mcp__server__tool；name 形如 mcp__filesystem__read_file。"""
        try:
            _, server, tool = name.split("__", 2)
        except ValueError:
            return "error", f"非法 MCP 工具名：{name}"
        entry = self._ensure_client(server)
        if entry is None:
            return "error", f"MCP server 未连接：{server}"

        from mcp.types import CallToolRequest, CallToolRequestParams

        loop = entry["loop"]

        async def run():
            session = entry["session"]
            result = await session.call_tool(
                CallToolRequest(params=CallToolRequestParams(name=tool, arguments=args))
            )
            texts = []
            for c in result.content or []:
                if getattr(c, "type", "") == "text":
                    texts.append(getattr(c, "text", ""))
            return "\n".join(texts)

        try:
            text = loop.run_until_complete(run())
            return "ok", text or "(空结果)"
        except Exception as exc:
            return "error", f"MCP 调用失败：{exc}"

    # ---------- 连接 ----------

    def _ensure_client(self, name: str):
        with self._lock:
            if name in self._clients:
                return self._clients[name]
        url = self._urls.get(name, "")
        if not url:
            return None
        try:
            from mcp import ClientSession
            from mcp.client.sse import sse_client

            loop = asyncio.new_event_loop()
            entry = {}

            async def connect():
                ctx = sse_client(url)
                read_stream, write_stream = await ctx.__aenter__()
                session = ClientSession(read_stream, write_stream)
                await session.__aenter__()
                await session.initialize()
                tools = await session.list_tools()
                entry.update(ctx=ctx, session=session, tools=tools)

            loop.run_until_complete(connect())
            entry["loop"] = loop
            with self._lock:
                self._clients[name] = entry
                self._tools[name] = [t for t in (entry["tools"].tools or [])]
            return entry
        except Exception as exc:
            logger.warning("MCP connect failed %s: %s", name, exc)
            return None

    def status(self) -> list[dict]:
        """当前配置与连接状态。"""
        with self._lock:
            return [
                {"name": name, "url": url, "connected": name in self._clients, "tools": len(self._tools.get(name, []))}
                for name, url in self._urls.items()
            ]
