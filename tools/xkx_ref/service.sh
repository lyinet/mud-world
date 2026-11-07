#!/usr/bin/env bash
# 用 macOS launchd 守护 4004 参照实例：开机/登录即起，进程退出 5 秒后自动拉起。
#
#   tools/xkx_ref/service.sh install     # 渲染 plist 到 ~/Library/LaunchAgents 并加载
#   tools/xkx_ref/service.sh restart      # 一键重启（kill 后由 KeepAlive 或 kickstart 拉起）
#   tools/xkx_ref/service.sh status       # launchd 状态 + 4004 监听 + 驱动 PID
#   tools/xkx_ref/service.sh logs         # 跟踪驱动 stdout/stderr
#   tools/xkx_ref/service.sh stop         # 停止并卸载本次加载（不删 plist）
#   tools/xkx_ref/service.sh uninstall    # 完全移除 LaunchAgent
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.xkx.ref"
DOMAIN="gui/$(id -u)"
PLIST_SRC="$ROOT/service/com.xkx.ref.plist.in"
PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"
DRIVER="$ROOT/src/fluffos-v2019/build/src/driver"
MUDLIB="$ROOT/run/mudlib"
LOGDIR="$ROOT/logs"
PORT=4004

require_runtime() {
  [[ -x "$DRIVER" ]] || { echo "驱动不存在：${DRIVER}（先跑 build_fluffos_v2019.sh）" >&2; exit 1; }
  [[ -f "$MUDLIB/config.ini" ]] || { echo "mudlib 不存在：${MUDLIB}（先跑 prepare_mudlib.sh）" >&2; exit 1; }
  mkdir -p "$LOGDIR"
}

render_plist() {
  mkdir -p "$(dirname "$PLIST_DST")"
  sed -e "s|__DRIVER__|$DRIVER|g" \
      -e "s|__MUDLIB__|$MUDLIB|g" \
      -e "s|__LOGDIR__|$LOGDIR|g" \
      "$PLIST_SRC" > "$PLIST_DST"
}

install_service() {
  require_runtime
  render_plist
  launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
  launchctl bootstrap "$DOMAIN" "$PLIST_DST"
  launchctl enable "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
  echo "已安装并启动 $LABEL"
  status_service
}

stop_service() {
  launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
  echo "已停止 ${LABEL}（plist 保留，install 可恢复）"
}

status_service() {
  if launchctl print "$DOMAIN/$LABEL" >/tmp/xkx_svc_state 2>/dev/null; then
    grep -E "state = |pid = |last exit" /tmp/xkx_svc_state | head -5 || true
  else
    echo "launchd: 未加载"
  fi
  echo "listen:"
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | tail -n +2 || echo "  (无)"
}

case "${1:-}" in
  install)   install_service ;;
  restart)   require_runtime; launchctl kickstart -k "$DOMAIN/$LABEL"; echo "已重启 $LABEL"; sleep 2; status_service ;;
  启动)      install_service ;;
  stop)      stop_service ;;
  停止)      stop_service ;;
  status)    status_service ;;
  状态)      status_service ;;
  logs)
    require_runtime
    tail -n 40 -f "$LOGDIR/driver.out.log" "$LOGDIR/driver.err.log"
    ;;
  uninstall)
    launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
    rm -f "$PLIST_DST"
    echo "已移除 $LABEL 与 plist"
    ;;
  *)
    echo "用法：$0 {install|restart|stop|status|logs|uninstall}" >&2
    exit 2
    ;;
esac
