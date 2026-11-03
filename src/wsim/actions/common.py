"""动作层公共工具。

动作是纯函数：只读 state，返回 Effect[]；不得修改 state，不得读墙钟/全局随机。
关系数值采用内容包的 -100..100 口径（与 K6 单向强依赖阈值一致）。
"""
from __future__ import annotations

from collections import deque

from ..core.models import Effect, Reject
from ..world.effects import party_account
from ..world.state import Business, Entity, Goal, WorldState

GOAL_DEFAULTS = {
    "wealth": 600.0,
    "mastery": 0.2,
    "bond": 30.0,
    "shop": 1.0,
    "faction": 1.0,
    "escort": 3.0,
}

GOAL_TYPE_MAP = {
    "earn_money": "wealth",
    "advance_skill": "mastery",
    "build_relation": "bond",
    "build_shop": "shop",
    "gain_status": "faction",
    "join_sect": "faction",
    "found_sect": "faction",
}


def goal_kind(content_type: str) -> str:
    return GOAL_TYPE_MAP.get(content_type, "wealth")


def goal_target(content, kind: str) -> float:
    table = content.economy.get("goal_targets") or {}
    return float(table.get(kind, GOAL_DEFAULTS.get(kind, 1.0)))


def require_entity(state: WorldState, actor: str) -> Entity:
    ent = state.entities.get(actor)
    if ent is None:
        raise Reject("unknown_actor")
    if ent.status != "alive":
        raise Reject("actor_not_alive")
    return ent


def alive(state: WorldState, eid: str) -> Entity:
    ent = state.entities.get(eid)
    if ent is None:
        raise Reject("unknown_target")
    if ent.status != "alive":
        raise Reject("target_not_alive")
    return ent


def founding_members(
    state: WorldState,
    ent: Entity,
    min_relation_value: float = 30.0,
) -> list[str]:
    """找到可以随 founder 一起开宗的成员。

    内容包只给了 sect.min_members，没有单独的门派招人动作，因此 P1 把
    “创始成员”定义为：创始人本人 + 在世弟子 + 亲密度 >= min_relation_value
    的 friend/family/ally，且这些人当前不在其他 sect:。
    """
    members = {ent.id}
    for did in ent.disciples:
        other = state.entities.get(did)
        if other is None or other.status != "alive":
            continue
        if other.faction and str(other.faction).startswith("sect:"):
            continue
        members.add(did)
    for rel in state.relations.values():
        if rel.status != "active" or rel.a != ent.id:
            continue
        if rel.kind not in ("friend", "family", "ally"):
            continue
        if rel.affection < min_relation_value:
            continue
        other = state.entities.get(rel.b)
        if other is None or other.status != "alive":
            continue
        if other.faction and str(other.faction).startswith("sect:"):
            continue
        members.add(rel.b)
    return sorted(members)


def arg_str(intent, key: str, required: bool = True) -> str | None:
    v = intent.args.get(key)
    if v is None:
        if required:
            raise Reject(f"missing_arg:{key}")
        return None
    if not isinstance(v, str) or not v:
        raise Reject(f"bad_arg:{key}")
    return v


def arg_int(intent, key: str, default: int | None = None, minimum: int = 1, maximum: int | None = None) -> int:
    v = intent.args.get(key, default)
    if v is None:
        raise Reject(f"missing_arg:{key}")
    if isinstance(v, bool) or not isinstance(v, int) or v < minimum:
        raise Reject(f"bad_arg:{key}")
    if maximum is not None and v > maximum:
        raise Reject(f"bad_arg:{key}")
    return v


def active_goal(state: WorldState, npc: str, kind: str | None = None) -> Goal | None:
    entity = state.entities.get(npc)
    if entity is None or not entity.goal_id:
        return None
    goal = state.goals.get(entity.goal_id)
    if goal is None or goal.status != "active":
        return None
    if kind is not None and goal.kind != kind:
        return None
    return goal


def goal_progress(state: WorldState, npc: str, kind: str, units: float, tick: int) -> list[Effect]:
    goal = active_goal(state, npc, kind)
    if goal is None or goal.target <= 0:
        return []
    delta = max(0.0, float(units)) / goal.target
    if delta <= 0:
        return []
    return [Effect("goal_progress", {"id": goal.id, "delta": delta, "tick": tick})]


def memory(state: WorldState, npc: str, tick: int, text: str, importance: float = 0.1) -> list[Effect]:
    return [Effect("memory_add", {"npc": npc, "tick": tick, "text": text, "importance": importance})]


def wallet(npc: str) -> str:
    return f"wallet:{npc}"


def first_business_at(state: WorldState, location: str) -> Business | None:
    biz = state.business_at(location)
    return biz[0] if biz else None


def business_selling(state: WorldState, location: str, item: str) -> Business | None:
    for biz in state.business_at(location):
        if biz.stock.get(item, 0) > 0:
            return biz
    return None


def business_with_cash(state: WorldState, location: str, need: int) -> Business | None:
    for biz in state.business_at(location):
        if state.account_balance(biz.cash_account) >= need:
            return biz
    return None


def business_by_kind(state: WorldState, kind: str) -> Business | None:
    for biz in sorted(state.businesses.values(), key=lambda b: b.id):
        if biz.kind == kind:
            return biz
    return None


def food_locations(state: WorldState, content) -> list[str]:
    out: list[str] = []
    for lid in sorted(state.locations):
        for biz in state.business_at(lid):
            for iid, qty in biz.stock.items():
                it = content.items.get(iid)
                if qty > 0 and it and it.get("category") in ("food", "drink"):
                    out.append(lid)
                    break
            if out and out[-1] == lid:
                break
    return out


def food_item(content) -> str:
    for iid, item in sorted(content.items.items()):
        if item.get("category") in ("food", "drink"):
            return iid
    return sorted(content.items)[0] if content.items else "item.food"


def cheapest_food(content, location: str, state: WorldState, min_stock: int = 5) -> str | None:
    """最便宜的可用食物；min_stock 避免多个 NPC 同时抢最后一份库存。"""
    candidates = []
    for biz in state.business_at(location):
        for iid, qty in biz.stock.items():
            if qty < min_stock:
                continue
            it = content.items.get(iid)
            if it and it.get("category") in ("food", "drink"):
                candidates.append((content.item_price(iid), iid))
    if not candidates:
        # 库存很浅时退回任意 >0，保证不因阈值直接饿死
        for biz in state.business_at(location):
            for iid, qty in biz.stock.items():
                if qty <= 0:
                    continue
                it = content.items.get(iid)
                if it and it.get("category") in ("food", "drink"):
                    candidates.append((content.item_price(iid), iid))
    return sorted(candidates)[0][1] if candidates else None


def sell_price(content, item: str) -> int:
    ratio = float(content.economy.get("sell_ratio", 0.6))
    return max(1, int(round(content.item_price(item) * ratio)))


def sellable_items(content, ent: Entity) -> list[str]:
    return sorted(
        iid
        for iid, qty in ent.inventory.items()
        if qty > 0 and iid in content.items and content.items[iid].get("category") != "service"
    )


def employer_of(state: WorldState, ent: Entity) -> str | None:
    if ent.employed_at and ent.employed_at in state.businesses:
        return ent.employed_at
    if ent.business and ent.business in state.businesses:
        return ent.business
    biz = first_business_at(state, ent.location)
    return biz.id if biz else None


def skill_for(content, role: str) -> str:
    table = content.economy.get("skill_by_role") or {}
    return str(table.get(role, role))


def max_skill(ent: Entity) -> float:
    return max(ent.skills.values()) if ent.skills else 0.0


def path_between(state: WorldState, start: str, goal: str) -> list[str]:
    """BFS 最短路径（不含起点，含终点）。出口排序保证确定性。"""
    if start == goal:
        return []
    q = deque([(start, [])])
    seen = {start}
    while q:
        cur, path = q.popleft()
        for nxt in sorted(state.locations.get(cur, {}).get("exits", [])):
            if nxt in seen:
                continue
            npath = path + [nxt]
            if nxt == goal:
                return npath
            seen.add(nxt)
            q.append((nxt, npath))
    return []


def nearest_location(state: WorldState, start: str, wanted: set[str]) -> str | None:
    """BFS 到 wanted 中最近的一个；同距离按地点 id 排序。"""
    if start in wanted:
        return start
    q = deque([start])
    seen = {start}
    while q:
        cur = q.popleft()
        for nxt in sorted(state.locations.get(cur, {}).get("exits", [])):
            if nxt in seen:
                continue
            if nxt in wanted:
                return nxt
            seen.add(nxt)
            q.append(nxt)
    return None


def transaction_tax(content, amount: int) -> int:
    rate = float((content.economy.get("taxes") or {}).get("transaction", 0.0))
    return int(amount * rate)


def income_tax(content, amount: int) -> int:
    rate = float((content.economy.get("taxes") or {}).get("income", 0.0))
    return int(amount * rate)


def role_allowed(content, verb: str, role: str) -> bool:
    allowed = content.allowed_roles(verb)
    if not allowed or "*" in allowed:
        return True
    return role in allowed


def living_cost(content, role: str) -> dict[str, int]:
    table = (content.economy.get("living_costs") or {}).get("by_role") or {}
    row = table.get(role) or {}
    return {str(k): int(v) for k, v in row.items()}


def escort_business(state: WorldState, location: str | None = None):
    for biz in sorted(state.businesses.values(), key=lambda b: b.id):
        if biz.kind == "service_escort" and (location is None or biz.location == location):
            return biz
    return None


def escort_agency_location(state: WorldState) -> str | None:
    biz = escort_business(state)
    return biz.location if biz else None


def organization_at(state: WorldState, location: str):
    for fac in sorted(state.factions.values(), key=lambda f: f.id):
        if fac.location == location and fac.founder == "":
            return fac
    return None


def join_target_location(state: WorldState, content) -> str | None:
    """join_sect 的目标组织所在地：优先具备抄经服务的地点（内容包的书院讲会）。"""
    for lid in sorted(state.locations):
        services = set(state.locations[lid].get("services") or [])
        if services & {"service_copying", "service_incense"}:
            return lid
    return sorted(state.locations)[0] if state.locations else None


def payment_with_shortfall(
    state: WorldState,
    payer_party: str,
    payee_party: str,
    amount: int,
    *,
    debt_id: str,
    created_tick: int,
    note: str,
    long_term: bool = False,
    transfer_reason: str | None = None,
) -> list[Effect]:
    """付款方余额不足时：能付多少付多少，余额记欠账（不增发）。

    payer_party / payee_party 是实体、商业体、门派或账户 id；账户由 party_account 解析。

    transfer_reason 用于把“实际支付的现金”与“未付欠账”分开：
    欠账的 note 仍然是 unpaid_wage，但成功的转账 reason 必须是 wage，
    否则现金流审计会把已支付工资误读成未付工资（金额差一个数量级）。
    """
    if amount <= 0:
        return []
    payer = party_account(state, payer_party)
    payee = party_account(state, payee_party)
    bal = state.account_balance(payer)
    paid = min(bal, amount)
    effects: list[Effect] = []
    if paid > 0:
        effects.append(
            Effect(
                "transfer",
                {"src": payer, "dst": payee, "amount": paid, "reason": transfer_reason or note},
            )
        )
    short = amount - paid
    if short > 0:
        effects.append(
            Effect(
                "debt_create",
                {
                    "id": debt_id,
                    "creditor": payee_party,
                    "debtor": payer_party,
                    "amount": short,
                    "long_term": long_term,
                    "created_tick": created_tick,
                    "note": note,
                },
            )
        )
    return effects
