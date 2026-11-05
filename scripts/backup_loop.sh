#!/usr/bin/env bash
# 每 6 小时（BK_INTERVAL 秒）执行一次在线备份；首轮在启动时立即执行。
#
# 用法（宿主机）：
#   scripts/backup_loop.sh
#   BK_INTERVAL=60 BK_RUN=service scripts/backup_loop.sh   # 演练可缩短间隔
#
# 环境变量：
#   BK_ROOT      含 runs/、content/、tools/ 的根目录（默认仓库根）
#   BK_RUN       runs/<RUN_ID> 的 RUN_ID（默认 service）
#   BK_DIR       备份根目录（默认 $BK_ROOT/backups）
#   BK_INTERVAL  备份间隔秒数（默认 21600 = 6 小时，与 tools/backup_snapshot.py 常量一致）
#   BK_PYTHON    Python 解释器（默认 .venv/bin/python，回退 python3）
#
# 与 docs/BACKUP-RESTORE.md 的轮转常量配套：每 6 小时一份 → 48 小时滚动约 8 份，
# 另有最近 7 个 UTC 日每日 1 份。
set -u

ROOT="${BK_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
RUN_ID="${BK_RUN:-service}"
BK_DIR="${BK_DIR:-$ROOT/backups}"
INTERVAL="${BK_INTERVAL:-21600}"

log() { printf '[backup-loop] %s\n' "$*"; }

case "$INTERVAL" in
  ''|*[!0-9]*) log "BK_INTERVAL 必须是非负整数秒，当前: $INTERVAL"; exit 2 ;;
esac

if [ -n "${BK_PYTHON:-}" ]; then
  PY="$BK_PYTHON"
elif [ -x "$ROOT/.venv/bin/python" ]; then
  PY="$ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY="$(command -v python3)"
else
  PY="$(command -v python)"
fi
[ -n "$PY" ] || { log "找不到 python"; exit 1; }

TOOL="$ROOT/tools/backup_snapshot.py"
[ -f "$TOOL" ] || { log "找不到 $TOOL"; exit 1; }

log "root=$ROOT run=$RUN_ID dir=$BK_DIR interval=${INTERVAL}s python=$PY"

trap 'log "收到停止信号，退出"; exit 0' TERM INT

while :; do
  "$PY" "$TOOL" --root "$ROOT" --run "$RUN_ID" --backup-dir "$BK_DIR" >/dev/null
  rc=$?
  if [ "$rc" -eq 0 ]; then
    log "备份成功"
  else
    log "备份失败 exit=$rc（下一轮继续重试）"
  fi
  sleep "$INTERVAL" &
  SLEEP_PID=$!
  wait "$SLEEP_PID"
done
