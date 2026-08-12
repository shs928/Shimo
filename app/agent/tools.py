"""Agent 原生工具：schema 定义 + 处理器。

- 只读工具（search/read/list/sql）：模型直接调用。
- 写工具（create_note/update_note/image.generate）：需用户确认，由引擎在调用前
  发出 confirm 事件并等待决策。
- 图片理解 image.analyze：读取附件转 base64，走多模态消息。
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path

from ..rag.provider import ProviderConfig, generate_image
from ..services.vault import Vault, VaultError
from .system_prompt import DEFAULT_AGENT_SYSTEM_PROMPT

_SAFE_NAME_RE = re.compile(r"^[\w\u4e00-\u9fff][\w\u4e00-\u9fff ._-]*$")
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".avif"}

# OpenAI 兼容 function 定义（给 LLM 的 tools 参数）
TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "knowledge_search",
            "description": "在个人知识库中检索与关键词相关的笔记片段，返回来源路径与原文。回答知识库问题前优先调用。",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "搜索关键词"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_note",
            "description": "读取知识库中一篇笔记的完整内容。路径形如 'dir/note.md'。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "笔记相对路径"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_notes",
            "description": "列出知识库目录下的笔记与子目录（Markdown 文件）。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "目录相对路径，空为根目录"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sql",
            "description": "对知识库索引数据库执行只读 SQL 查询（SELECT），返回最多 50 行。",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "只读 SELECT 语句"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_note",
            "description": "在知识库中新建一篇 Markdown 笔记（写入前需用户确认）。路径不存在则创建父目录。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "笔记相对路径，如 'projects/x.md'"},
                    "content": {"type": "string", "description": "Markdown 内容"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_note",
            "description": "覆盖更新一篇已存在的笔记内容（写入前需用户确认）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "笔记相对路径"},
                    "content": {"type": "string", "description": "新的 Markdown 内容"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "image.analyze",
            "description": "理解知识库附件中的一张图片（PNG/JPG/WebP 等），返回图片内容的文字描述。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "图片相对路径，如 'assets/x.png'"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "image.generate",
            "description": "根据提示词生成一张图片，保存到知识库 assets/ 目录（写入前需用户确认）。",
            "parameters": {
                "type": "object",
                "properties": {"prompt": {"type": "string", "description": "图片描述"}},
                "required": ["prompt"],
            },
        },
    },
]

TOOL_NAMES = {t["function"]["name"] for t in TOOL_SCHEMAS}
WRITE_TOOLS = {"create_note", "update_note", "image.generate"}


class ToolContext:
    """处理器所需的运行时依赖，由路由按请求注入。"""

    def __init__(self, vault: Vault, indexer, rag, settings_provider, registry, mcp_manager, ai_store, history=None):
        self.vault = vault
        self.indexer = indexer
        self.rag = rag
        self.settings_provider = settings_provider  # () -> AiSettings
        self.registry = registry
        self.mcp_manager = mcp_manager
        self.ai_store = ai_store
        self.history = history  # HistoryStore | None（Agent 更新进入历史链路）


def enabled_schemas(ctx: ToolContext) -> list[dict]:
    """拼 Agent 的 tools 参数：启用的原生工具 + allowlist 内的 MCP 工具。

    MCP 工具默认不注入（fail-closed）：只有 agent.tools 中显式开启的
    `mcp__server__tool` 键才出现在 schema 中。构建前先按需发现（带 TTL）。
    """
    s = ctx.settings_provider()
    schemas = [t for t in TOOL_SCHEMAS if s.tool_enabled(t["function"]["name"])]
    for name in ctx.mcp_manager.server_names():
        ctx.mcp_manager.ensure_connected(name)
    for tool in ctx.mcp_manager.tool_entries():
        full = f"mcp__{tool['server']}__{tool['name']}"
        if not s.tool_enabled(full):
            continue
        schemas.append({
            "type": "function",
            "function": {
                "name": full,
                "description": f"[MCP:{tool['server']}] {tool['description']}",
                "parameters": tool["input_schema"] or {"type": "object", "properties": {}},
            },
        })
    return schemas


def authorize(ctx: ToolContext, name: str) -> str:
    """统一授权（fail-closed）：每次执行前重新读取最新配置。

    返回：
      "run"     只读且启用
      "confirm" 有副作用且启用（写工具 / MCP 工具一律需确认）
      "reject"  未知、被禁用、或 MCP 不在 allowlist
    """
    s = ctx.settings_provider()
    if name.startswith("mcp__"):
        # MCP 工具外部副作用未知：默认需确认，未在 allowlist 中 → 拒绝
        return "confirm" if s.tool_enabled(name) else "reject"
    if name not in TOOL_NAMES:
        return "reject"
    if not s.tool_enabled(name):
        return "reject"
    return "confirm" if name in WRITE_TOOLS else "run"


def tool_summary(name: str, args: dict) -> str:
    """给确认卡片展示的简短摘要。"""
    if name == "create_note":
        return f"新建笔记 {args.get('path')}（{len(args.get('content') or '')} 字符）"
    if name == "update_note":
        return f"更新笔记 {args.get('path')}（{len(args.get('content') or '')} 字符）"
    if name == "image.generate":
        return f"生成图片：{args.get('prompt') or ''}"
    return f"{name}({json.dumps(args, ensure_ascii=False)[:80]})"


def execute(ctx: ToolContext, name: str, args: dict) -> tuple[str, str]:
    """执行工具，返回 (status, result_text)。status ∈ ok|error。

    二次校验：未知 / 被禁用工具拒绝执行（防止绕过 engine 的授权流程直接调用）。
    """
    s = ctx.settings_provider()
    if name.startswith("mcp__"):
        if not s.tool_enabled(name):
            return "error", f"MCP 工具未启用或不在允许列表：{name}"
    elif name not in TOOL_NAMES:
        return "error", f"未知工具：{name}"
    elif not s.tool_enabled(name):
        return "error", f"工具已禁用：{name}"

    try:
        if name == "knowledge_search":
            return _search(ctx, args)
        if name == "read_note":
            return _read(ctx, args)
        if name == "list_notes":
            return _list(ctx, args)
        if name == "sql":
            return _sql(ctx, args)
        if name == "create_note":
            return _create(ctx, args)
        if name == "update_note":
            return _update(ctx, args)
        if name == "image.analyze":
            return _analyze(ctx, args)
        if name == "image.generate":
            return _generate(ctx, args)
        if name.startswith("mcp__"):
            return ctx.mcp_manager.call(name, args)
        return "error", f"未知工具：{name}"
    except VaultError as exc:
        return "error", str(exc)
    except Exception as exc:  # 工具失败不中断 Agent 循环
        return "error", f"工具执行失败：{exc}"


def _search(ctx: ToolContext, args: dict) -> tuple[str, str]:
    query = str(args.get("query") or "")
    if not query:
        return "error", "缺少 query 参数"
    s = ctx.settings_provider()
    # 外呼闸门：AI 关闭时 active_embedding_config() 返回 None → FTS-only，不调 Embedding
    embed_cfg = s.active_embedding_config()
    results = ctx.rag.search(query, k=5, embedding_cfg=embed_cfg)
    if not results:
        return "ok", "知识库中没有检索到相关内容。"
    lines = []
    for r in results:
        heading = r.get("heading") or ""
        lines.append(f"### [{r['file_path']}]({r['file_path']})" + (f" → {heading}" if heading else ""))
        lines.append(r["text"])
    return "ok", "\n\n".join(lines)


def _read(ctx: ToolContext, args: dict) -> tuple[str, str]:
    path = str(args.get("path") or "")
    fc = ctx.vault.read_markdown(path)
    return "ok", f"[{path}]\n\n{fc.content}"


def _list(ctx: ToolContext, args: dict) -> tuple[str, str]:
    path = str(args.get("path") or "")
    entries = ctx.vault.list_children(path)
    if not entries:
        return "ok", f"目录 {path or '/'} 为空。"
    lines = []
    for e in entries:
        lines.append(("📁 " if e.type == "dir" else "") + e.path)
    return "ok", "\n".join(lines)


def _sql(ctx: ToolContext, args: dict) -> tuple[str, str]:
    query = str(args.get("query") or "").strip()
    if not query.lower().startswith("select"):
        return "error", "只允许 SELECT 查询"
    import sqlite3

    db_path = ctx.rag.db.path
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(query).fetchmany(50)
            cols = list(rows[0].keys()) if rows else []
            out = ["\t".join(cols)] + ["\t".join(str(r[c]) for c in cols) for r in rows]
            return "ok", "\n".join(out)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return "error", f"SQL 执行失败：{exc}"


def _create(ctx: ToolContext, args: dict) -> tuple[str, str]:
    path = str(args.get("path") or "").strip()
    content = str(args.get("content") or "")
    if not _valid_note_path(path):
        return "error", f"非法笔记路径：{path}"
    node = ctx.vault.create(path, "file", content)
    try:
        ctx.indexer.index_file(node.path)
        ctx.rag.reindex_file(node.path, content)
    except Exception:
        pass
    return "ok", f"已创建笔记 {node.path}"


def _update(ctx: ToolContext, args: dict) -> tuple[str, str]:
    path = str(args.get("path") or "").strip()
    content = str(args.get("content") or "")
    if not _valid_note_path(path):
        return "error", f"非法笔记路径：{path}"
    on_before = None
    if ctx.history is not None:
        on_before = lambda rel, text, etag: ctx.history.save_snapshot(rel, text)
    result = ctx.vault.write_markdown(path, content, None, on_before_write=on_before)
    try:
        ctx.indexer.index_file(path)
        ctx.rag.reindex_file(path, result.content)
    except Exception:
        pass
    return "ok", f"已更新笔记 {path}"


def _analyze(ctx: ToolContext, args: dict) -> tuple[str, str]:
    path = str(args.get("path") or "").strip()
    if not path:
        return "error", "缺少 path 参数"
    # 路径边界：拒绝绝对路径 / .. 穿越 / 符号链接 / 隐藏路径 / 回收站
    try:
        from ..services.path_guard import is_hidden_rel, normalize_rel, resolve_in_root

        rel = normalize_rel(path)
        if is_hidden_rel(rel) or rel.startswith(".trash"):
            return "error", f"不允许访问隐藏路径或回收站：{path}"
        full = resolve_in_root(ctx.vault.root, rel)
    except Exception as exc:
        return "error", f"非法图片路径：{exc}"
    if not full.is_file():
        return "error", f"文件不存在：{path}"
    ext = full.suffix.lower()
    if ext not in _IMAGE_EXT:
        return "error", f"不是支持的图片类型：{path}"
    # 先 stat 检查原始大小，再读取（避免读入超大文件）
    try:
        size = full.stat().st_size
    except OSError as exc:
        return "error", f"无法访问文件：{exc}"
    if size <= 0:
        return "error", f"图片为空：{path}"
    if size > 10 * 1024 * 1024:
        return "error", "图片过大（超过 10MB），无法分析"
    try:
        data = full.read_bytes()
    except OSError as exc:
        return "error", f"读取失败：{exc}"
    # Pillow 校验真实图片格式（防止伪装扩展名）
    from ..services.safe_download import validate_image

    try:
        validate_image(data)
    except Exception as exc:
        return "error", f"不是有效的图片文件：{exc}"
    b64 = base64.b64encode(data).decode("ascii")
    mime = _mime_for(ext)
    s = ctx.settings_provider()
    cfg = s.vision_config() or s.agent_config()  # 优先独立 Vision 组，回退 Agent 模型
    if cfg is None:
        return "error", "未配置图片理解模型（Vision 或 Agent 配置）"
    from ..rag.provider import chat_complete

    result = chat_complete(
        cfg,
        [
            {"role": "user", "content": [
                {"type": "text", "text": "请描述这张图片的内容。"},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ]},
        ],
        temperature=0.2,
        max_tokens=512,
    )
    return ("ok", result.content) if result.content else ("error", "模型未返回内容")


def _generate(ctx: ToolContext, args: dict) -> tuple[str, str]:
    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        return "error", "缺少 prompt 参数"
    s = ctx.settings_provider()
    cfg = s.vision_config() or s.agent_config()  # 优先独立 Vision 组，回退 Agent 模型
    if cfg is None:
        return "error", "未配置图片生成模型（Vision 或 Agent 配置）"
    images = generate_image(cfg, prompt)
    if not images:
        return "error", "图片生成服务未返回结果"
    item = images[0]

    from ..services.safe_download import decode_b64, download_image, validate_image

    data = item.get("b64_json")
    if data:
        raw = decode_b64(data)
    else:
        url = item.get("url")
        if not url:
            return "error", "图片生成服务未返回数据"
        raw = download_image(url)  # SSRF 防护：非公网 IP / 重定向逐跳校验
    try:
        ext = validate_image(raw)  # Pillow 校验真实格式，按真实格式保存扩展名
    except Exception as exc:
        return "error", f"生成结果不是有效图片：{exc}"
    rel = ctx.vault.create_unique_asset(f"ai-{_slug(prompt)[:20]}{ext}", raw)
    return "ok", f"图片已保存到 {rel}"


def _valid_note_path(path: str) -> bool:
    if not path.endswith(".md"):
        return False
    parts = [p for p in path.split("/") if p]
    if not parts:
        return False
    return all(_SAFE_NAME_RE.match(p) for p in parts) and not any(p.startswith(".") for p in parts)


def _slug(text: str) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "-", text).strip("-")
    return text or "image"


def _mime_for(ext: str) -> str:
    return {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp", ".avif": "image/avif",
    }.get(ext, "image/png")
