#!/usr/bin/env python3
"""等待 URL 可用（容器入口用，避免 API 未起就拉前端）。"""
import sys
import time
import urllib.request

url = sys.argv[1]
timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0
deadline = time.time() + timeout
while time.time() < deadline:
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            if resp.status == 200:
                sys.exit(0)
    except Exception:
        pass
    time.sleep(0.5)
print(f"wait_for: {url} 在 {timeout}s 内不可达", file=sys.stderr)
sys.exit(1)
