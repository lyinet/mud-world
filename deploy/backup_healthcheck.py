#!/usr/bin/env python3
"""backup 容器的 HEALTHCHECK：读 backups/last_backup_status.json 判断备份链路是否健康。

判 unhealthy 的三种情况（任一即退出码 1）：
  1. 状态文件不存在（一轮都没跑成功）；
  2. 最新一轮 ok=false（备份或异地推送失败，含失败阶段与退出码）；
  3. 状态文件过旧：距今 > BK_INTERVAL * BK_STALE_FACTOR + 600s（进程卡死/未调度）。

环境变量：
  BK_STATUS_FILE      默认 <BK_ROOT>/backups/last_backup_status.json
  BK_ROOT             默认 /app
  BK_INTERVAL         默认 21600
  BK_STALE_FACTOR     默认 2
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("BK_ROOT") or "/app")
STATUS_FILE = Path(os.environ.get("BK_STATUS_FILE") or (ROOT / "backups" / "last_backup_status.json"))
INTERVAL = int(os.environ.get("BK_INTERVAL") or 21600)
FACTOR = float(os.environ.get("BK_STALE_FACTOR") or 2)


def main() -> int:
    if not STATUS_FILE.is_file():
        print(f"unhealthy: 缺少状态文件 {STATUS_FILE}")
        return 1
    try:
        status = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except Exception as exc:  # 状态文件写坏同样视为不健康
        print(f"unhealthy: 状态文件无法解析 {type(exc).__name__}: {exc}")
        return 1
    if not status.get("ok"):
        print(
            "unhealthy: 最近一轮失败 stage={stage} exit={code} error={err}".format(
                stage=status.get("stage"), code=status.get("exit_code"), err=status.get("error")
            )
        )
        return 1
    generated = int(status.get("generated_at_unix") or 0)
    if not generated:
        print("unhealthy: 状态文件缺少 generated_at_unix")
        return 1
    age = time.time() - generated
    limit = INTERVAL * FACTOR + 600
    if age > limit:
        print(f"unhealthy: 备份状态已过期 {int(age)}s > {int(limit)}s")
        return 1
    print(f"healthy: last_ok age={int(age)}s backup_dir={status.get('backup_dir')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
