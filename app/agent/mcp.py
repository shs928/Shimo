"""MCP client：外部 MCP Server 工具接入 Agent（SDK 2.x）。

- 专用 asyncio 事件循环线程：所有连接/发现/调用/关闭通过
  run_coroutine_threadsafe 在同一 loop 执行，与请求线程/uvicorn 主循环隔离。
- transport 显式配置：streamable_http（默认）/ sse_legacy（旧配置迁移）。
- 工具发现带 TTL 缓存；call_tool 正确处理 is_error / 文本 / structured content。
- 输出长度限制；非文本块只返回安全摘要。
- 配置校验：URL 必须 http(s)、server 名唯一且合法。
- 安全：MCP 工具默认需确认并受 allowlist 控制（见 agent/tools.py authorize）。
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
import time

logger = logging.getLogger(__name__)

_TOOL_TTL = 300.0  # 工具发现缓存 TTL（秒）
_MAX_OUTPUT = 8000  # MCP 输出总长限制（字符）
_MAX_BLOCK = 4000  # 单个文本块长度

_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_TRANSPORTS = {"streamable_http", "sse_legacy"}


class McpConfigError(ValueError):
    """MCP 配置非法（URL / 名称 / transport）。"""


class McpConnection:
    """单个 server 的连接状态（仅在专用 loop 线程内使用）。"""

    def __init__(self, name: str, url: str, transport: str):
        self.name = name
        self.url = url
        self.transport = transport
        self.client = None
        self._streams = None
        self._gen = None
        self._discovered_at = 0.0
        self._tools: list[dict] = []

    def stale(self) -> bool:
        return time.monotonic() - self._discovered_at > _TOOL_TTL


class McpManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._config: dict[str, dict] = {}  # name -> {"url", "transport"}
        self._clients: dict[str, McpConnection] = {}

    # ---------- 生命周期（lifespan 调用） ----------

    def start(self) -> None:
        """启动专用事件循环线程（幂等）。"""
        with self._lock:
            if self._thread is not None:
                return
            loop = asyncio.new_event_loop()
            self._loop = loop
            self._thread = threading.Thread(
                target=self._run_loop, args=(loop,), daemon=True, name="mcp-loop"
            )
            self._thread.start()

    @staticmethod
    def _run_loop(loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()

    def stop(self) -> None:
        """关闭所有连接并停止事件循环（幂等）。"""
        with self._lock:
            thread, loop = self._thread, self._loop
        if loop is None or thread is None:
            return
        try:
            if self._clients:
                self._run_on(loop, self._close_all(), timeout=15)
        except Exception:
            logger.warning("MCP close failed", exc_info=True)
        with self._lock:
            self._thread = None
            self._loop = None
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)

    async def _close_all(self) -> None:
        for conn in list(self._clients.values()):
            await self._close_conn(conn)
        self._clients.clear()

    # ---------- 配置 ----------

    @staticmethod
    def _validate_server(name: str, url: str, transport: str) -> None:
        if not name or not _NAME_RE.match(name):
            raise McpConfigError(f"MCP server 名称非法：{name!r}（仅允许字母/数字/._-，≤64 字符）")
        if not url.startswith(("http://", "https://")):
            raise McpConfigError(f"MCP server URL 必须为 http(s)：{url!r}")
        if transport not in _TRANSPORTS:
            raise McpConfigError(f"MCP transport 非法：{transport!r}（可选 {sorted(_TRANSPORTS)}）")

    def configure(self, servers: list) -> None:
        """同步配置（幂等）：新增/变更的连接刷新；移除的断开。校验在应用层执行。"""
        with self._lock:
            # 校验：名称唯一 + 每条合法
            seen: set[str] = set()
            for s in servers:
                name, url = s.name, s.url
                transport = getattr(s, "transport", None) or "sse_legacy"
                self._validate_server(name, url, transport)
                if name in seen:
                    raise McpConfigError(f"MCP server 名称重复：{name}")
                seen.add(name)

            new_config = {s.name: {"url": s.url, "transport": getattr(s, "transport", None) or "sse_legacy"} for s in servers}

            # 断开：已移除 或 url/transport 变更
            for name in list(self._clients.keys()):
                cfg = new_config.get(name)
                if cfg is None or cfg["url"] != self._config[name]["url"] or cfg["transport"] != self._config[name]["transport"]:
                    conn = self._clients.pop(name, None)
                    if conn is not None and self._loop is not None:
                        try:
                            self._run(self._close_conn(conn), timeout=10)
                        except Exception:
                            logger.warning("MCP disconnect failed: %s", name)
            self._config = new_config

    # ---------- 工具 ----------

    def server_names(self) -> list[str]:
        """当前配置的 server 名称列表。"""
        with self._lock:
            return list(self._config.keys())

    def tool_entries(self) -> list[dict]:
        """已连接 server 的工具列表（TTL 内复用；过期则刷新发现）。

        供 Agent 拼 tools 参数；allowlist 过滤在 tools.enabled_schemas 完成。
        """
        with self._lock:
            entries: list[dict] = []
            for name, conn in list(self._clients.items()):
                if conn.stale() and self._loop is not None:
                    try:
                        self._run(self._discover(conn), timeout=10)
                    except Exception as exc:
                        logger.warning("MCP tool discovery failed %s: %s", name, exc)
                for tool in conn._tools:
                    entries.append({
                        "name": f"mcp__{conn.name}__{tool['name']}",
                        "server": conn.name,
                        "description": tool.get("description") or "",
                        "input_schema": tool.get("input_schema"),
                    })
            return entries

    def call(self, name: str, args: dict) -> tuple[str, str]:
        """执行 mcp__server__tool；正确处理 is_error / 文本 / structured content。"""
        try:
            _, server, tool = name.split("__", 2)
        except ValueError:
            return "error", f"非法 MCP 工具名：{name}"
        with self._lock:
            conn = self._clients.get(server)
            if conn is None:
                return "error", f"MCP server 未连接：{server}"
            if conn.client is None:
                return "error", f"MCP server 未就绪：{server}"
        try:
            result = self._run(conn.client.call_tool(tool, args), timeout=120)
        except Exception as exc:
            return "error", f"MCP 调用失败：{exc}"

        if result.is_error:
            return "error", _extract_text(result) or "MCP 工具返回错误"

        text = _extract_text(result)
        if not text:
            return "ok", "(空结果)"
        if len(text) > _MAX_OUTPUT:
            text = text[:_MAX_OUTPUT] + f"\n…（输出已截断，共 {len(text)} 字符）"
        return "ok", text

    # ---------- 连接 ----------

    def ensure_connected(self, name: str) -> None:
        """按需建连（懒连接）：工具发现/调用前确保已连接。"""
        with self._lock:
            if name in self._clients:
                return
            cfg = self._config.get(name)
            if cfg is None or self._loop is None:
                return
            conn = McpConnection(name, cfg["url"], cfg["transport"])
            try:
                self._run(self._discover(conn), timeout=15)
            except Exception as exc:
                logger.warning("MCP connect failed %s: %s", name, exc)
                return
            self._clients[name] = conn

    async def _discover(self, conn: McpConnection) -> None:
        """建立连接并发现工具（在专用 loop 内执行）。"""
        await self._close_conn(conn)
        if conn.transport == "streamable_http":
            from mcp.client.streamable_http import streamable_http_client

            gen = streamable_http_client(conn.url)
        else:
            from mcp.client.sse import sse_client

            gen = sse_client(conn.url)

        from mcp import Client

        streams = await gen.__aenter__()
        conn._gen = gen
        client = Client(streams.read_stream, streams.write_stream)
        await client.__aenter__()
        conn.client = client
        tools = await client.list_tools()
        conn._tools = [
            {
                "name": t.name,
                "description": t.description or "",
                "input_schema": t.input_schema,
            }
            for t in (tools.tools or [])
        ]
        conn._discovered_at = time.monotonic()

    async def _close_conn(self, conn: McpConnection) -> None:
        """断开单个连接（幂等，在专用 loop 内执行）。"""
        if conn.client is not None:
            try:
                await conn.client.__aexit__(None, None, None)
            except Exception:
                pass
            conn.client = None
        if conn._gen is not None:
            try:
                await conn._gen.__aexit__(None, None, None)
            except Exception:
                pass
            conn._gen = None
        conn._tools = []

    # ---------- 状态 / 工具 ----------

    def _run(self, coro, timeout: float):
        """在专用 loop 上执行协程并等待结果。"""
        if self._loop is None:
            raise RuntimeError("MCP 事件循环未启动")
        return self._run_on(self._loop, coro, timeout)

    @staticmethod
    def _run_on(loop: asyncio.AbstractEventLoop, coro, timeout: float):
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        return fut.result(timeout=timeout)

    def status(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "name": name,
                    "url": cfg["url"],
                    "transport": cfg["transport"],
                    "connected": name in self._clients,
                    "tools": len(self._clients[name]._tools) if name in self._clients else 0,
                }
                for name, cfg in self._config.items()
            ]


def _extract_text(result) -> str:
    """从 CallToolResult 提取文本；非文本块返回安全摘要。"""
    parts: list[str] = []
    for block in result.content or []:
        btype = getattr(block, "type", "")
        if btype == "text":
            text = getattr(block, "text", "") or ""
            if len(text) > _MAX_BLOCK:
                text = text[:_MAX_BLOCK] + "\n…（块已截断）"
            parts.append(text)
        elif btype == "image":
            data = getattr(block, "data", "") or ""
            mime = getattr(block, "mime_type", "") or getattr(block, "mimeType", "") or "image"
            parts.append(f"[图片 {mime}，约 {len(data) * 3 // 4 // 1024} KB]")
        else:
            parts.append(f"[非文本内容：{btype}]")
    text = "\n".join(parts)
    if len(text) > _MAX_OUTPUT:
        text = text[:_MAX_OUTPUT] + "\n…（输出已截断）"
    return text
