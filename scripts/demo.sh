#!/usr/bin/env bash
# world-sim 一键演示：wsim.api(8020) + 玩家前端(8000)
#
# 兼容 macOS 自带 bash 3.2（不使用 bash 4+ 语法）。
#
# 用法:
#   scripts/demo.sh
#   API_PORT=8120 PORT=8010 scripts/demo.sh
#   RUN_ID=my-demo scripts/demo.sh
#
# 行为:
#   1. 检查 Python >= 3.10、PyYAML、两个端口
#   2. 处理 runs/player-demo genesis 冲突：已有 events.sqlite 就自动换名，绝不静默追加
#   3. 启动 wsim.api，等 /api/player/state 就绪
#   4. 启动 ui/player/server.py；若它已支持反代则转发到 API，否则以只读模式兜底
#   5. 两个健康检查都通过才打印地址；任一失败打印真实错误并停掉另一个
#   6. Ctrl-C 同时收掉两个进程，释放两个端口
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1

API_PORT="${API_PORT:-8020}"
UI_PORT="${PORT:-8000}"
API_HOST="${API_HOST:-127.0.0.1}"
UI_HOST="${UI_HOST:-127.0.0.1}"
RUN_ID="${RUN_ID:-player-demo}"
UI_RUN="${UI_RUN:-}"

API_BASE="http://$API_HOST:$API_PORT"
UI_BASE="http://$UI_HOST:$UI_PORT"
API_PID=""
UI_PID=""
SIGNAL="TERM"
CLEANED=0
UI_PROXY=0
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/wsim-demo.XXXXXX" 2>/dev/null || true)"
API_LOG="${WORK_DIR:-/tmp}/api.log"
UI_LOG="${WORK_DIR:-/tmp}/ui.log"
BODY_FILE="${WORK_DIR:-/tmp}/body.json"

log() { printf '[demo] %s\n' "$*"; }
die() {
  printf '[demo] 错误: %s\n' "$*" >&2
  if [ -n "${WORK_DIR:-}" ]; then
    rm -rf "$WORK_DIR"
  fi
  exit 1
}

# ---------------------------------------------------------------- 1. Python
PY=""
if [ -x "$ROOT/.venv/bin/python" ]; then
  PY="$ROOT/.venv/bin/python"
else
  PY="$(command -v python3 2>/dev/null || true)"
fi
[ -n "$PY" ] || die "找不到 python3。请先安装 Python 3.10+（macOS: brew install python@3.12）"
if ! "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  die "Python 版本过低：$("$PY" --version 2>&1)，需要 >= 3.10"
fi
log "Python: $("$PY" --version 2>&1) ($PY)"

# ---------------------------------------------------------------- 2. 依赖
if ! "$PY" -c 'import yaml' 2>/dev/null; then
  die "缺少 PyYAML。请运行: $PY -m pip install -r requirements.txt"
fi
log "依赖: PyYAML 可用"
[ -f "$ROOT/src/wsim/api/__main__.py" ] || die "缺少 src/wsim/api/__main__.py，无法启动玩家 API"
[ -f "$ROOT/ui/player/server.py" ] || die "缺少 ui/player/server.py，无法启动玩家前端"

# ---------------------------------------------------------------- 3. 端口
for port in "$API_PORT" "$UI_PORT"; do
  if command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    die "端口 $port 已被占用。换端口: API_PORT=8120 PORT=8010 scripts/demo.sh；查看占用: lsof -nP -iTCP:$port -sTCP:LISTEN"
  fi
done
log "端口: API=$API_PORT 前端=$UI_PORT 均空闲"

# ---------------------------------------------------------------- 4. genesis 陷阱
if [ -f "$ROOT/runs/$RUN_ID/events.sqlite" ]; then
  NEW_RUN_ID="$RUN_ID-$(date +%Y%m%d-%H%M%S)-$$"
  log "检测到 runs/$RUN_ID/events.sqlite；不静默追加，改用 ${NEW_RUN_ID}（旧数据未动）"
  RUN_ID="$NEW_RUN_ID"
fi
log "API run: runs/$RUN_ID"

# ---------------------------------------------------------------- 5. 前端模式
if grep -q "/api/player" "$ROOT/ui/player/server.py" 2>/dev/null; then
  UI_PROXY=1
  UI_MODE="反向前端 -> $API_BASE"
else
  UI_MODE="只读前端（ui/player 尚未反代 API）"
fi

run_ok() {
  [ -f "$1/summary.json" ] && [ -f "$1/events.jsonl" ] && [ -f "$1/lod.json" ]
}

if [ "$UI_PROXY" = "0" ] && [ -z "$UI_RUN" ]; then
  for cand in "$ROOT/runs/delivery-10d" "$ROOT/runs/b10-10d" "$ROOT/runs/inspect-10d" "$ROOT/runs/p3c-players-1d"; do
    if [ -d "$cand" ] && run_ok "$cand"; then
      UI_RUN="$cand"
      break
    fi
  done
  if [ -z "$UI_RUN" ]; then
    for cand in "$ROOT"/runs/*; do
      [ -d "$cand" ] || continue
      if run_ok "$cand"; then
        UI_RUN="$cand"
        break
      fi
    done
  fi
fi
if [ "$UI_PROXY" = "0" ]; then
  if [ -n "$UI_RUN" ] && [ -d "$UI_RUN" ] && run_ok "$UI_RUN"; then
    :
  elif [ -z "$UI_RUN" ]; then
    UI_RUN="$ROOT/runs/quickstart-10d"
    log "没有可用的只读 run，生成 10 天演示 run（只需一次）..."
    if ! "$PY" "$ROOT/sim/run_headless.py" --days 10 --seed 42 --npc 0 --run-id quickstart-10d >"$API_LOG.run" 2>&1; then
      tail -n 20 "$API_LOG.run" | sed 's/^/       /'
      die "生成 10 天 run 失败；请手动运行: $PY sim/run_headless.py --days 10 --seed 42 --npc 0 --run-id quickstart-10d"
    fi
    run_ok "$UI_RUN" || die "自动生成的 run 不完整：$UI_RUN"
  else
    die "UI_RUN 缺少 summary.json / events.jsonl / lod.json：$UI_RUN"
  fi
  log "前端只读 run: $UI_RUN"
fi

# ---------------------------------------------------------------- 6. 退出清理
stop_one() {
  local pid="$1"
  local label="$2"
  [ -n "$pid" ] || return 0
  if ! kill -0 "$pid" 2>/dev/null; then
    log "$label 已退出"
    return 0
  fi
  # Ctrl-C 时终端已经把 SIGINT 发给两个子进程，先等它们自己收尾。
  if [ "$SIGNAL" = "INT" ]; then
    local i=0
    while kill -0 "$pid" 2>/dev/null && [ "$i" -lt 10 ]; do
      sleep 0.1
      i=$((i + 1))
    done
  fi
  if kill -0 "$pid" 2>/dev/null; then
    log "正在停止 $label (pid=$pid)"
    kill -INT "$pid" 2>/dev/null || true
    local i=0
    while kill -0 "$pid" 2>/dev/null && [ "$i" -lt 15 ]; do
      sleep 0.1
      i=$((i + 1))
    done
    if kill -0 "$pid" 2>/dev/null; then
      log "$label 未在 1.5 秒内结束，发送 SIGTERM"
      kill -TERM "$pid" 2>/dev/null || true
      sleep 0.3
    fi
    if kill -0 "$pid" 2>/dev/null; then
      log "$label 未结束，发送 SIGKILL"
      kill -KILL "$pid" 2>/dev/null || true
    fi
  else
    log "$label 已退出"
  fi
  return 0
}

port_free() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    ! lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
  else
    return 0
  fi
}

cleanup() {
  [ "$CLEANED" -eq 1 ] && return 0
  CLEANED=1
  trap - INT TERM EXIT
  stop_one "$UI_PID" "前端"
  UI_PID=""
  stop_one "$API_PID" "API"
  API_PID=""
  sleep 0.2
  if port_free "$UI_PORT"; then
    log "清理完成: 端口 $UI_PORT 已释放"
  else
    log "警告: 端口 $UI_PORT 仍有监听，请用 lsof -nP -iTCP:$UI_PORT -sTCP:LISTEN 检查"
  fi
  if port_free "$API_PORT"; then
    log "清理完成: 端口 $API_PORT 已释放"
  else
    log "警告: 端口 $API_PORT 仍有监听，请用 lsof -nP -iTCP:$API_PORT -sTCP:LISTEN 检查"
  fi
  if [ -n "${WORK_DIR:-}" ]; then
    rm -rf "$WORK_DIR"
  fi
}
trap 'SIGNAL=INT; cleanup' INT
trap 'SIGNAL=TERM; cleanup' TERM
trap cleanup EXIT

# ---------------------------------------------------------------- 7. API
log "启动 wsim.api -> $API_BASE"
PYTHONPATH="$ROOT/src" PYTHONUNBUFFERED=1 "$PY" -m wsim.api \
  --root "$ROOT" --run "$RUN_ID" --port "$API_PORT" >"$API_LOG" 2>&1 &
API_PID=$!
disown "$API_PID" 2>/dev/null || true

http_json_ok() {
  local url="$1"
  local require_ok="$2"
  local code
  code="$(curl -sS -o "$BODY_FILE" -w '%{http_code}' "$url" 2>"$BODY_FILE.err" || echo 000)"
  [ "$code" = "200" ] || return 1
  "$PY" - "$BODY_FILE" "$require_ok" <<'PY' 2>/dev/null
import json
import sys

path, require_ok = sys.argv[1], sys.argv[2] == "1"
try:
    data = json.load(open(path, encoding="utf-8"))
except Exception:
    raise SystemExit(1)
if require_ok and data.get("ok") is not True:
    raise SystemExit(1)
PY
}

wait_http() {
  local url="$1"
  local require_ok="$2"
  local pid="$3"
  local tries="$4"
  local i=0
  while [ "$i" -lt "$tries" ]; do
    if ! kill -0 "$pid" 2>/dev/null; then
      return 1
    fi
    if http_json_ok "$url" "$require_ok"; then
      return 0
    fi
    sleep 0.25
    i=$((i + 1))
  done
  return 1
}

if ! wait_http "$API_BASE/api/player/state?account_id=u_demo" 1 "$API_PID" 160; then
  code="$(curl -sS -o "$BODY_FILE" -w '%{http_code}' "$API_BASE/api/player/state?account_id=u_demo" 2>/dev/null || echo 000)"
  log "API 健康检查失败: HTTP $code  $API_BASE/api/player/state"
  if [ -s "$BODY_FILE" ]; then
    log "响应: $(head -c 300 "$BODY_FILE")"
  fi
  log "wsim.api 日志最后 30 行:"
  tail -n 30 "$API_LOG" | sed 's/^/       /'
  exit 1
fi
log "健康检查: API 200 (GET /api/player/state)"

# ---------------------------------------------------------------- 8. 前端
if [ "$UI_PROXY" = "1" ]; then
  log "启动玩家前端（反代模式）-> $UI_BASE (upstream $API_BASE)"
  PLAYER_API="$API_BASE" PYTHONPATH="$ROOT/src" PYTHONUNBUFFERED=1 "$PY" "$ROOT/ui/player/server.py" \
    --upstream "$API_BASE" --port "$UI_PORT" --host "$UI_HOST" >"$UI_LOG" 2>&1 &
else
  log "启动玩家前端（只读兜底）-> $UI_BASE"
  PYTHONPATH="$ROOT/src" PYTHONUNBUFFERED=1 "$PY" "$ROOT/ui/player/server.py" \
    --readonly --run "$UI_RUN" --port "$UI_PORT" --host "$UI_HOST" >"$UI_LOG" 2>&1 &
fi
UI_PID=$!
disown "$UI_PID" 2>/dev/null || true

if ! wait_http "$UI_BASE/api/health" 0 "$UI_PID" 160; then
  code="$(curl -sS -o "$BODY_FILE" -w '%{http_code}' "$UI_BASE/api/health" 2>/dev/null || echo 000)"
  log "前端健康检查失败: HTTP $code  $UI_BASE/api/health"
  if [ -s "$BODY_FILE" ]; then
    log "响应: $(head -c 300 "$BODY_FILE")"
  fi
  log "ui/player 日志最后 30 行:"
  tail -n 30 "$UI_LOG" | sed 's/^/       /'
  exit 1
fi
log "健康检查: 前端 200 (GET /api/health)"

if [ "$UI_PROXY" = "1" ]; then
  if ! "$PY" - "$BODY_FILE" <<'PY' 2>/dev/null
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
raise SystemExit(0 if data.get("mode") == "live" and data.get("upstream_ok") is True else 1)
PY
  then
    log "前端健康检查失败: /api/health 未显示 live/upstream_ok"
    if [ -s "$BODY_FILE" ]; then
      log "响应: $(head -c 300 "$BODY_FILE")"
    fi
    log "ui/player 日志最后 30 行:"
    tail -n 30 "$UI_LOG" | sed 's/^/       /'
    exit 1
  fi
  log "健康检查: 前端已连上 API (mode=live, upstream_ok=true)"
fi

# ---------------------------------------------------------------- 9. 就绪
log "API:  $API_BASE  (run=runs/$RUN_ID)"
log "前端: $UI_BASE/  ($UI_MODE)"
log "打开 $UI_BASE/ 查看；Ctrl-C 同时停止两个服务"
if [ -s "$API_LOG" ]; then
  head -n 2 "$API_LOG" | sed 's/^/[api] /'
fi
if [ -s "$UI_LOG" ]; then
  head -n 3 "$UI_LOG" | sed 's/^/[ui] /'
fi

# ---------------------------------------------------------------- 10. 等待
while :; do
  if [ "$CLEANED" -eq 1 ]; then
    exit 130
  fi
  if ! kill -0 "$API_PID" 2>/dev/null; then
    log "wsim.api 进程已退出，停止前端"
    exit 1
  fi
  if ! kill -0 "$UI_PID" 2>/dev/null; then
    log "玩家前端进程已退出，停止 API"
    exit 1
  fi
  sleep 0.5
done
