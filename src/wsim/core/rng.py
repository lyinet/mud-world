"""确定性 PRNG。

禁止全局 random。所有随机数来自 PRNG(master_seed, seq, salt)：
- seq 是事件序号，保证同一事件在不同进程/机器上拿到同一随机流；
- salt 用于同一 seq 下区分不同用途（action / planner / decision）。
"""
from __future__ import annotations

import hashlib

_MASK = (1 << 64) - 1


class PRNG:
    def __init__(self, seed: int, seq: int, salt: str = ""):
        raw = hashlib.sha256(f"{seed}|{seq}|{salt}".encode("utf-8")).digest()
        self._state = int.from_bytes(raw[:8], "big")
        self.seed = seed
        self.seq = seq
        self.salt = salt

    def _next_u64(self) -> int:
        # SplitMix64
        self._state = (self._state + 0x9E3779B97F4A7C15) & _MASK
        z = self._state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK
        return (z ^ (z >> 31)) & _MASK

    def random(self) -> float:
        return self._next_u64() / float(1 << 64)

    def randint(self, a: int, b: int) -> int:
        if b < a:
            raise ValueError("b < a")
        return a + int(self.random() * (b - a + 1))

    def choice(self, seq):
        if not seq:
            raise ValueError("empty sequence")
        return seq[self.randint(0, len(seq) - 1)]

    def weighted_choice(self, items, weights):
        total = sum(weights)
        if total <= 0:
            raise ValueError("weights must be positive")
        r = self.random() * total
        acc = 0.0
        for item, w in zip(items, weights):
            acc += w
            if r < acc:
                return item
        return items[-1]

    def chance(self, p: float) -> bool:
        return self.random() < p
