# W5 容器镜像：单镜像同时承载引擎/API（wsim.api）与玩家前端（ui/player）。
# 构建上下文为仓库根目录：docker build -f deploy/Dockerfile .
# 镜像内不复制 .env；运行时密钥只来自 compose 的 env_file（挂载式只读环境注入）。

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PIP_NO_CACHE_DIR=1 \
    RUN_ID=service \
    API_PORT=8020 \
    UI_PORT=8030

WORKDIR /app

# 仅安装锁定依赖，避免构建期解析版本漂移。
COPY requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir -r /app/requirements.lock

# 只复制运行必需资产；.env 永不进入镜像（另见 deploy/Dockerfile.dockerignore）。
COPY src /app/src
COPY content /app/content
COPY config /app/config
COPY ui /app/ui
COPY scripts /app/scripts
COPY deploy /app/deploy

# 非 root 运行；runs 目录由镜像预建，命名卷首次挂载会继承其属主。
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app \
    && mkdir -p /app/runs \
    && chown -R app:app /app/runs /app/deploy \
    && chmod +x /app/deploy/entrypoint.sh

USER app

EXPOSE 8020 8030

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD ["python", "/app/deploy/healthcheck.py"]

ENTRYPOINT ["/app/deploy/entrypoint.sh"]
