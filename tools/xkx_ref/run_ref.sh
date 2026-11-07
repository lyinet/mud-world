#!/usr/bin/env bash
# 前台启动参照驱动；输出直接进终端，便于录屏/抓取。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRIVER="$ROOT/src/fluffos-v2019/build/src/driver"
MUDLIB="$ROOT/run/mudlib"

[[ -x "$DRIVER" ]] || { echo "驱动未编译：先运行 $ROOT/build_fluffos_v2019.sh" >&2; exit 1; }
[[ -f "$MUDLIB/config.ini" ]] || { echo "mudlib 未准备：先运行 $ROOT/prepare_mudlib.sh" >&2; exit 1; }

cd "$MUDLIB"
exec "$DRIVER" config.ini
