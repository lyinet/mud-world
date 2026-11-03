"""虚拟时钟。没有任何墙钟调用。

1 tick = 10 游戏分钟；1 游戏日 = 144 ticks；1 小时 = 6 ticks。
"""
from __future__ import annotations

TICKS_PER_HOUR = 6
TICKS_PER_DAY = 144


def day_of(tick: int) -> int:
    """0-based 游戏日。"""
    return tick // TICKS_PER_DAY


def hour_of(tick: int) -> int:
    return (tick % TICKS_PER_DAY) // TICKS_PER_HOUR


def tick_for(day: int, hour: int, minute: int = 0) -> int:
    if not (0 <= hour < 24):
        raise ValueError("hour out of range")
    return day * TICKS_PER_DAY + hour * TICKS_PER_HOUR + (minute // 10)


def stamp(tick: int) -> str:
    """日志用虚拟时间戳，如 D03 08:30。"""
    return f"D{day_of(tick) + 1:02d} {hour_of(tick):02d}:{(tick % TICKS_PER_HOUR) * 10:02d}"
