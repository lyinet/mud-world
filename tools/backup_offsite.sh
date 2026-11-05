#!/usr/bin/env bash
# 把最新一份备份推送到异机/异地，并在**远端**取 sha256/size 与 manifest 逐项比对。
#
# 三种模式（--mode 或 XKX_OFFSITE_MODE）：
#   local  目标是一个目录（本机另一块盘、NAS 挂载点，测试用 file:// 也可）
#   rsync  rsync over ssh；目标形如 user@host:/srv/xkx-backups，或配合 XKX_OFFSITE_SSH_HOST
#   rclone 任意 rclone remote（如 s3:xkx-backups）；远端 sha256 由 rclone hashsum 取
#   默认 disabled：不推送，但输出 offsite=disabled（不会假装成功）
#
# 环境变量：
#   XKX_OFFSITE_MODE      local|rsync|rclone|disabled（默认 disabled）
#   XKX_OFFSITE_TARGET    目标目录 / ssh 目标 / rclone remote
#   XKX_OFFSITE_REQUIRED  1 = 生产语义：未配置目标视为失败（退出码 3）
#   XKX_OFFSITE_SSH_HOST  rsync 模式的远端主机（目标只给路径时使用）
#   XKX_OFFSITE_SSH_OPTS  额外 ssh 参数（默认 "-o BatchMode=yes -o ConnectTimeout=10"）
#
# 参数：
#   --backup-dir DIR   本地备份根目录
#   --backup NAME      指定备份名（默认取最新一份）
#   --verify-only      不推送，只复核远端已有副本（运维定期核对 / 演练篡改检测）
#
# 退出码：0 成功（含 disabled 且非必需）；1 推送或远端校验失败；2 用法/配置错误；
#         3 生产语义下未配置异地目标。
#
# 输出：stdout 一行 JSON（ok/mode/target/backup/files[].sha256+remote_sha256/offsite），
#       日志写 stderr。
set -u

BACKUP_DIR=""
BACKUP_NAME=""
VERIFY_ONLY=0
MODE="${XKX_OFFSITE_MODE:-disabled}"
TARGET="${XKX_OFFSITE_TARGET:-}"
REQUIRED="${XKX_OFFSITE_REQUIRED:-0}"
SSH_HOST="${XKX_OFFSITE_SSH_HOST:-}"
SSH_OPTS="${XKX_OFFSITE_SSH_OPTS:--o BatchMode=yes -o ConnectTimeout=10}"

log() { printf '[offsite] %s\n' "$*" >&2; }

while [ $# -gt 0 ]; do
  case "$1" in
    --backup-dir) BACKUP_DIR="${2:?}"; shift 2 ;;
    --backup)     BACKUP_NAME="${2:?}"; shift 2 ;;
    --mode)       MODE="${2:?}"; shift 2 ;;
    --target)     TARGET="${2:?}"; shift 2 ;;
    --required)   REQUIRED=1; shift ;;
    --verify-only) VERIFY_ONLY=1; shift ;;
    -h|--help)    sed -n '2,30p' "$0"; exit 0 ;;
    *) log "未知参数: $1"; exit 2 ;;
  esac
done

PY="${BK_PYTHON:-}"
if [ -z "$PY" ]; then
  if command -v python3 >/dev/null 2>&1; then PY=python3; else PY=python; fi
fi

emit() { printf '%s\n' "$1"; }

# disabled：显式标记，不假装成功；生产语义（REQUIRED=1）下视为失败。
if [ "$MODE" = "disabled" ] || [ -z "$MODE" ]; then
  if [ "$VERIFY_ONLY" = "1" ]; then
    log "--verify-only 需要明确的异地目标（XKX_OFFSITE_MODE/TARGET），当前为 disabled"
    emit '{"ok": false, "stage": "config", "error": "verify_only_requires_target"}'
    exit 2
  fi
  if [ "$REQUIRED" = "1" ]; then
    log "XKX_OFFSITE_REQUIRED=1 但未配置异地目标（XKX_OFFSITE_MODE/TARGET）"
    emit '{"ok": false, "offsite": "disabled", "error": "offsite_required_but_disabled"}'
    exit 3
  fi
  log "未配置异地目标，跳过（offsite=disabled）"
  emit '{"ok": true, "offsite": "disabled", "mode": "disabled"}'
  exit 0
fi

case "$MODE" in
  local|rsync|rclone) ;;
  *) log "不支持的 mode: ${MODE}（可选 local|rsync|rclone|disabled）"; exit 2 ;;
esac
[ -n "$TARGET" ] || { log "mode=$MODE 但未设置 XKX_OFFSITE_TARGET"; exit 2; }
[ -n "$BACKUP_DIR" ] || { log "缺少 --backup-dir"; exit 2; }

# 选最新自生成备份（manifest.generator 必须是 backup_snapshot.py）
SEL="$("$PY" - "$BACKUP_DIR" "$BACKUP_NAME" <<'PY'
import json, pathlib, re, sys
root = pathlib.Path(sys.argv[1])
want = sys.argv[2]
pat = re.compile(r"^(\d{8}T\d{6}Z)(?:-\d+)?$")
if want:
    p = root / want
    if not p.is_dir():
        print(f"ERR not a backup dir: {p}", file=sys.stderr); raise SystemExit(2)
    print(p.name); raise SystemExit(0)
cands = []
for p in root.iterdir():
    if not p.is_dir() or not pat.match(p.name):
        continue
    mf = p / "manifest.json"
    if not mf.is_file():
        continue
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
    except Exception:
        continue
    if data.get("generator") != "tools/backup_snapshot.py":
        continue
    cands.append((p.name, data))
if not cands:
    print("ERR no backup produced by tools/backup_snapshot.py", file=sys.stderr); raise SystemExit(2)
name, data = max(cands, key=lambda x: x[0])
print(name)
PY
)" || exit 2
[ -n "$SEL" ] || exit 2
log "最新备份: $SEL"

SRC="$BACKUP_DIR/$SEL"
REMOTE_PATH=""

# 1) 推送（--verify-only 时只定位远端已有副本，不写入）
if [ "$VERIFY_ONLY" = "1" ]; then
  case "$MODE" in
    local)  REMOTE_PATH="${TARGET#file://}/$SEL" ;;
    rsync|rclone) REMOTE_PATH="$TARGET/$SEL" ;;
  esac
  log "仅校验模式：不推送，直接复核远端 $REMOTE_PATH"
else
case "$MODE" in
  local)
    T="${TARGET#file://}"
    mkdir -p "$T/$SEL" || { log "无法创建目标目录 $T/$SEL"; emit '{"ok": false, "stage": "push", "error": "target_mkdir_failed"}'; exit 1; }
    cp -a "$SRC/." "$T/$SEL/" || { log "复制失败 $SRC -> $T/$SEL"; emit '{"ok": false, "stage": "push", "error": "copy_failed"}'; exit 1; }
    REMOTE_PATH="$T/$SEL"
    ;;
  rsync)
    command -v rsync >/dev/null 2>&1 || { log "本机没有 rsync（安装 rsync 或改用 rclone 模式）"; emit '{"ok": false, "stage": "push", "error": "rsync_missing"}'; exit 1; }
    DEST="$TARGET"
    if [ -n "$SSH_HOST" ]; then DEST="$SSH_HOST:$TARGET"; fi
    rsync -az --timeout=60 -e "ssh $SSH_OPTS" "$SRC/." "$DEST/$SEL/" >&2 \
      || { log "rsync 推送失败: $SRC -> $DEST/$SEL"; emit '{"ok": false, "stage": "push", "error": "rsync_push_failed"}'; exit 1; }
    REMOTE_PATH="$TARGET/$SEL"
    ;;
  rclone)
    command -v rclone >/dev/null 2>&1 || { log "本机没有 rclone（安装 rclone 或改用 rsync 模式）"; emit '{"ok": false, "stage": "push", "error": "rclone_missing"}'; exit 1; }
    rclone sync "$SRC" "$TARGET/$SEL" >&2 \
      || { log "rclone 推送失败: $SRC -> $TARGET/$SEL"; emit '{"ok": false, "stage": "push", "error": "rclone_push_failed"}'; exit 1; }
    REMOTE_PATH="$TARGET/$SEL"
    ;;
esac
fi

# 2) 远端取 sha256/size，与 manifest 逐项比对（不接受"只校验本地文件"）
if ! "$PY" - "$SRC/manifest.json" "$MODE" "$REMOTE_PATH" "$SSH_HOST" "$SSH_OPTS" <<'PY'
import json, os, pathlib, re, subprocess, sys

manifest_path, mode, remote, ssh_host, ssh_opts = sys.argv[1:6]
manifest = json.loads(pathlib.Path(manifest_path).read_text(encoding="utf-8"))
files = manifest.get("databases") or {}
entries = [(n, m) for n, m in files.items() if m.get("present") and m.get("sha256")]
if not entries:
    print(json.dumps({"ok": False, "stage": "verify", "error": "manifest_has_no_files"}))
    raise SystemExit(1)

def remote_sha256(name):
    """返回 (sha256, size) 或抛异常。"""
    if mode == "local":
        # 本地目标直接用 python 计算，避免 BSD/GNU stat 差异（macOS 无 stat -c）。
        import hashlib
        p = pathlib.Path(remote) / name
        if not p.is_file():
            raise RuntimeError(f"remote file missing: {p}")
        h = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest(), p.stat().st_size
    if mode == "rsync":
        host = ssh_host or None
        if host is None:
            # 目标已形如 user@host:/path 时从 remote 里拆主机
            m = re.match(r"^([^:/]+):(.+)$", remote)
            if not m:
                raise RuntimeError("rsync 模式需要 XKX_OFFSITE_SSH_HOST 或 user@host:path 目标")
            host, path = m.group(1), m.group(2)
        else:
            path = remote
        # wc -c 是 POSIX，比 stat -c 更通用（远端不必是 GNU coreutils）。
        cmd = ["ssh", *ssh_opts.split(), host, f'sha256sum "{path}/{name}"; wc -c < "{path}/{name}"']
        out = subprocess.run(cmd, capture_output=True, text=True)
    else:  # rclone
        h = subprocess.run(["rclone", "hashsum", "sha256", f"{remote}/{name}"],
                           capture_output=True, text=True)
        s = subprocess.run(["rclone", "size", "--json", f"{remote}/{name}"],
                           capture_output=True, text=True)
        if h.returncode == 0 and s.returncode == 0:
            sha = h.stdout.strip().split()[0] if h.stdout.strip() else ""
            size = int(json.loads(s.stdout).get("bytes") or 0)
            return sha, size
        raise RuntimeError((h.stderr or s.stderr or "rclone 读取失败").strip()[:200])
    if out.returncode != 0:
        raise RuntimeError((out.stderr or out.stdout or "远端读取失败").strip()[:200])
    lines = [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
    sha = lines[0].split()[0]
    size = int(lines[1])
    return sha, size

result = {"ok": True, "mode": mode, "target": remote, "offsite": "enabled", "files": []}
bad = []
for name, meta in entries:
    expect = str(meta["sha256"]).lower()
    try:
        sha, size = remote_sha256(name)
        sha = sha.lower()
    except Exception as exc:
        sha, size = "", 0
        bad.append(f"{name}: remote_read_failed ({exc})")
    else:
        if sha != expect:
            bad.append(f"{name}: sha256 mismatch remote={sha[:12]} manifest={expect[:12]}")
        elif size != int(meta.get("size_bytes") or -1):
            bad.append(f"{name}: size mismatch remote={size} manifest={meta.get('size_bytes')}")
    result["files"].append({"name": name, "sha256": expect, "remote_sha256": sha,
                            "size_bytes": meta.get("size_bytes"), "remote_size_bytes": size,
                            "verified": sha == expect and size == int(meta.get("size_bytes") or -1)})
if bad:
    result["ok"] = False
    result["stage"] = "verify"
    result["error"] = "; ".join(bad)[:400]
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(1)
print(json.dumps(result, ensure_ascii=False))
PY
then
  log "远端校验失败"
  exit 1
fi
