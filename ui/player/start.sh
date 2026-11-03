#!/usr/bin/env bash
# 玩家门面启动脚本：默认启动真 API（可写 run）+ UI 反向代理。
#
# 用法：
#   ./ui/player/start.sh                  # 自动换名（若 runs/player-demo 已有 events.sqlite）
#   ./ui/player/start.sh --reset          # 删除 runs/player-demo 后重新创世
#   ./ui/player/start.sh --run player-demo-2
#   ./ui/player/start.sh --readonly       # 不启动真 API，UI 进入只读降级
#   ./ui/player/start.sh --reuse          # 仅当已确认上游会 replay 时才用（需 WSIM_REPLAY_CONFIRMED=1）
#
# 写入边界：
#   可写：runs/player-demo*（由本脚本启动的 wsim.api 产生 events.sqlite）
#   只读：runs/p3c-players-1d 等既有 run（本脚本不会打开）
#
# 为什么默认换名：wsim.api 的 WorldSession 每次启动都重新 bootstrap genesis，
# 不会 replay 既有 events.sqlite。在同一目录重启会追加第二段创世，污染账本。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUN=""
API_PORT="8020"
UI_PORT="8030"
READONLY=0
REUSE=0
RESET=0

while [ $# -gt 0 ]; do
  case "$1" in
    --readonly) READONLY=1 ;;
    --reuse) REUSE=1 ;;
    --reset) RESET=1 ;;
    --run) RUN="${2:-}"; shift ;;
    --api-port) API_PORT="${2:-}"; shift ;;
    --ui-port) UI_PORT="${2:-}"; shift ;;
    -h|--help)
      sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "[start] 未知参数：$1" >&2
      exit 2
      ;;
  esac
  shift
done

echo "[start] root=$ROOT run=${RUN:-"(默认)"} api=$API_PORT ui=$UI_PORT readonly=$READONLY"

if [ "$READONLY" -eq 1 ]; then
  echo "[start] 只读降级：不启动 wsim.api，UI 只做本地回放 + 提案。"
  if [ -n "$RUN" ]; then
    exec python3 "$ROOT/ui/player/server.py" --readonly --port "$UI_PORT" --run "runs/$RUN"
  else
    exec python3 "$ROOT/ui/player/server.py" --readonly --port "$UI_PORT"
  fi
fi

RUN="${RUN:-player-demo}"
RUN_DIR="$ROOT/runs/$RUN"
if [ -f "$RUN_DIR/events.sqlite" ]; then
  if [ "$RESET" -eq 1 ]; then
    echo "[start] --reset：删除 $RUN_DIR 后重新创世"
    rm -rf "$RUN_DIR"
  elif [ "$REUSE" -eq 1 ]; then
    if [ "${WSIM_REPLAY_CONFIRMED:-0}" != "1" ]; then
      echo "[start] 拒绝 --reuse：wsim.api 目前不 replay events.sqlite。" >&2
      echo "[start] 复用会追加第二段创世；确认上游支持 replay 后设 WSIM_REPLAY_CONFIRMED=1 再用。" >&2
      exit 3
    fi
    echo "[start] 警告：--reuse 复用 ${RUN_DIR}（WSIM_REPLAY_CONFIRMED=1，账本请自行核对）" >&2
  else
    OLD_RUN="$RUN"
    RUN="${RUN}-$(date +%Y%m%d%H%M%S)"
    RUN_DIR="$ROOT/runs/$RUN"
    echo "[start] 检测到 runs/$OLD_RUN/events.sqlite 已存在，自动换名为 runs/$RUN"
    echo "[start] 旧的 runs/$OLD_RUN 不会被写入；需要复用请用 --reset 或显式 --reuse。"
  fi
fi

API_PID=""
cleanup() {
  if [ -n "$API_PID" ] && kill -0 "$API_PID" 2>/dev/null; then
    echo "[start] 停止 wsim.api（pid ${API_PID}）"
    kill "$API_PID" 2>/dev/null || true
    wait "$API_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

echo "[start] 启动 wsim.api：run=$RUN port=$API_PORT"
(
  cd "$ROOT"
  PYTHONPATH=src python3 -m wsim.api --run "$RUN" --port "$API_PORT"
) &
API_PID=$!

for i in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$API_PORT/api/player/state?account_id=u_demo" 2>/dev/null \
      | grep -q '"profile_version"'; then
    echo "[start] wsim.api 就绪（run=${RUN}）"
    break
  fi
  if ! kill -0 "$API_PID" 2>/dev/null; then
    echo "[start] wsim.api 启动失败，请查看上面的日志。" >&2
    exit 4
  fi
  sleep 0.5
done

if ! curl -fsS "http://127.0.0.1:$API_PORT/api/player/state?account_id=u_demo" 2>/dev/null \
    | grep -q '"profile_version"'; then
  echo "[start] 等待 wsim.api 超时：http://127.0.0.1:$API_PORT" >&2
  exit 5
fi

echo "[start] 启动 UI 反向代理：http://127.0.0.1:$UI_PORT/ -> $API_PORT"
python3 "$ROOT/ui/player/server.py" --upstream "http://127.0.0.1:$API_PORT" --port "$UI_PORT"
