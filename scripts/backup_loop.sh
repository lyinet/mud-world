#!/usr/bin/env bash
# 每 6 小时（BK_INTERVAL 秒）执行一次在线备份；首轮在启动时立即执行。
#
# 失败语义（P0 要求：不容许静默失败）：
#   备份或异地推送失败 → 记录 last_backup_status.json（ok=false + 失败阶段 + 退出码）
#   → 日志打印原因 → 退出码 1。容器因此进入 Exited(非零)，不会静默 Up。
#   单次失败先按 BK_RETRIES 重试（默认 2 次，间隔 BK_RETRY_DELAY 秒）。
#
# 用法（宿主机）：
#   scripts/backup_loop.sh
#   BK_INTERVAL=60 BK_RUN=service scripts/backup_loop.sh   # 演练可缩短间隔
#   XKX_OFFSITE_MODE=local XKX_OFFSITE_TARGET=/mnt/offsite scripts/backup_loop.sh
#
# 环境变量：
#   BK_ROOT          含 runs/、content/、tools/ 的根目录（默认仓库根）
#   BK_RUN           runs/<RUN_ID> 的 RUN_ID（默认 service）
#   BK_DIR           备份根目录（默认 $BK_ROOT/backups）
#   BK_INTERVAL      备份间隔秒数（默认 21600 = 6 小时，与 tools/backup_snapshot.py 常量一致）
#   BK_PYTHON        Python 解释器（默认 .venv/bin/python，回退 python3）
#   BK_RETRIES       单轮失败重试次数（默认 2）
#   BK_RETRY_DELAY   重试间隔秒（默认 30）
#   XKX_OFFSITE_MODE / XKX_OFFSITE_TARGET / XKX_OFFSITE_REQUIRED
#                    异地推送配置，含义见 tools/backup_offsite.sh
#
# 轮转常量见 tools/backup_snapshot.py：每 6 小时一份 → 48 小时滚动约 8 份，
# 另有最近 7 个 UTC 日每日 1 份。
set -u

ROOT="${BK_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
RUN_ID="${BK_RUN:-service}"
BK_DIR="${BK_DIR:-$ROOT/backups}"
INTERVAL="${BK_INTERVAL:-21600}"
RETRIES="${BK_RETRIES:-2}"
RETRY_DELAY="${BK_RETRY_DELAY:-30}"
STATUS_FILE="$BK_DIR/last_backup_status.json"

log() { printf '[backup-loop] %s\n' "$*"; }

case "$INTERVAL" in
  ''|*[!0-9]*) log "BK_INTERVAL 必须是非负整数秒，当前: $INTERVAL"; exit 2 ;;
esac
case "$RETRIES" in
  ''|*[!0-9]*) log "BK_RETRIES 必须是非负整数，当前: $RETRIES"; exit 2 ;;
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
OFFSITE="$ROOT/tools/backup_offsite.sh"
[ -f "$TOOL" ] || { log "找不到 $TOOL"; exit 1; }

mkdir -p "$BK_DIR" || { log "无法创建备份目录 $BK_DIR"; exit 1; }

log "root=$ROOT run=$RUN_ID dir=$BK_DIR interval=${INTERVAL}s retries=$RETRIES python=$PY"

trap 'log "收到停止信号，退出"; exit 0' TERM INT

# write_status <ok> <exit_code> <stage> <error> <duration_ms> [extra_json]
# 原子写 last_backup_status.json；运维脚本与 /api/metrics 读取同一个文件。
write_status() {
  STATUS_OK="$1" STATUS_CODE="$2" STATUS_STAGE="$3" STATUS_ERR="$4" \
  STATUS_MS="$5" STATUS_EXTRA="${6:-{\}}" INS="$BK_DIR/status.$$.tmp" \
  "$PY" - "$STATUS_FILE" <<'PY'
import json, os, socket, sys, time, pathlib
path = pathlib.Path(sys.argv[1])
now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
extra = json.loads(os.environ.get("STATUS_EXTRA") or "{}")
payload = {
    "generated_at": now,
    "generated_at_unix": int(time.time()),
    "host": socket.gethostname(),
    "ok": os.environ["STATUS_OK"] == "1",
    "exit_code": int(os.environ["STATUS_CODE"]),
    "stage": os.environ["STATUS_STAGE"],
    "error": os.environ["STATUS_ERR"] or None,
    "duration_ms": int(os.environ["STATUS_MS"]),
    "interval_seconds": int(os.environ.get("BK_INTERVAL", "21600")),
    **extra,
}
tmp = pathlib.Path(os.environ["INS"])
tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
os.replace(tmp, path)
PY
}

slept() {  # 可被 TERM/INT 打断的 sleep
  sleep "$1" &
  SLEEP_PID=$!
  wait "$SLEEP_PID"
}

# 启动竞态：world 容器可能还在建 events.sqlite，先等源库出现（默认最多 300s）。
# 这不是"吞错"：等不到仍然退出 1，只是避免把正常启动竞态记成备份失败。
SOURCE="$ROOT/runs/$RUN_ID/events.sqlite"
WAIT="${BK_STARTUP_WAIT:-300}"
waited=0
while [ ! -f "$SOURCE" ] && [ "$waited" -lt "$WAIT" ]; do
  if [ "$waited" -eq 0 ]; then log "等待源库就绪: $SOURCE（最多 ${WAIT}s）"; fi
  slept 5
  waited=$((waited + 5))
done
if [ ! -f "$SOURCE" ]; then
  err="源库在启动等待窗口内未出现: $SOURCE"
  log "$err"
  write_status 0 1 backup "$err" 0 '{}' || log "写状态文件失败: $STATUS_FILE"
  exit 1
fi

while :; do
  START_MS=$("$PY" -c 'import time;print(int(time.time()*1000))')
  attempt=0
  rc=1
  err=""
  while :; do
    attempt=$((attempt + 1))
    OUT="$BK_DIR/run.$$.json"
    "$PY" "$TOOL" --root "$ROOT" --run "$RUN_ID" --backup-dir "$BK_DIR" >"$OUT" 2>"$BK_DIR/run.$$.err"
    rc=$?
    if [ "$rc" -eq 0 ]; then
      log "备份成功（第 ${attempt} 次，$(wc -c <"$OUT" | tr -d ' ') 字节结果）"
      break
    fi
    err="$(tail -3 "$BK_DIR/run.$$.err" | tr '\n' ' ')"
    log "备份失败 exit=$rc 第 ${attempt}/${RETRIES} 次：$err"
    if [ "$attempt" -gt "$RETRIES" ]; then break; fi
    slept "$RETRY_DELAY"
  done

  END_MS=$("$PY" -c 'import time;print(int(time.time()*1000))')
  DUR_MS=$((END_MS - START_MS))

  if [ "$rc" -ne 0 ]; then
    write_status 0 "$rc" backup "$err" "$DUR_MS" '{}' || log "写状态文件失败: $STATUS_FILE"
    log "备份连续失败 ${attempt} 次，退出码 $rc，容器将进入 Exited(非零) 状态，等待人工介入"
    rm -f "$BK_DIR/run.$$.json" "$BK_DIR/run.$$.err"
    exit 1
  fi

  # 异地推送：未配置时 backup_offsite.sh 明确输出 offsite=disabled 并在状态文件里可见。
  OFFSITE_JSON='null'
  if [ -f "$OFFSITE" ]; then
    OS_JSON="$BK_DIR/offsite.$$.json"
    sh "$OFFSITE" --backup-dir "$BK_DIR" >"$OS_JSON" 2>"$BK_DIR/offsite.$$.err"
    OSRC=$?
    OFFSITE_JSON="$(cat "$OS_JSON" 2>/dev/null || echo '{"ok":false}')"
    rm -f "$OS_JSON"
    if [ "$OSRC" -ne 0 ]; then
      err="$(tail -3 "$BK_DIR/offsite.$$.err" | tr '\n' ' ')"
      log "异地推送失败 exit=$OSRC：$err"
      EXTRA=$(OFFSITE_JSON="$OFFSITE_JSON" "$PY" -c 'import json,os;print(json.dumps({"offsite": json.loads(os.environ["OFFSITE_JSON"])}))')
      write_status 0 "$OSRC" offsite "$err" "$DUR_MS" "$EXTRA" || log "写状态文件失败"
      rm -f "$OUT" "$BK_DIR/run.$$.err" "$BK_DIR/offsite.$$.err"
      log "异地推送失败，退出码 $OSRC，容器将进入 Exited(非零) 状态"
      exit 1
    fi
    rm -f "$BK_DIR/offsite.$$.err"
  fi

  # 成功状态：把 backup_snapshot 的摘要（backup_dir / events seq / sha256）与 offsite 一并落盘。
  export OFFSITE_JSON
  EXTRA="$("$PY" -c '
import json, os, sys
d = json.load(open(sys.argv[1]))
ev = d.get("events", {})
out = {
    "backup_dir": d.get("backup_dir"),
    "created_at": d.get("created_at"),
    "events": {k: ev.get(k) for k in ("seq", "content_hash", "state_hash", "event_hash")},
    "files": {
        name: {"sha256": meta.get("sha256"), "size_bytes": meta.get("size_bytes")}
        for name, meta in (d.get("files") or {}).items()
    },
    "offsite": json.loads(os.environ["OFFSITE_JSON"]),
}
print(json.dumps(out))
' "$OUT")"
  write_status 1 0 ok "" "$DUR_MS" "$EXTRA" || log "写状态文件失败: $STATUS_FILE"
  log "本轮完成：$(head -c 400 "$STATUS_FILE" 2>/dev/null)"
  rm -f "$OUT" "$BK_DIR/run.$$.err"

  slept "$INTERVAL"
done
