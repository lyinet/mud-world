"""管理面的唯一墙钟入口（审计/遥测专用）。

- 这里的值只用于 `admin_settings.updated_at`、`admin_secrets.created_at/last_used_at`、
  审计时间、能力位 `last_check_at` 等观测字段。
- **禁止**进入世界状态、事件序列、`content_hash` 或任何影响仿真结果的计算。
- 影响仿真的参数只在 tick 边界从持久化配置读取。
- 其他 admin 模块不得直接取墙钟（无论 stdlib 的哪种写法）；统一走 `now_utc()`。
"""
from __future__ import annotations

import time

# wallclock: audit-only
def now_utc() -> float:
    """当前 UTC epoch 秒（审计/遥测专用）。"""
    return float(time.clock_gettime(time.CLOCK_REALTIME))
