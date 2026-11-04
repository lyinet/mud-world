"""A4 长周期战略层：计划 schema、校验与消费台账。

设计约束（见 specs/a4-strategist/spec.md）：
- 计划只含方向（目标/关系对象/预算/理由），不含任何动作动词或参数；
- 每个 NPC 每 N 游戏日一份计划，tick 级动作仍由规则 planner 执行；
- 计划不写入 WorldState / events.jsonl，只写 runs/<id>/strategy.jsonl；
- 开关关闭时本模块不参与任何计算，事件流与哈希不变。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.clock import TICKS_PER_DAY, day_of
from ..world.state import WorldState

# 核心类型：仍有未完成目标时，计划至少覆盖其中一类（G3）。
CORE_GOAL_TYPES = ("build_shop", "earn_money", "advance_skill")

DEFAULT_BUDGET = {
    "cash_floor": 40,
    "max_discretionary_spend": 200,
    "max_goal_actions_per_day": 2,
}

_FORBIDDEN_KEYS = ("verb", "args", "actions", "params", "system_")


@dataclass(frozen=True)
class StrategyEntry:
    role: str          # primary / secondary / preserve
    goal_id: str       # 状态目标 id：<npc>:<content_goal_id>
    content_goal_id: str
    content_type: str


@dataclass
class StrategyPlan:
    plan_id: str
    npc: str
    created_tick: int
    expires_tick: int
    horizon_days: int
    entries: list[StrategyEntry]
    relation_targets: list[str]
    budget: dict[str, Any]
    type_quota: list[str]
    confidence: float
    rationale: str
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def goal_ids(self) -> list[str]:
        return [e.goal_id for e in self.entries]

    def entry_for(self, goal_id: str) -> StrategyEntry | None:
        for entry in self.entries:
            if entry.goal_id == goal_id:
                return entry
        return None


def _content_rows(content, npc: str) -> list[dict[str, Any]]:
    row = next((n for n in content.npcs if str(n.get("id")) == npc), None)
    return list((row or {}).get("goals") or [])


def allowed_goal_refs(content, npc: str) -> dict[str, dict[str, Any]]:
    """NPC 可引用的内容目标：自身内容目标 + 门派发起人的 found_sect 目标。"""
    out: dict[str, dict[str, Any]] = {}
    for goal in _content_rows(content, npc):
        gid = str(goal.get("id") or "")
        if gid:
            out[gid] = {"id": gid, "type": str(goal.get("type") or "")}
    for sid in sorted(getattr(content, "sects", {}) or {}):
        sect = content.sects[sid]
        if str(sect.get("founder_candidate") or "") != npc:
            continue
        out.setdefault(f"found_{sid}", {"id": f"found_{sid}", "type": "found_sect", "sect": sid})
    return out


def normalize_goal_ref(npc: str, ref: Any, allowed: dict[str, dict[str, Any]]) -> tuple[str, str, str] | None:
    """把模型给的 goal 引用归一成 (状态目标 id, content_goal_id, type)。"""
    raw = str(ref or "").strip()
    if not raw:
        return None
    if raw.startswith(f"{npc}:"):
        raw = raw.split(":", 1)[1]
    meta = allowed.get(raw)
    if meta is None:
        return None
    return f"{npc}:{raw}", raw, str(meta.get("type") or "")


def validate_plan(plan: StrategyPlan, content, state: WorldState) -> tuple[bool, list[str]]:
    """按 spec 第 3.3 节校验；返回 (是否合法, 原因列表)。"""
    reasons: list[str] = []
    if len(plan.entries) > 3:
        reasons.append("too_many_goals")
    if not plan.entries:
        reasons.append("empty_plan")
    allowed = allowed_goal_refs(content, plan.npc)
    seen: set[str] = set()
    for entry in plan.entries:
        if entry.goal_id in seen:
            reasons.append(f"duplicate_goal:{entry.goal_id}")
        seen.add(entry.goal_id)
        if entry.content_goal_id not in allowed:
            reasons.append(f"unknown_goal:{entry.goal_id}")
        goal = state.goals.get(entry.goal_id)
        if goal is not None and goal.status in ("completed", "abandoned"):
            reasons.append(f"closed_goal:{entry.goal_id}")
    for raw_key in plan.raw:
        low = str(raw_key).lower()
        if any(token in low for token in _FORBIDDEN_KEYS):
            reasons.append(f"forbidden_key:{raw_key}")
    if any(str(x) not in {str(n.get("id")) for n in content.npcs} for x in plan.relation_targets):
        reasons.append("unknown_relation_target")

    # G1 进度保护：当前规则目标已推进（progress>=0.1）时必须被计划覆盖。
    ent = state.entities.get(plan.npc)
    active = state.goals.get(ent.goal_id) if ent is not None and ent.goal_id else None
    if active is not None and active.status == "active" and not str(active.content_type).startswith("urgent_"):
        progress = float(active.progress)
        if progress >= 0.1 and active.id not in plan.goal_ids:
            reasons.append(f"g1_progress_unprotected:{active.id}")

    # G3 类型配额：仍有未完成核心类型时，计划必须至少覆盖一个核心类型。
    if plan.type_quota:
        unfinished_core = []
        for gid, meta in allowed.items():
            ctype = str(meta.get("type") or "")
            if ctype not in CORE_GOAL_TYPES:
                continue
            sg = state.goals.get(f"{plan.npc}:{gid}")
            if sg is not None and sg.status in ("completed", "abandoned"):
                continue
            unfinished_core.append(ctype)
        plan_types = {e.content_type for e in plan.entries}
        if unfinished_core and not (plan_types & set(CORE_GOAL_TYPES)):
            reasons.append("g3_core_type_missing")
    return (not reasons, reasons)


def parse_strategy_plan(
    payload: dict[str, Any],
    *,
    npc: str,
    tick: int,
    horizon_days: int,
    content,
) -> StrategyPlan:
    """把模型 JSON 转成 StrategyPlan；未知 goal 引用保留在 raw 里，由校验裁定。"""
    allowed = allowed_goal_refs(content, npc)
    primary_ref = payload.get("primary_goal_id")
    secondary_raw = payload.get("secondary_goal_ids")
    preserve_raw = payload.get("preserve_goal_ids")
    entries: list[StrategyEntry] = []
    for role, refs in (
        ("primary", [primary_ref] if primary_ref not in (None, "") else []),
        ("secondary", secondary_raw if isinstance(secondary_raw, list) else []),
        ("preserve", preserve_raw if isinstance(preserve_raw, list) else []),
    ):
        for ref in refs:
            normalized = normalize_goal_ref(npc, ref, allowed)
            if normalized is None:
                # 未知引用也进 entries，便于审计判为 unknown_goal。
                raw = str(ref or "")
                entries.append(StrategyEntry(role=role, goal_id=raw, content_goal_id=raw, content_type=""))
                continue
            gid, cgid, ctype = normalized
            if any(e.goal_id == gid for e in entries):
                continue
            entries.append(StrategyEntry(role=role, goal_id=gid, content_goal_id=cgid, content_type=ctype))
    budget = dict(DEFAULT_BUDGET)
    raw_budget = payload.get("budget")
    if isinstance(raw_budget, dict):
        for key in DEFAULT_BUDGET:
            if key in raw_budget:
                budget[key] = raw_budget[key]
    quota_raw = payload.get("type_quota")
    quota = [str(x) for x in quota_raw] if isinstance(quota_raw, list) else []
    rationale = str(payload.get("rationale") or "").strip()[:80]
    try:
        confidence = float(payload.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    return StrategyPlan(
        plan_id=str(payload.get("plan_id") or f"plan:{npc}:{tick}"),
        npc=npc,
        created_tick=int(tick),
        expires_tick=int(tick) + max(1, int(horizon_days)) * TICKS_PER_DAY,
        horizon_days=max(1, int(horizon_days)),
        entries=entries,
        relation_targets=[str(x) for x in payload.get("relation_targets") or []]
        if isinstance(payload.get("relation_targets"), list) else [],
        budget=budget,
        type_quota=quota,
        confidence=confidence,
        rationale=rationale,
        raw=dict(payload),
    )


class StrategyBook:
    """计划台账：每 NPC 当前计划、消费记录、偏差与紧急暂停。全部只读世界。"""

    def __init__(self):
        self.plans: dict[str, StrategyPlan] = {}
        self.records: list[dict[str, Any]] = []
        self.consumptions: dict[str, dict[str, dict[str, Any]]] = {}
        self.counts = {
            "plans_issued": 0,
            "plans_valid": 0,
            "plans_invalid": 0,
            "consumed_entries": 0,
            "paused": 0,
            "deviations": 0,
            "infeasible_deviations": 0,
        }

    def active_plan(self, npc: str, tick: int) -> StrategyPlan | None:
        plan = self.plans.get(npc)
        if plan is None:
            return None
        if tick < plan.created_tick or tick >= plan.expires_tick:
            return None
        return plan

    def register(self, plan: StrategyPlan, valid: bool, reasons: list[str]) -> None:
        self.counts["plans_issued"] += 1
        if valid:
            self.counts["plans_valid"] += 1
            self.plans[plan.npc] = plan
        else:
            self.counts["plans_invalid"] += 1
        self.records.append(
            {
                "type": "plan",
                "plan_id": plan.plan_id,
                "npc": plan.npc,
                "day": day_of(plan.created_tick),
                "created_tick": plan.created_tick,
                "expires_tick": plan.expires_tick,
                "horizon_days": plan.horizon_days,
                "valid": bool(valid),
                "reasons": list(reasons),
                "rationale": plan.rationale,
                "confidence": plan.confidence,
                "relation_targets": list(plan.relation_targets),
                "type_quota": list(plan.type_quota),
                "budget": dict(plan.budget),
                "entries": [
                    {
                        "role": e.role,
                        "goal_id": e.goal_id,
                        "content_goal_id": e.content_goal_id,
                        "content_type": e.content_type,
                    }
                    for e in plan.entries
                ],
                "raw": plan.raw,
            }
        )

    def record_consumption(
        self,
        plan: StrategyPlan,
        goal_id: str,
        tick: int,
        *,
        scheduled: bool,
        actions: int,
    ) -> None:
        entry = plan.entry_for(goal_id)
        if entry is None:
            return
        rows = self.consumptions.setdefault(plan.plan_id, {})
        row = rows.get(goal_id)
        if row is None:
            row = {
                "tick": tick,
                "day": day_of(tick),
                "npc": plan.npc,
                "plan_id": plan.plan_id,
                "goal_id": goal_id,
                "role": entry.role,
                "content_goal_id": entry.content_goal_id,
                "content_type": entry.content_type,
                "scheduled": bool(scheduled),
                "actions": int(actions),
                "count": 0,
            }
            rows[goal_id] = row
            self.records.append({"type": "consumption", **row})
            self.counts["consumed_entries"] += 1
        row["count"] += 1
        if scheduled:
            row["scheduled"] = True
            row["actions"] = max(int(row["actions"]), int(actions))

    def record_deviation(self, plan: StrategyPlan, tick: int, reason: str, goal_id: str = "") -> None:
        self.counts["deviations"] += 1
        if reason.startswith("infeasible"):
            self.counts["infeasible_deviations"] += 1
        self.records.append(
            {
                "type": "deviation",
                "tick": tick,
                "day": day_of(tick),
                "npc": plan.npc,
                "plan_id": plan.plan_id,
                "goal_id": goal_id,
                "reason": str(reason)[:160],
            }
        )

    def record_pause(self, plan: StrategyPlan, tick: int, urgent_type: str, goal_id: str) -> None:
        self.counts["paused"] += 1
        self.records.append(
            {
                "type": "pause",
                "tick": tick,
                "day": day_of(tick),
                "npc": plan.npc,
                "plan_id": plan.plan_id,
                "goal_id": goal_id,
                "urgent_type": urgent_type,
            }
        )

    def summary(self) -> dict[str, Any]:
        rows = {k: v for k, v in self.counts.items()}
        rows["plans_by_day"] = {}
        for rec in self.records:
            if rec.get("type") == "plan":
                key = str(rec.get("day"))
                rows["plans_by_day"][key] = rows["plans_by_day"].get(key, 0) + 1
        rows["active_plans"] = len(self.plans)
        rows["log"] = "strategy.jsonl"
        return rows

    def to_jsonl(self) -> str:
        import json

        return "".join(
            json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in self.records
        )
