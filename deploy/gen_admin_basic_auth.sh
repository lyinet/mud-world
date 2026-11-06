#!/usr/bin/env bash
# 生成反代层的管理面 HTTP Basic 凭据（admin.htpasswd）。
#
# 这是**第一层**访问控制；控制台自身还有 XKX_ADMIN_TOKEN 登录（第二层）。
# 不要把 .htpasswd 提交到仓库：deploy/certs/ 已在 .gitignore。
#
# 用法：
#   deploy/gen_admin_basic_auth.sh admin 'your-strong-password'
#   # 或从环境变量读（便于脚本化）：
#   XKX_ADMIN_BASIC_USER=admin XKX_ADMIN_BASIC_PASS=… deploy/gen_admin_basic_auth.sh
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/certs"
USER_NAME="${1:-${XKX_ADMIN_BASIC_USER:-}}"
PASS="${2:-${XKX_ADMIN_BASIC_PASS:-}}"

if [ -z "$USER_NAME" ] || [ -z "$PASS" ]; then
  echo "用法: $0 <user> <password>  或设置 XKX_ADMIN_BASIC_USER / XKX_ADMIN_BASIC_PASS" >&2
  exit 2
fi

# htpasswd 的 user 字段不能含冒号/空白
case "$USER_NAME" in
  *:*|*" "*|*"	"*|"") echo "用户名不能包含冒号、空白或为空" >&2; exit 2 ;;
esac

command -v openssl >/dev/null 2>&1 || { echo "缺少 openssl" >&2; exit 1; }

mkdir -p "$OUT"
chmod 700 "$OUT"

HASH="$(openssl passwd -apr1 "$PASS")"
umask 077
printf '%s:%s\n' "$USER_NAME" "$HASH" > "$OUT/admin.htpasswd"
chmod 600 "$OUT/admin.htpasswd"

echo "已写入 $OUT/admin.htpasswd（user=${USER_NAME}，算法=apr1）"
