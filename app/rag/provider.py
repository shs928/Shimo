"""AI provider：OpenAI 兼容 Chat / Embeddings / Rerank / 图片生成客户端。

- Chat：POST /chat/completions，SSE 流式（stream=true），支持 function calling。
- Embedding：POST /embeddings，一次批量。
- Rerank：POST /rerank（Jina / Cohere 风格，预留）。
- 图片生成：POST /images/generations。
- 连接测试：POST /chat/completions，max_tokens=1，stream=false。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx

_TIMEOUT = 120.0


@dataclass(frozen=True)
class ProviderConfig:
    base_url: str = ""
    api_key: str = ""
    model: str = ""


@dataclass
class ToolCall:
    id: str = ""
    name: str = ""
    arguments: str = ""  # JSON 字符串，流式时按分片累积
    index: int = 0


@dataclass
class ChatResult:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""


class ProviderError(RuntimeError):
    pass


def _url(base: str, path: str) -> str:
    return base.rstrip("/") + path


def _headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def chat_ping(cfg: ProviderConfig) -> dict:
    if not cfg.base_url or not cfg.model:
        raise ProviderError("未配置 Chat 模型")
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            r = client.post(
                _url(cfg.base_url, "/chat/completions"),
                headers=_headers(cfg.api_key),
                json={"model": cfg.model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1},
            )
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as exc:
        raise ProviderError(f"连接失败：{exc}") from exc


def list_models(cfg: ProviderConfig) -> list[str]:
    """GET {base}/models；未实现 /models 的服务返回空列表。"""
    if not cfg.base_url:
        raise ProviderError("未配置服务地址")
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            r = client.get(_url(cfg.base_url, "/models"), headers=_headers(cfg.api_key))
            r.raise_for_status()
            data = r.json()
            ids = [m["id"] for m in data.get("data", []) if m.get("id")]
            return sorted(set(ids))
    except httpx.HTTPError as exc:
        raise ProviderError(f"拉取模型列表失败：{exc}") from exc


def embed_texts(cfg: ProviderConfig, texts: list[str]) -> list[list[float]]:
    if not cfg.base_url or not cfg.model:
        raise ProviderError("未配置 Embedding 模型")
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            r = client.post(
                _url(cfg.base_url, "/embeddings"),
                headers=_headers(cfg.api_key),
                json={"model": cfg.model, "input": texts},
            )
            r.raise_for_status()
            data = r.json()
            return [item["embedding"] for item in data["data"]]
    except httpx.HTTPError as exc:
        raise ProviderError(f"嵌入调用失败：{exc}") from exc


def rerank(cfg: ProviderConfig, query: str, documents: list[str], top_n: int | None = None) -> list[tuple[int, float]]:
    """POST {base}/rerank；返回 [(原下标, 相关性分), ...] 按相关性降序。"""
    if not cfg.base_url or not cfg.model:
        raise ProviderError("未配置 Rerank 模型")
    body: dict = {"model": cfg.model, "query": query, "documents": documents}
    if top_n:
        body["top_n"] = top_n
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            r = client.post(_url(cfg.base_url, "/rerank"), headers=_headers(cfg.api_key), json=body)
            r.raise_for_status()
            data = r.json()
        return [(item.get("index", 0), float(item.get("relevance_score", 0.0))) for item in data.get("results", [])]
    except httpx.HTTPError as exc:
        raise ProviderError(f"Rerank 调用失败：{exc}") from exc


def generate_image(cfg: ProviderConfig, prompt: str, size: str = "1024x1024", n: int = 1) -> list[dict]:
    """POST {base}/images/generations；返回 [{b64_json|url}]。"""
    if not cfg.base_url or not cfg.model:
        raise ProviderError("未配置图片生成模型")
    body: dict = {"model": cfg.model, "prompt": prompt, "n": n, "size": size}
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            r = client.post(_url(cfg.base_url, "/images/generations"), headers=_headers(cfg.api_key), json=body)
            r.raise_for_status()
            data = r.json()
        return list(data.get("data", []))
    except httpx.HTTPError as exc:
        raise ProviderError(f"图片生成失败：{exc}") from exc


def stream_chat(
    cfg: ProviderConfig,
    messages: list[dict],
    temperature: float = 0.3,
    max_tokens: int = 1024,
):
    """SSE 流式聊天（纯文本）；yield 每次增量文本。"""
    for kind, value in stream_chat_events(cfg, messages, temperature=temperature, max_tokens=max_tokens):
        if kind == "content":
            yield value


def stream_chat_events(
    cfg: ProviderConfig,
    messages: list[dict],
    tools: list[dict] | None = None,
    tool_choice: str | dict | None = None,
    temperature: float = 0.3,
    max_tokens: int = 1024,
):
    """SSE 流式聊天（支持 function calling）。

    yield 事件元组：
      ("content", str)      增量正文
      ("tool_call", ToolCall) 流结束前按 index 累积合并
      ("done", finish_reason)
    """
    if not cfg.base_url or not cfg.model:
        raise ProviderError("未配置 Chat 模型")
    body: dict = {
        "model": cfg.model,
        "messages": messages,
        "stream": True,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = tool_choice or "auto"
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            with client.stream(
                "POST",
                _url(cfg.base_url, "/chat/completions"),
                headers=_headers(cfg.api_key),
                json=body,
            ) as resp:
                resp.raise_for_status()
                pending: dict[int, ToolCall] = {}
                finish = ""
                for line in resp.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        obj = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    finish = choice.get("finish_reason") or finish
                    delta = choice.get("delta") or {}
                    text = delta.get("content")
                    if text:
                        yield ("content", text)
                    for tc in delta.get("tool_calls") or []:
                        idx = int(tc.get("index", 0))
                        call = pending.setdefault(idx, ToolCall(index=idx))
                        if tc.get("id"):
                            call.id = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            call.name += fn["name"]
                        if fn.get("arguments"):
                            call.arguments += fn["arguments"]
                for idx in sorted(pending):
                    yield ("tool_call", pending[idx])
                yield ("done", finish)
    except httpx.HTTPError as exc:
        raise ProviderError(f"对话失败：{exc}") from exc


def chat_complete(
    cfg: ProviderConfig,
    messages: list[dict],
    tools: list[dict] | None = None,
    tool_choice: str | dict | None = None,
    temperature: float = 0.3,
    max_tokens: int = 1024,
) -> ChatResult:
    """非流式单次对话；返回正文与工具调用（用于测试与简单场景）。"""
    if not cfg.base_url or not cfg.model:
        raise ProviderError("未配置 Chat 模型")
    body: dict = {
        "model": cfg.model,
        "messages": messages,
        "stream": False,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = tool_choice or "auto"
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            r = client.post(_url(cfg.base_url, "/chat/completions"), headers=_headers(cfg.api_key), json=body)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as exc:
        raise ProviderError(f"对话失败：{exc}") from exc
    choices = data.get("choices") or []
    choice = choices[0] if choices else {}
    msg = choice.get("message") or {}
    calls = [
        ToolCall(id=tc.get("id", ""), name=(tc.get("function") or {}).get("name", ""),
                 arguments=(tc.get("function") or {}).get("arguments", ""), index=tc.get("index", 0))
        for tc in msg.get("tool_calls") or []
    ]
    return ChatResult(content=msg.get("content") or "", tool_calls=calls, finish_reason=choice.get("finish_reason") or "")
