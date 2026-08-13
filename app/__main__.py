"""支持 `python -m app` 直接启动。"""
from __future__ import annotations

import os
import sys
import threading
import webbrowser

import uvicorn

# 注意：此处必须用绝对导入。PyInstaller 冻结入口脚本以顶层 `__main__` 运行，
# 无包上下文，相对导入（from .config import ...）会抛 ImportError。
from app.config import load_config


def _maybe_open_browser(host: str, port: int) -> None:
    """便携版（PyInstaller 冻结）监听本机地址时，启动后自动打开浏览器。

    - `SHIMO_OPEN_BROWSER=0` 强制关闭；`SHIMO_OPEN_BROWSER=1` 强制开启。
    - 默认仅在冻结打包且监听本机地址时开启（Docker / 源码开发不触发）。
    """
    env = os.environ.get("SHIMO_OPEN_BROWSER", "").strip()
    if env == "0":
        return
    frozen_local = getattr(sys, "frozen", False) and host in ("127.0.0.1", "localhost", "::1")
    if env == "1" or frozen_local:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()


def main() -> None:
    cfg = load_config()
    _maybe_open_browser(cfg.host, cfg.port)
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host=cfg.host,
        port=cfg.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
