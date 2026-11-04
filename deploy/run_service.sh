#!/usr/bin/env bash
# 启动 W1 服务模式：鉴权开启，run_dir 与账号库都在 runs/<RUN>/ 下。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${PY:-python3}"
RUN="${RUN:-service}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8020}"
TOKEN_TTL="${TOKEN_TTL:-86400}"
RECONNECT_GRACE="${RECONNECT_GRACE:-60}"

export PYTHONPATH="$ROOT/src"
echo "[run_service] root=$ROOT run=$RUN listen=http://$HOST:$PORT auth=auto"
exec "$PY" -m wsim.api \
  --root "$ROOT" \
  --run "$RUN" \
  --host "$HOST" \
  --port "$PORT" \
  --auth auto \
  --token-ttl "$TOKEN_TTL" \
  --reconnect-grace "$RECONNECT_GRACE"
