"""K6：非法 Intent 比例。

两种口径同时输出，结论一律取更严格的“违规尝试”口径：

- `illegal_rate`（已执行动作口径）：进入事件日志的 Reject / 全部事件。
- `illegal_attempt_rate`（违规尝试口径，严格）：(Reject + 提案前拦截) / 全部事件。
  提案前拦截（`precondition_blocked`）虽然不落事件、无副作用，但属于一次违规尝试，
  计入分子；分母沿用事件日志规模，因此该口径不会比已执行动作口径更宽松。
"""
from __future__ import annotations

from collections import Counter

from ..core.models import Event


def illegal_stats(events: list[Event], precondition_blocked: int = 0) -> dict:
    total = len(events)
    rejected = [e for e in events if e.outcome == "rejected"]
    blocked = max(0, int(precondition_blocked))
    by_reason: Counter[str] = Counter()
    by_verb: Counter[str] = Counter()
    for e in rejected:
        by_reason[e.reason] += 1
        by_verb[e.verb] += 1
    rate = len(rejected) / total if total else 0.0
    attempt_rate = (len(rejected) + blocked) / total if total else 0.0
    return {
        "events_total": total,
        "rejected": len(rejected),
        "precondition_blocked": blocked,
        "illegal_rate": rate,
        "illegal_attempt_rate": attempt_rate,
        "by_reason": dict(sorted(by_reason.items(), key=lambda x: (-x[1], x[0]))),
        "by_verb": dict(sorted(by_verb.items(), key=lambda x: (-x[1], x[0]))),
    }
