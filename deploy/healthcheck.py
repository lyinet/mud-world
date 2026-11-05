#!/usr/bin/env python3
"""容器健康检查（真探活）。

- API `/api/health`：HTTP 200 且 `ok` 为真、`storage.events_ok` 为真、
  `storage.disk_free_mb` 不低于阈值（`XKX_HEALTHCHECK_MIN_DISK_MB`，默认 50MB）。
- API `/api/service/info` 与前端 `/api/health`：基础可达性。
任一失败都以非零退出码结束。
"""
import json
import os
import sys
import urllib.request

API_PORT = int(os.environ.get("API_PORT", "8020"))
UI_PORT = int(os.environ.get("UI_PORT", "8030"))
MIN_DISK_MB = float(os.environ.get("XKX_HEALTHCHECK_MIN_DISK_MB", "50"))


def fetch(url: str) -> tuple[int, dict]:
    with urllib.request.urlopen(url, timeout=3) as resp:
        body = json.loads(resp.read().decode("utf-8") or "{}")
        return int(getattr(resp, "status", 200)), body


def check(name: str, fn) -> tuple[str, bool, str]:
    try:
        ok, message = fn()
    except Exception as exc:  # 探活失败不能抛栈，统一转成 FAIL 行
        ok, message = False, f"{type(exc).__name__}: {exc}"
    return name, ok, message


def api_health() -> tuple[bool, str]:
    status, body = fetch(f"http://127.0.0.1:{API_PORT}/api/health")
    if status != 200 or body.get("ok") is not True:
        return False, f"status={status} ok={body.get('ok')!r}"
    storage = body.get("storage") or {}
    if storage.get("events_ok") is not True:
        return False, f"storage.events_ok={storage.get('events_ok')!r}"
    try:
        free_mb = float(storage.get("disk_free_mb"))
    except (TypeError, ValueError):
        return False, f"storage.disk_free_mb={storage.get('disk_free_mb')!r}"
    if free_mb < MIN_DISK_MB:
        return False, f"storage.disk_free_mb={free_mb} < {MIN_DISK_MB}"
    return True, f"ok=true events_ok=true disk_free_mb={free_mb}"


def api_service_info() -> tuple[bool, str]:
    status, body = fetch(f"http://127.0.0.1:{API_PORT}/api/service/info")
    return status == 200 and bool(body.get("ok", True)), f"status={status}"


def ui_health() -> tuple[bool, str]:
    status, body = fetch(f"http://127.0.0.1:{UI_PORT}/api/health")
    return status == 200 and bool(body.get("ok", True)), f"status={status}"


def main() -> int:
    checks = [
        check("api /api/service/info", api_service_info),
        check("api /api/health", api_health),
        check("ui /api/health", ui_health),
    ]
    for name, ok, message in checks:
        print(("PASS " if ok else "FAIL ") + f"{name} -> {message}", file=sys.stdout)
    return 0 if all(ok for _, ok, _ in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
