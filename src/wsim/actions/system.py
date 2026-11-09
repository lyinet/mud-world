"""系统动作：每日计划、体力恢复、生活成本、债务结算、店铺月税。

系统动作不计入 K3 的 NPC 行为多样性口径（source=system）。
不铸币：除 genesis 与外部镖局客户外，本模块只做转移/销毁。
"""
from __future__ import annotations

from ..core.clock import TICKS_PER_DAY, day_of, tick_for
from ..core.models import Effect, Intent, Reject
from ..world.effects import party_account
from ..world.state import WorldState
from .common import (
    active_goal,
    alive,
    cheapest_food,
    escort_business,
    food_locations,
    founding_members,
    goal_kind,
    goal_target,
    income_tax,
    join_target_location,
    living_cost,
    max_skill,
    memory,
    nearest_location,
    organization_at,
    path_between,
    payment_with_shortfall,
    require_entity,
    role_allowed,
    sellable_items,
    sell_price,
    skill_for,
    treasury_guard_active,
    wallet,
)
from .registry import Action


def _monthly_upkeep(content, kind: str) -> int:
    table = (content.economy.get("shop_opening") or {}).get("monthly_upkeep") or {}
    if kind in table:
        return int(table[kind])
    return int((content.economy.get("taxes") or {}).get("shop_monthly_tax", 60))


class SystemPlanAction(Action):
    """P1 日程驱动遗留实现，**不再注册**（P2-B 起由 sim.intent.Planner 取代）。

    保留代码仅用于对照旧 run 的效应语义；headless 不会再提交 system_plan。
    """

    verb = "system_plan"

    def __init__(self, content, seed: int):
        self.content = content
        self.seed = seed
        self._npc_goals = {
            npc["id"]: sorted(
                npc.get("goals") or [],
                key=lambda g: (float(g.get("priority", 9)), str(g.get("id", ""))),
            )
            for npc in content.npcs
        }

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = state.entities.get(intent.actor)
        if ent is None or ent.status != "alive":
            return ["unknown_actor"]
        return []

    # ---------- schedule helpers ----------

    def _sched(self, effects, tick, ent_id, verb, args, idx, created_tick):
        effects.append(
            Effect(
                "schedule_add",
                {
                    "id": f"plan:{day_of(tick)}:{ent_id}:{idx}",
                    "tick": tick,
                    "actor": ent_id,
                    "verb": verb,
                    "args": args,
                    "created_tick": created_tick,
                },
            )
        )
        return idx + 1

    def _moves(self, state, effects, ent_id, start, dest, tick, idx, created_tick):
        loc = start
        for hop in path_between(state, loc, dest):
            idx = self._sched(effects, tick, ent_id, "move", {"to": hop}, idx, created_tick)
            tick += 1
            loc = hop
        return loc, tick, idx

    # ---------- decisions ----------

    def _next_goal(self, state: WorldState, ent, tick: int):
        existing = {g.content_goal_id for g in state.goals.values() if g.npc == ent.id}
        for g in self._npc_goals.get(ent.id, []):
            if g["id"] in existing:
                continue
            kind = goal_kind(g["type"])
            return (
                Effect(
                    "goal_create",
                    {
                        "id": f"{ent.id}:{g['id']}",
                        "npc": ent.id,
                        "kind": kind,
                        "target": goal_target(self.content, kind),
                        "created_tick": tick,
                        "source": "npc",
                        "content_goal_id": g["id"],
                        "content_type": g["type"],
                    },
                ),
                g,
            )
        return None, None

    def _work_location(self, state: WorldState, ent) -> str:
        if ent.employed_at and ent.employed_at in state.businesses:
            return state.businesses[ent.employed_at].location
        return ent.home or ent.location

    def _expected_net_wage(self, state: WorldState, ent) -> int:
        if not ent.employed_at or ent.employed_at not in state.businesses:
            return 0
        biz = state.businesses[ent.employed_at]
        gross = max(1, int(round(self.content.wage_for(ent.role))))
        received = min(gross, state.account_balance(biz.cash_account))
        return max(0, received - income_tax(self.content, received))

    def _dinner_location(self, state: WorldState) -> str:
        foods = set(food_locations(state, self.content))
        if not foods:
            return sorted(state.locations)[0]
        def stock_score(lid: str) -> tuple[int, str]:
            total = 0
            for biz in state.business_at(lid):
                for iid, qty in biz.stock.items():
                    it = self.content.items.get(iid)
                    if it and it.get("category") in ("food", "drink"):
                        total += qty
            return (-total, lid)
        return sorted(foods, key=stock_score)[0]

    def _peer(self, state: WorldState, ent, money_delta: int = 0, min_affection: float = -20.0):
        ids = sorted(e.id for e in state.entities.values() if e.id != ent.id and e.status == "alive")
        if not ids:
            return None
        start = sum(ord(c) for c in ent.id) % len(ids)
        for off in range(len(ids)):
            tid = ids[(start + off) % len(ids)]
            other = state.entities[tid]
            if state.affection_between(ent.id, tid) < min_affection:
                continue
            if money_delta > 0 and state.account_balance(wallet(tid)) < money_delta:
                continue
            return other
        return None

    def _apprentice_target(self, state: WorldState, ent):
        if ent.master is not None:
            return None
        ids = sorted(e.id for e in state.entities.values() if e.id != ent.id and e.status == "alive")
        for tid in ids:
            other = state.entities[tid]
            if max_skill(other) < 0.5 or max_skill(other) < max_skill(ent) + 0.1:
                continue
            cur = other
            seen = {ent.id}
            ok = True
            while cur.master:
                if cur.master in seen:
                    ok = False
                    break
                seen.add(cur.master)
                nxt = state.entities.get(cur.master)
                if nxt is None:
                    break
                cur = nxt
            if ok:
                return other
        return None

    def _marriage_target(self, state: WorldState, ent):
        if ent.spouse is not None:
            return None
        for tid in sorted(e.id for e in state.entities.values() if e.id != ent.id and e.status == "alive"):
            other = state.entities[tid]
            if other.spouse is not None:
                continue
            if any(
                r.kind == "marriage" and r.status == "active" and ent.id in (r.a, r.b)
                for r in state.relations.values()
            ):
                continue
            if state.affection_between(ent.id, tid) >= 60.0:
                return other
        return None

    def _reputation(self, state: WorldState, npc: str) -> float:
        return sum(
            max(0.0, rel.affection)
            for rel in state.relations.values()
            if rel.a == npc and rel.kind in ("friend", "family", "master", "disciple", "ally", "marriage")
        )

    # ---------- main plan ----------

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        base = day_of(intent.tick) * TICKS_PER_DAY
        effects: list[Effect] = []
        idx = 0

        goal = active_goal(state, ent.id)
        content_type = goal.content_type if goal is not None else ""
        if goal is None:
            eff, g = self._next_goal(state, ent, intent.tick)
            if eff is not None:
                effects.append(eff)
                content_type = g["type"]
                goal = None  # 本日按新目标安排；goal_id 在事务提交后生效

        home = ent.home or ent.location
        work_loc = self._work_location(state, ent)
        loc = ent.location

        # 06:00 通勤
        loc, tick, idx = self._moves(state, effects, ent.id, loc, work_loc, base + 36, idx, intent.tick)
        # 07:30 上工
        work_planned = False
        if (
            ent.employed_at
            and ent.employed_at in state.businesses
            and state.businesses[ent.employed_at].location == loc
        ):
            idx = self._sched(effects, base + 45, ent.id, "work", {"at": loc, "hours": 8}, idx, intent.tick)
            work_planned = True

        available = state.account_balance(wallet(ent.id)) + (self._expected_net_wage(state, ent) if work_planned else 0)

        # 11:00 去吃饭
        foods = set(food_locations(state, self.content))
        food_loc = nearest_location(state, loc, foods) if foods else loc
        loc, tick, idx = self._moves(state, effects, ent.id, loc, food_loc, base + 66, idx, intent.tick)
        lunch = cheapest_food(self.content, loc, state)
        lunch_cost = 0
        if lunch:
            lunch_cost = self.content.item_price(lunch, state) + int(self.content.item_price(lunch, state) * float((self.content.economy.get("taxes") or {}).get("transaction", 0)))
        if lunch and available >= lunch_cost:
            idx = self._sched(effects, base + 72, ent.id, "consume", {"item": lunch, "qty": 1}, idx, intent.tick)
            available -= lunch_cost

        # 15:30 目标动作
        acted = False
        if content_type in ("advance_skill",):
            idx = self._sched(effects, base + 93, ent.id, "cultivate", {"skill": skill_for(self.content, ent.role), "hours": 4}, idx, intent.tick)
            acted = True
        elif content_type in ("earn_money",):
            items = [i for i in sellable_items(self.content, ent) if ent.inventory.get(i, 0) >= 2]
            if items:
                item = items[0]
                price = sell_price(self.content, item, state)
                if any(state.account_balance(b.cash_account) >= price for b in state.business_at(loc)):
                    idx = self._sched(effects, base + 93, ent.id, "sell", {"item": item, "qty": 1, "to": loc}, idx, intent.tick)
                    acted = True
            if not acted:
                idx = self._sched(effects, base + 93, ent.id, "cultivate", {"skill": skill_for(self.content, ent.role), "hours": 2}, idx, intent.tick)
                acted = True
        elif content_type == "build_shop":
            if ent.business is None:
                shop = self.content.economy.get("shop_opening") or {}
                bal = state.account_balance(wallet(ent.id))
                kind = "storefront" if bal >= int(shop.get("storefront", 10**9)) else "stall"
                cost = int(shop.get(kind, 10**9))
                if bal >= cost:
                    idx = self._sched(
                        effects,
                        base + 93,
                        ent.id,
                        "open_shop",
                        {"at": loc, "shop_type": kind, "name": f"{ent.name}的{ent.role}铺"},
                        idx,
                        intent.tick,
                    )
                    acted = True
            if not acted:
                idx = self._sched(effects, base + 93, ent.id, "cultivate", {"skill": skill_for(self.content, ent.role), "hours": 2}, idx, intent.tick)
                acted = True
        elif content_type in ("gain_status",):
            org = organization_at(state, loc)
            if org is None or ent.faction is not None:
                idx = self._sched(effects, base + 93, ent.id, "cultivate", {"skill": skill_for(self.content, ent.role), "hours": 2}, idx, intent.tick)
            else:
                dues = 20 if state.account_balance(wallet(ent.id)) >= 20 else 0
                idx = self._sched(effects, base + 93, ent.id, "join_sect", {"target": loc, "dues": dues}, idx, intent.tick)
            acted = True
        elif content_type == "join_sect":
            target_loc = join_target_location(state, self.content) or loc
            loc, tick, idx = self._moves(state, effects, ent.id, loc, target_loc, base + 84, idx, intent.tick)
            dues = 20 if state.account_balance(wallet(ent.id)) >= 20 else 0
            idx = self._sched(effects, base + 93, ent.id, "join_sect", {"target": target_loc, "dues": dues}, idx, intent.tick)
            acted = True
        elif content_type == "found_sect":
            sect = self.content.economy.get("sect") or {}
            fee = int(sect.get("registration_fee", 500))
            need = max(fee, int(sect.get("min_money", 2000)))
            min_members = int(sect.get("min_members", 5))
            members = founding_members(state, ent, float(sect.get("min_relation_value", 30)))
            if (
                state.account_balance(wallet(ent.id)) >= need
                and self._reputation(state, ent.id) >= float(sect.get("min_reputation", 40))
                and (min_members <= 0 or len(members) >= min_members)
                and not (ent.faction is not None and str(ent.faction).startswith("sect:"))
            ):
                idx = self._sched(
                    effects,
                    base + 93,
                    ent.id,
                    "found_sect",
                    {"name": f"{ent.name}会", "at": loc, "doctrine": "自守"},
                    idx,
                    intent.tick,
                )
                acted = True
            if not acted:
                idx = self._sched(effects, base + 93, ent.id, "cultivate", {"skill": skill_for(self.content, ent.role), "hours": 2}, idx, intent.tick)
                acted = True

        # 职业性押镖（非目标驱动，用于规模与多样性）
        if not acted and role_allowed(self.content, "escort", ent.role) and ent.escort_id is None:
            agency = escort_business(state)
            cfg = self.content.economy.get("escort") or {}
            fee = int(cfg.get("base_fee", 180))
            if agency is not None and state.account_balance(agency.cash_account) >= fee and rng.chance(0.3):
                dest = sorted(state.locations[agency.location].get("exits", []))[0]
                loc, tick, idx = self._moves(state, effects, ent.id, loc, agency.location, base + 84, idx, intent.tick)
                idx = self._sched(
                    effects,
                    base + 93,
                    ent.id,
                    "escort",
                    {"route": [agency.location, dest], "cargo_value": 150, "fee": fee},
                    idx,
                    intent.tick,
                )
                acted = True

        # 16:30 去晚饭地点
        dinner_loc = self._dinner_location(state)
        loc, tick, idx = self._moves(state, effects, ent.id, loc, dinner_loc, base + 99, idx, intent.tick)
        dinner = cheapest_food(self.content, loc, state)
        dinner_cost = 0
        if dinner:
            dinner_cost = self.content.item_price(dinner, state) + int(self.content.item_price(dinner, state) * float((self.content.economy.get("taxes") or {}).get("transaction", 0)))
        if dinner and available >= dinner_cost:
            idx = self._sched(effects, base + 108, ent.id, "consume", {"item": dinner, "qty": 1}, idx, intent.tick)
            available -= dinner_cost

        # 18:10 社交/交易
        peer = self._peer(state, ent, money_delta=0)
        if peer is not None and content_type in ("build_relation", "earn_money"):
            offer = []
            if ent.inventory:
                for item in sorted(ent.inventory):
                    if ent.inventory[item] >= 1 and self.content.items.get(item, {}).get("category") != "service":
                        offer = [{"item": item, "qty": 1}]
                        break
            delta = 5 if (content_type == "earn_money" and state.account_balance(wallet(peer.id)) >= 5) else 0
            idx = self._sched(
                effects,
                base + 109,
                ent.id,
                "trade",
                {"target": peer.id, "offer": offer, "want": [], "money_delta": delta},
                idx,
                intent.tick,
            )

        # 拜师（build_relation 目标且尚无师父）
        if content_type == "build_relation" and ent.master is None:
            master = self._apprentice_target(state, ent)
            if master is not None:
                idx = self._sched(effects, base + 110, ent.id, "apprentice", {"target": master.id, "duration_days": 30}, idx, intent.tick)

        # 成婚（关系足够且双方未婚）
        spouse = self._marriage_target(state, ent)
        if spouse is not None and rng.chance(0.5):
            idx = self._sched(effects, base + 111, ent.id, "marry", {"target": spouse.id}, idx, intent.tick)

        # 随机额外消费（酒/服务），用于制造真实损耗
        if rng.chance(0.35):
            extra = [i for i in ("wine", "service_storytelling") if i in self.content.items]
            for item in extra:
                if any(b.stock.get(item, 0) >= 5 for b in state.business_at(loc)):
                    price = self.content.item_price(item, state) + int(self.content.item_price(item, state) * float((self.content.economy.get("taxes") or {}).get("transaction", 0)))
                    if available >= price:
                        idx = self._sched(effects, base + 110, ent.id, "consume", {"item": item, "qty": 1}, idx, intent.tick)
                        available -= price
                        break

        # 随机买入耐用品（wealth 目标下）
        if content_type == "earn_money" and rng.chance(0.2):
            candidates = [
                i
                for i, it in sorted(self.content.items.items())
                if it.get("category") in ("tool", "material") and any(b.stock.get(i, 0) >= 5 for b in state.business_at(loc))
            ]
            if candidates:
                item = candidates[sum(ord(c) for c in ent.id) % len(candidates)]
                price = self.content.item_price(item, state) + int(self.content.item_price(item, state) * float((self.content.economy.get("taxes") or {}).get("transaction", 0)))
                if available >= price:
                    idx = self._sched(effects, base + 109, ent.id, "buy", {"item": item, "qty": 1, "from": loc}, idx, intent.tick)

        # 18:45 回家
        loc, tick, idx = self._moves(state, effects, ent.id, loc, home, base + 112, idx, intent.tick)
        return effects


class SystemGoalAction(Action):
    """意图层写目标：由 GoalSelector 决定后经 Writer 落成 goal_create / set_field。

    内容目标 source=npc（计入 K1）；紧急目标 source=urgent（不计入内容目标分母）。
    """

    verb = "system_goal"

    def __init__(self, content):
        self.content = content

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = state.entities.get(intent.actor)
        if ent is None or ent.status != "alive":
            return ["unknown_actor"]
        if not str(intent.args.get("goal", "")):
            return ["missing_goal"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        args = intent.args
        goal_id = str(args.get("goal", ""))
        effects: list[Effect] = []
        abandon = str(args.get("abandon", ""))
        if abandon and abandon in state.goals and abandon != goal_id:
            effects.append(Effect("goal_abandon", {"id": abandon}))
        goal_source = str(args.get("goal_source", "content"))
        payload = {
            "id": goal_id,
            "npc": ent.id,
            "kind": str(args.get("kind", "wealth")),
            "target": float(args.get("target", 1.0)),
            "created_tick": intent.tick,
            # A3：只有 urgent 目标不进 K1 分母；content/model 都是内容目标（source=npc）。
            # 自提案目标 source=self，不进内容目标分母，也便于生命周期审计识别。
            "source": (
                "urgent"
                if goal_source == "urgent"
                else ("self" if goal_source == "self" else "npc")
            ),
            "content_goal_id": str(args.get("content_goal_id", "")),
            "content_type": str(args.get("content_type", "")),
            "secondary": bool(args.get("secondary", False)),
        }
        # 次级目标（自提案）：只建目标，不切换 entity.goal_id，因此绝不顶掉
        # 既有的内容主线；它的进度由带 `_goal_id` 的独立额度动作单独记。
        secondary = bool(args.get("secondary", False))
        if goal_id in state.goals:
            if not secondary:
                effects.append(Effect("set_field", {"entity": ent.id, "field": "goal_id", "value": goal_id}))
        else:
            effects.append(Effect("goal_create", payload))
        reason = str(args.get("reason", "")) or intent.reason
        if reason:
            effects.append(
                Effect(
                    "memory_add",
                    {"npc": ent.id, "tick": intent.tick, "text": reason, "importance": 0.4},
                )
            )
        return effects


class SystemRecoverAction(Action):
    verb = "system_recover"

    def __init__(self, content):
        self.content = content

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        amount = float(self.content.economy.get("stamina_recover", 40))
        return [Effect("stamina", {"entity": e.id, "delta": amount}) for e in state.alive_entities()]


# 1 tick = 600 游戏秒；原版 1 心跳 = 365 游戏秒（natured.lpc:8），默认 1000ms。
# 因此 1 tick = 600/365 心跳 → 单位 1/10000 心跳的信用为 16438。（见 eff-qi.spec.md §2）
HEAL_CREDIT_PER_TICK = 16438
# 原版 `char.lpc:106-109`：`tick = 5 + random(10)`，下一次触发间隔 6..15 心跳。
HEAL_CD_MIN = 6
HEAL_CD_SPAN = 10


class SystemHealAction(Action):
    """原版 `heal_up`：被动心跳恢复 `eff_qi`/`qi`，并驱动昏迷实体苏醒。

    只在 `combat.eff_qi` 打开时由 scheduler 发出；每次事件逐实体结算。
    """

    verb = "system_heal"

    def __init__(self, content=None, jing: bool = False):
        self.content = content
        self.jing = bool(jing)

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        effects: list[Effect] = []
        for ent in sorted(state.entities.values(), key=lambda e: e.id):
            max_hp = int(ent.max_hp)
            if max_hp <= 0:
                continue
            # 原版 `char.lpc:65-78` 心跳开头：`eff_jing<0` → die()；`jing<0` → 昏迷。
            # 这里的死亡/昏迷与 qi 无关（qi 可以仍满），事件显式标 `cause="jing"`。
            if self.jing and int(ent.max_jing) > 0 and ent.status == "alive":
                if int(ent.eff_jing) < 0:
                    effects.append(Effect("entity_status", {
                        "entity": ent.id,
                        "status": "dead",
                        "tick": int(intent.tick),
                        "location": str(ent.location),
                        "cause": "jing",
                        "remove_all_enemy": True,
                    }))
                    continue
                if int(ent.jing) < 0:
                    con = int((getattr(ent, "attrs", None) or {}).get("con", 20))
                    span = max(1, 100 - con)
                    revive_ticks = max(
                        1, int(round((30 + rng.randint(0, span - 1)) * 0.6083))
                    )
                    if int(ent.hp) != 0:
                        effects.append(Effect("hp_set", {"entity": ent.id, "value": 0}))
                    effects.append(Effect("jing_set", {"entity": ent.id, "value": 0}))
                    effects.append(Effect("entity_status", {
                        "entity": ent.id,
                        "status": "unconcious",
                        "revive_tick": int(intent.tick) + revive_ticks,
                        "cause": "jing",
                    }))
                    continue
            if ent.status == "unconcious":
                # 原版 `revive()` 只恢复行动力：qi 仍是 0，eff_qi 不变。
                if ent.revive_tick and int(intent.tick) >= int(ent.revive_tick):
                    effects.append(
                        Effect("entity_status", {"entity": ent.id, "status": "alive"})
                    )
                continue
            if ent.status != "alive":
                continue
            eff = int(ent.eff_hp) if int(ent.eff_hp) >= -1 else max_hp
            hp = int(ent.hp)
            use_jing = self.jing and int(ent.max_jing) > 0
            eff_jing = (
                int(ent.eff_jing) if int(ent.eff_jing) >= -1 else int(ent.max_jing)
            )
            jing = int(ent.jing)
            jing_healthy = (not use_jing) or (
                jing >= eff_jing and eff_jing >= int(ent.max_jing)
            )
            if hp >= eff and eff >= max_hp and jing_healthy:
                # 完全健康：清掉残留计数（只在确实非零时发事件）。
                if ent.heal_credit or ent.heal_cd:
                    data = {
                        "entity": ent.id,
                        "hp": hp,
                        "eff": eff,
                        "heal_credit": 0,
                        "heal_cd": 0,
                    }
                    if use_jing:
                        data["jing"] = jing
                        data["eff_jing"] = eff_jing
                    effects.append(Effect("heal_apply", data))
                continue
            con = int((getattr(ent, "attrs", None) or {}).get("con", 20))
            credit = int(ent.heal_credit) + HEAL_CREDIT_PER_TICK
            cd = int(ent.heal_cd) or rng.randint(HEAL_CD_MIN, HEAL_CD_MIN + HEAL_CD_SPAN - 1)
            while credit >= cd * 10000:
                credit -= cd * 10000
                hp = min(hp + con // 3, eff)
                if hp >= eff:
                    hp = eff
                    if eff < max_hp:
                        eff += 1
                # 原版 `heal_up`（damage.lpc:360-367）：同一次触发也给 jing 加 con/3，
                # 封顶后 `eff_jing += 1`（每 tick 最多 +1）。
                if use_jing:
                    jing = min(jing + con // 3, eff_jing)
                    if jing >= eff_jing:
                        jing = eff_jing
                        if eff_jing < int(ent.max_jing):
                            eff_jing += 1
                cd = rng.randint(HEAL_CD_MIN, HEAL_CD_MIN + HEAL_CD_SPAN - 1)
            # 即使本 tick 没触发，也要把累积信用写回，否则永远凑不满一次 heal_up。
            data = {
                "entity": ent.id,
                "hp": hp,
                "eff": eff,
                "heal_credit": credit,
                "heal_cd": cd,
            }
            if use_jing:
                data["jing"] = jing
                data["eff_jing"] = eff_jing
            effects.append(Effect("heal_apply", data))
        return effects


class SystemLivingAction(Action):
    verb = "system_living"

    def __init__(self, content):
        self.content = content

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = state.entities.get(intent.actor)
        if ent is None or ent.status != "alive":
            return ["unknown_actor"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        row = living_cost(self.content, ent.role)
        home = ent.home or ent.location
        businesses = state.business_at(home)
        org = organization_at(state, home)
        housing = next((b for b in businesses if b.kind == "service_room"), businesses[0] if businesses else None)
        upkeep = businesses[1] if len(businesses) > 1 else (businesses[0] if businesses else None)
        dues_party = org.id if org is not None else (businesses[0].id if businesses else None)
        if dues_party is None:
            return []
        payees = {
            "housing": housing.id if housing is not None else dues_party,
            "upkeep": upkeep.id if upkeep is not None else dues_party,
            "dues": dues_party,
        }
        # NPC 日：共享世界由日界事件 args["day"] 显式给出；headless 时等于
        # day_of(intent.tick)，旧哈希不变。
        day = int(intent.args.get("day", day_of(intent.tick)))
        effects: list[Effect] = []
        for kind in ("housing", "upkeep", "dues"):
            amount = int(row.get(kind, 0))
            if amount <= 0:
                continue
            effects.extend(
                payment_with_shortfall(
                    state,
                    ent.id,
                    payees[kind],
                    amount,
                    debt_id=f"debt:living:{ent.id}:{day}:{kind}",
                    created_tick=intent.tick,
                    note=f"living_{kind}",
                )
            )
        return effects


class SystemDebtsAction(Action):
    verb = "system_debts"

    def __init__(self, content):
        self.content = content

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        effects: list[Effect] = []
        for did in sorted(state.debts):
            debt = state.debts[did]
            if debt.status != "active" or debt.long_term:
                continue
            if debt.note == "initial_debt":
                continue
            try:
                debtor_acc = party_account(state, debt.debtor)
            except Exception:
                continue
            bal = state.account_balance(debtor_acc)
            if bal < debt.amount:
                continue
            # 只自动清偿工资欠条/镖银；生活欠账需留出生活缓冲
            if debt.note.startswith("living_") and debt.debtor in state.entities:
                if bal < debt.amount + 200:
                    continue
            effects.append(Effect("debt_settle", {"id": did, "amount": debt.amount}))
        return effects


class SystemSavingsTaxAction(Action):
    """储蓄税：对超过阈值的闲钱按比例销毁，抑制囤积（月度）。"""

    verb = "system_savings_tax"

    def __init__(self, content):
        self.content = content

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        taxes = self.content.economy.get("taxes") or {}
        rate = float(taxes.get("savings_rate", 0.0))
        threshold = int(taxes.get("savings_threshold", 0))
        if rate <= 0:
            return []
        effects: list[Effect] = []
        for eid in sorted(state.entities):
            ent = state.entities[eid]
            if ent.status != "alive":
                continue
            bal = state.account_balance(wallet(eid))
            excess = bal - threshold
            if excess <= 0:
                continue
            amount = int(round(excess * rate))
            if amount > 0:
                effects.append(
                    Effect("burn", {"account": wallet(eid), "amount": amount, "reason": "savings_tax"})
                )
        return effects


class SystemTreasuryGuardAction(Action):
    """金库保底告警事件：进入/退出 treasury_guard 只做日志，不改变任何余额。

    由 sim.headless 在日界按 `treasury:region` 实际余额判定后提交；
    apply 必须无副作用，保证回放状态与原始 run 一致。
    """

    verb = "system_treasury_guard"

    def __init__(self, content):
        self.content = content

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        if not isinstance(intent.args.get("active"), bool):
            return ["missing_arg:active"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        return []


class SystemBudgetAction(Action):
    """LLM 预算审计：超过上限降级 Mock 时写一条只读事件，不改变世界状态。

    由 sim.headless 在日终按 BudgetGuard.take_audit(day) 提交；apply 必须无副作用，
    保证回放状态与原始 run 一致。
    """

    verb = "system_budget"

    def __init__(self, content):
        self.content = content

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        if not isinstance(intent.args.get("mocked", 0), int):
            return ["bad_arg:mocked"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        return []


class SystemShopDividendAction(Action):
    """私店分红：把当月利润的一部分转给店主（月度）。

    treasury_guard 期间暂停：这是金库为数不多的非必要流出，优先保工资与镖单。
    """

    verb = "system_shop_dividend"

    def __init__(self, content):
        self.content = content

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        rate = float((self.content.economy.get("dividends") or {}).get("rate", 0.0))
        if rate <= 0 or treasury_guard_active(state, self.content):
            return []
        effects: list[Effect] = []
        for bid in sorted(state.businesses):
            biz = state.businesses[bid]
            if biz.owner is None or biz.owner not in state.entities:
                continue
            cash = state.account_balance(biz.cash_account)
            if cash <= 100:
                continue
            amount = int(round(cash * rate))
            if amount <= 0:
                continue
            effects.append(
                Effect(
                    "transfer",
                    {"src": biz.cash_account, "dst": wallet(biz.owner), "amount": amount,
                     "reason": "shop_dividend"},
                )
            )
        return effects


class SystemShopTaxAction(Action):
    verb = "system_shop_tax"

    def __init__(self, content):
        self.content = content

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        effects: list[Effect] = []
        for biz in sorted(state.businesses.values(), key=lambda b: b.id):
            if biz.owner is None:
                continue
            tax = _monthly_upkeep(self.content, biz.kind)
            if tax > 0 and state.account_balance(biz.cash_account) >= tax:
                effects.append(Effect("burn", {"account": biz.cash_account, "amount": tax, "reason": "shop_monthly_tax"}))
        return effects


class SystemShopSettleAction(Action):
    """玩家店铺日结（独立事件，不与 system_debts 混账）。

    流水由 `Business.day_revenue/day_restock`（transfer 效果维护）读取，
    状态变更全部走 Effect：工资不足记欠账（不增发），日摊税直接销毁，
    雇工产出计入 items_created；结算成功后再清零当日计数。
    没有玩家店铺时拒绝，保证旧世界事件序列逐字节不变。
    """

    verb = "system_shop_settle"

    def __init__(self, content):
        self.content = content

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        from .business import has_player_shop

        if not has_player_shop(state):
            return ["no_player_shop"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        from .business import (
            build_shop_settlements,
            shop_day_reset_effects,
            shop_settlement_effects,
        )

        clock_arg = getattr(intent, "clock_tick", None)
        if clock_arg is None:
            # 无自走：沿用世界 tick 口径，逐字节兼容旧日结/出勤/历史行。
            rows = build_shop_settlements(self.content, state, int(intent.tick))
        else:
            # 有 NPC 时钟：出勤判定与账本日都读同一个 NPC 日（args["day"] = 刚结束的
            # NPC 日）；显示（7 日账 day）与判定（出勤/工资）同源。
            day = int(intent.args.get("day", int(clock_arg) // TICKS_PER_DAY))
            rows = build_shop_settlements(
                self.content, state, int(intent.tick), ledger_day=day, clock_day=day
            )
        effects = shop_settlement_effects(self.content, state, rows, int(intent.tick))
        effects.extend(shop_day_reset_effects(state, rows))
        return effects

