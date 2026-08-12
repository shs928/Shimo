"""API Key 安全存储：系统凭据库（keyring）。

- Windows Credential Manager / macOS Keychain / Linux keyring。
- 环境变量优先且永不落盘（由 AiStore._apply_env 注入，不经过本模块）。
- keyring 不可用时 fail closed：拒绝保存并提示使用环境变量；
  读取返回空串（表示无 Key），绝不降级为本地明文。
- 测试环境使用 InMemorySecretStore（见 tests/conftest.py 注入）。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_SERVICE = "shimo-ai"


class SecretStoreError(RuntimeError):
    pass


class SecretStore:
    """系统凭据库封装（keyring）。接口与 InMemorySecretStore 一致。"""

    def __init__(self, service: str = _SERVICE):
        self._service = service
        self._available: bool | None = None

    @property
    def available(self) -> bool:
        if self._available is None:
            try:
                import keyring

                keyring.get_keyring()  # 触发后端加载（无头环境可能抛错）
                self._available = True
            except Exception:
                self._available = False
        return self._available

    def set(self, provider_id: str, key: str) -> None:
        if not key:
            return
        if not self.available:
            raise SecretStoreError(
                "系统凭据库不可用，请改用环境变量 AI_CHAT_API_KEY / AI_EMBED_API_KEY 注入密钥"
            )
        try:
            import keyring

            keyring.set_password(self._service, provider_id, key)
        except Exception as exc:
            raise SecretStoreError(f"写入系统凭据库失败：{exc}") from exc

    def get(self, provider_id: str) -> str:
        if not self.available:
            return ""
        try:
            import keyring

            return keyring.get_password(self._service, provider_id) or ""
        except Exception:
            return ""

    def delete(self, provider_id: str) -> None:
        if not self.available:
            return
        try:
            import keyring

            keyring.delete_password(self._service, provider_id)
        except Exception:
            pass  # 删除不存在的凭据抛错，忽略


class InMemorySecretStore:
    """内存版 SecretStore（测试专用，不依赖系统凭据库）。"""

    def __init__(self):
        self._data: dict[str, str] = {}

    @property
    def available(self) -> bool:
        return True

    def set(self, provider_id: str, key: str) -> None:
        if key:
            self._data[provider_id] = key

    def get(self, provider_id: str) -> str:
        return self._data.get(provider_id, "")

    def delete(self, provider_id: str) -> None:
        self._data.pop(provider_id, None)
