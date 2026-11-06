#!/usr/bin/env bash
# 生成反向代理用的自签证书（本地/内网演练与门 3 验收用）。
#
# 生产不要用自签：把运维签发的 fullchain/私钥按同样的文件名放到
# deploy/certs/（xkx.crt / xkx.key），或改 compose 的挂载路径。
#
# 用法：
#   deploy/gen_selfsigned_cert.sh                 # 默认 CN=localhost，SAN 含 localhost/127.0.0.1
#   deploy/gen_selfsigned_cert.sh xkx.example.com # 追加域名到 SAN
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/certs"
EXTRA="${1:-}"

command -v openssl >/dev/null 2>&1 || { echo "缺少 openssl" >&2; exit 1; }

mkdir -p "$OUT"
chmod 700 "$OUT"

SAN="DNS:localhost,IP:127.0.0.1,IP:::1"
if [ -n "$EXTRA" ]; then
  SAN="$SAN,DNS:$EXTRA"
fi

openssl req -x509 -newkey rsa:2048 -sha256 -days 825 -nodes \
  -keyout "$OUT/xkx.key" -out "$OUT/xkx.crt" \
  -subj "/CN=${EXTRA:-localhost}/O=XKX-DEV" \
  -addext "subjectAltName=${SAN}" \
  -addext "keyUsage=digitalSignature,keyEncipherment" \
  -addext "extendedKeyUsage=serverAuth" >/dev/null 2>&1

chmod 600 "$OUT/xkx.key"
chmod 644 "$OUT/xkx.crt"

echo "已生成:"
echo "  $OUT/xkx.crt"
echo "  $OUT/xkx.key"
openssl x509 -in "$OUT/xkx.crt" -noout -subject -ext subjectAltName -dates
