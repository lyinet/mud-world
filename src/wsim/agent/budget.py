"""LLM 预算护栏：日/月 token 与金额上限，接近上限时降档，超过上限降级 Mock。

- 降档顺序：spotlight → active → background → silent → mock。
- 只做纯计算，不发事件；审计事件由 headless 在日终统一写一条 `system_budget`。
- 金额与 token 均按累计值判断；月度按整个 run 记账（P3-A 的单 run 即一个月度窗口）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

TIER_ORDER = ("silent", "background", "active", "spotlight")


def _demote(tier: str, steps: int) -> str | None:
    if tier not in TIER_ORDER:
        tier = "active"
    idx = TIER_ORDER.index(tier) - max(0, steps)
    if idx < 0:
        return None
    return TIER_ORDER[idx]


@dataclass(frozen=True)
class BudgetLimits:
    daily_tokens: int = 200_000
    monthly_tokens: int = 5_000_000
    daily_cost: float = 5.0
    monthly_cost: float = 50.0
    soft_ratio: float = 0.8
    dialogue_daily_calls: int = 50
    intent_daily_calls: int = 2000

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "BudgetLimits":
        raw = raw or {}
        return cls(
            daily_tokens=int(raw.get("daily_tokens", raw.get("decision_daily_tokens", cls.daily_tokens))),
            monthly_tokens=int(raw.get("monthly_tokens", cls.monthly_tokens)),
            daily_cost=float(raw.get("daily_cost", cls.daily_cost)),
            monthly_cost=float(raw.get("monthly_cost", cls.monthly_cost)),
            soft_ratio=float(raw.get("soft_ratio", cls.soft_ratio)),
            dialogue_daily_calls=int(raw.get("dialogue_daily_calls", cls.dialogue_daily_calls)),
            intent_daily_calls=int(raw.get("intent_daily_calls", cls.intent_daily_calls)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_daily_tokens": self.daily_tokens,
            "monthly_tokens": self.monthly_tokens,
            "daily_cost": self.daily_cost,
            "monthly_cost": self.monthly_cost,
            "soft_ratio": self.soft_ratio,
            "dialogue_daily_calls": self.dialogue_daily_calls,
            "intent_daily_calls": self.intent_daily_calls,
        }


@dataclass
class BudgetGuard:
    limits: BudgetLimits
    input_rate: float = 0.0
    output_rate: float = 0.0
    day_tokens: dict[int, int] = field(default_factory=dict)
    day_cost: dict[int, float] = field(default_factory=dict)
    month_tokens: int = 0
    month_cost: float = 0.0
    degraded: int = 0
    mocked: int = 0
    mocked_by_day: dict[int, int] = field(default_factory=dict)
    degraded_by_day: dict[int, int] = field(default_factory=dict)
    dialogue_calls: dict[int, int] = field(default_factory=dict)
    dialogue_mocked: int = 0
    dialogue_mocked_by_day: dict[int, int] = field(default_factory=dict)
    intent_calls: dict[int, int] = field(default_factory=dict)
    intent_mocked: int = 0
    intent_mocked_by_day: dict[int, int] = field(default_factory=dict)
    _audited_days: set[int] = field(default_factory=set)

    def estimate_cost(self, tokens_in: int, tokens_out: int) -> float:
        return tokens_in * self.input_rate + tokens_out * self.output_rate

    def check(
        self,
        day: int,
        tier: str,
        prompt_text: str,
        max_tokens: int,
    ) -> tuple[str | None, str]:
        """返回 (有效档位或 None=mock, 原因)。None 表示本次不得调用模型。"""
        tokens_in = max(1, len(prompt_text) // 4)
        tokens_out = max(1, int(max_tokens))
        cost = self.estimate_cost(tokens_in, tokens_out)
        daily_tokens = self.day_tokens.get(day, 0) + tokens_in + tokens_out
        daily_cost = self.day_cost.get(day, 0.0) + cost
        month_tokens = self.month_tokens + tokens_in + tokens_out
        month_cost = self.month_cost + cost
        ratio = max(
            daily_tokens / max(1, self.limits.daily_tokens),
            daily_cost / max(1e-9, self.limits.daily_cost),
            month_tokens / max(1, self.limits.monthly_tokens),
            month_cost / max(1e-9, self.limits.monthly_cost),
        )
        if ratio >= 1.0:
            self.mocked += 1
            self.mocked_by_day[day] = self.mocked_by_day.get(day, 0) + 1
            return None, "budget_exceeded"
        steps = 0
        if ratio >= (self.limits.soft_ratio + 1.0) / 2:
            steps = 2
        elif ratio >= self.limits.soft_ratio:
            steps = 1
        effective = tier
        if steps:
            effective = _demote(tier, steps) or "silent"
            self.degraded += 1
            self.degraded_by_day[day] = self.degraded_by_day.get(day, 0) + 1
        return effective, ""

    def record(self, day: int, tokens_in: int, tokens_out: int, cost: float) -> None:
        self.day_tokens[day] = self.day_tokens.get(day, 0) + int(tokens_in)
        self.day_cost[day] = self.day_cost.get(day, 0.0) + float(cost)
        self.month_tokens += int(tokens_in)
        self.month_cost += float(cost)

    def check_dialogue(self, day: int) -> tuple[bool, str]:
        """对话槽按“每日调用次数”限额；超限只降级对话，不影响决策槽。"""
        used = self.dialogue_calls.get(day, 0)
        limit = int(self.limits.dialogue_daily_calls)
        if limit <= 0 or used >= limit:
            self.dialogue_mocked += 1
            self.dialogue_mocked_by_day[day] = self.dialogue_mocked_by_day.get(day, 0) + 1
            return False, "dialogue_budget_exceeded"
        return True, ""

    def record_dialogue(self, day: int) -> None:
        self.dialogue_calls[day] = self.dialogue_calls.get(day, 0) + 1

    def check_intent(self, day: int) -> tuple[bool, str]:
        """意图槽按调用次数限额；超限只回退规则调度，不影响其他槽。"""
        used = self.intent_calls.get(day, 0)
        if used >= max(1, self.limits.intent_daily_calls):
            self.intent_mocked += 1
            self.intent_mocked_by_day[day] = self.intent_mocked_by_day.get(day, 0) + 1
            return False, "intent_budget_exceeded"
        self.intent_calls[day] = used + 1
        return True, ""

    def take_audit(self, day: int) -> bool:
        """一天只允许写一条预算审计事件（决策/对话/意图任一槽超限）。"""
        if (self.mocked_by_day.get(day, 0) <= 0 and self.dialogue_mocked_by_day.get(day, 0) <= 0
                and self.intent_mocked_by_day.get(day, 0) <= 0) or day in self._audited_days:
            return False
        self._audited_days.add(day)
        return True

    def summary(self) -> dict[str, Any]:
        return {
            "limits": self.limits.to_dict(),
            "month_tokens": self.month_tokens,
            "month_cost": round(self.month_cost, 6),
            "day_tokens": {str(k): v for k, v in sorted(self.day_tokens.items())},
            "day_cost": {str(k): round(v, 6) for k, v in sorted(self.day_cost.items())},
            "degraded": self.degraded,
            "mocked": self.mocked,
            "mocked_by_day": {str(k): v for k, v in sorted(self.mocked_by_day.items())},
            "degraded_by_day": {str(k): v for k, v in sorted(self.degraded_by_day.items())},
            "dialogue_calls": {str(k): v for k, v in sorted(self.dialogue_calls.items())},
            "dialogue_mocked": self.dialogue_mocked,
            "dialogue_mocked_by_day": {str(k): v for k, v in sorted(self.dialogue_mocked_by_day.items())},
            "intent_calls": {str(k): v for k, v in sorted(self.intent_calls.items())},
            "intent_mocked": self.intent_mocked,
            "intent_mocked_by_day": {str(k): v for k, v in sorted(self.intent_mocked_by_day.items())},
        }

