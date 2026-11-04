#!/usr/bin/env bash
# 容器入口：先拉起引擎/API，健康后再拉起玩家前端；任一进程退出即整体退出。
set -euo pipefail

RUN_ID="${RUN_ID:-service}"
API_PORT="${API_PORT:-8020}"
UI_PORT="${UI_PORT:-8030}"
API_HOST="${API_HOST:-0.0.0.0}"
UI_HOST="${UI_HOST:-0.0.0.0}"
AUTH_MODE="${AUTH_MODE:-on}"
TICK_STEP="${TICK_STEP:-6}"

mkdir -p /app/runs

python -m wsim.api \
  --root /app \
  --run "$RUN_ID" \
  --host "$API_HOST" \
  --port "$API_PORT" \
  --auth "$AUTH_MODE" \
  --tick-step "$TICK_STEP" &
API_PID=$!

python /app/deploy/wait_for.py "http://127.0.0.1:${API_PORT}/api/service/info" 30 \
  || { echo "[entrypoint] API 未在 30s 内就绪，退出" >&2; kill -TERM "$API_PID" 2>/dev/null || true; exit 1; }

python /app/ui/player/server.py \
  --upstream "http://127.0.0.1:${API_PORT}" \
  --host "$UI_HOST" \
  --port "$UI_PORT" &
UI_PID=$!

terminate() {
  echo "[entrypoint] 收到停止信号，优雅退出" >&2
  kill -TERM "$API_PID" "$UI_PID" 2>/dev/null || true
  wait "$API_PID" "$UI_PID" 2>/dev/null || true
  exit 0
}
trap terminate TERM INT

# 任一子进程意外退出，整体退出，交给 restart 策略处理。
set +e
wait -n "$API_PID" "$UI_PID"
CODE=$?
set -e
echo "[entrypoint] 子进程退出 code=$CODE，停止另一进程" >&2
kill -TERM "$API_PID" "$UI_PID" 2>/dev/null || true
wait "$API_PID" "$UI_PID" 2>/dev/null || true
exit "$CODE"
