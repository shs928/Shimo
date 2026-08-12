#!/usr/bin/env bash
# 拾墨（Shimo）启动器（macOS / Linux）
set -e
cd "$(dirname "$0")/.."

echo "============================================"
echo "  拾墨（Shimo）- 启动器"
echo "============================================"

PYTHON_BIN=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    PYTHON_BIN="$candidate"
    break
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  echo "[错误] 未找到 Python 3.10+，请先安装。"
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "[首次运行] 正在创建虚拟环境..."
  "$PYTHON_BIN" -m venv .venv
fi

# requirements 哈希检测：依赖清单变化时重新安装（防止旧 venv 漏装新依赖）
REQ_HASH_FILE=".venv/.requirements-hash"
CURRENT_HASH="$( (cat requirements.txt; echo) | shasum -a 256 | awk '{print $1}' )"
INSTALLED_HASH="$(cat "$REQ_HASH_FILE" 2>/dev/null || echo '')"
if [ "$CURRENT_HASH" != "$INSTALLED_HASH" ]; then
  echo "[安装] 检测到依赖清单变化，正在安装后端依赖..."
  ./.venv/bin/python -m pip install -r requirements.txt
  echo "$CURRENT_HASH" > "$REQ_HASH_FILE"
elif ! ./.venv/bin/python -c "import fastapi, uvicorn, argon2" >/dev/null 2>&1; then
  echo "[安装] 正在安装后端依赖..."
  ./.venv/bin/python -m pip install -r requirements.txt
  echo "$CURRENT_HASH" > "$REQ_HASH_FILE"
fi

if [ ! -f "frontend/dist/index.html" ]; then
  echo "[错误] 未找到前端构建产物，请先在 frontend 目录执行 npm run build"
  exit 1
fi

echo "[启动] 浏览器将自动打开 http://127.0.0.1:8848"
( sleep 2; xdg-open http://127.0.0.1:8848 2>/dev/null || open http://127.0.0.1:8848 2>/dev/null || true ) &
exec ./.venv/bin/python -m app
