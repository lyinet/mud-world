#!/usr/bin/env bash
# 同一启动路径起两个世界：
#   scripts/start_world.sh --world xkx      # 侠客行复刻（8041 / 8011）
#   scripts/start_world.sh --world default  # 九地点世界（8031 / 8000）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="$(command -v python3)"
fi

WORLD="xkx"
CONFIG=""
API_PORT_OVERRIDE=""
UI_PORT_OVERRIDE=""
RESET=0

usage() {
  cat <<'EOF'
用法：scripts/start_world.sh [--world <名字>] [--config <路径>]
                           [--api-port <端口>] [--ui-port <端口>] [--reset]

  --world   读取 config/worlds/<名字>.yaml（默认 xkx）
  --config  直接指定世界配置文件
  --reset   重建该世界的 run 目录（会清空该世界进度；不影响其他世界）
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --world) WORLD="${2:-}"; shift 2 ;;
    --config) CONFIG="${2:-}"; shift 2 ;;
    --api-port) API_PORT_OVERRIDE="${2:-}"; shift 2 ;;
    --ui-port) UI_PORT_OVERRIDE="${2:-}"; shift 2 ;;
    --reset) RESET=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数：$1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "$CONFIG" ]]; then
  CONFIG="$ROOT/config/worlds/${WORLD}.yaml"
fi
if [[ ! -f "$CONFIG" ]]; then
  echo "找不到世界配置：$CONFIG" >&2
  echo "可用配置：" >&2
  ls "$ROOT/config/worlds"/*.yaml 2>/dev/null | sed 's#^#  #' >&2 || true
  exit 2
fi

FIELDS="$("$PY" - "$CONFIG" "$ROOT" <<'PY'
import sys
from pathlib import Path

import yaml

config_path = Path(sys.argv[1])
root = Path(sys.argv[2])
cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

yaml_markers = ("locations.yaml", "npcs.yaml", "items.yaml")
xkx_markers = ("rooms.json", "exits.json", "npcs.json")


def usable(probe: Path) -> bool:
    return all((probe / name).is_file() for name in yaml_markers) or all(
        (probe / name).is_file() for name in xkx_markers
    )


chosen = None
candidates = cfg.get("content_root_candidates") or ["content"]
for raw in candidates:
    base = (root / str(raw)).resolve()
    for probe in (base, base / "content"):
        if usable(probe):
            chosen = probe
            break
    if chosen:
        break
if chosen is None:
    fallback = (root / "content").resolve()
    if usable(fallback):
        chosen = fallback
if chosen is None:
    print(
        "找不到可用的内容包（需要 locations.yaml/npcs.yaml/items.yaml，"
        "或 xkx 的 rooms.json/exits.json/npcs.json）。",
        file=sys.stderr,
    )
    sys.exit(3)

fields = [
    str(cfg.get("run_id") or cfg.get("id") or "xkx-world"),
    str(cfg.get("host") or "127.0.0.1"),
    str(cfg.get("api_port") or 8041),
    str(cfg.get("ui_port") or 8011),
    str(cfg.get("seed") or 42),
    str(cfg.get("tick_step") or 6),
    str(cfg.get("npc_limit") or 0),
    str(cfg.get("spawn") or "market"),
    str(cfg.get("auth") or "off"),
    str(cfg.get("ambient") or 0),
    str(cfg.get("ui_kind") or "xkx"),
    str(chosen),
]
print("\t".join(fields))
PY
)" || exit $?

IFS=$'\t' read -r RUN_ID HOST API_PORT UI_PORT SEED TICK_STEP NPC_LIMIT SPAWN AUTH AMBIENT UI_KIND CONTENT_ROOT <<<"$FIELDS"
API_PORT="${API_PORT_OVERRIDE:-$API_PORT}"
UI_PORT="${UI_PORT_OVERRIDE:-$UI_PORT}"

if [[ ! -d "$CONTENT_ROOT" ]]; then
  echo "内容目录不存在：$CONTENT_ROOT" >&2
  exit 2
fi
if [[ "$CONTENT_ROOT" != "$ROOT/content" ]]; then
  echo "内容包：$CONTENT_ROOT"
else
  echo "内容包：$CONTENT_ROOT（现有九地点内容）"
fi

RUN_DIR="$ROOT/runs/$RUN_ID"
if [[ "$RESET" == "1" && -d "$RUN_DIR" ]]; then
  echo "重建世界：删除 $RUN_DIR"
  rm -rf "$RUN_DIR"
fi
mkdir -p "$RUN_DIR/logs"
API_LOG="$RUN_DIR/logs/api.log"
UI_LOG="$RUN_DIR/logs/ui.log"

port_free() {
  "$PY" - "$1" "$2" <<'PY'
import socket
import sys

host, port = sys.argv[1], int(sys.argv[2])
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
# 刚被停掉的服务会留下 TIME_WAIT；允许复用才能判断“确实没人监听”。
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    sock.bind((host, port))
except OSError:
    sys.exit(1)
finally:
    sock.close()
PY
}

wait_port_free() {
  local host="$1" port="$2" i
  for i in $(seq 1 80); do
    if port_free "$host" "$port"; then
      return 0
    fi
    sleep 0.25
  done
  return 1
}

if ! wait_port_free "$HOST" "$API_PORT"; then
  echo "端口 $HOST:$API_PORT 已被占用；没有启动任何新进程。" >&2
  echo "若这是已在运行的 xkx 世界，直接访问 http://$HOST:$UI_PORT 。" >&2
  exit 1
fi
if ! wait_port_free "$HOST" "$UI_PORT"; then
  echo "端口 $HOST:$UI_PORT 已被占用；没有启动任何新进程。" >&2
  exit 1
fi

export PYTHONPATH="$ROOT/src"
API_PID=""
UI_PID=""
cleanup() {
  trap - EXIT INT TERM
  if [[ -n "$UI_PID" ]] && kill -0 "$UI_PID" 2>/dev/null; then kill "$UI_PID" 2>/dev/null || true; fi
  if [[ -n "$API_PID" ]] && kill -0 "$API_PID" 2>/dev/null; then kill "$API_PID" 2>/dev/null || true; fi
  wait "$UI_PID" 2>/dev/null || true
  wait "$API_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

XKX_AMBIENT="$AMBIENT" "$PY" -m wsim.api \
  --root "$ROOT" \
  --run "$RUN_ID" \
  --host "$HOST" \
  --port "$API_PORT" \
  --seed "$SEED" \
  --tick-step "$TICK_STEP" \
  --npcs "$NPC_LIMIT" \
  --spawn "$SPAWN" \
  --auth "$AUTH" \
  --content-root "$CONTENT_ROOT" \
  --world-config "$CONFIG" \
  >>"$API_LOG" 2>&1 &
API_PID=$!

wait_http() {
  local url="$1" pid="$2" i
  for i in $(seq 1 160); do
    if curl -fsS --max-time 2 "$url" >/dev/null 2>&1; then
      return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      return 1
    fi
    sleep 0.25
  done
  return 1
}

if ! wait_http "http://$HOST:$API_PORT/api/health" "$API_PID"; then
  echo "玩家 API 启动失败。最近日志：" >&2
  tail -n 30 "$API_LOG" >&2 || true
  echo "提示：若日志是 content_hash_mismatch，说明内容包变了；确认后可加 --reset 重建该世界。" >&2
  exit 1
fi

if [[ "$UI_KIND" == "player" ]]; then
  "$PY" "$ROOT/ui/player/server.py" \
    --host "$HOST" --port "$UI_PORT" --upstream "http://$HOST:$API_PORT" \
    >>"$UI_LOG" 2>&1 &
  UI_HEALTH="http://$HOST:$UI_PORT/api/health"
else
  "$PY" "$ROOT/ui/xkx/server.py" \
    --host "$HOST" --port "$UI_PORT" --upstream "http://$HOST:$API_PORT" \
    --static-dir "$ROOT/ui/xkx" \
    >>"$UI_LOG" 2>&1 &
  UI_HEALTH="http://$HOST:$UI_PORT/healthz"
fi
UI_PID=$!

if ! wait_http "$UI_HEALTH" "$UI_PID"; then
  echo "前端启动失败。最近日志：" >&2
  tail -n 30 "$UI_LOG" >&2 || true
  exit 1
fi

echo ""
echo "世界：$(basename "$CONFIG")  run=$RUN_ID"
echo "访问地址：http://$HOST:$UI_PORT"
echo "玩家 API：http://$HOST:$API_PORT"
echo "日志：$API_LOG"
echo "      $UI_LOG"
echo "按 Ctrl-C 停止本次启动的进程（不会影响其他世界）。"
wait "$API_PID"
