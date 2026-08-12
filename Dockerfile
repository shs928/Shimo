# 拾墨 Shimo — 多阶段构建（前端 + 后端）
# 构建：docker build -t shimo .
# 运行：docker run -p 8848:8848 -v shimo-data:/app/data -v shimo-vault:/app/vault shimo

# ---------- 阶段 1：前端构建 ----------
FROM node:20-alpine AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- 阶段 2：后端依赖 ----------
FROM python:3.12-slim AS backend
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# ---------- 阶段 3：运行镜像 ----------
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    SHIMO_HOST=0.0.0.0 \
    SHIMO_PORT=8848
COPY --from=backend /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=backend /usr/local/bin /usr/local/bin
COPY app/ ./app/
COPY --from=frontend /build/frontend/dist ./frontend/dist
RUN mkdir -p /app/data /app/vault
VOLUME ["/app/data", "/app/vault"]
EXPOSE 8848
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8848/health/ready', timeout=3)" || exit 1
CMD ["python", "-m", "app"]
