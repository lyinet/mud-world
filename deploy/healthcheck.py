#!/usr/bin/env python3
"""容器健康检查：API /api/service/info 与前端 /api/health 都必须 200 且 ok。"""
import json
import os
import sys
import urllib.request

API_PORT = int(os.environ.get("API_PORT", "8020"))
UI_PORT = int(os.environ.get("UI_PORT", "8030"))


def probe(url: str) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            body = json.loads(resp.read().decode("utf-8") or "{}")
            return (resp.status == 200 and bool(body.get("ok", True))), f"{url} -> {resp.status} ok"
    except Exception as exc:
        return False, f"{url} -> {type(exc).__name__}: {exc}"


def main() -> int:
    checks = [
        probe(f"http://127.0.0.1:{API_PORT}/api/service/info"),
        probe(f"http://127.0.0.1:{UI_PORT}/api/health"),
    ]
    for ok, message in checks:
        print(("PASS " if ok else "FAIL ") + message, file=sys.stdout)
    return 0 if all(ok for ok, _ in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
