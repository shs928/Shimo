"""单用户认证：Argon2id 密码哈希 + 签名式随机会话令牌。

- 密码哈希与运行配置存放在 data/config.json（不含 AI Key 的明文）。
- 会话令牌存 SQLite，随 HttpOnly Cookie 下发，不进入 localStorage。
- 首次启动：无密码哈希时允许通过 /auth/init 设置密码，或由
  SHIMO_INITIAL_PASSWORD 环境变量直接完成初始化（云端部署用）。
"""
from __future__ import annotations

import json
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError

from .db import Database

SESSION_COOKIE = "kb_session"
SESSION_DAYS = 30
_hasher = PasswordHasher(time_cost=2, memory_cost=19 * 1024, parallelism=1)


class AuthError(ValueError):
    """认证错误（密码错误、未初始化等）。"""


class AuthStore:
    def __init__(self, config_file: Path, db: Database):
        self.config_file = config_file
        self.db = db
        self._lock = threading.Lock()

    # ---------- 密码 ----------

    @property
    def initialized(self) -> bool:
        return bool(self._read().get("password_hash"))

    def set_password(self, password: str) -> None:
        if not password or len(password) < 6:
            raise AuthError("密码至少需要 6 位")
        with self._lock:
            cfg = self._read()
            cfg["password_hash"] = _hasher.hash(password)
            cfg["auth_version"] = cfg.get("auth_version", 0) + 1
            self._write(cfg)
        self._revoke_all_sessions()

    def verify_password(self, password: str) -> bool:
        cfg = self._read()
        pwd_hash = cfg.get("password_hash")
        if not pwd_hash:
            return False
        try:
            _hasher.verify(pwd_hash, password)
            return True
        except (VerifyMismatchError, VerificationError):
            return False

    # ---------- 会话 ----------

    def create_session(self) -> str:
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=SESSION_DAYS)
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO sessions (token, created_at, expires_at) VALUES (?, ?, ?)",
                (token, now.isoformat(), expires.isoformat()),
            )
        return token

    def validate_session(self, token: str | None) -> bool:
        if not token:
            return False
        now = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM sessions WHERE token = ? AND expires_at > ?",
                (token, now),
            ).fetchone()
        return row is not None

    def revoke_session(self, token: str | None) -> None:
        if not token:
            return
        with self.db.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))

    def _revoke_all_sessions(self) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM sessions")

    # ---------- 配置读写 ----------

    def _read(self) -> dict:
        try:
            return json.loads(self.config_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _write(self, cfg: dict) -> None:
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.config_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.config_file)
