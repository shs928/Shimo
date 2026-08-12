"""应用配置：路径、端口、环境变量。

配置优先级：环境变量 > config.json > 默认值。
路径默认位于可执行文件 / 项目根同级，保证便携版与源码版行为一致。
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


def app_root() -> Path:
    """返回可执行文件（打包后）或项目根（源码运行时）所在目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    vault_path: Path
    data_path: Path
    host: str = "127.0.0.1"
    port: int = 8848
    initial_password: str | None = None

    @property
    def config_file(self) -> Path:
        return self.data_path / "config.json"

    @property
    def index_db(self) -> Path:
        return self.data_path / "index.db"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def load_config() -> Config:
    root = app_root()

    def _path(name: str, default: Path) -> Path:
        raw = _env(name)
        return Path(raw).expanduser().resolve() if raw else default

    vault_path = _path("SHIMO_VAULT_PATH", root / "vault")
    data_path = _path("SHIMO_DATA_PATH", root / "data")

    host = _env("SHIMO_HOST") or "127.0.0.1"
    port = int(_env("SHIMO_PORT") or 8848)
    initial_password = _env("SHIMO_INITIAL_PASSWORD") or None

    return Config(
        vault_path=vault_path,
        data_path=data_path,
        host=host,
        port=port,
        initial_password=initial_password,
    )
