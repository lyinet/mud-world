#!/usr/bin/env bash
# 首发一键启动：准备 venv/依赖 -> 生成 2 日种子世界 -> 起 API + 前端。
# 兼容 macOS 自带 bash 3.2。
#
# 用法:
#   ./start.sh
#   API_PORT=8120 PORT=8010 ./start.sh
#   RUN_ID=my-demo ./start.sh
#
# 可选:
#   SKIP_SEED=1 ./start.sh     # 跳过种子世界生成（只玩实时会话）
set -u

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT" || exit 1

log() { printf '[start] %s\n' "$*"; }
die() {
  printf '[start] 错误: %s\n' "$*" >&2
  exit 1
}

# ---------------------------------------------------------------- Python
PY="$(command -v python3 2>/dev/null || true)"
[ -n "$PY" ] || die "找不到 python3。请先安装 Python 3.10+（macOS: brew install python@3.12）"
if ! "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  die "Python 版本过低：$("$PY" --version 2>&1)，需要 >= 3.10"
fi

# ---------------------------------------------------------------- venv / 依赖
VENV_PY="$ROOT/.venv/bin/python"
NEED_INSTALL=0
if [ ! -x "$VENV_PY" ]; then
  NEED_INSTALL=1
elif ! "$VENV_PY" -c 'import yaml, pytest' >/dev/null 2>&1; then
  NEED_INSTALL=1
fi

if [ "$NEED_INSTALL" = "1" ]; then
  if [ ! -x "$VENV_PY" ]; then
    log "首次运行：创建 .venv ..."
    if ! "$PY" -m venv "$ROOT/.venv" >/tmp/wsim-venv.log 2>&1; then
      tail -n 20 /tmp/wsim-venv.log | sed 's/^/       /' >&2 || true
      die "创建 .venv 失败。macOS 可先执行: xcode-select --install，或手动 python3 -m venv .venv"
    fi
  fi
  log "安装依赖 requirements.lock（首次可能需要几十秒）..."
  if ! "$VENV_PY" -m pip install --disable-pip-version-check -r "$ROOT/requirements.lock" >/tmp/wsim-pip.log 2>&1; then
    tail -n 25 /tmp/wsim-pip.log | sed 's/^/       /' >&2 || true
    die "依赖安装失败。请检查网络/代理后重试，或手动运行: .venv/bin/pip install -r requirements.lock"
  fi
fi
VENV_PY="$ROOT/.venv/bin/python"
log "Python: $("$VENV_PY" --version 2>&1) ($VENV_PY)"

# ---------------------------------------------------------------- 种子世界
SEED_RUN="quickstart-2d"
SEED_DIR="$ROOT/runs/$SEED_RUN"
if [ "${SKIP_SEED:-0}" != "1" ]; then
  if [ ! -s "$SEED_DIR/events.jsonl" ] || [ ! -s "$SEED_DIR/summary.json" ]; then
    log "生成 2 日种子世界（约 2 秒，只需一次）..."
    if ! PYTHONPATH="$ROOT/src" "$VENV_PY" "$ROOT/sim/run_headless.py" \
        --days 2 --seed 42 --npc 0 --out "$ROOT/runs" --run-id "$SEED_RUN" \
        >/tmp/wsim-seed.log 2>&1; then
      tail -n 20 /tmp/wsim-seed.log | sed 's/^/       /' >&2 || true
      die "种子世界生成失败。可手动重试: PYTHONPATH=src .venv/bin/python sim/run_headless.py --days 2 --seed 42 --npc 0 --run-id $SEED_RUN"
    fi
  fi
  printf '\n'
  "$VENV_PY" "$ROOT/scripts/world_feed.py" --root "$ROOT" --run "runs/$SEED_RUN" --lines 8 || true
  printf '\n'
fi

# ---------------------------------------------------------------- 两个服务
log "启动 API + 玩家前端 ..."
exec "$ROOT/scripts/demo.sh"
