"""自提案目标：可机器校验的提案、可行性校验器与生命周期台账。

约束：
- 本模块不直接改 WorldState；提案只有通过校验、且激活硬门放行后，
  才由 MockReasoner 转成 `system_goal` 意图，经 Writer/Effect 建目标。
- 全部判定确定性：只用排序与 state 数值，不读墙钟、不用全局随机。
"""
from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..actions.common import (
    employer_of,
    goal_kind,
    goal_target,
    max_skill,
    organization_at,
    sell_price,
    sellable_items,
    treasury_guard_active,
    wallet,
)
from ..core.clock import TICKS_PER_DAY, day_of
from ..world.state import WorldState
from .intent import GoalChoice

SUPPORTED_TYPES = frozenset(
    {
        "earn_money",
        "advance_skill",
        "build_relation",
        "build_shop",
        "join_sect",
        "escort",
    }
)
ALLOWED_PRECONDITIONS = frozenset(
    {"has_employer", "at_location", "target_present", "owns_no_shop", "has_org"}
)
MASTERY_PER_ACTION = 0.0025 * 2  # 独立额度动作走 cultivate 2h（与 Planner._goal_step 一致）
SHOP_STALL_COST = 300
# 自提案的独立额度：每个 NPC 每天额外 1 次自目标深度动作，不占用内容目标的
# DEPTH_GOAL_ACTIONS 名额。校验器的 action_capacity 必须按这个额度算。
SELF_GOAL_ACTIONS_PER_DAY = 1
TRADE_MONEY_PER_ACTION = 5.0  # `_goal_step` 的 trade 兜底：money_delta=5


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _valid_precondition(text: str) -> bool:
    if text in ALLOWED_PRECONDITIONS:
        return True
    if text.startswith("has_cash:"):
        digits = text.split(":", 1)[1]
        return digits.isdigit() and int(digits) >= 0
    return False


@dataclass(frozen=True)
class SelfGoalProposal:
    proposal_id: str
    npc: str
    content_type: str
    kind: str
    target: float
    deadline_days: int
    deadline_tick: int
    expected_actions: int
    expected_money: int
    preconditions: tuple[str, ...]
    objects: tuple[str, ...]
    purpose: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "npc": self.npc,
            "goal_type": self.content_type,
            "target": self.target,
            "deadline_days": self.deadline_days,
            "deadline_tick": self.deadline_tick,
            "expected_actions": self.expected_actions,
            "expected_money": self.expected_money,
            "preconditions": list(self.preconditions),
            "objects": list(self.objects),
            "purpose": self.purpose,
        }


def parse_proposal(
    raw: Any,
    npc: str,
    tick: int,
    max_window_days: int = 3,
) -> tuple[SelfGoalProposal | None, list[str]]:
    """严格解析模型提案；任何不合法都返回错误列表，不构造半成品。"""
    errors: list[str] = []
    if not isinstance(raw, dict):
        return None, ["schema:root:not_object"]
    required = {
        "npc",
        "goal_type",
        "target",
        "deadline_days",
        "preconditions",
        "expected_actions",
        "expected_money",
        "objects",
        "purpose",
    }
    missing = sorted(required - set(raw))
    if missing:
        errors.append(f"schema:missing:{','.join(missing)}")
    extra = sorted(set(raw) - required)
    if extra:
        errors.append(f"schema:extra:{','.join(extra)}")
    if errors:
        return None, errors

    if not isinstance(raw["npc"], str) or raw["npc"] != npc:
        errors.append("schema:npc:mismatch")
    ctype = raw["goal_type"]
    if not isinstance(ctype, str) or ctype not in SUPPORTED_TYPES:
        errors.append("schema:goal_type:unsupported")
    target = raw["target"]
    if isinstance(target, bool) or not isinstance(target, (int, float)):
        errors.append("schema:target:not_number")
    elif not math.isfinite(float(target)) or float(target) <= 0:
        errors.append("schema:target:not_positive")
    days = raw["deadline_days"]
    if not _is_int(days) or days < 1 or days > max_window_days:
        errors.append("schema:deadline_days:out_of_range")
    pre = raw["preconditions"]
    if not isinstance(pre, list) or not all(isinstance(x, str) for x in pre):
        errors.append("schema:preconditions:not_string_list")
    else:
        bad = [x for x in pre if not _valid_precondition(x)]
        if bad:
            errors.append(f"schema:preconditions:unknown:{','.join(sorted(bad))}")
    exp_actions = raw["expected_actions"]
    if not _is_int(exp_actions) or exp_actions < 1:
        errors.append("schema:expected_actions:bad")
    exp_money = raw["expected_money"]
    if not _is_int(exp_money) or exp_money < 0:
        errors.append("schema:expected_money:bad")
    objects = raw["objects"]
    if not isinstance(objects, list) or not all(isinstance(x, str) and x for x in objects):
        errors.append("schema:objects:not_string_list")
    purpose = raw["purpose"]
    if not isinstance(purpose, str) or not purpose.strip() or len(purpose) > 120:
        errors.append("schema:purpose:bad")
    if errors:
        return None, errors

    assert isinstance(ctype, str)
    proposal = SelfGoalProposal(
        proposal_id=f"self:{npc}:{int(tick)}:{ctype}",
        npc=npc,
        content_type=ctype,
        kind=goal_kind(ctype),
        target=float(target),
        deadline_days=int(days),
        deadline_tick=int(tick) + int(days) * TICKS_PER_DAY,
        expected_actions=int(exp_actions),
        expected_money=int(exp_money),
        preconditions=tuple(pre),
        objects=tuple(objects),
        purpose=purpose.strip(),
    )
    return proposal, []


@dataclass
class Verdict:
    accepted: bool
    reason: str
    equation: str
    days: int
    quota: int
    per_action_units: float
    capacity_units: float
    cash: int
    checks: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "reason": self.reason,
            "equation": self.equation,
            "days": self.days,
            "quota": self.quota,
            "per_action_units": round(self.per_action_units, 6),
            "capacity_units": round(self.capacity_units, 6),
            "cash": self.cash,
            "checks": self.checks,
        }


def _check(
    name: str,
    ok: bool,
    actual: Any,
    limit: Any,
    equation: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "ok": bool(ok),
        "actual": actual,
        "limit": limit,
        "equation": equation,
    }


class FeasibilityValidator:
    """按当前 state 判定提案在声明的窗口内是否可达、预算是否自洽。"""

    def __init__(self, content):
        self.content = content

    def _wage_net(self, role: str) -> float:
        wages = self.content.economy.get("wages_daily") or {}
        taxes = self.content.economy.get("taxes") or {}
        wage = wages.get(role)
        if not _is_int(wage):
            return 0.0
        return float(wage) * (1.0 - float(taxes.get("income", 0.0)))

    def _best_sell_revenue(self, state: WorldState, npc: str) -> float:
        """单次 sell 动作的净收益上限（一次只卖 1 件，不是全部库存）。"""
        ent = state.entities[npc]
        best = 0.0
        tax_rate = float((self.content.economy.get("taxes") or {}).get("transaction", 0.0))
        for item in sellable_items(self.content, ent):
            if int(ent.inventory.get(item, 0)) <= 0:
                continue
            price = float(sell_price(self.content, item, state))
            best = max(best, price * (1.0 - tax_rate))
        return best

    def _target_present(self, state: WorldState, proposal: SelfGoalProposal) -> bool:
        for oid in proposal.objects:
            other = state.entities.get(oid)
            if other is None or other.status != "alive":
                continue
            if other.location == state.entities[proposal.npc].location:
                return True
        return False

    def _check_preconditions(
        self, state: WorldState, proposal: SelfGoalProposal, ent
    ) -> tuple[str, list[dict[str, Any]]]:
        checks: list[dict[str, Any]] = []
        cash = state.account_balance(wallet(ent.id))
        for token in proposal.preconditions:
            if token.startswith("has_cash:"):
                need = int(token.split(":", 1)[1])
                ok = cash >= need
                checks.append(
                    _check(
                        token,
                        ok,
                        cash,
                        need,
                        f"cash={cash} >= need={need}",
                    )
                )
            elif token == "has_employer":
                bid = employer_of(state, ent)
                ok = bool(bid)
                checks.append(_check(token, ok, bid or "", "employer", f"employer={bid or 'none'}"))
            elif token == "at_location":
                ok = ent.location in state.locations
                checks.append(
                    _check(token, ok, ent.location, "known_location", f"location={ent.location}")
                )
            elif token == "target_present":
                ok = self._target_present(state, proposal)
                checks.append(
                    _check(token, ok, list(proposal.objects), "alive_npc_at_location", "objects_present")
                )
            elif token == "owns_no_shop":
                ok = ent.business is None
                checks.append(_check(token, ok, ent.business or "", "no_business", f"business={ent.business or 'none'}"))
            elif token == "has_org":
                org = organization_at(state, ent.location)
                ok = org is not None
                checks.append(_check(token, ok, getattr(org, "id", ""), "organization", f"org={getattr(org, 'id', 'none')}"))
            if not checks[-1]["ok"]:
                return f"missing_precondition:{token}", checks
        return "", checks

    def validate(
        self,
        state: WorldState,
        proposal: SelfGoalProposal,
        tick: int,
        tier: str,
    ) -> Verdict:
        days = int(proposal.deadline_days)
        quota = SELF_GOAL_ACTIONS_PER_DAY
        action_capacity = days * quota
        ent = state.entities.get(proposal.npc)
        checks: list[dict[str, Any]] = []

        def reject(reason: str, equation: str, per: float = 0.0, capacity: float = 0.0) -> Verdict:
            return Verdict(
                accepted=False,
                reason=reason,
                equation=equation,
                days=days,
                quota=quota,
                per_action_units=per,
                capacity_units=capacity,
                cash=state.account_balance(wallet(proposal.npc)) if ent is not None else 0,
                checks=checks,
            )

        if ent is None or ent.status != "alive":
            return reject("unknown_npc" if ent is None else "npc_not_alive", "entity missing or not alive")
        if not (1 <= days <= 3):
            return reject("window_out_of_range", f"deadline_days={days} not in 1..3")
        if proposal.content_type not in SUPPORTED_TYPES:
            return reject("unsupported_type", f"goal_type={proposal.content_type}")

        pre_reason, pre_checks = self._check_preconditions(state, proposal, ent)
        checks.extend(pre_checks)
        if pre_reason:
            return reject(pre_reason, pre_reason)

        kind = proposal.kind
        per = 0.0
        capacity = 0.0
        cash = state.account_balance(wallet(ent.id))
        if kind == "wealth":
            # 独立额度不排 work：现钱只来自 sell（单件净收益）或 trade（+5 文兜底）。
            sell = self._best_sell_revenue(state, ent.id)
            per = max(TRADE_MONEY_PER_ACTION, sell)
            checks.append(
                _check(
                    "income_source",
                    per > 0,
                    round(per, 4),
                    ">0",
                    f"max(trade={TRADE_MONEY_PER_ACTION:.2f}, best_sell_net={sell:.2f})={per:.2f}",
                )
            )
            if per <= 0:
                return reject("no_income_source", f"per_action=max({TRADE_MONEY_PER_ACTION},{sell:.2f})=0")
            capacity = action_capacity * per
        elif kind == "mastery":
            per = MASTERY_PER_ACTION
            headroom = max(0.0, 1.0 - max_skill(ent))
            capacity = min(action_capacity * per, headroom)
            checks.append(
                _check(
                    "skill_headroom",
                    headroom > 0,
                    round(headroom, 6),
                    ">0",
                    f"headroom=1-max_skill={headroom:.6f}",
                )
            )
            if headroom <= 0:
                return reject("skill_capped", f"headroom={headroom:.6f} <= 0")
        elif kind == "bond":
            present = self._target_present(state, proposal)
            has_biz = bool(state.business_at(ent.location))
            per = 3.0 if (present or has_biz) else 0.0
            checks.append(
                _check(
                    "bond_source",
                    per > 0,
                    per,
                    ">0",
                    f"target_present={present}, business_at_location={has_biz}",
                )
            )
            if per <= 0:
                return reject("bond_target_absent", "no target present and no business at location")
            capacity = action_capacity * per
        elif kind == "shop":
            cost = SHOP_STALL_COST
            has_landlord = bool(state.business_at(ent.location) or organization_at(state, ent.location))
            guard = treasury_guard_active(state, self.content)
            checks.append(
                _check("shop_cash", cash >= cost, cash, cost, f"cash={cash} >= stall={cost}")
            )
            checks.append(
                _check("shop_owner_slot", ent.business is None, ent.business or "", "none", f"business={ent.business or 'none'}")
            )
            checks.append(
                _check("shop_landlord", has_landlord, ent.location, "landlord_or_org", f"landlord={has_landlord}")
            )
            checks.append(
                _check("shop_treasury_guard", not guard, guard, False, f"treasury_guard={guard}")
            )
            if cash < cost:
                return reject("shop_insufficient_cash", f"cash={cash} < stall={cost}")
            if ent.business is not None:
                return reject("shop_already_owned", f"business={ent.business}")
            if not has_landlord:
                return reject("shop_no_landlord", f"location={ent.location} has no landlord")
            if guard:
                return reject("shop_treasury_guard", "treasury guard active")
            per = 1.0
            capacity = float(action_capacity)
        elif kind == "faction":
            org = organization_at(state, ent.location)
            checks.append(
                _check("org_present", org is not None, getattr(org, "id", ""), "organization", f"org={getattr(org, 'id', 'none')}")
            )
            if org is None:
                return reject("org_absent", f"location={ent.location} has no organization")
            per = 1.0
            capacity = float(action_capacity)
        elif kind == "escort":
            routes = [
                q
                for q in state.quests.values()
                if str(getattr(q, "verb", "")) == "escort"
                and str(getattr(q, "status", "")) in ("open", "accepted")
            ]
            checks.append(
                _check("escort_route", bool(routes), len(routes), ">0", f"open_escort_quests={len(routes)}")
            )
            if not routes:
                return reject("escort_route_absent", "no open escort quest")
            per = 1.0
            capacity = float(action_capacity)
        else:
            return reject("unsupported_type", f"kind={kind}")

        checks.append(
            _check(
                "capacity",
                capacity >= proposal.target,
                round(capacity, 6),
                proposal.target,
                f"capacity={action_capacity}×{per:.4f}={capacity:.4f} >= target={proposal.target:.4f}",
            )
        )
        if capacity + 1e-9 < proposal.target:
            return reject(
                "target_exceeds_capacity",
                f"capacity={action_capacity}×{per:.4f}={capacity:.4f} < target={proposal.target:.4f}",
                per,
                capacity,
            )

        checks.append(
            _check(
                "budget_actions",
                proposal.expected_actions <= action_capacity,
                proposal.expected_actions,
                action_capacity,
                f"expected_actions={proposal.expected_actions} <= days×quota={action_capacity}",
            )
        )
        if proposal.expected_actions > action_capacity:
            return reject(
                "budget_actions_exceeded",
                f"expected_actions={proposal.expected_actions} > action_capacity={action_capacity}",
                per,
                capacity,
            )
        checks.append(
            _check(
                "budget_money",
                proposal.expected_money <= cash,
                proposal.expected_money,
                cash,
                f"expected_money={proposal.expected_money} <= cash={cash}",
            )
        )
        if proposal.expected_money > cash:
            return reject(
                "budget_money_exceeded",
                f"expected_money={proposal.expected_money} > cash={cash}",
                per,
                capacity,
            )
        if kind == "shop" and proposal.expected_money < SHOP_STALL_COST:
            return reject(
                "budget_money_understated",
                f"expected_money={proposal.expected_money} < stall={SHOP_STALL_COST}",
                per,
                capacity,
            )

        return Verdict(
            accepted=True,
            reason="accepted",
            equation=(
                f"capacity={action_capacity}×{per:.4f}={capacity:.4f} >= target={proposal.target:.4f};"
                f"actions={proposal.expected_actions}<={action_capacity};"
                f"money={proposal.expected_money}<={cash}"
            ),
            days=days,
            quota=quota,
            per_action_units=per,
            capacity_units=capacity,
            cash=cash,
            checks=checks,
        )


class SelfGoalLedger:
    """提案台账：校验通过的提案先入队，激活硬门放行后才允许建目标。"""

    def __init__(
        self,
        content,
        mode: str = "competent",
        every_days: int = 3,
        max_window_days: int = 3,
        trace_path: str = "",
    ):
        self.content = content
        self.mode = mode
        self.every_days = max(1, int(every_days))
        self.max_window_days = max(1, int(max_window_days))
        self.validator = FeasibilityValidator(content)
        self.trace_path = trace_path
        self.pending: dict[str, SelfGoalProposal] = {}
        self._pending_verdict: dict[str, Verdict] = {}
        self._last_issue: dict[str, int] = {}
        self._activated: dict[str, int] = {}  # goal_id -> deadline_tick
        self._seen_completed: set[str] = set()
        self._seen_abandoned: set[str] = set()
        self._activation_rejected: set[tuple[str, str, str]] = set()
        self._scenario_active_seen: set[tuple[str, str]] = set()
        self._initial_goal_ids: dict[str, str] = {}
        for npc in content.npcs:
            goals = sorted(
                npc.get("goals") or [],
                key=lambda g: (float(g.get("priority", 9)), str(g.get("id", ""))),
            )
            if goals:
                self._initial_goal_ids[str(npc["id"])] = f"{npc['id']}:{goals[0]['id']}"
        self.stats: Counter[str] = Counter()
        self.by_reason: Counter[str] = Counter()
        self.records: list[dict[str, Any]] = []

    # ---- 留痕 ----
    def _trace(self, rec: dict[str, Any]) -> None:
        self.records.append(rec)
        if not self.trace_path:
            return
        path = Path(self.trace_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")

    # ---- stub 提案 ----
    def _wage_net(self, role: str) -> float:
        wages = self.content.economy.get("wages_daily") or {}
        taxes = self.content.economy.get("taxes") or {}
        wage = wages.get(role)
        if not _is_int(wage):
            return 0.0
        return float(wage) * (1.0 - float(taxes.get("income", 0.0)))

    def _best_sell(self, state: WorldState, npc: str) -> float:
        ent = state.entities[npc]
        best = 0.0
        for item in sellable_items(self.content, ent):
            qty = int(ent.inventory.get(item, 0))
            if qty > 0:
                best = max(best, float(sell_price(self.content, item, state) * qty))
        return best

    def _stub_proposal(
        self, state: WorldState, npc: str, tick: int, tier: str, index: int, cycle: int
    ) -> dict[str, Any] | None:
        ent = state.entities[npc]
        days = self.max_window_days
        actions = days * SELF_GOAL_ACTIONS_PER_DAY
        has_employer = bool(employer_of(state, ent))
        use_mastery = max_skill(ent) < 0.9 and (not has_employer or index % 2 == 0)
        if use_mastery:
            ctype = "advance_skill"
            per = MASTERY_PER_ACTION
            target = min(float(goal_target(self.content, "mastery")), 0.9 * actions * per)
            pre = []
        else:
            ctype = "earn_money"
            per = max(TRADE_MONEY_PER_ACTION, self._best_sell(state, npc))
            target = min(float(goal_target(self.content, "wealth")), 0.9 * actions * per)
            pre = ["has_employer"] if has_employer else []
        expected_actions = actions
        expected_money = 0
        purpose = (
            f"把{max_skill(ent):.3f}的手艺再精进到{target:.3f}"
            if use_mastery
            else f"攒{target:.0f}文件盘缠"
        )

        if self.mode == "scenarios":
            cls = (index + cycle) % 3
            if cls == 1:
                expected_actions = actions * 2
                purpose += "（超预算样例）"
            elif cls == 2:
                target = 9999.0
                purpose += "（窗口内不可达样例）"
        return {
            "npc": npc,
            "goal_type": ctype,
            "target": round(float(target), 4),
            "deadline_days": days,
            "preconditions": pre,
            "expected_actions": int(expected_actions),
            "expected_money": int(expected_money),
            "objects": [ent.location],
            "purpose": purpose,
        }

    # ---- 提案入队 ----
    def maybe_issue(self, state: WorldState, npc: str, tick: int, tier: str, index: int = 0) -> None:
        ent = state.entities.get(npc)
        if ent is None or ent.status != "alive":
            return
        self.scan(state, tick)
        active_self = self._active_self_goal(state, npc)
        force_active = active_self is not None and self.mode == "scenarios"
        if force_active:
            key = (npc, active_self.id)
            if key in self._scenario_active_seen:
                return
            self._scenario_active_seen.add(key)
        else:
            cycle = day_of(tick) // self.every_days
            last = self._last_issue.get(npc)
            if last is not None and cycle <= last:
                return
            self._last_issue[npc] = cycle

        if active_self is not None and self.mode != "scenarios":
            return

        raw = self._stub_proposal(state, npc, tick, tier, index, day_of(tick) // self.every_days)
        if raw is None:
            return
        proposal, errors = parse_proposal(raw, npc, tick, self.max_window_days)
        self.stats["proposed"] += 1
        if proposal is None:
            reason = errors[0] if errors else "schema:unknown"
            self.stats["rejected"] += 1
            self.by_reason[reason] += 1
            self._trace(
                {
                    "kind": "proposal",
                    "tick": int(tick),
                    "npc": npc,
                    "mode": self.mode,
                    "raw": raw,
                    "accepted": False,
                    "reason": reason,
                    "equation": reason,
                    "errors": errors,
                }
            )
            return

        verdict = self.validator.validate(state, proposal, tick, tier)
        record = {
            "kind": "proposal",
            "tick": int(tick),
            "npc": npc,
            "mode": self.mode,
            "raw": raw,
            "proposal": proposal.to_dict(),
            **verdict.to_dict(),
        }
        self._trace(record)
        if verdict.accepted and active_self is not None:
            # scenarios 强制提交：用于证明“同 NPC 活跃自目标 ≤1”的制度性硬门。
            self.stats["rejected"] += 1
            self.by_reason["self_goal_active"] += 1
            self._trace(
                {
                    "kind": "activation_rejected",
                    "tick": int(tick),
                    "npc": npc,
                    "proposal_id": proposal.proposal_id,
                    "reason": "self_goal_active",
                    "detail": f"active_self_goal={active_self.id}",
                }
            )
            return
        if verdict.accepted:
            self.stats["accepted"] += 1
            self.pending[npc] = proposal
            self._pending_verdict[npc] = verdict
        else:
            self.stats["rejected"] += 1
            self.by_reason[verdict.reason] += 1

    # ---- 生命周期硬门 ----
    def _initial_goal_active(self, state: WorldState, npc: str) -> bool:
        ent = state.entities.get(npc)
        if ent is None or not ent.goal_id:
            return False
        initial = self._initial_goal_ids.get(npc)
        if initial is None or ent.goal_id != initial:
            return False
        goal = state.goals.get(initial)
        return goal is not None and goal.status == "active"

    def _active_self_goal(self, state: WorldState, npc: str):
        for gid in sorted(state.goals):
            g = state.goals[gid]
            if g.npc == npc and getattr(g, "source", "") == "self" and g.status == "active":
                return g
        return None

    def can_activate(self, state: WorldState, npc: str) -> tuple[str, str]:
        ent = state.entities.get(npc)
        if ent is None or ent.status != "alive":
            return "npc_not_alive", "entity not alive"
        if self._initial_goal_active(state, npc):
            return "initial_goal_protected", f"active={ent.goal_id} is initial content goal"
        active_self = self._active_self_goal(state, npc)
        if active_self is not None:
            # 自目标是次级目标，始终不占 entity.goal_id；因此“同 NPC 同时只有
            # 一个活跃自目标”这条硬门不再需要豁免当前主目标的情况。
            return "self_goal_active", f"active_self_goal={active_self.id}"
        return "", ""

    # ---- 决策点消费 ----
    def consume_expired(self, state: WorldState, npc: str, tick: int) -> str:
        """到期的活跃自目标返回 goal_id，供上层发 abandon；无则返回空串。"""
        active = self._active_self_goal(state, npc)
        if active is None:
            return ""
        deadline = self._activated.get(active.id)
        if deadline is None or tick <= deadline:
            return ""
        self.stats["expired"] += 1
        self._trace(
            {
                "kind": "goal_expired",
                "tick": int(tick),
                "npc": npc,
                "goal_id": active.id,
                "deadline_tick": int(deadline),
            }
        )
        self._activated.pop(active.id, None)
        return active.id

    def choose(
        self, state: WorldState, npc: str, tick: int, tier: str = "active"
    ) -> GoalChoice | None:
        self.scan(state, tick)
        ent = state.entities.get(npc)
        if ent is None or ent.status != "alive":
            return None

        # 到期由上层 consume_expired 负责；这里只负责“给一个可规划的自目标”。
        active_self = self._active_self_goal(state, npc)
        if active_self is not None:
            return GoalChoice(
                npc=npc,
                goal_id=active_self.id,
                content_goal_id=active_self.content_goal_id,
                content_type=active_self.content_type,
                kind=active_self.kind,
                target=active_self.target,
                source="self",
                reason="继续推进自定目标",
                create=False,
                secondary=True,
            )

        proposal = self.pending.get(npc)
        if proposal is None:
            return None
        if tick > proposal.deadline_tick:
            self.pending.pop(npc, None)
            self._pending_verdict.pop(npc, None)
            self.stats["pending_expired"] += 1
            self._trace(
                {
                    "kind": "pending_expired",
                    "tick": int(tick),
                    "npc": npc,
                    "proposal_id": proposal.proposal_id,
                    "deadline_tick": int(proposal.deadline_tick),
                }
            )
            return None

        reason, detail = self.can_activate(state, npc)
        if reason:
            key = (npc, proposal.proposal_id, reason)
            if key not in self._activation_rejected:
                self._activation_rejected.add(key)
                self.by_reason[reason] += 1
                self._trace(
                    {
                        "kind": "activation_rejected",
                        "tick": int(tick),
                        "npc": npc,
                        "proposal_id": proposal.proposal_id,
                        "reason": reason,
                        "detail": detail,
                    }
                )
            return None

        # 激活时按当前 state 与当前档位重新校验；窗口从激活时刻重新起算，
        # 避免“入队时可达、激活时窗口已被等待吃掉”的旧问题。
        recheck = self.validator.validate(state, proposal, tick, tier)
        if not recheck.accepted:
            self.pending.pop(npc, None)
            self._pending_verdict.pop(npc, None)
            self.by_reason[recheck.reason] += 1
            self._trace(
                {
                    "kind": "activation_rejected",
                    "tick": int(tick),
                    "npc": npc,
                    "proposal_id": proposal.proposal_id,
                    "reason": recheck.reason,
                    "detail": "activation recheck",
                    "equation": recheck.equation,
                    "checks": recheck.checks,
                }
            )
            return None

        goal_id = proposal.proposal_id
        deadline_tick = int(tick) + proposal.deadline_days * TICKS_PER_DAY
        self.pending.pop(npc, None)
        self._pending_verdict.pop(npc, None)
        self._activated[goal_id] = deadline_tick
        self.stats["activated"] += 1
        self._trace(
            {
                "kind": "activation",
                "tick": int(tick),
                "npc": npc,
                "goal_id": goal_id,
                "deadline_tick": deadline_tick,
                "proposal": proposal.to_dict(),
                "equation": recheck.equation,
            }
        )
        return GoalChoice(
            npc=npc,
            goal_id=goal_id,
            content_goal_id=f"self:{proposal.proposal_id}",
            content_type=proposal.content_type,
            kind=proposal.kind,
            target=proposal.target,
            source="self",
            reason=proposal.purpose,
            create=True,
            secondary=True,
        )

    def scan(self, state: WorldState, tick: int) -> None:
        for gid in sorted(state.goals):
            g = state.goals[gid]
            if getattr(g, "source", "") != "self":
                continue
            if g.status == "completed" and gid not in self._seen_completed:
                self._seen_completed.add(gid)
                self.stats["completed"] += 1
                self._trace(
                    {
                        "kind": "completion",
                        "tick": int(tick),
                        "npc": g.npc,
                        "goal_id": gid,
                        "progress": round(float(g.progress), 6),
                        "target": float(g.target),
                    }
                )
            elif g.status == "abandoned" and gid not in self._seen_abandoned:
                self._seen_abandoned.add(gid)
                self.stats["abandoned"] += 1

    def summary(self) -> dict[str, Any]:
        proposed = int(self.stats.get("proposed", 0))
        accepted = int(self.stats.get("accepted", 0))
        return {
            "mode": self.mode,
            "every_days": self.every_days,
            "max_window_days": self.max_window_days,
            "proposed": proposed,
            "accepted": accepted,
            "rejected": int(self.stats.get("rejected", 0)),
            "pass_rate": (accepted / proposed) if proposed else 0.0,
            "activated": int(self.stats.get("activated", 0)),
            "completed": int(self.stats.get("completed", 0)),
            "expired": int(self.stats.get("expired", 0)),
            "pending_expired": int(self.stats.get("pending_expired", 0)),
            "by_reason": dict(sorted(self.by_reason.items())),
            "records": len(self.records),
        }
