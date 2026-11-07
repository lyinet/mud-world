#!/usr/bin/env bash
# 编译 FluffOS v2019.20220507（arm64 macOS 原生）。
#
# 为什么不是官方推荐的一键构建：
#   1) v2019 的磁盘内 libwebsockets 在 OpenSSL 3 下把 HMAC_CTX 当成完整结构体，
#      编译失败（field has incomplete type 'HMAC_CTX'）；因此固定使用源码构建的
#      OpenSSL 1.1.1w，并显式补齐两个 feature 宏。
#   2) 该版本 libwebsockets 用 -lssl/-lcrypto 而非绝对路径链接，macOS 未把
#      Homebrew 的 openssl 链入默认搜索路径，需要额外 link_directories。
# 依赖（Homebrew）：bison icu4c@76 pcre jemalloc
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$ROOT/src"
DEPS="$ROOT/deps"
LOGS="$ROOT/logs"
mkdir -p "$SRC" "$DEPS" "$LOGS"

for tool in cmake git curl bison; do
  command -v "$tool" >/dev/null 2>&1 || { echo "缺少 $tool，请先安装" >&2; exit 1; }
done
for pkg in /opt/homebrew/opt/icu4c@76 /opt/homebrew/opt/pcre /opt/homebrew/opt/jemalloc; do
  [[ -d "$pkg" ]] || { echo "缺少 Homebrew 依赖：$pkg（brew install icu4c@76 pcre jemalloc）" >&2; exit 1; }
done

ARCH="$(uname -m)"
if [[ "$ARCH" == "arm64" ]]; then
  OSSL_TARGET="darwin64-arm64-cc"
else
  OSSL_TARGET="darwin64-x86_64-cc"
fi

# 1) OpenSSL 1.1.1w（静态库，仅本目录使用，不污染系统）
if [[ ! -f "$DEPS/openssl-1.1.1w/lib/libssl.a" ]]; then
  echo "[1/3] 构建 OpenSSL 1.1.1w ($OSSL_TARGET)"
  cd "$SRC"
  curl -sSL -o openssl-1.1.1w.tar.gz \
    https://github.com/openssl/openssl/releases/download/OpenSSL_1_1_1w/openssl-1.1.1w.tar.gz
  tar xzf openssl-1.1.1w.tar.gz
  cd openssl-1.1.1w
  ./Configure "$OSSL_TARGET" --prefix="$DEPS/openssl-1.1.1w" no-shared no-tests \
    > "$LOGS/openssl-configure.log" 2>&1
  make -j"$(sysctl -n hw.ncpu)" > "$LOGS/openssl-build.log" 2>&1
  make install_sw > "$LOGS/openssl-install.log" 2>&1
else
  echo "[1/3] OpenSSL 1.1.1w 已存在，跳过"
fi

# 2) FluffOS v2019.20220507
if [[ ! -d "$SRC/fluffos-v2019/.git" ]]; then
  echo "[2/3] 克隆 FluffOS v2019.20220507"
  git clone --depth 1 --branch v2019.20220507 \
    https://github.com/fluffos/fluffos.git "$SRC/fluffos-v2019"
fi
cd "$SRC/fluffos-v2019"

python3 - <<'PY'
# 给 v2019 的 libwebsockets 补 feature 宏与 openssl 链接目录（幂等）。
p = "src/CMakeLists.txt"
s = open(p).read()
marker = 'set(LWS_WITH_SSL ON CACHE BOOL "" FORCE)'
if "LWS_HAVE_HMAC_CTX_new" not in s:
    s = s.replace(
        marker,
        'add_definitions(-DLWS_HAVE_HMAC_CTX_new=1 -DLWS_HAVE_EVP_MD_CTX_free=1)\n'
        'link_directories(${OPENSSL_ROOT_DIR}/lib)\n' + marker,
    )
    open(p, "w").write(s)
    print("[patch] src/CMakeLists.txt: LWS OpenSSL 兼容补丁已应用")
else:
    print("[patch] src/CMakeLists.txt 已有补丁，跳过")
PY

# 3) 配置 + 构建
echo "[3/3] 配置并构建（约 2~5 分钟）"
cmake -S . -B build \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_PREFIX_PATH="$DEPS/openssl-1.1.1w;/opt/homebrew/opt/icu4c@76;/opt/homebrew/opt/pcre;/opt/homebrew/opt/jemalloc" \
  -DOPENSSL_ROOT_DIR="$DEPS/openssl-1.1.1w" \
  -DBISON_EXECUTABLE=/opt/homebrew/opt/bison/bin/bison \
  > "$LOGS/cmake-configure.log" 2>&1
cmake --build build -j"$(sysctl -n hw.ncpu)" > "$LOGS/build.log" 2>&1

./build/src/driver --version
echo "drv: $SRC/fluffos-v2019/build/src/driver"
