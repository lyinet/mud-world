"""随机源接口。

战斗公式不自己造随机：所有随机数通过调用方传入的 `RandomSource.below(n)` 取得。
`below(n)` 必须等价于 LPC `random(n)`：返回 [0, n) 的整数；n <= 0 时返回 0。

回放依赖“取数顺序”，因此 `SequenceRandom` 会记录每一次调用（包括 n <= 0 的调用），
调用方即可用记录核对取数序列。
"""
from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable


@runtime_checkable
class RandomSource(Protocol):
    def below(self, n: int) -> int:
        """返回 [0, n) 的整数；n <= 0 时返回 0（等价 LPC `random(n)`）。"""
        ...


class SequenceRandom:
    """按预置数列取数，供测试与回放核对；不持有任何全局状态。"""

    def __init__(self, values: Sequence[int] = ()):
        self._values = list(values)
        self.calls: list[int] = []

    def below(self, n: int) -> int:
        self.calls.append(n)
        if n <= 0:
            return 0
        if not self._values:
            raise AssertionError(f"SequenceRandom 取数耗尽：请求 below({n})")
        value = self._values.pop(0)
        if value < 0 or value >= n:
            raise AssertionError(f"SequenceRandom 预置值 {value} 不在 [0, {n}) 内")
        return value

    @property
    def remaining(self) -> int:
        return len(self._values)
