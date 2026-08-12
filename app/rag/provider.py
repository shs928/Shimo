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
    provider_id: str = ""


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


def _parse_json(resp) -> dict:
    """校验响应 Content-Type 与 JSON 结构；提取上游 error 字段。

    任何契约违背统一转换为 ProviderError（结构化错误，不静默吞掉）。
    """
    ctype = resp.headers.get("content-type", "")
    if "json" not in ctype.lower():
        raise ProviderError(f"上游返回非 JSON 响应（Content-Type: {ctype or '未知'}）")
    try:
        data = resp.json()
    except Exception as exc:
        raise ProviderError(f"上游返回非法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise ProviderError("上游返回结构异常（期望 JSON 对象）")
    err = data.get("error")
    if err:
        if isinstance(err, dict):
            msg = err.get("message") or err.get("type") or str(err)
        else:
            msg = str(err)
        raise ProviderError(f"上游错误：{msg}")
    return data


def _require(data: dict, key: str, what: str):
    """校验必需字段存在且形状正确；缺失统一转换为 ProviderError。"""
    value = data.get(key)
    if value is None:
        raise ProviderError(f"上游响应缺少 {what} 字段：{key}")
    return value


def _http_error(exc: httpx.HTTPError, what: str) -> ProviderError:
    """把 httpx 异常（超时/关闭/HTTP 状态）转为结构化 ProviderError。"""
    if isinstance(exc, httpx.TimeoutException):
        return ProviderError(f"{what}超时：{exc}")
    if isinstance(exc, httpx.TransportError):
        return ProviderError(f"{what}连接失败：{exc}")
    if isinstance(exc, httpx.HTTPStatusError):
        # 尝试提取上游 JSON error 消息
        try:
            body = exc.response.json()
            err = body.get("error") if isinstance(body, dict) else None
            if err:
                msg = err.get("message") if isinstance(err, dict) else str(err)
                return ProviderError(f"{what}失败：HTTP {exc.response.status_code}：{msg}")
        except Exception:
            pass
        return ProviderError(f"{what}失败：HTTP {exc.response.status_code}")
    return ProviderError(f"{what}失败：{exc}")


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
            data = _parse_json(r)
            _require(data, "choices", "choices")
            return data
    except httpx.HTTPError as exc:
        raise _http_error(exc, "连接测试") from exc


def list_models(cfg: ProviderConfig) -> list[str]:
    """GET {base}/models；未实现 /models 的服务返回空列表。契约校验 data[].id。"""
    if not cfg.base_url:
        raise ProviderError("未配置服务地址")
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            r = client.get(_url(cfg.base_url, "/models"), headers=_headers(cfg.api_key))
            r.raise_for_status()
            data = _parse_json(r)
            items = _require(data, "data", "模型列表")
            if not isinstance(items, list):
                raise ProviderError("上游模型列表结构异常（期望 data 为数组）")
            ids = [m["id"] for m in items if isinstance(m, dict) and m.get("id")]
            return sorted(set(ids))
    except httpx.HTTPError as exc:
        raise _http_error(exc, "拉取模型列表") from exc


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
            data = _parse_json(r)
            items = _require(data, "data", "嵌入结果")
            if not isinstance(items, list):
                raise ProviderError("上游嵌入结果结构异常（期望 data 为数组）")
            vectors = []
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get("embedding"), list):
                    raise ProviderError("上游嵌入条目缺少 embedding 数组")
                vectors.append(item["embedding"])
            return vectors
    except httpx.HTTPError as exc:
        raise _http_error(exc, "嵌入调用") from exc


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
            data = _parse_json(r)
            results = _require(data, "results", "精排结果")
            if not isinstance(results, list):
                raise ProviderError("上游精排结果结构异常（期望 results 为数组）")
            out = []
            for item in results:
                if not isinstance(item, dict):
                    raise ProviderError("上游精排条目结构异常")
                if "index" not in item or "relevance_score" not in item:
                    raise ProviderError("上游精排条目缺少 index/relevance_score")
                try:
                    out.append((int(item["index"]), float(item["relevance_score"])))
                except (TypeError, ValueError):
                    raise ProviderError("上游精排条目 index/relevance_score 类型异常") from None
            return out
    except httpx.HTTPError as exc:
        raise _http_error(exc, "Rerank 调用") from exc


def generate_image(cfg: ProviderConfig, prompt: str, size: str = "1024x1024", n: int = 1) -> list[dict]:
    """POST {base}/images/generations；返回 [{b64_json|url}]。"""
    if not cfg.base_url or not cfg.model:
        raise ProviderError("未配置图片生成模型")
    body: dict = {"model": cfg.model, "prompt": prompt, "n": n, "size": size}
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            r = client.post(_url(cfg.base_url, "/images/generations"), headers=_headers(cfg.api_key), json=body)
            r.raise_for_status()
            data = _parse_json(r)
            items = _require(data, "data", "生成结果")
            if not isinstance(items, list):
                raise ProviderError("上游生成结果结构异常（期望 data 为数组）")
            out = []
            for item in items:
                if not isinstance(item, dict):
                    raise ProviderError("上游生成条目结构异常")
                if not (item.get("b64_json") or item.get("url")):
                    raise ProviderError("上游生成条目缺少 b64_json/url")
                out.append(item)
            return out
    except httpx.HTTPError as exc:
        raise _http_error(exc, "图片生成") from exc


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
                if resp.status_code != 200:
                    # 非 2xx：尝试读取错误体并结构化
                    try:
                        err_data = resp.json()
                        err = err_data.get("error") if isinstance(err_data, dict) else None
                        if err:
                            msg = err.get("message") if isinstance(err, dict) else str(err)
                            raise ProviderError(f"对话失败：HTTP {resp.status_code}：{msg}")
                    except ProviderError:
                        raise
                    except Exception:
                        pass
                    raise ProviderError(f"对话失败：HTTP {resp.status_code}")
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
                        # 非法 JSON 帧不静默吞掉：无法解析的结构化错误
                        raise ProviderError(f"上游返回非法 SSE 数据：{payload[:120]}")
                    if not isinstance(obj, dict):
                        raise ProviderError("上游 SSE 数据帧结构异常")
                    err = obj.get("error")
                    if err:
                        msg = err.get("message") if isinstance(err, dict) else str(err)
                        raise ProviderError(f"上游流式错误：{msg}")
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
        raise _http_error(exc, "对话") from exc


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
            data = _parse_json(r)
    except httpx.HTTPError as exc:
        raise _http_error(exc, "对话") from exc
    choices = _require(data, "choices", "choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderError("上游响应缺少有效 choices")
    choice = choices[0]
    msg = choice.get("message") or {}
    calls = [
        ToolCall(id=tc.get("id", ""), name=(tc.get("function") or {}).get("name", ""),
                 arguments=(tc.get("function") or {}).get("arguments", ""), index=tc.get("index", 0))
        for tc in msg.get("tool_calls") or []
    ]
    return ChatResult(content=msg.get("content") or "", tool_calls=calls, finish_reason=choice.get("finish_reason") or "")
