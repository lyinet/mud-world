#!/usr/bin/env python3
"""管理服务健康检查（compose healthcheck 使用）。

判 unhealthy 的条件：
  1. /api/admin/health 不可达（进程挂死、端口未监听、容器网络异常）；
  2. 返回非 200 或 `ok=false`；
  3. 设置了 XKX_ADMIN_REQUIRE_SECRET=1 且 `master_key=false`
     （XKX_SECRET_KEY 缺失会导致库里的 provider key 解不开并静默回退 env）。

用法：
    python /app/deploy/admin_healthcheck.py
环境变量：
    XKX_ADMIN_HEALTH_URL     默认 http://127.0.0.1:8031/api/admin/health
    XKX_ADMIN_REQUIRE_SECRET 默认 0；生产建议 1
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

URL = os.environ.get("XKX_ADMIN_HEALTH_URL", "http://127.0.0.1:8031/api/admin/health")
REQUIRE_SECRET = os.environ.get("XKX_ADMIN_REQUIRE_SECRET", "0") == "1"


def main() -> int:
    try:
        with urllib.request.urlopen(URL, timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        print(f"unhealthy: 管理接口不可达 {type(exc).__name__}: {exc}")
        return 1

    if not payload.get("ok"):
        print(f"unhealthy: health ok=false payload={json.dumps(payload, ensure_ascii=False)}")
        return 1

    if REQUIRE_SECRET and not payload.get("master_key"):
        print("unhealthy: master_key=false，XKX_SECRET_KEY 缺失，provider key 无法解密")
        return 1

    print(
        "healthy: admin v{ver} settings_version={sv} providers={p} master_key={mk}".format(
            ver=payload.get("admin_version"),
            sv=payload.get("settings_version"),
            p=payload.get("providers"),
            mk=payload.get("master_key"),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
