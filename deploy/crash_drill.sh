#!/usr/bin/env bash
# W5 崩溃恢复演练：20 并发压测中途 kill -9 服务端，重启后校验
#   1) sqlite 完整性 + seq 连续 + 钱包守恒（无半写）
#   2) 无重复创世
#   3) 旧令牌仍有效（service.sqlite 持久化）
#   4) 内容 hash 校验仍生效（篡改副本启动必须退出码 3）
#
# 用法：deploy/crash_drill.sh
# 产物：reports/w5/crash-drill/（令牌摘要、前后事件计数、不变量 JSON、日志）
# 安全：令牌只存在于进程内存/局部变量，不落盘；如需核对只写 sha256 与末 4 位。
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
PY=${PYTHON:-"$ROOT/.venv/bin/python"}
RUN=${RUN:-w5-crash-drill}
PORT=${PORT:-8099}
BASE="http://127.0.0.1:${PORT}"
OUT=${OUT:-"$ROOT/reports/w5/crash-drill"}
LOAD_DURATION=${LOAD_DURATION:-60}
KILL_AFTER=${KILL_AFTER:-20}

mkdir -p "$OUT"
rm -rf "$ROOT/runs/$RUN" "$ROOT/runs/$RUN-tampered"
rm -f "$OUT"/*.json "$OUT"/*.log "$OUT"/token.txt "$OUT"/token-digest.txt

start_server() {
  local tag=$1 run=${2:-$RUN} port=${3:-$PORT}
  PYTHONPATH="$ROOT/src" "$PY" -m wsim.api --root "$ROOT" --run "$run" \
    --host 127.0.0.1 --port "$port" --auth on --auth-iterations 1000 \
    >"$OUT/server-$tag.log" 2>&1 &
  echo $!
}

wait_ready() {
  local i
  for i in $(seq 1 60); do
    if curl -sf -m 2 "$BASE/api/service/info" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.3
  done
  echo "FAIL 服务未就绪" >&2
  return 1
}

counts() {
  sqlite3 "$ROOT/runs/$RUN/events.sqlite" \
    "SELECT COUNT(*), SUM(CASE WHEN verb='genesis' AND outcome='ok' THEN 1 ELSE 0 END), COALESCE(MAX(seq),0) FROM events;"
}

PID=$(start_server pre)
cleanup() { if [ "${PID:-0}" -gt 0 ]; then kill -9 "$PID" 2>/dev/null || true; fi; }
trap cleanup EXIT
wait_ready

TOKEN=$(PORT="$PORT" "$PY" - <<'PY'
import json, os, urllib.request
base = f"http://127.0.0.1:{os.environ.get('PORT', '8099')}"
def post(path, body, token=""):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type":"application/json", "Authorization":f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())
post("/api/auth/register", {"username":"crashdrill01","password":"drill-secret"})
tok = post("/api/auth/login", {"username":"crashdrill01","password":"drill-secret"})["token"]
post("/api/player/enter", {"display_name":"崩演练01","archetype":"游侠"}, tok)
print(tok)
PY
)
# 不落盘完整令牌：只记录 sha256 与末 4 位，便于核对同一令牌而不泄漏凭证。
printf 'sha256=%s\nlast4=%s\n' \
  "$(printf '%s' "$TOKEN" | shasum -a 256 | cut -d' ' -f1)" \
  "${TOKEN: -4}" > "$OUT/token-digest.txt"

BEFORE=$(counts)
echo "kill 前事件统计(count,genesis,max_seq): $BEFORE" | tee "$OUT/counts.txt"

"$PY" "$ROOT/tools/load_test.py" --players 20 --duration "$LOAD_DURATION" --think-time 2 \
  --ramp-up 3 --base-url "$BASE" --run-dir "$ROOT/runs/$RUN" \
  --report "$OUT/load-before-kill.json" >"$OUT/load.log" 2>&1 &
LOAD=$!

sleep "$KILL_AFTER"
echo "kill -9 PID=${PID}（压测进行中）" | tee -a "$OUT/counts.txt"
kill -9 "$PID" || true
wait "$PID" 2>/dev/null || true
PID=0
AT_KILL=$(counts)
echo "kill 瞬间事件统计: $AT_KILL" | tee -a "$OUT/counts.txt"

# 立刻重启，让压测在恢复后的服务上继续跑完，验证“断线→重启→继续”闭环。
PID=$(start_server post)
wait_ready
wait "$LOAD" || true   # 压测会记录停机期间的连接错误，并在重启后继续成功写入

AFTER_KILL=$(counts)
echo "压测结束（含重启后写入）事件统计: $AFTER_KILL" | tee -a "$OUT/counts.txt"

ME=$(PORT="$PORT" "$PY" - "$TOKEN" <<'PY'
import json, os, sys, urllib.request
token = sys.argv[1]
req = urllib.request.Request(f"http://127.0.0.1:{os.environ.get('PORT', '8099')}/api/auth/me",
                             headers={"Authorization": f"Bearer {token}"})
try:
    with urllib.request.urlopen(req, timeout=5) as r:
        payload = json.loads(r.read().decode())
        print(r.status, "ok=" + str(payload.get("ok")), "expires_at=" + str(payload.get("expires_at")))
except Exception as exc:
    print("ERROR", type(exc).__name__)
PY
)
echo "重启后 /api/auth/me: $ME" | tee "$OUT/token-check.txt"

"$PY" "$ROOT/tools/load_test.py" --verify-only --run-dir "$ROOT/runs/$RUN" \
  --report "$OUT/invariants.json" > "$OUT/invariants.log"
RESTART_COUNTS=$(counts)
echo "重启后事件统计: $RESTART_COUNTS" | tee -a "$OUT/counts.txt"

# 内容 hash 校验：停服后复制 run，篡改 meta，再启动必须退出码 3
kill -TERM "$PID" 2>/dev/null || true
wait "$PID" 2>/dev/null || true
PID=0
cp -R "$ROOT/runs/$RUN" "$ROOT/runs/$RUN-tampered"
sqlite3 "$ROOT/runs/$RUN-tampered/events.sqlite" \
  "UPDATE meta SET value='deadbeef' WHERE key='content_hash';"
set +e
PYTHONPATH="$ROOT/src" "$PY" -m wsim.api --root "$ROOT" --run "$RUN-tampered" \
  --host 127.0.0.1 --port "$PORT" --auth on --auth-iterations 1000 \
  >"$OUT/server-tampered.log" 2>&1
TAMPER_CODE=$?
set -e
echo "内容 hash 篡改后退出码: $TAMPER_CODE" | tee "$OUT/tamper-check.txt"

# 汇总
"$PY" - "$OUT" "$BEFORE" "$AFTER_KILL" "$RESTART_COUNTS" "$TAMPER_CODE" "$ME" <<'PY'
import json, sys, re
from pathlib import Path
out = Path(sys.argv[1])
before = [int(x) for x in sys.argv[2].split("|")]
after_kill = [int(x) for x in sys.argv[3].split("|")]
restart = [int(x) for x in sys.argv[4].split("|")]
tamper = int(sys.argv[5])
me = sys.argv[6]
inv = json.loads((out / "invariants.json").read_text())
load = json.loads((out / "load-before-kill.json").read_text())
checks = {
    "no_duplicate_genesis": restart[1] == 1,
    "event_count_monotonic": restart[0] >= before[0] and after_kill[0] >= before[0],
    "seq_contiguous": inv["seq"]["contiguous"] and restart[2] == restart[0],
    "sqlite_integrity_ok": inv["integrity_check"] == "ok",
    "wallet_conserved": inv["wallet"]["delta"] == 0,
    "no_half_write": not inv["half_write_rows"],
    "old_token_still_valid": me.startswith("200 "),
    "content_hash_enforced_exit_3": tamper == 3,
}
summary = {
    "counts": {"before": before, "after_kill": after_kill, "after_restart": restart},
    "load_errors_during_outage": load["totals"]["errors"],
    "tamper_exit_code": tamper,
    "checks": checks,
    "ok": all(checks.values()),
}
(out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
print("=== 崩溃恢复演练 ===")
for k, v in checks.items():
    print(f"{'PASS' if v else 'FAIL'}  {k}")
print("总体:", "PASS" if summary["ok"] else "FAIL")
PY

# 演练结束即吊销测试会话：令牌即时失效，且脚本全程未把令牌原文写入磁盘。
for d in "$RUN" "$RUN-tampered"; do
  if [ -f "$ROOT/runs/$d/service.sqlite" ]; then
    sqlite3 "$ROOT/runs/$d/service.sqlite" "DELETE FROM sessions;" || true
  fi
done
echo "演练会话已吊销（DELETE FROM sessions），令牌即时失效。"
