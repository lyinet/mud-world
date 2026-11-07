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
    "escort": "escort",
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

    P2-B 起，创始成员只认**真实招揽动作**（`recruit`）建立的 sworn 关系与弟子，
    不再把开局就有的 friend/family/ally 直接算进人数（避免“一次性拉人”）。
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
        if rel.kind != "sworn":
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


def max_encumbrance(ent: Entity) -> int:
    """原版负重上限：base_str*5000 + (query_str-base_str)*1000。

    `query_str` 的提升按有效武功等级/10 估算；旧内容实体没有 attrs 时返回 0，
    表示“不启用负重”，保持旧世界行为。
    """
    base = int((ent.attrs or {}).get("str", 0) or 0)
    if base <= 0:
        return 0
    improve = 0
    for level in (ent.skill_levels or {}).values():
        try:
            improve = max(improve, int(level) // 10)
        except (TypeError, ValueError):
            continue
    return base * 5000 + improve * 1000


def carried_weight(content, ent: Entity) -> int:
    """背包 + 已装备物品的总重量；缺失 weight 按 0。"""
    items = getattr(content, "items", {}) or {}

    def weight_of(item: str) -> int:
        value = (items.get(item) or {}).get("weight")
        if isinstance(value, bool) or not isinstance(value, int):
            return 0
        return int(value)

    total = 0
    for item, qty in (ent.inventory or {}).items():
        try:
            total += weight_of(item) * int(qty)
        except (TypeError, ValueError):
            continue
    for item in (ent.equipped or {}).values():
        total += weight_of(item)
    return total


def overweight_reason(content, ent: Entity, added_weight: int) -> str:
    """超重时返回 `overloaded:<need>:<max>`，否则空串。"""
    limit = max_encumbrance(ent)
    if limit <= 0:
        return ""
    need = carried_weight(content, ent) + max(0, int(added_weight))
    if need <= limit:
        return ""
    return f"overloaded:{need}:{limit}"


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


def explicit_goal_id(intent) -> str:
    """计划器给独立额度动作打的标记：把进度只记到该目标。"""
    return str(intent.args.get("_goal_id") or "")


def goal_progress(
    state: WorldState,
    npc: str,
    kind: str,
    units: float,
    tick: int,
    goal_id: str = "",
) -> list[Effect]:
    """记目标进度。

    `goal_id` 显式指定时只认该目标，且类型必须匹配；不匹配就返回空，
    绝不回落到当前活跃目标。自提案目标的独立动作走这条路径，保证它
    不会把进度记到既有内容目标上（即不占既有目标的额度）。
    """
    if goal_id:
        goal = state.goals.get(goal_id)
        if goal is None or goal.status != "active" or goal.npc != npc or goal.kind != kind:
            return []
    else:
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


TREASURY_ACCOUNT = "treasury:region"


def treasury_balance(state: WorldState) -> int:
    """区域结算金库余额（创世时所有商业体共用该 cash_account）。"""
    return state.account_balance(TREASURY_ACCOUNT)


def treasury_guard_threshold(content) -> int:
    """保底线 × 倍数（默认 2000 × 1.2 = 2400 文）。"""
    cfg = content.economy.get("work") or {}
    reserve = int(cfg.get("employer_reserve", 2000))
    mult = float(cfg.get("treasury_guard_multiplier", 1.2))
    return int(round(reserve * mult))


def treasury_guard_active(state: WorldState, content) -> bool:
    """金库低于阈值即进入 treasury_guard：暂停非必要资本开支。"""
    return treasury_balance(state) < treasury_guard_threshold(content)


def first_business_at(state: WorldState, location: str) -> Business | None:
    biz = state.business_at(location)
    return biz[0] if biz else None


def is_player_business(biz: Business) -> bool:
    """玩家开的店：owner 形如 player:<account>，或 id 形如 shop:player:*。"""
    return str(getattr(biz, "owner", "") or "").startswith("player:") or str(biz.id).startswith("shop:player:")


def stable_slot(key: str, size: int) -> int:
    """FNV-1a 32 位稳定散列取模。

    不用内建 `hash()`：它受 PYTHONHASHSEED 影响，跨进程不一致；这里是纯函数，
    同 key 同 size 永远同结果（确定性护栏）。
    """
    if size <= 0:
        return 0
    h = 0x811C9DC5
    for ch in key:
        h = ((h ^ ord(ch)) * 0x01000193) & 0xFFFFFFFF
    return h % size


def player_market_active(state: WorldState) -> bool:
    """世界里是否已有玩家店铺（决定是否启用玩家同权/新采购口径）。"""
    return any(is_player_business(b) for b in state.businesses.values())


def business_order(state: WorldState, location: str, salt: str = "", day: int = 0) -> list[Business]:
    """店铺遍历顺序：同价同货的平局在同地点有玩家店时按稳定散列轮转。

    轮转键 `(地点, 买主, 世界日)`：不同买主、不同日子把不同店排到首位，
    N 家店长期各占约 1/N，平局公平且完全确定性（无 random / 无墙钟）。
    没有玩家店（或未给 salt）时直接返回原顺序，无玩家世界逐字节不变。
    """
    biz = state.business_at(location)
    if not salt or len(biz) < 2:
        return biz
    if not any(is_player_business(b) for b in biz):
        return biz
    slot = stable_slot(f"{location}|{salt}|{day}", len(biz))
    return biz[slot:] + biz[:slot]


def shop_price(content, state: WorldState, biz: Business | None, item: str) -> int:
    """当店售价：店主 `price_overrides` 优先，否则内容包基准价（含供需乘数）。

    只读，不写 state；无覆盖时与 `content.item_price(item, state)` 完全一致，
    保证旧世界/未定价店铺的价格逐字节不变。
    """
    if biz is not None:
        override = getattr(biz, "price_overrides", {}).get(item)
        if isinstance(override, int) and not isinstance(override, bool) and override > 0:
            return override
    return content.item_price(item, state)


def selling_shops(
    state: WorldState, location: str, item: str, salt: str = "", day: int = 0,
    content=None,
) -> list[Business]:
    """同地点所有有货的店，按“同货同价”公平顺序排列。

    传 `content` 时先筛掉更贵的卖家（同货不同价时买主选便宜的）；
    在同价卖家中只有出现玩家店时才按 `(地点, 物品, 买主, 世界日)` 轮转，
    让玩家店与 NPC 店长期各占约 1/N。缺省 `content=None` 保持旧顺序与旧轮转，
    无玩家世界逐字节不变。
    """
    sellers = [b for b in state.business_at(location) if b.stock.get(item, 0) > 0]
    if content is not None and sellers:
        cheapest = min(shop_price(content, state, b, item) for b in sellers)
        sellers = [b for b in sellers if shop_price(content, state, b, item) == cheapest]
    if not salt or len(sellers) < 2 or not any(is_player_business(b) for b in sellers):
        return sellers
    slot = stable_slot(f"{location}|{item}|{salt}|{day}", len(sellers))
    return sellers[slot:] + sellers[:slot]


def business_selling(
    state: WorldState, location: str, item: str, salt: str = "", day: int = 0,
    content=None,
) -> Business | None:
    sellers = selling_shops(state, location, item, salt, day, content=content)
    return sellers[0] if sellers else None


def business_with_cash(
    state: WorldState, location: str, need: int, salt: str = "", day: int = 0
) -> Business | None:
    for biz in business_order(state, location, salt, day):
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
                candidates.append((content.item_price(iid, state), iid))
    if not candidates:
        # 库存很浅时退回任意 >0，保证不因阈值直接饿死
        for biz in state.business_at(location):
            for iid, qty in biz.stock.items():
                if qty <= 0:
                    continue
                it = content.items.get(iid)
                if it and it.get("category") in ("food", "drink"):
                    candidates.append((content.item_price(iid, state), iid))
    return sorted(candidates)[0][1] if candidates else None


def sell_price(content, item: str, state: WorldState | None = None) -> int:
    ratio = float(content.economy.get("sell_ratio", 0.6))
    return max(1, int(round(content.item_price(item, state) * ratio)))


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


def reputation_points(state: WorldState, pid: str) -> float:
    """声望点：完成委托 + 关系好感 + 门派/产业身份。

    放在动作层，供接单前置校验与玩家成长线视图共用，避免两处公式漂移。
    """
    done = sum(
        1 for q in state.quests.values() if q.acceptor == pid and q.status == "completed"
    )
    relation = sum(
        max(0.0, float(rel.affection))
        for rel in state.relations.values()
        if rel.status == "active" and pid in (rel.a, rel.b)
    )
    ent = state.entities.get(pid)
    faction = 10.0 if ent is not None and ent.faction else 0.0
    business = 10.0 if ent is not None and ent.business else 0.0
    return 3.0 * done + 0.5 * relation + faction + business


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


def tax_split_effects(content, account: str, amount: int, reason: str = "transaction_tax") -> list[Effect]:
    """交易税分流：一部分转入区域金库（账户间转移），其余 burn。

    默认 50% 回流；比例由 `taxes.transaction_treasury_share` 配置。
    总额守恒：transfer + burn == amount，不产生 mint。
    """
    if amount <= 0:
        return []
    share = float((content.economy.get("taxes") or {}).get("transaction_treasury_share", 0.5))
    share = max(0.0, min(1.0, share))
    to_treasury = int(round(amount * share))
    burned = amount - to_treasury
    effects: list[Effect] = []
    if to_treasury > 0 and account != TREASURY_ACCOUNT:
        effects.append(
            Effect(
                "transfer",
                {
                    "src": account,
                    "dst": TREASURY_ACCOUNT,
                    "amount": to_treasury,
                    "reason": f"{reason}_treasury",
                },
            )
        )
    elif to_treasury > 0:
        burned += to_treasury  # 税源本身就是金库：只能销毁，避免自转
    if burned > 0:
        effects.append(Effect("burn", {"account": account, "amount": burned, "reason": reason}))
    return effects


def treasury_open_shop_buffer(content, cost: int, shop_type: str) -> int:
    """金库余额门槛：保底线 + 开店成本 + 该店 3 日预计工资。

    只用店铺自身岗位（`shop_opening.expected_staff`）× 对应职业日薪，
    不把全区域工资池算进来。
    """
    cfg = content.economy.get("shop_opening") or {}
    reserve = int((content.economy.get("work") or {}).get("employer_reserve", 2000))
    days = int(cfg.get("expected_wage_days", 3))
    roles = (cfg.get("expected_staff") or {}).get(shop_type) or []
    wage = sum(int(content.wage_for(str(role))) for role in roles)
    return reserve + int(cost) + days * wage


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
        existing = state.debts.get(debt_id)
        if existing is not None and existing.status == "active":
            effects.append(Effect("debt_increase", {"id": debt_id, "amount": short}))
        else:
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


def quest_required_units(quest) -> int:
    """任务目标动作的完成门槛（与交付校验共用，避免两处口径漂移）。

    work 按模板声明的工时累计；buy 按要买的件数累计；其余动作一次即完成。
    """
    args = getattr(quest, "args", None) or {}
    if quest.verb == "work":
        return max(1, int(args.get("hours") or 1))
    if quest.verb == "buy":
        return max(1, int(args.get("qty") or 1))
    return 1


def quest_progress_effects(
    state: WorldState,
    actor: str,
    verb: str,
    *,
    tick: int,
    location: str | None = None,
    target: str | None = None,
    item: str | None = None,
    qty: int = 0,
    hours: int = 0,
    money_delta: int | None = None,
) -> list[Effect]:
    """把“目标动作确实做过”记进世界状态（quest_step Effect）。

    只对玩家接单人写入：NPC 的任务链保持原样，从而无玩家世界的事件/状态哈希
    逐字节不变。单位口径见 `quest_required_units`。
    """
    if not str(actor).startswith("player:"):
        return []
    effects: list[Effect] = []
    for qid in sorted(state.quests):
        quest = state.quests[qid]
        if quest.status != "accepted" or quest.acceptor != actor:
            continue
        if quest.verb != verb:
            continue
        if verb == "trade":
            if not target or str(target) != str(quest.target_npc):
                continue
            if location is not None and quest.target_location and location != quest.target_location:
                continue
            want_money = int((quest.args or {}).get("money_delta") or 0)
            if want_money and int(money_delta or 0) != want_money:
                continue
            units = 1
        elif verb == "buy":
            want_item = str((quest.args or {}).get("item") or "")
            if not want_item or str(item or "") != want_item:
                continue
            units = max(0, int(qty))
        elif verb == "work":
            if location is not None and quest.target_location and location != quest.target_location:
                continue
            units = max(0, int(hours))
        elif verb == "escort":
            if location is not None and quest.target_location and location != quest.target_location:
                continue
            units = 1
        else:
            continue
        if units <= 0:
            continue
        if quest.progress_units >= quest_required_units(quest):
            continue
        effects.append(
            Effect(
                "quest_step",
                {
                    "id": qid,
                    "actor": actor,
                    "units": units,
                    "tick": int(tick),
                    "verb": verb,
                    "note": quest_progress_note(quest, verb, units),
                },
            )
        )
    return effects


def quest_progress_note(quest, verb: str, units: int) -> str:
    if verb == "work":
        return f"已上工 {units} 小时"
    if verb == "buy":
        return f"已购入目标物品 {units} 件"
    if verb == "escort":
        return "镖单已送达目标地点"
    if verb == "trade":
        return "已与目标人物当面完成交易"
    return f"已完成目标动作（{verb}）"
