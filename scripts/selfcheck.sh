#!/usr/bin/env bash
# world-sim 自检：内容包校验 + 单元测试 + 框架验收 + 玩家闭环
# 兼容 macOS 自带 bash 3.2。
#
# 用法:
#   scripts/selfcheck.sh
#
# 说明:
#   - framework_check 会在临时副本里运行，避免写入仓库的 runs/ 与 reports/。
#   - 玩家闭环会临时启动 wsim.api 与 ui/player，跑完自动停止；数据写临时目录。
#   - 单项失败不会中断，最后统一给 PASS/FAIL 计数。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1

log() { printf '[selfcheck] %s\n' "$*"; }

PY=""
if [ -x "$ROOT/.venv/bin/python" ]; then
  PY="$ROOT/.venv/bin/python"
else
  PY="$(command -v python3 2>/dev/null || true)"
fi
[ -n "$PY" ] || { printf '[selfcheck] 错误: 找不到 python3\n' >&2; exit 1; }
if ! "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  printf '[selfcheck] 错误: Python 版本过低：%s，需要 >= 3.10\n' "$("$PY" --version 2>&1)" >&2
  exit 1
fi
if ! "$PY" -c 'import yaml' 2>/dev/null; then
  printf '[selfcheck] 错误: 缺少 PyYAML，请运行 %s -m pip install -r requirements.txt\n' "$PY" >&2
  exit 1
fi
if ! "$PY" -m pytest --version >/dev/null 2>&1; then
  printf '[selfcheck] 错误: 缺少 pytest，请运行 %s -m pip install -r requirements.txt\n' "$PY" >&2
  exit 1
fi

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/wsim-selfcheck.XXXXXX")" || exit 1
LOG_DIR="$WORK_DIR/logs"
mkdir -p "$LOG_DIR"
FC_TMP="$WORK_DIR/fc-tree"
PLAYER_API_PID=""
PLAYER_UI_PID=""

stop_pid() {
  _stop_pid="$1"
  _stop_label="$2"
  [ -n "$_stop_pid" ] || return 0
  if ! kill -0 "$_stop_pid" 2>/dev/null; then
    log "$_stop_label 已退出"
    return 0
  fi
  kill -INT "$_stop_pid" 2>/dev/null || true
  _i=0
  while kill -0 "$_stop_pid" 2>/dev/null && [ "$_i" -lt 30 ]; do
    sleep 0.1
    _i=$((_i + 1))
  done
  if kill -0 "$_stop_pid" 2>/dev/null; then
    kill -TERM "$_stop_pid" 2>/dev/null || true
    sleep 0.3
  fi
  if kill -0 "$_stop_pid" 2>/dev/null; then
    kill -KILL "$_stop_pid" 2>/dev/null || true
  fi
  log "$_stop_label 已停止"
}

cleanup() {
  stop_pid "$PLAYER_UI_PID" "玩家前端"
  PLAYER_UI_PID=""
  stop_pid "$PLAYER_API_PID" "wsim.api"
  PLAYER_API_PID=""
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

PASS_COUNT=0
FAIL_COUNT=0
FAIL_NAMES=""

# run_and_report <日志文件名> <显示名> <命令...>
run_and_report() {
  _file="$1"
  _label="$2"
  shift 2
  _log="$LOG_DIR/$_file.log"
  "$@" >"$_log" 2>&1
  _rc=$?
  if [ "$_rc" -eq 0 ]; then
    PASS_COUNT=$((PASS_COUNT + 1))
    printf '[PASS] %s\n' "$_label"
    tail -n 4 "$_log" | sed 's/^/       /'
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
    FAIL_NAMES="$FAIL_NAMES $_label"
    printf '[FAIL] %s (exit=%d)\n' "$_label" "$_rc"
    tail -n 15 "$_log" | sed 's/^/       /'
  fi
  return 0
}

framework_step() {
  mkdir -p "$FC_TMP"
  cp -R "$ROOT/src" "$ROOT/content" "$ROOT/config" "$ROOT/tools" \
        "$ROOT/sim" "$ROOT/plugins" "$ROOT/fixtures" "$FC_TMP/" || return 90
  "$PY" "$FC_TMP/tools/framework_check.py"
}

pick_port() {
  "$PY" -c 'import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()'
}

player_http_ok() {
  _p_url="$1"
  _p_require_ok="$2"
  _p_code="$(curl -sS -o "$WORK_DIR/player-body.json" -w '%{http_code}' "$_p_url" 2>/dev/null || echo 000)"
  [ "$_p_code" = "200" ] || return 1
  "$PY" - "$WORK_DIR/player-body.json" "$_p_require_ok" <<'PY' 2>/dev/null
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

player_wait_http() {
  _p_url="$1"
  _p_require_ok="$2"
  _p_pid="$3"
  _p_tries="$4"
  _p_i=0
  while [ "$_p_i" -lt "$_p_tries" ]; do
    if ! kill -0 "$_p_pid" 2>/dev/null; then
      return 1
    fi
    if player_http_ok "$_p_url" "$_p_require_ok"; then
      return 0
    fi
    sleep 0.25
    _p_i=$((_p_i + 1))
  done
  return 1
}

player_run_ok() {
  [ -f "$1/summary.json" ] && [ -f "$1/events.jsonl" ] && [ -f "$1/lod.json" ]
}

player_loop_step() {
  _api_port="$(pick_port)"
  _ui_port="$(pick_port)"
  while [ "$_ui_port" = "$_api_port" ]; do
    _ui_port="$(pick_port)"
  done
  _api_base="http://127.0.0.1:$_api_port"
  _ui_base="http://127.0.0.1:$_ui_port"
  _api_log="$WORK_DIR/player-api.log"
  _ui_log="$WORK_DIR/player-ui.log"
  _world="$WORK_DIR/player-world"
  mkdir -p "$_world"
  cp -R "$ROOT/content" "$ROOT/config" "$_world/" || return 90

  printf '[loop] 临时根目录: %s\n' "$_world"
  printf '[loop] 启动 wsim.api  port=%s\n' "$_api_port"
  PYTHONPATH="$ROOT/src" PYTHONUNBUFFERED=1 "$PY" -m wsim.api \
    --root "$_world" --run player-demo --port "$_api_port" >"$_api_log" 2>&1 &
  PLAYER_API_PID=$!

  if ! player_wait_http "$_api_base/api/player/state?account_id=u_demo" 1 "$PLAYER_API_PID" 160; then
    printf '[loop] wsim.api 健康检查失败\n'
    tail -n 30 "$_api_log" | sed 's/^/       /'
    stop_pid "$PLAYER_API_PID" "wsim.api"
    PLAYER_API_PID=""
    return 1
  fi
  printf '[loop] API 就绪: %s\n' "$_api_base"

  _proxy=0
  if grep -q "/api/player" "$ROOT/ui/player/server.py" 2>/dev/null; then
    _proxy=1
  fi

  if [ "$_proxy" = "1" ]; then
    printf '[loop] 启动玩家前端（反代）port=%s upstream=%s\n' "$_ui_port" "$_api_base"
    PLAYER_API="$_api_base" PYTHONPATH="$ROOT/src" PYTHONUNBUFFERED=1 \
      "$PY" "$ROOT/ui/player/server.py" --upstream "$_api_base" \
      --port "$_ui_port" --host 127.0.0.1 >"$_ui_log" 2>&1 &
  else
    _ui_run=""
    for _cand in "$ROOT/runs/delivery-10d" "$ROOT/runs/b10-10d" "$ROOT/runs/inspect-10d" "$ROOT/runs/p3c-players-1d"; do
      if [ -d "$_cand" ] && player_run_ok "$_cand"; then
        _ui_run="$_cand"
        break
      fi
    done
    if [ -z "$_ui_run" ]; then
      for _cand in "$ROOT"/runs/*; do
        [ -d "$_cand" ] || continue
        if player_run_ok "$_cand"; then
          _ui_run="$_cand"
          break
        fi
      done
    fi
    if [ -z "$_ui_run" ]; then
      printf '[loop] 没有现成只读 run，生成 1 天临时 run\n'
      if ! "$PY" "$ROOT/sim/run_headless.py" --days 1 --seed 42 --npc 0 \
          --out "$WORK_DIR/ui-runs" --run-id selfcheck-ui >"$WORK_DIR/ui-run.log" 2>&1; then
        tail -n 30 "$WORK_DIR/ui-run.log" | sed 's/^/       /'
        stop_pid "$PLAYER_API_PID" "wsim.api"
        PLAYER_API_PID=""
        return 1
      fi
      _ui_run="$WORK_DIR/ui-runs/selfcheck-ui"
    fi
    printf '[loop] 启动玩家前端（只读兜底）port=%s run=%s\n' "$_ui_port" "$_ui_run"
    PYTHONPATH="$ROOT/src" PYTHONUNBUFFERED=1 "$PY" "$ROOT/ui/player/server.py" \
      --readonly --run "$_ui_run" --port "$_ui_port" --host 127.0.0.1 >"$_ui_log" 2>&1 &
  fi
  PLAYER_UI_PID=$!

  if ! player_wait_http "$_ui_base/api/health" 0 "$PLAYER_UI_PID" 160; then
    printf '[loop] 玩家前端健康检查失败\n'
    tail -n 30 "$_ui_log" | sed 's/^/       /'
    stop_pid "$PLAYER_UI_PID" "玩家前端"
    PLAYER_UI_PID=""
    stop_pid "$PLAYER_API_PID" "wsim.api"
    PLAYER_API_PID=""
    return 1
  fi
  printf '[loop] 前端就绪: %s\n' "$_ui_base"

  if [ "$_proxy" = "1" ]; then
    if ! "$PY" - "$WORK_DIR/player-body.json" <<'PY' 2>/dev/null
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
raise SystemExit(0 if data.get("mode") == "live" and data.get("upstream_ok") is True else 1)
PY
    then
      printf '[loop] 前端未连上 API（mode/upstream_ok 异常）\n'
      tail -n 30 "$_ui_log" | sed 's/^/       /'
      stop_pid "$PLAYER_UI_PID" "玩家前端"
      PLAYER_UI_PID=""
      stop_pid "$PLAYER_API_PID" "wsim.api"
      PLAYER_API_PID=""
      return 1
    fi
    printf '[loop] 前端已连上 API（mode=live）\n'
  fi

  _walk_base="$_api_base"
  if [ "$_proxy" = "1" ]; then
    _walk_base="$_ui_base"
  fi
  printf '[loop] 跑闭环（写入端: %s）\n' "$_walk_base"
  "$PY" "$ROOT/scripts/player_smoke.py" --base-url "$_walk_base"
  _walk_rc=$?

  stop_pid "$PLAYER_UI_PID" "玩家前端"
  PLAYER_UI_PID=""
  stop_pid "$PLAYER_API_PID" "wsim.api"
  PLAYER_API_PID=""

  if [ "$_walk_rc" -ne 0 ]; then
    printf '[loop] 闭环失败，wsim.api 日志最后 30 行:\n'
    tail -n 30 "$_api_log" | sed 's/^/       /'
    printf '[loop] 闭环失败，玩家前端日志最后 30 行:\n'
    tail -n 30 "$_ui_log" | sed 's/^/       /'
    return "$_walk_rc"
  fi
  printf '[loop] 闭环 PASS\n'
  return 0
}

run_player_step() {
  printf '[RUN ] 玩家闭环（进世界 / 移动 / 对话 / 接任务）\n'
  player_loop_step
  _player_rc=$?
  if [ "$_player_rc" -eq 0 ]; then
    PASS_COUNT=$((PASS_COUNT + 1))
    printf '[PASS] 玩家闭环（wsim.api + ui/player + HTTP 动作）\n'
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
    FAIL_NAMES="$FAIL_NAMES 玩家闭环"
    printf '[FAIL] 玩家闭环（wsim.api + ui/player + HTTP 动作）(exit=%d)\n' "$_player_rc"
  fi
  return 0
}

printf '=== world-sim selfcheck ===\n'
printf '仓库: %s\n' "$ROOT"
printf 'Python: %s (%s)\n\n' "$("$PY" --version 2>&1)" "$PY"

run_and_report validate "内容包校验 content/validate_content.py" \
  "$PY" "$ROOT/content/validate_content.py"

run_and_report pytest "单元测试 pytest" \
  "$PY" -m pytest

run_and_report framework "框架验收 tools/framework_check.py（临时副本，不写 runs/）" \
  framework_step

run_player_step

printf '\n=== 总结 ===\n'
if [ "$FAIL_COUNT" -eq 0 ]; then
  printf '总结: selfcheck PASS（%d 项全部通过）\n' "$PASS_COUNT"
  exit 0
fi
printf '总结: selfcheck FAIL（PASS %d / FAIL %d；失败项:%s）\n' "$PASS_COUNT" "$FAIL_COUNT" "$FAIL_NAMES"
exit 1
