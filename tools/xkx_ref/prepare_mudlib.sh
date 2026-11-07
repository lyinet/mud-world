#!/usr/bin/env bash
# 从固定 commit 生成可直接启动的 xkx100 mudlib 工作副本（全部落在被 gitignore 的 run/ 下）。
#
# 关键处理：
#   1) fluffos/xkx100 源码用 .lpc 后缀，但 FluffOS v2019 的 simulate.cc 在加载
#      simul_efun/master 时硬编码拼 ".c"，会报 "simul_efun file ... was not loaded"。
#      因此把 .lpc 全部改名为 .c，并把 inherit/include 里显式的 ".lpc" 字面量改成 ".c"。
#   2) 原 config.ini 开 5555/6666/8888（telnet+websocket），这里只保留 telnet 4004，
#      避免和本机其他服务冲突，也避开 v2019 的 websocket 链路。
#   3) 编译期 combatd 会写 log/nosave/KILLRECORD；该目录不存在会刷屏运行时错误，
#      预建 log/nosave。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$ROOT/src"
XKX_COMMIT="${XKX_COMMIT:-c1b87db0014c85514e053b2a149a2efccb9914e3}"
XKX_DIR="$SRC/xkx100"
RUN_DIR="$ROOT/run/mudlib"

mkdir -p "$SRC"

if [[ ! -d "$XKX_DIR/.git" ]]; then
  echo "[1/4] 克隆 fluffos/xkx100 @ $XKX_COMMIT"
  git clone https://github.com/fluffos/xkx100.git "$XKX_DIR"
fi

cd "$XKX_DIR"
if [[ "$(git rev-parse HEAD)" != "$XKX_COMMIT" ]]; then
  echo "[2/4] 切到 $XKX_COMMIT"
  git fetch --depth 1 origin "$XKX_COMMIT"
  git checkout --detach FETCH_HEAD
fi

echo "[3/4] 生成 run/mudlib"
rm -rf "$RUN_DIR"
mkdir -p "$RUN_DIR"
rsync -a "$XKX_DIR/" "$RUN_DIR/"

find "$RUN_DIR" -name '*.lpc' -type f -print0 |
  while IFS= read -r -d '' f; do mv "$f" "${f%.lpc}.c"; done
grep -rl '\.lpc"' --include='*.c' --include='*.h' "$RUN_DIR" 2>/dev/null |
  while IFS= read -r f; do sed -i '' 's/\.lpc"/.c"/g' "$f"; done

python3 - "$RUN_DIR/config.ini" <<'PY'
import sys
p = sys.argv[1]
lines = open(p).read().splitlines()
out, placed = [], False
for line in lines:
    if line.startswith("external_port_") or line.startswith("# external_port_"):
        if not placed:
            out.append("external_port_1 : telnet 4004")
            placed = True
        continue
    out.append(line)
open(p, "w").write("\n".join(out) + "\n")
PY

mkdir -p "$RUN_DIR/log/nosave"

echo "[4/4] 完成"
echo "mudlib: $RUN_DIR"
echo "启动:  $ROOT/run_ref.sh"
echo "登录:  telnet 127.0.0.1 4004"
