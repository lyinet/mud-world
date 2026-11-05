#!/usr/bin/env bash
# 构建可回滚的 world-sim 镜像：同一镜像打 <version> 与 latest 两个 tag。
#
# 用法:
#   tools/build_image.sh                 # version 默认取 git describe --tags --always
#   tools/build_image.sh v1.2.3          # 显式指定版本
#   XKX_IMAGE_NAME=xkx-world-sim tools/build_image.sh
#
# 输出（供发布记录直接粘贴）:
#   version / revision / image_id / digest / /app/VERSION
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

IMAGE_NAME="${XKX_IMAGE_NAME:-xkx-world-sim}"
VERSION="${1:-${XKX_VERSION:-$(git describe --tags --always 2>/dev/null || echo dev)}}"
REVISION="${XKX_REVISION:-$(git rev-parse HEAD 2>/dev/null || echo unknown)}"

if [ -n "$(git status --porcelain --untracked-files=no 2>/dev/null || true)" ]; then
  echo "[build_image] 警告: 工作区有未提交改动；镜像内容与 revision=$REVISION 不完全对应" >&2
fi

echo "[build_image] image=$IMAGE_NAME version=$VERSION revision=$REVISION"

docker build \
  -f deploy/Dockerfile \
  --build-arg "XKX_VERSION=$VERSION" \
  --build-arg "XKX_REVISION=$REVISION" \
  -t "$IMAGE_NAME:$VERSION" \
  -t "$IMAGE_NAME:latest" \
  "$ROOT"

IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$IMAGE_NAME:$VERSION")"
# 本地 build 没有 push/pull，RepoDigests 通常为空；此时如实标注，不伪造 digest。
DIGEST="$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$IMAGE_NAME:$VERSION" | sed -n '1p')"
VERSION_FILE="$(docker run --rm --entrypoint cat "$IMAGE_NAME:$VERSION" /app/VERSION 2>/dev/null || echo '<读取失败>')"

echo "------------------------------------------------------------"
echo "version:      $VERSION"
echo "revision:     $REVISION"
echo "tags:         $IMAGE_NAME:$VERSION, $IMAGE_NAME:latest"
echo "image_id:     $IMAGE_ID"
if [ -n "$DIGEST" ]; then
  echo "digest:       $DIGEST"
else
  echo "digest:       <none>（本地构建未 push/pull；image_id 即 config digest）"
fi
echo "/app/VERSION: $VERSION_FILE"
echo "------------------------------------------------------------"
