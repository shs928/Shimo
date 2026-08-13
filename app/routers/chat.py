"""AI 路由：RAG 问答、自由对话、选中文本操作、语义搜索、模型与索引管理。

- /ai/chat：mode=rag（检索注入）| free（直接对话，带多轮会话）。
- /ai/action：选中文本 AI 操作（续写/摘要/…），对齐 chatGPTWithAction。
- /ai/semantic-search：向量 / FTS 检索独立端点（可选 Rerank 精排）。
- /ai/embedding-stat、/ai/list-models、/ai/rebuild、/ai/config、/ai/status、/ai/test。

固定约束：模型不可直接调用工具（那是 Agent 的能力）、不可修改文件；
AI 未配置时全部接口给出明确状态，不发起外部请求。
"""
from __future__ import annotations

import json
import os
from collections import deque
from pathlib import Path
from typing import Iterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..deps import csrf_guard, get_indexer, get_vault, require_auth
from ..rag.ai_store import AiSettings
from ..rag.provider import ProviderConfig, ProviderError, chat_ping, list_models, rerank, stream_chat
from ..services.doc_parser import is_document
from ..services.indexer import Indexer
from ..services.vault import Vault

router = APIRouter(prefix="/api/v1/ai", tags=["ai"], dependencies=[Depends(require_auth)])
_write_deps = [Depends(csrf_guard)]

_RAG_SYSTEM_PROMPT = """你是个人知识库的 AI 助手，结合「知识库笔记」与「你自身的知识能力」给出高质量回答。

回答原则：
1. 提供的知识片段是重要参考：与问题相关的片段内容作为回答的主干，并给相关结论标注来源编号（如 [1]）。
2. 发挥你自身的能力：当片段不足以完整回答时，用你自己的知识解释、补充、延伸，让答案完整实用；
   不要因为"笔记里没有"就拒绝回答或只说不知道。
3. 清晰区分来源：来自笔记的结论标注 [n]；来自你自身知识的内容用"据我所知""一般来说"等措辞区分，
   绝不要把自身知识伪装成笔记内容，也绝不要给笔记里没有的信息标注来源编号。
4. 知识库没有相关内容时（片段显示"未检索到"），直接用自己的知识正常回答，
   开头用一句话说明"知识库中没有检索到相关笔记"即可，不要反复强调。
5. 笔记内容可能过时、片面或相互矛盾：如有疑问可以指出，并给出你的判断。
6. 回答使用 Markdown；来源编号放在对应结论之后。
- 片段中每行开头形如 [1] path → 标题 的，是来源编号。"""

_FREE_SYSTEM_PROMPT = "你是通用 AI 助手。回答使用 Markdown 格式。"


def _ai(request: Request):
    return request.app.state.ai_store


def _rag(request: Request):
    return request.app.state.rag


def _settings(request: Request) -> AiSettings:
    return _ai(request).load()


def _sessions(request: Request):
    """自由对话会话存储（持久化到 data/chat_sessions.json）。"""
    store = getattr(request.app.state, "chat_sessions", None)
    if store is None:
        from ..services.chat_sessions import ChatSessionStore

        store = ChatSessionStore(Path(request.app.state.config.data_path) / "chat_sessions.json")
        request.app.state.chat_sessions = store
    return store


# ---------- 请求模型 ----------


class AiConfigIn(BaseModel):
    enabled: bool | None = None
    providers: list | None = None
    chat: dict | None = None
    embedding: dict | None = None
    rerank: dict | None = None
    vision: dict | None = None
    agent: dict | None = None
    ocr: dict | None = None
    mcp: dict | None = None


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    mode: str = Field(default="rag", pattern="^(rag|free)$")
    session_id: str | None = None


class ActionIn(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    action: str = Field(min_length=1, max_length=2000)


class SemanticSearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    k: int = Field(default=5, ge=1, le=50)


class ListModelsIn(BaseModel):
    provider_id: str = Field(min_length=1)


# ---------- 状态 / 配置 ----------


@router.get("/status")
def status(request: Request) -> dict:
    s = _settings(request)
    rag = _rag(request)
    job = getattr(request.app.state, "embedding_job", None)
    chat_cfg = s.chat_config()
    embed_cfg = s.embedding_config()
    # 状态拆分：configured（已配置）/ active（启用且配置）/ worker_alive（调度器存活）
    return {
        "enabled": s.enabled,
        "chat_configured": chat_cfg is not None,
        "embed_configured": embed_cfg is not None,
        "embedding_active": s.active_embedding_config() is not None,
        "agent_configured": s.agent_config() is not None,
        "providers": len(s.providers),
        "chunks": rag.count_chunks(),
        "embedded": rag.count_embedded(),
        "has_vectors": rag.has_vectors(),
        "worker_alive": bool(job and job.running),
        "mcp_servers": len(s.mcp_servers),
    }


@router.get("/config")
def get_config(request: Request) -> dict:
    """返回当前配置（脱敏：不返回 api_key）。"""
    s = _settings(request)
    return {
        "enabled": s.enabled,
        "providers": [
            {"id": p.id, "name": p.name, "base_url": p.base_url, "models": p.models,
             "has_key": bool(p.api_key)}
            for p in s.providers
        ],
        "chat": {
            "provider_id": s.chat.provider_id, "model": s.chat.model,
            "temperature": s.chat.temperature, "max_tokens": s.chat.max_tokens,
            "max_history_messages": s.chat.max_history_messages,
        },
        "embedding": {"provider_id": s.embedding.provider_id, "model": s.embedding.model, "batch": s.embedding.batch},
        "rerank": {"enabled": s.rerank.enabled, "provider_id": s.rerank.provider_id, "model": s.rerank.model},
        "vision": {"provider_id": s.vision.provider_id, "model": s.vision.model},
        "ocr": {"enabled": s.ocr.enabled},
        "agent": {
            "provider_id": s.agent.provider_id, "model": s.agent.model,
            "max_iterations": s.agent.max_iterations, "system_prompt": s.agent.system_prompt,
            "tools": s.agent.tools,
        },
        "mcp": {"servers": [{"name": m.name, "url": m.url, "transport": m.transport} for m in s.mcp_servers]},
    }


@router.post("/config", dependencies=_write_deps)
def save_config(payload: AiConfigIn, request: Request) -> dict:
    ai = _ai(request)
    s = ai.save(payload.model_dump(exclude_none=True))
    # 模型签名变化 → 立即重置向量库（事务内），前端提示"后台重新嵌入"
    embedding_changed = False
    embed = s.active_embedding_config()
    if embed is not None:
        try:
            embedding_changed = _rag(request).ensure_embedding_signature(
                embed.provider_id, embed.base_url, embed.model
            )
        except Exception:
            embedding_changed = False
    return {
        "enabled": s.enabled,
        "chat_configured": s.chat_config() is not None,
        "embed_configured": s.embedding_config() is not None,
        "providers": len(s.providers),
        "embedding_changed": embedding_changed,
    }


@router.post("/test", dependencies=_write_deps)
def test(request: Request) -> dict:
    s = _settings(request)
    cfg = s.chat_config()
    if cfg is None:
        return {"ok": False, "message": "未配置 Chat 模型"}
    try:
        chat_ping(cfg)
        return {"ok": True, "message": "连接成功"}
    except ProviderError as exc:
        return {"ok": False, "message": str(exc)}


@router.post("/list-models", dependencies=_write_deps)
def list_models_endpoint(payload: ListModelsIn, request: Request) -> dict:
    s = _settings(request)
    prov = s.provider(payload.provider_id)
    if prov is None:
        return {"ok": False, "models": [], "message": "Provider 不存在"}
    try:
        models = list_models(ProviderConfig(prov.base_url, prov.api_key, ""))
        return {"ok": True, "models": models, "message": ""}
    except ProviderError as exc:
        return {"ok": False, "models": [], "message": str(exc)}


# ---------- 索引 ----------


@router.post("/rebuild", dependencies=_write_deps)
def rebuild_ai(request: Request) -> dict:
    """全库重新分块（.md 原文 + 文档解析文本）；嵌入由后台任务异步消费。"""
    rag = _rag(request)
    vault: Vault = get_vault(request)
    indexer: Indexer = get_indexer(request)

    reindexed = 0
    parsed_chars = 0
    ocr_queued = 0
    ocr_service = request.app.state.ocr_service
    rag.delete_path("templates")
    for rel in _iter_importable(vault.root):
        try:
            if rel.lower().endswith(".md"):
                fc = vault.read_markdown(rel)
                text = fc.content
            elif is_document(rel):
                # 解析文本优先；扫描件 PDF 用 OCR 结果并确保任务入队
                text = ocr_service.text_for_index(vault, rel)
                if text is None:
                    ocr_queued += 1
                    continue
            else:
                continue
            rag.reindex_file(rel, text)
            reindexed += 1
            parsed_chars += len(text)
        except Exception:
            continue
    job = getattr(request.app.state, "embedding_job", None)
    stat = job.stats() if job else {"pending": 0, "embedded": 0, "total": 0}
    return {
        "reindexed": reindexed,
        "parsed_chars": parsed_chars,
        "ocr_queued": ocr_queued,
        "pending": stat["pending"],
        "embedded": stat["embedded"],
        "total": stat["total"],
        "chunks": rag.count_chunks(),
    }


def _iter_importable(root: Path):
    """遍历 vault 下可进 AI 索引的文件（.md + 文档），跳过隐藏目录与回收站。"""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and not (
                Path(dirpath) == root and d.casefold() == "templates"
            )
        ]
        for name in filenames:
            if name.lower().endswith(".md") or is_document(name):
                yield (Path(dirpath) / name).relative_to(root).as_posix()


@router.get("/embedding-stat")
def embedding_stat(request: Request) -> dict:
    job = getattr(request.app.state, "embedding_job", None)
    if job is None:
        rag = _rag(request)
        return {"running": False, "pending": 0, "embedded": rag.count_embedded(), "total": rag.count_chunks(),
                "last_error": None, "backoff_seconds": 0.0}
    return job.stats()


# ---------- 语义搜索 ----------


@router.post("/semantic-search")
def semantic_search(payload: SemanticSearchIn, request: Request) -> dict:
    s = _settings(request)
    rag = _rag(request)
    # 外呼闸门：AI 关闭 → FTS-only，且 Rerank 不调用
    embed_cfg = s.active_embedding_config()
    rerank_cfg = s.active_rerank_config()

    k = payload.k
    if rerank_cfg is not None:
        k = max(20, k)  # 先召回更多候选再做精排
    results = rag.search(payload.query, k=k, embedding_cfg=embed_cfg)

    if rerank_cfg is not None and len(results) > 1:
        try:
            ordered = rerank(rerank_cfg, payload.query, [r["text"] for r in results], top_n=payload.k)
            ranked = []
            for idx, score in ordered:
                if 0 <= idx < len(results):
                    item = dict(results[idx])
                    item["score"] = score
                    ranked.append(item)
            results = ranked[: payload.k]
        except ProviderError:
            results = results[: payload.k]
    else:
        results = results[: payload.k]

    return {"query": payload.query, "results": results}


# ---------- 对话 ----------


@router.post("/chat")
def chat(payload: ChatIn, request: Request) -> StreamingResponse:
    s = _settings(request)
    if not s.enabled:
        return _sse_error("AI 未启用，请先配置并开启")
    cfg = s.chat_config()
    if cfg is None:
        return _sse_error("未配置 Chat 模型")

    rag = _rag(request)
    if payload.mode == "free":
        return _free_chat(payload, cfg, s, request)
    return _rag_chat(payload, cfg, s, rag)


def _rag_chat(payload: ChatIn, cfg: ProviderConfig, s: AiSettings, rag) -> StreamingResponse:
    # 外呼闸门：AI 关闭时 active_embedding_config() 返回 None → FTS-only
    embed_cfg = s.active_embedding_config()
    sources = rag.search(payload.message, k=5, embedding_cfg=embed_cfg)

    context_parts = []
    for i, src in enumerate(sources, start=1):
        path = src["file_path"]
        heading = src.get("heading") or ""
        text = src["text"]
        context_parts.append(f"[{i}] {path} → {heading}\n{text}")
    context = "\n\n---\n\n".join(context_parts) if context_parts else "（知识库中未检索到相关内容）"

    messages = [
        {"role": "system", "content": _RAG_SYSTEM_PROMPT},
        {"role": "user", "content": f"知识片段：\n{context}\n\n问题：{payload.message}"},
    ]

    refs = [
        {
            "index": i,
            "path": src["file_path"],
            "heading": src.get("heading") or "",
            "line_start": src.get("line_start"),
            "line_end": src.get("line_end"),
        }
        for i, src in enumerate(sources, start=1)
    ]

    def event_stream():
        try:
            for piece in stream_chat(cfg, messages, temperature=s.chat.temperature, max_tokens=s.chat.max_tokens):
                yield _sse({"type": "delta", "content": piece})
        except ProviderError as exc:
            yield _sse({"type": "error", "error": str(exc)})
            return
        yield _sse({"type": "done", "sources": refs})

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _free_chat(payload: ChatIn, cfg: ProviderConfig, s: AiSettings, request: Request) -> StreamingResponse:
    sessions = _sessions(request)
    # 首次发送自动建会话（后端兜底，不依赖前端先建）
    sid = payload.session_id or sessions.create()["id"]
    data = sessions.get(sid) or {"messages": []}
    history = deque(data["messages"], maxlen=2 * max(1, s.chat.max_history_messages) + 2)
    messages = [{"role": "system", "content": _FREE_SYSTEM_PROMPT}, *list(history)]
    messages.append({"role": "user", "content": payload.message})

    def event_stream():
        assistant_text = []
        yield _sse({"type": "session", "session_id": sid})
        try:
            for piece in stream_chat(cfg, messages, temperature=s.chat.temperature, max_tokens=s.chat.max_tokens):
                assistant_text.append(piece)
                yield _sse({"type": "delta", "content": piece})
        except ProviderError as exc:
            yield _sse({"type": "error", "error": str(exc)})
            return
        # 完成后持久化（原子写入），标题默认取首条用户问题
        sessions.append(sid, payload.message, "".join(assistant_text), s.chat.max_history_messages)
        yield _sse({"type": "done", "session_id": sid})

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/chat/session")
def ls_sessions(request: Request) -> dict:
    return {"sessions": _sessions(request).ls()}


@router.post("/chat/session", dependencies=_write_deps)
def new_session(request: Request) -> dict:
    """新建自由对话会话，返回 session_id。"""
    return {"session_id": _sessions(request).create()["id"]}


@router.get("/chat/session/{session_id}")
def get_session(session_id: str, request: Request) -> dict:
    data = _sessions(request).get(session_id)
    if data is None:
        return {"session": None}
    return {"session": data}


@router.delete("/chat/session/{session_id}")
def clear_session(session_id: str, request: Request) -> dict:
    _sessions(request).remove(session_id)
    return {"ok": True}


# ---------- 选中文本 AI 操作 ----------


@router.post("/action")
def action(payload: ActionIn, request: Request) -> StreamingResponse:
    s = _settings(request)
    if not s.enabled:
        return _sse_error("AI 未启用，请先配置并开启")
    cfg = s.chat_config()
    if cfg is None:
        return _sse_error("未配置 Chat 模型")

    # 对齐 SiYuan chatGPTWithAction：action 直接拼接在正文前
    messages = [{"role": "user", "content": f"{payload.action}:\n\n{payload.text}"}]

    def event_stream():
        try:
            for piece in stream_chat(cfg, messages, temperature=s.chat.temperature, max_tokens=s.chat.max_tokens):
                yield _sse({"type": "delta", "content": piece})
        except ProviderError as exc:
            yield _sse({"type": "error", "error": str(exc)})
            return
        yield _sse({"type": "done"})

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------- 工具 ----------


def _sse(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def _sse_error(message: str) -> StreamingResponse:
    return StreamingResponse(iter([_sse({"type": "error", "error": message})]), media_type="text/event-stream")
