"""pytest fixtures：隔离的临时 Vault + 内存级测试应用。"""
from __future__ import annotations

import pytest

from app.config import Config
from app.main import create_app
from app.services.vault import Vault


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    v = Vault(root)
    v.ensure_initialized()
    return v


@pytest.fixture
def client(tmp_path):
    """已初始化密码的测试客户端（auth 无需初始化流程）。"""
    cfg = Config(vault_path=tmp_path / "vault", data_path=tmp_path / "data")
    app = create_app(cfg)

    from fastapi.testclient import TestClient

    with TestClient(app) as tc:
        # 测试环境注入内存凭据库，不触碰真实系统 keyring
        from app.rag.secret_store import InMemorySecretStore

        tc.app.state.ai_store._secrets = InMemorySecretStore()
        tc.post("/api/v1/auth/init", json={"password": "test-password"})
        yield tc
