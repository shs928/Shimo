"""3.6 Provider 协议错误处理：契约校验矩阵（FakeClient，不发真实外网）。"""
from __future__ import annotations

import json

import pytest

from app.rag.provider import (
    ProviderConfig,
    ProviderError,
    chat_complete,
    chat_ping,
    embed_texts,
    generate_image,
    list_models,
    rerank,
    stream_chat_events,
)

CFG = ProviderConfig("https://mock.local/v1", "key", "model")


class FakeResp:
    def __init__(self, status=200, content_type="application/json", body=None, lines=None):
        self.status_code = status
        self.headers = {"content-type": content_type}
        self._body = body
        self._lines = lines or []

    def json(self):
        if isinstance(self._body, str):
            return json.loads(self._body)
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=self  # type: ignore[arg-type]
            )

    def iter_lines(self):
        return iter(self._lines)


class FakeClient:
    """可编程的 httpx.Client 替身：按 (method, url, **kw) 返回预设响应。"""

    def __init__(self, *a, **k):
        self._responses = []
        self._streams = []
        self._calls = []

    @classmethod
    def respond(cls, *responses):
        inst = cls()
        inst._responses = list(responses)
        return inst

    @classmethod
    def stream_respond(cls, *streams):
        inst = cls()
        inst._streams = list(streams)
        return inst

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def _take(self, resp_list):
        if not resp_list:
            return FakeResp(status=200, body={"data": []})
        return resp_list.pop(0)

    def post(self, url, **kw):
        self._calls.append(("POST", url))
        return self._take(self._responses)

    def get(self, url, **kw):
        self._calls.append(("GET", url))
        return self._take(self._responses)

    def stream(self, method, url, **kw):
        self._calls.append((method, url))
        resp = self._take(self._streams)

        class _StreamCtx:
            def __enter__(self_inner):
                return resp

            def __exit__(self_inner, *a):
                return False

        return _StreamCtx()


def _patch_client(monkeypatch, fake):
    import httpx

    monkeypatch.setattr(httpx, "Client", lambda *a, **k: fake)


# ---------- 非 JSON / error 字段 / 缺字段 ----------


def test_chat_ping_rejects_non_json(monkeypatch):
    _patch_client(monkeypatch, FakeClient.respond(FakeResp(content_type="text/html", body="<html>")))
    with pytest.raises(ProviderError, match="非 JSON"):
        chat_ping(CFG)


def test_chat_ping_rejects_upstream_error_field(monkeypatch):
    _patch_client(monkeypatch, FakeClient.respond(FakeResp(body={"error": {"message": "rate limited"}})))
    with pytest.raises(ProviderError, match="rate limited"):
        chat_ping(CFG)


def test_chat_ping_rejects_missing_choices(monkeypatch):
    _patch_client(monkeypatch, FakeClient.respond(FakeResp(body={"id": "x"})))
    with pytest.raises(ProviderError, match="缺少 choices"):
        chat_ping(CFG)


def test_list_models_rejects_bad_shape(monkeypatch):
    _patch_client(monkeypatch, FakeClient.respond(FakeResp(body={"data": "not-a-list"})))
    with pytest.raises(ProviderError, match="期望 data 为数组"):
        list_models(CFG)


def test_embed_rejects_missing_embedding(monkeypatch):
    _patch_client(monkeypatch, FakeClient.respond(FakeResp(body={"data": [{"index": 0}]})))
    with pytest.raises(ProviderError, match="缺少 embedding"):
        embed_texts(CFG, ["x"])


def test_rerank_rejects_missing_fields(monkeypatch):
    _patch_client(monkeypatch, FakeClient.respond(FakeResp(body={"results": [{"foo": 1}]})))
    with pytest.raises(ProviderError, match="index"):
        rerank(CFG, "q", ["d"])


def test_generate_image_rejects_missing_data(monkeypatch):
    _patch_client(monkeypatch, FakeClient.respond(FakeResp(body={"data": [{"foo": "bar"}]})))
    with pytest.raises(ProviderError, match="b64_json/url"):
        generate_image(CFG, "一只猫")


def test_http_error_extracts_json_message(monkeypatch):
    resp = FakeResp(status=429, body={"error": {"message": "quota exceeded"}})
    _patch_client(monkeypatch, FakeClient.respond(resp))
    with pytest.raises(ProviderError, match="HTTP 429：quota exceeded"):
        chat_ping(CFG)


# ---------- SSE 流式契约 ----------


def test_stream_rejects_error_frame(monkeypatch):
    lines = [
        'data: {"choices":[{"delta":{"content":"部分内容"}}]}',
        'data: {"error":{"message":"上下文超长"}}',
        "data: [DONE]",
    ]
    _patch_client(monkeypatch, FakeClient.stream_respond(FakeResp(lines=lines)))
    with pytest.raises(ProviderError, match="上下文超长"):
        list(stream_chat_events(CFG, [{"role": "user", "content": "hi"}]))


def test_stream_rejects_invalid_json_frame(monkeypatch):
    _patch_client(monkeypatch, FakeClient.stream_respond(FakeResp(lines=['data: {broken json'])))
    with pytest.raises(ProviderError, match="非法 SSE"):
        list(stream_chat_events(CFG, [{"role": "user", "content": "hi"}]))


def test_stream_rejects_non_200(monkeypatch):
    _patch_client(monkeypatch, FakeClient.stream_respond(FakeResp(status=401, body={"error": {"message": "bad key"}})))
    with pytest.raises(ProviderError, match="HTTP 401"):
        list(stream_chat_events(CFG, [{"role": "user", "content": "hi"}]))


def test_stream_ok_events(monkeypatch):
    lines = [
        'data: {"choices":[{"delta":{"content":"你好"}}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
        "data: [DONE]",
    ]
    _patch_client(monkeypatch, FakeClient.stream_respond(FakeResp(lines=lines)))
    events = list(stream_chat_events(CFG, [{"role": "user", "content": "hi"}]))
    assert ("content", "你好") in events
    assert events[-1] == ("done", "stop")


def test_chat_complete_rejects_missing_choices(monkeypatch):
    _patch_client(monkeypatch, FakeClient.respond(FakeResp(body={"id": "x"})))
    with pytest.raises(ProviderError, match="choices"):
        chat_complete(CFG, [{"role": "user", "content": "hi"}])
