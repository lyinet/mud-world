"""K6：非法 Intent 比例。"""
from __future__ import annotations

from collections import Counter

from ..core.models import Event


def illegal_stats(events: list[Event]) -> dict:
    total = len(events)
    rejected = [e for e in events if e.outcome == "rejected"]
    by_reason: Counter[str] = Counter()
    by_verb: Counter[str] = Counter()
    for e in rejected:
        by_reason[e.reason] += 1
        by_verb[e.verb] += 1
    rate = len(rejected) / total if total else 0.0
    return {
        "events_total": total,
        "rejected": len(rejected),
        "illegal_rate": rate,
        "by_reason": dict(sorted(by_reason.items(), key=lambda x: (-x[1], x[0]))),
        "by_verb": dict(sorted(by_verb.items(), key=lambda x: (-x[1], x[0]))),
    }
