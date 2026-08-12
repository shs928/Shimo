"""AI 配置模型测试：v1→v2 迁移、providers 管理、环境变量覆盖、保存格式兼容。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


def _write_ai_json(client: TestClient, raw: dict) -> None:
    path = client.app.state.ai_store.path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")


def test_v1_config_migrates_to_provider(client: TestClient):
    """旧格式（chat/embedding 内联 base_url）读取时自动迁移为 providers。"""
    _write_ai_json(client, {
        "enabled": True,
        "chat": {"base_url": "https://a.com/v1", "api_key": "sk-a", "model": "m1"},
        "embedding": {"base_url": "https://a.com/v1", "api_key": "sk-a", "model": "e1"},
    })
    s = client.app.state.ai_store.load()
    assert s.enabled is True
    assert len(s.providers) >= 1
    prov = s.provider("default")
    assert prov is not None
    assert prov.base_url == "https://a.com/v1"
    assert s.chat.provider_id == "default"
    assert s.chat.model == "m1"
    assert s.embedding.provider_id == "default"
    assert s.embedding.model == "e1"
    assert s.chat_config() is not None
    assert s.embedding_config() is not None


def test_provider_management_save_and_reload(client: TestClient):
    r = client.post("/api/v1/ai/config", json={
        "enabled": True,
        "providers": [
            {"id": "p1", "name": "甲", "base_url": "https://a.com/v1", "api_key": "k1", "models": ["gpt-4o"]},
            {"id": "p2", "name": "乙", "base_url": "https://b.com/v1", "api_key": "k2", "models": []},
        ],
        "chat": {"provider_id": "p1", "model": "gpt-4o"},
        "embedding": {"provider_id": "p2", "model": "e"},
    })
    assert r.status_code == 200
    data = r.json()
    assert data["providers"] == 2
    assert data["chat_configured"] is True

    s = client.app.state.ai_store.load()
    assert s.provider("p1").api_key == "k1"
    assert s.chat_config().base_url == "https://a.com/v1"


def test_save_without_key_keeps_old_key(client: TestClient):
    client.post("/api/v1/ai/config", json={
        "providers": [{"id": "p1", "name": "甲", "base_url": "https://a.com/v1", "api_key": "secret"}],
    })
    # 再次保存且不提交 api_key：应沿用旧值
    client.post("/api/v1/ai/config", json={
        "providers": [{"id": "p1", "name": "甲", "base_url": "https://a.com/v1"}],
    })
    assert client.app.state.ai_store.load().provider("p1").api_key == "secret"


def test_env_vars_override(client: TestClient, monkeypatch):
    _write_ai_json(client, {"enabled": True, "providers": [], "chat": {}, "embedding": {}})
    monkeypatch.setenv("AI_CHAT_BASE_URL", "https://env.com/v1")
    monkeypatch.setenv("AI_CHAT_MODEL", "env-model")
    s = client.app.state.ai_store.load()
    assert s.chat_config() is not None
    assert s.chat_config().base_url == "https://env.com/v1"
    assert s.chat.model == "env-model"


def test_legacy_save_payload_still_works(client: TestClient):
    """旧前端 payload（chat.base_url 内联）保存后仍能配置成功。"""
    r = client.post(
        "/api/v1/ai/config",
        json={"enabled": True, "chat": {"base_url": "https://example.com/v1", "api_key": "sk-test", "model": "m"}},
    )
    assert r.status_code == 200
    assert r.json()["chat_configured"] is True
    s = client.app.state.ai_store.load()
    assert s.provider("default") is not None
    assert s.chat.provider_id == "default"


def test_config_get_returns_sanitized(client: TestClient):
    client.post("/api/v1/ai/config", json={
        "providers": [{"id": "p1", "name": "甲", "base_url": "https://a.com/v1", "api_key": "secret"}],
    })
    r = client.get("/api/v1/ai/config")
    assert r.status_code == 200
    data = r.json()
    prov = data["providers"][0]
    assert prov["id"] == "p1"
    assert "api_key" not in prov
    assert prov["has_key"] is True


def test_default_agent_system_prompt(client: TestClient):
    from app.agent.system_prompt import DEFAULT_AGENT_SYSTEM_PROMPT

    s = client.app.state.ai_store.load()
    assert s.agent_prompt() == DEFAULT_AGENT_SYSTEM_PROMPT


# ---------- 2.1 API Key 凭据库迁移 ----------


def _secrets(client: TestClient):
    return client.app.state.ai_store._secrets


def test_saved_json_never_contains_api_key(client: TestClient):
    """保存后 data/ai.json 不得包含 Key 字段或 Key 文本。"""
    client.post("/api/v1/ai/config", json={
        "providers": [{"id": "p1", "name": "甲", "base_url": "https://a.com/v1", "api_key": "k-secret-42"}],
    })
    raw = json.loads(client.app.state.ai_store.path.read_text(encoding="utf-8"))
    text = client.app.state.ai_store.path.read_text(encoding="utf-8")
    assert "api_key" not in raw["providers"][0]
    assert "k-secret-42" not in text
    # Key 在系统凭据库中，加载时注入
    assert _secrets(client).get("p1") == "k-secret-42"
    assert client.app.state.ai_store.load().provider("p1").api_key == "k-secret-42"


def test_legacy_plain_key_migrates_to_secret_store(client: TestClient):
    """旧版 JSON 明文 Key：先写系统凭据库，再原子重写 JSON 删除。"""
    _write_ai_json(client, {
        "enabled": True,
        "providers": [{"id": "p1", "name": "甲", "base_url": "https://a.com/v1", "api_key": "legacy-key"}],
    })
    s = client.app.state.ai_store.load()
    assert s.provider("p1").api_key == "legacy-key"
    assert _secrets(client).get("p1") == "legacy-key"
    raw = client.app.state.ai_store.path.read_text(encoding="utf-8")
    assert "api_key" not in raw
    assert "legacy-key" not in raw


def test_clear_api_key_semantics(client: TestClient):
    """clear_api_key=true 显式清空；字段缺失表示保留（不能用空字符串混淆）。"""
    client.post("/api/v1/ai/config", json={
        "providers": [{"id": "p1", "name": "甲", "base_url": "https://a.com/v1", "api_key": "secret"}],
    })
    assert _secrets(client).get("p1") == "secret"
    # 不提交 api_key（字段缺失）→ 保留
    client.post("/api/v1/ai/config", json={
        "providers": [{"id": "p1", "name": "甲", "base_url": "https://a.com/v1"}],
    })
    assert _secrets(client).get("p1") == "secret"
    assert client.app.state.ai_store.load().provider("p1").api_key == "secret"
    # 显式清空 → 删除凭据，has_key=False
    client.post("/api/v1/ai/config", json={
        "providers": [{"id": "p1", "name": "甲", "base_url": "https://a.com/v1", "clear_api_key": True}],
    })
    assert _secrets(client).get("p1") == ""
    assert client.app.state.ai_store.load().provider("p1").api_key == ""
    data = client.get("/api/v1/ai/config").json()
    assert data["providers"][0]["has_key"] is False


def test_provider_delete_clears_secret(client: TestClient):
    client.post("/api/v1/ai/config", json={
        "providers": [
            {"id": "p1", "name": "甲", "base_url": "https://a.com/v1", "api_key": "k1"},
            {"id": "p2", "name": "乙", "base_url": "https://b.com/v1", "api_key": "k2"},
        ],
    })
    assert _secrets(client).get("p2") == "k2"
    client.post("/api/v1/ai/config", json={
        "providers": [{"id": "p1", "name": "甲", "base_url": "https://a.com/v1"}],
    })
    assert _secrets(client).get("p2") == ""
    assert _secrets(client).get("p1") == "k1"


def test_secret_store_unavailable_fails_closed(client: TestClient, monkeypatch):
    """keyring 不可用时：保存拒绝（不明文降级）；迁移失败 JSON 原样保留。"""

    class BrokenStore:
        available = False

        def set(self, provider_id, key):
            raise RuntimeError("keyring unavailable")

        def get(self, provider_id):
            return ""

        def delete(self, provider_id):
            pass

    client.app.state.ai_store._secrets = BrokenStore()  # type: ignore[assignment]
    r = client.post("/api/v1/ai/config", json={
        "providers": [{"id": "p1", "name": "甲", "base_url": "https://a.com/v1", "api_key": "k"}],
    })
    assert r.status_code == 400  # save 中止并给出可读错误，不静默降级
    # JSON 未被污染：要么不存在，要么无 providers（保存被中止）
    if client.app.state.ai_store.path.exists():
        raw = json.loads(client.app.state.ai_store.path.read_text(encoding="utf-8"))
        assert raw.get("providers", []) == []

    # 迁移场景：明文仍在 JSON，secret 不可用 → 保持原样（下次重试）
    client.app.state.ai_store._secrets = BrokenStore()  # type: ignore[assignment]
    _write_ai_json(client, {
        "providers": [{"id": "p1", "name": "甲", "base_url": "https://a.com/v1", "api_key": "legacy"}],
    })
    s = client.app.state.ai_store.load()
    text = client.app.state.ai_store.path.read_text(encoding="utf-8")
    assert "legacy" in text  # 未半迁移
    assert s.provider("p1").api_key == ""  # 无法注入（fail closed，不降级读取）
