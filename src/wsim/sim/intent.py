"""意图层：GoalSelector + Planner + MockReasoner v1。

约束（与 P2 框架层一致）：
- 确定性：只用 core.rng.PRNG（SplitMix64）或纯排序，不读墙钟、不用全局 random；
- 决策必须给出 reason（一句话动机），由 Writer 写入事件日志，审计可解释；
- 计划器在前置条件不满足时换路径（不是直接拒绝）；运行期被拒后由 replan 切换变体；
- 本模块只读 state，所有变更仍走 Intent -> Writer -> Effect。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from ..actions.common import (
    cheapest_food,
    escort_business,
    food_locations,
    founding_members,
    goal_kind,
    goal_target,
    join_target_location,
    max_skill,
    nearest_location,
    organization_at,
    path_between,
    sell_price,
    skill_for,
    wallet,
)
from ..core.clock import TICKS_PER_DAY, day_of
from ..core.models import Intent
from ..core.rng import PRNG
from ..world.state import WorldState

# 每个 LOD 档位每天允许的“目标动作”数（routine 通勤/上工/吃饭不计入深度）。
DEPTH_GOAL_ACTIONS = {
    "silent": 1,
    "background": 1,
    "active": 2,
    "spotlight": 3,
}

GOAL_LABEL = {
    "advance_skill": "精进手艺",
    "earn_money": "攒钱",
    "build_relation": "结交",
    "build_shop": "开铺子",
    "gain_status": "立身",
    "join_sect": "入会",
    "found_sect": "开宗立派",
    "escort": "押镖",
    "urgent_recover": "恢复体力",
    "urgent_debt": "还债",
    "urgent_money": "找现钱",
}

CONTENT_GOAL_TYPES = {
    "advance_skill",
    "earn_money",
    "build_relation",
    "build_shop",
    "gain_status",
    "join_sect",
    "found_sect",
}


@dataclass(frozen=True)
class GoalChoice:
    npc: str
    goal_id: str
    content_goal_id: str
    content_type: str
    kind: str
    target: float
    source: str  # content / urgent
    reason: str
    create: bool = True
    preempted: bool = False
    abandon: str = ""


class GoalSelector:
    """从内容包目标 + 当前状态选出当前主目标。

    抢占规则（只在紧急状态下允许）：
    1. 受伤：stamina < 25；
    2. 被追债：未结清欠条合计 >= 50 文；
    3. 破产：现钱 < 40 文。
    紧急目标按上述优先级选取；同一 NPC 两次紧急抢占至少间隔
    `preempt_cooldown_ticks`（默认 2 天），避免来回抖动。
    """

    def __init__(
        self,
        content,
        preempt_cooldown_ticks: int = TICKS_PER_DAY * 2,
        injured_stamina: float = 25.0,
        bankrupt_cash: int = 40,
        debt_pressure: int = 50,
    ):
        self.content = content
        self.preempt_cooldown_ticks = preempt_cooldown_ticks
        self.injured_stamina = injured_stamina
        self.bankrupt_cash = bankrupt_cash
        self.debt_pressure = debt_pressure
        self._urgent_last: dict[str, int] = {}
        self.changes: list[dict[str, Any]] = []
        self._content_goals = {
            str(n["id"]): sorted(
                n.get("goals") or [],
                key=lambda g: (float(g.get("priority", 9)), str(g.get("id", ""))),
            )
            for n in content.npcs
        }

    def _name(self, npc: str) -> str:
        row = next((n for n in self.content.npcs if str(n["id"]) == npc), None)
        return str(row.get("name", npc)) if row else npc

    def _trait(self, npc: str) -> str:
        row = next((n for n in self.content.npcs if str(n["id"]) == npc), None)
        traits = ((row or {}).get("personality") or {}).get("traits") or []
        return str(traits[0]) if traits else "务实"

    def _active(self, state: WorldState, npc: str):
        ent = state.entities.get(npc)
        if ent is None or not ent.goal_id:
            return None
        goal = state.goals.get(ent.goal_id)
        if goal is None or goal.status != "active":
            return None
        return goal

    def _next_content_goal(self, state: WorldState, npc: str):
        for g in self._content_goals.get(npc, []):
            gid = f"{npc}:{g['id']}"
            sg = state.goals.get(gid)
            if sg is not None and sg.status in ("completed", "abandoned"):
                continue
            return g
        return None

    def _urgent(self, state: WorldState, npc: str):
        ent = state.entities.get(npc)
        if ent is None or ent.status != "alive":
            return None
        cash = state.account_balance(wallet(npc))
        debt_sum = sum(
            d.amount
            for d in state.debts.values()
            if d.debtor == npc and d.status == "active" and d.amount > 0
        )
        if ent.stamina < self.injured_stamina:
            return ("urgent_recover", "wealth", f"体力只剩{int(ent.stamina)}，先吃饭恢复")
        if debt_sum >= self.debt_pressure:
            return ("urgent_debt", "wealth", f"欠债{debt_sum}文，先还债")
        if cash < self.bankrupt_cash:
            return ("urgent_money", "wealth", f"现钱只剩{cash}文，先找钱")
        return None

    def choose(self, state: WorldState, npc: str, tick: int) -> GoalChoice | None:
        ent = state.entities.get(npc)
        if ent is None or ent.status != "alive":
            return None
        name = self._name(npc)
        trait = self._trait(npc)
        active = self._active(state, npc)
        urgent = self._urgent(state, npc)
        last_urgent = self._urgent_last.get(npc, -10_000)
        cooldown_ok = tick - last_urgent >= self.preempt_cooldown_ticks

        if urgent is not None:
            ctype, kind, motivation = urgent
            if active is not None and active.content_type == ctype:
                # 已经在处理同类紧急事件，继续，不重复建目标
                return GoalChoice(
                    npc=npc,
                    goal_id=active.id,
                    content_goal_id=active.content_goal_id,
                    content_type=ctype,
                    kind=active.kind,
                    target=active.target,
                    source="urgent",
                    reason=f"{name}（{trait}）：{motivation}，继续处理",
                    create=False,
                )
            if active is not None and active.content_type.startswith("urgent_") and not cooldown_ok:
                return self._as_choice(active, name, trait, "紧急目标冷却中，维持当前", create=False)
            if active is not None and active.content_type in CONTENT_GOAL_TYPES and not cooldown_ok:
                return self._as_choice(active, name, trait, "紧急目标冷却中，先做主线", create=False)

            gid = f"urgent:{npc}:{ctype}:{day_of(tick)}"
            self._urgent_last[npc] = tick
            choice = GoalChoice(
                npc=npc,
                goal_id=gid,
                content_goal_id="",
                content_type=ctype,
                kind=kind,
                target=float(goal_target(self.content, kind)),
                source="urgent",
                reason=f"{name}（{trait}）：{motivation}，抢占主线",
                create=gid not in state.goals,
                preempted=active is not None,
                abandon=active.id if active is not None and active.id != gid else "",
            )
            self.changes.append(
                {
                    "tick": tick,
                    "npc": npc,
                    "type": "goal_preempt",
                    "from": active.id if active else "",
                    "to": gid,
                    "reason": choice.reason,
                }
            )
            return choice

        # 无紧急事件：回到内容主线
        content_goal = self._next_content_goal(state, npc)
        if content_goal is None:
            return None
        gid = f"{npc}:{content_goal['id']}"
        if active is not None and active.id == gid:
            return GoalChoice(
                npc=npc,
                goal_id=gid,
                content_goal_id=str(content_goal["id"]),
                content_type=str(content_goal.get("type", "")),
                kind=goal_kind(str(content_goal.get("type", ""))),
                target=float(goal_target(self.content, goal_kind(str(content_goal.get("type", ""))))),
                source="content",
                reason=f"{name}（{trait}）：按内容目标推进「{GOAL_LABEL.get(str(content_goal.get('type')), content_goal.get('type'))}」",
                create=False,
            )
        abandon = active.id if active is not None and active.content_type.startswith("urgent_") else ""
        reason = f"{name}（{trait}）：急事已了，回到主线「{GOAL_LABEL.get(str(content_goal.get('type')), content_goal.get('type'))}」"
        if abandon:
            self.changes.append(
                {
                    "tick": tick,
                    "npc": npc,
                    "type": "goal_return",
                    "from": active.id,
                    "to": gid,
                    "reason": reason,
                }
            )
        return GoalChoice(
            npc=npc,
            goal_id=gid,
            content_goal_id=str(content_goal["id"]),
            content_type=str(content_goal.get("type", "")),
            kind=goal_kind(str(content_goal.get("type", ""))),
            target=float(goal_target(self.content, goal_kind(str(content_goal.get("type", ""))))),
            source="content",
            reason=reason,
            create=gid not in state.goals,
            abandon=abandon,
        )

    def _as_choice(self, goal, name: str, trait: str, reason: str, create: bool) -> GoalChoice:
        return GoalChoice(
            npc=goal.npc,
            goal_id=goal.id,
            content_goal_id=goal.content_goal_id,
            content_type=goal.content_type,
            kind=goal.kind,
            target=goal.target,
            source="urgent" if goal.content_type.startswith("urgent_") else "content",
            reason=f"{name}（{trait}）：{reason}",
            create=create,
        )


class Planner:
    """把目标拆成可执行动作序列；routine（通勤/上工/吃饭）每天固定，目标动作按档位深度。

    计划窗口 = LOD 唤醒间隔（silent 一次计划 10 天，spotlight 一次 6 小时），
    因此推理次数随档位变化，但生活不会因为少推理而停摆。
    """

    def __init__(self, content, seed: int):
        self.content = content
        self.seed = seed
        self._stamina = 100.0

    def _cost(self, verb: str, args: dict) -> float:
        econ = self.content.economy
        if verb == "move":
            return float(econ.get("stamina_move", 5))
        if verb == "work":
            return float(econ.get("stamina_work", 12)) * float(args.get("hours", 8)) / 8.0
        if verb == "cultivate":
            return float(econ.get("stamina_cultivate", 4)) * float(args.get("hours", 1))
        return 0.0

    def _add(self, intents: list[Intent], npc: str, verb: str, args: dict, cursor: int, end: int, reason: str) -> int:
        if cursor >= end:
            return cursor
        cost = self._cost(verb, args)
        if cost > 0 and self._stamina < cost:
            return cursor  # 体力不够：不生成必被拒的动作，交给 replan
        self._stamina -= cost
        if verb == "consume":
            self._stamina = min(100.0, self._stamina + float(self.content.economy.get("food_stamina", 25)))
        intents.append(
            Intent(actor=npc, verb=verb, args=args, tick=cursor, source="planner", reason=reason)
        )
        return cursor + 1

    def _move(
        self,
        state: WorldState,
        intents: list[Intent],
        npc: str,
        loc: str,
        dest: str,
        cursor: int,
        end: int,
        reason: str,
    ) -> tuple[str, int]:
        for hop in path_between(state, loc, dest):
            if cursor >= end:
                break
            before = cursor
            cursor = self._add(intents, npc, "move", {"to": hop}, cursor, end, reason)
            if cursor == before:
                break
            loc = hop
        return loc, cursor

    # ---------- 计划 ----------

    def plan(
        self,
        state: WorldState,
        npc: str,
        choice: GoalChoice,
        start_tick: int,
        interval: int,
        tier: str,
        variant: int = 0,
    ) -> list[Intent]:
        ent = state.entities.get(npc)
        if ent is None or ent.status != "alive":
            return []
        horizon = start_tick + max(1, interval)
        intents: list[Intent] = []
        loc = ent.location
        cursor = start_tick
        cash = state.account_balance(wallet(npc))
        depth = DEPTH_GOAL_ACTIONS.get(tier, 1)
        memo: dict[str, Any] = {}
        self._stamina = float(ent.stamina)
        recover = float((self.content.economy.get("recovery") or {}).get("stamina_per_day", 40))
        label = GOAL_LABEL.get(choice.content_type, choice.content_type)
        name = ent.name

        def reason(text: str) -> str:
            return f"{name}：{text}（目标：{label}）"

        day_last = day_of(max(start_tick, horizon - 1))
        for day in range(day_of(start_tick), day_last + 1):
            day_start = day * TICKS_PER_DAY
            seg_start = max(day_start, start_tick)
            seg_end = min(horizon, day_start + TICKS_PER_DAY)
            if seg_end <= seg_start:
                continue
            cursor = max(cursor, seg_start)
            if day > day_of(start_tick):
                self._stamina = min(100.0, self._stamina + recover)

            work_loc = self._work_location(state, ent)
            if work_loc:
                loc, cursor = self._move(state, intents, npc, loc, work_loc, cursor, seg_end, reason("去上工"))
                if loc == work_loc and cursor < seg_end:
                    cursor = self._add(intents, npc, "work", {"at": loc, "hours": 8}, cursor, seg_end, reason("上工糊口"))
                    cash += self._net_wage(ent)
            else:
                cursor = self._add(
                    intents,
                    npc,
                    "cultivate",
                    {"skill": skill_for(self.content, ent.role), "hours": 4},
                    cursor,
                    seg_end,
                    reason("没有雇主，练手艺"),
                )

            # 午饭（一天一顿，减少无效往返；晚饭取消）
            loc, cursor, cash = self._meal(state, intents, npc, loc, cursor, seg_end, cash, "午饭")

            # 目标动作：按档位深度
            for k in range(depth):
                if cursor >= seg_end:
                    break
                before = len(intents)
                loc, cursor, cash = self._goal_step(
                    state, intents, ent, choice, loc, cursor, seg_end, variant + k, cash, memo
                )
                if len(intents) == before:
                    break

            # 只在计划窗口的最后一天回家，避免每天往返
            if day == day_last:
                home = ent.home or ent.location
                loc, cursor = self._move(state, intents, npc, loc, home, cursor, seg_end, reason("回家"))

        return intents

    def _work_location(self, state: WorldState, ent) -> str:
        if ent.employed_at and ent.employed_at in state.businesses:
            return state.businesses[ent.employed_at].location
        return ""

    def _net_wage(self, ent) -> int:
        gross = max(1, int(round(self.content.wage_for(ent.role))))
        tax = int(gross * float((self.content.economy.get("taxes") or {}).get("income", 0.0)))
        return gross - tax

    def _meal(
        self,
        state: WorldState,
        intents: list[Intent],
        npc: str,
        loc: str,
        cursor: int,
        end: int,
        cash: int,
        label: str,
    ) -> tuple[str, int, int]:
        foods = set(food_locations(state, self.content))
        if not foods:
            return loc, cursor, cash
        food_loc = nearest_location(state, loc, foods)
        if food_loc is None:
            return loc, cursor, cash
        loc, cursor = self._move(state, intents, npc, loc, food_loc, cursor, end, f"{label}：去饭点")
        item = cheapest_food(self.content, loc, state, min_stock=10)
        if item is None:
            return loc, cursor, cash
        price = self.content.item_price(item)
        tax = int(price * float((self.content.economy.get("taxes") or {}).get("transaction", 0.0)))
        if cash < price + tax:
            return loc, cursor, cash
        cursor = self._add(
            intents,
            npc,
            "consume",
            {"item": item, "qty": 1},
            cursor,
            end,
            f"{label}：吃{item}",
        )
        cash -= price + tax
        return loc, cursor, cash

    def _goal_step(
        self,
        state: WorldState,
        intents: list[Intent],
        ent,
        choice: GoalChoice,
        loc: str,
        cursor: int,
        end: int,
        variant: int,
        cash: int,
        memo: dict[str, Any],
    ) -> tuple[str, int, int]:
        npc = ent.id
        ct = choice.content_type
        name = ent.name
        reason = lambda text: f"{name}：{text}（{choice.reason.split('：', 1)[-1]}）"

        if ct == "advance_skill":
            cursor = self._add(
                intents, npc, "cultivate",
                {"skill": skill_for(self.content, ent.role), "hours": 2},
                cursor, end, reason("练手艺"),
            )
            return loc, cursor, cash

        if ct in ("earn_money", "urgent_money", "urgent_debt"):
            step = variant % 3
            if step == 0:
                item = self._sellable(ent, memo)
                if item is not None:
                    dest = self._sell_location(state, item, loc)
                    if dest:
                        loc, cursor = self._move(state, intents, npc, loc, dest, cursor, end, reason("带货去卖"))
                    if loc == dest and state.business_at(loc) and cursor < end:
                        cursor = self._add(
                            intents, npc, "sell",
                            {"item": item, "qty": 1, "to": loc},
                            cursor, end, reason(f"卖{item}换现钱"),
                        )
                        self._mark_used(memo, item)
                        cash += sell_price(self.content, item)
                        return loc, cursor, cash
            if step == 1:
                peer = self._peer(state, ent, money_delta=5)
                if peer is not None:
                    loc, cursor = self._move(state, intents, npc, loc, peer.location, cursor, end, reason(f"找{peer.name}交易"))
                    if loc == peer.location and cursor < end:
                        cursor = self._add(
                            intents, npc, "trade",
                            {"target": peer.id, "offer": [], "want": [], "money_delta": 5},
                            cursor, end, reason(f"与{peer.name}做买卖"),
                        )
                        cash += 5
                        return loc, cursor, cash
            work_loc = self._work_location(state, ent)
            if work_loc:
                loc, cursor = self._move(state, intents, npc, loc, work_loc, cursor, end, reason("加个短工"))
                if loc == work_loc and cursor < end:
                    cursor = self._add(
                        intents, npc, "work", {"at": loc, "hours": 4},
                        cursor, end, reason("加短工挣现钱"),
                    )
                    cash += self._net_wage(ent) // 2
            return loc, cursor, cash

        if ct == "urgent_recover":
            foods = set(food_locations(state, self.content))
            if foods:
                food_loc = nearest_location(state, loc, foods)
                if food_loc:
                    loc, cursor = self._move(state, intents, npc, loc, food_loc, cursor, end, reason("找吃的恢复体力"))
                item = cheapest_food(self.content, loc, state, min_stock=1)
                price = self.content.item_price(item) + int(
                    self.content.item_price(item) * float((self.content.economy.get("taxes") or {}).get("transaction", 0.0))
                ) if item else 10 ** 9
                if item and cash >= price and cursor < end:
                    cursor = self._add(
                        intents, npc, "consume", {"item": item, "qty": 1},
                        cursor, end, reason("吃口热的，恢复体力"),
                    )
                    cash -= price
            return loc, cursor, cash

        if ct == "build_shop":
            if ent.business is not None or memo.get("opened"):
                return loc, cursor, cash
            kind = "stall"
            cost = int((self.content.economy.get("shop_opening") or {}).get(kind, 300))
            if cash >= cost:
                cursor = self._add(
                    intents, npc, "open_shop",
                    {"at": loc, "shop_type": kind, "name": f"{ent.name}的小摊"},
                    cursor, end, reason("盘下一个小摊"),
                )
                memo["opened"] = True
                cash -= cost
                return loc, cursor, cash
            item = self._sellable(ent, memo)
            if item is not None:
                cursor = self._add(
                    intents, npc, "sell", {"item": item, "qty": 1, "to": loc},
                    cursor, end, reason("先攒开店本钱"),
                )
                self._mark_used(memo, item)
                cash += sell_price(self.content, item)
            return loc, cursor, cash

        if ct in ("gain_status", "join_sect"):
            if ent.faction is not None or memo.get("joined"):
                return loc, cursor, cash
            target_loc = join_target_location(state, self.content) or loc
            loc, cursor = self._move(state, intents, npc, loc, target_loc, cursor, end, reason("去行会/书院"))
            if loc != target_loc:
                return loc, cursor, cash
            fac = organization_at(state, target_loc)
            target = fac.id if fac is not None else target_loc
            dues = 20 if cash >= 20 else 0
            if cursor < end:
                cursor = self._add(
                    intents, npc, "join_sect",
                    {"target": target, "dues": dues},
                    cursor, end, reason("递帖子入会"),
                )
                memo["joined"] = True
                cash -= dues
            return loc, cursor, cash

        if ct == "found_sect":
            if (ent.faction is not None and str(ent.faction).startswith("sect:")) or memo.get("founded"):
                return loc, cursor, cash
            seat = self.content.sect_at(ent.home or ent.location) or {}
            at = str(seat.get("location") or ent.home or ent.location)
            loc, cursor = self._move(state, intents, npc, loc, at, cursor, end, reason("回门派驻地"))
            members = founding_members(state, ent, float((self.content.economy.get("sect") or {}).get("min_relation_value", 30)))
            need = max(5, int((self.content.economy.get("sect") or {}).get("min_members", 5)))
            if len(members) >= need and cash >= 2500 and cursor < end:
                cursor = self._add(
                    intents, npc, "found_sect",
                    {"name": f"{ent.name}会", "at": loc, "doctrine": "自守"},
                    cursor, end, reason("凑齐人手，开宗立派"),
                )
                memo["founded"] = True
                cash -= 2500
            else:
                cursor = self._add(
                    intents, npc, "cultivate",
                    {"skill": skill_for(self.content, ent.role), "hours": 2},
                    cursor, end, reason(f"人手{len(members)}/{need}，先攒声望"),
                )
            return loc, cursor, cash

        if ct == "build_relation":
            step = variant % 3
            if step == 0:
                peer = self._peer(state, ent, prefer_same=True) or self._peer(state, ent, money_delta=0)
                if peer is not None:
                    loc, cursor = self._move(state, intents, npc, loc, peer.location, cursor, end, reason(f"去见{peer.name}"))
                    if loc == peer.location and cursor < end:
                        offer = []
                        for item in sorted(ent.inventory):
                            if self._available(ent, item, memo) >= 1 and self.content.items.get(item, {}).get("category") != "service":
                                offer = [{"item": item, "qty": 1}]
                                self._mark_used(memo, item)
                                break
                        cursor = self._add(
                            intents, npc, "trade",
                            {"target": peer.id, "offer": offer, "want": [], "money_delta": 0},
                            cursor, end, reason(f"与{peer.name}叙旧"),
                        )
                return loc, cursor, cash
            if step == 1 and ent.master is None and not memo.get("apprentice"):
                master = self._master(state, ent)
                if master is not None:
                    loc, cursor = self._move(state, intents, npc, loc, master.location, cursor, end, reason(f"去拜{master.name}"))
                    if loc == master.location and cursor < end:
                        cursor = self._add(
                            intents, npc, "apprentice",
                            {"target": master.id, "duration_days": 30},
                            cursor, end, reason(f"拜{master.name}为师"),
                        )
                        memo["apprentice"] = True
                return loc, cursor, cash
            spouse = self._spouse(state, ent)
            if spouse is not None and not memo.get("married"):
                loc, cursor = self._move(state, intents, npc, loc, spouse.location, cursor, end, reason(f"去见{spouse.name}"))
                if loc == spouse.location and cursor < end:
                    cursor = self._add(
                        intents, npc, "marry", {"target": spouse.id},
                        cursor, end, reason(f"与{spouse.name}成婚"),
                    )
                    memo["married"] = True
            return loc, cursor, cash

        if ct == "escort":
            agency = escort_business(state)
            if agency is not None:
                cfg = self.content.economy.get("escort") or {}
                fee = int(cfg.get("base_fee", 180))
                if state.account_balance(agency.cash_account) >= fee:
                    loc, cursor = self._move(state, intents, npc, loc, agency.location, cursor, end, reason("去镖局接单"))
                    dest = sorted(state.locations[agency.location].get("exits", []))
                    if loc == agency.location and dest and cursor < end:
                        cursor = self._add(
                            intents, npc, "escort",
                            {"route": [agency.location, dest[0]], "cargo_value": 150, "fee": fee},
                            cursor, end, reason("接一趟短镖"),
                        )
            return loc, cursor, cash

        cursor = self._add(
            intents, npc, "cultivate",
            {"skill": skill_for(self.content, ent.role), "hours": 2},
            cursor, end, reason("按目标修炼"),
        )
        return loc, cursor, cash

    # ---------- 选择辅助 ----------

    def _available(self, ent, item: str, memo: dict[str, Any] | None = None) -> int:
        used = (memo or {}).get("used", {}).get(item, 0)
        return int(ent.inventory.get(item, 0)) - used

    def _mark_used(self, memo: dict[str, Any], item: str) -> None:
        used = memo.setdefault("used", {})
        used[item] = used.get(item, 0) + 1

    def _sellable(self, ent, memo: dict[str, Any] | None = None) -> str | None:
        for item in sorted(ent.inventory):
            if self._available(ent, item, memo) > 0 and self.content.items.get(item, {}).get("category") != "service":
                return item
        return None

    def _sell_location(self, state: WorldState, item: str, loc: str) -> str | None:
        revenue = sell_price(self.content, item)
        cands = {
            lid
            for lid in sorted(state.locations)
            if any(state.account_balance(b.cash_account) >= revenue for b in state.business_at(lid))
        }
        if not cands:
            return None
        return nearest_location(state, loc, cands)

    def _peer(self, state: WorldState, ent, money_delta: int = 0, prefer_same: bool = False):
        ids = sorted(e.id for e in state.entities.values() if e.id != ent.id and e.status == "alive")
        if not ids:
            return None
        if prefer_same:
            ids = [tid for tid in ids if state.entities[tid].location == ent.location]
            if not ids:
                return None
        start = sum(ord(c) for c in ent.id) % len(ids)
        for off in range(len(ids)):
            tid = ids[(start + off) % len(ids)]
            other = state.entities[tid]
            if state.affection_between(ent.id, tid) < -20:
                continue
            if money_delta > 0 and state.account_balance(wallet(tid)) < money_delta:
                continue
            return other
        return None

    def _master(self, state: WorldState, ent):
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

    def _spouse(self, state: WorldState, ent):
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

    # ---------- 失败重规划 ----------

    def alternative(self, state: WorldState, npc: str, choice: GoalChoice | None, tick: int, variant: int, last_reject: str) -> Intent | None:
        """原计划被拒后的替代路径：一个立即可行的动作，而不是重复原动作。"""
        ent = state.entities.get(npc)
        if ent is None or ent.status != "alive":
            return None
        ctype = choice.content_type if choice is not None else "routine"
        label = GOAL_LABEL.get(ctype, ctype)
        reason = f"{ent.name}：原路径被拒（{last_reject}），改走{variant % 3 + 1}号替代路径（{label}）"
        cash = state.account_balance(wallet(npc))
        loc = ent.location

        # 0) 手上有货且当地有买家 → 卖
        item = self._sellable(ent)
        if item is not None and variant % 3 == 0:
            if any(state.account_balance(b.cash_account) >= sell_price(self.content, item) for b in state.business_at(loc)):
                return Intent(actor=npc, verb="sell", args={"item": item, "qty": 1, "to": loc}, tick=tick, source="planner", reason=reason)
        # 1) 当地有可交易对象 → 交易
        if variant % 3 == 1:
            peer = next(
                (
                    e
                    for e in sorted(state.entities.values(), key=lambda e: e.id)
                    if e.id != npc and e.status == "alive" and e.location == loc
                ),
                None,
            )
            if peer is not None and state.affection_between(npc, peer.id) >= -20:
                return Intent(
                    actor=npc,
                    verb="trade",
                    args={"target": peer.id, "offer": [], "want": [], "money_delta": 0},
                    tick=tick,
                    source="planner",
                    reason=reason,
                )
        # 2) 当地有雇主 → 短工
        if ent.employed_at and ent.employed_at in state.businesses and state.businesses[ent.employed_at].location == loc:
            return Intent(actor=npc, verb="work", args={"at": loc, "hours": 4}, tick=tick, source="planner", reason=reason)
        # 3) 兜底：就地修炼，不空转
        return Intent(
            actor=npc,
            verb="cultivate",
            args={"skill": skill_for(self.content, ent.role), "hours": 2},
            tick=tick,
            source="planner",
            reason=reason,
        )


class Reasoner(ABC):
    """真实模型接入点。实现必须是确定性的或由外部显式注入随机源。"""

    @abstractmethod
    def decide(self, npc_state: dict[str, Any], world_view: dict[str, Any]) -> list[Intent]:
        raise NotImplementedError


class MockReasoner(Reasoner):
    """v1：GoalSelector + Planner 的确定性规则桩。

    无 world_view.state/content 时退化为旧的规则桩（单测/Provider 桩会用这条路径）。
    """

    def __init__(self, seed: int = 42, content=None):
        self.seed = seed
        self.content = content
        self.selector = GoalSelector(content) if content is not None else None
        self.planner = Planner(content, seed) if content is not None else None
        self._planned_through: dict[str, int] = {}
        self._variant: dict[str, int] = {}
        self._last_reject: dict[str, str] = {}
        self.decisions: list[dict[str, Any]] = []
        self.replan_count = 0

    def decide(self, npc_state: dict[str, Any], world_view: dict[str, Any]) -> list[Intent]:
        npc = str(npc_state.get("id", ""))
        tick = int(world_view.get("tick", 0))
        state: WorldState | None = world_view.get("state")
        content = world_view.get("content") or self.content

        if state is None or content is None or self.selector is None or self.planner is None:
            return self._legacy(npc, npc_state, tick)

        if self._planned_through.get(npc, -1) > tick:
            return []
        start = max(tick, self._planned_through.get(npc, tick))
        interval = int(world_view.get("interval", TICKS_PER_DAY))
        tier = str(world_view.get("tier", "active"))
        choice = self.selector.choose(state, npc, tick)
        if choice is None:
            return []

        intents: list[Intent] = []
        active = state.entities[npc].goal_id if npc in state.entities else None
        if choice.create or active != choice.goal_id:
            args = {
                "goal": choice.goal_id,
                "content_goal_id": choice.content_goal_id,
                "content_type": choice.content_type,
                "kind": choice.kind,
                "target": choice.target,
                "goal_source": choice.source,
                "create": bool(choice.create),
                "abandon": choice.abandon,
                "reason": choice.reason,
            }
            intents.append(
                Intent(actor=npc, verb="system_goal", args=args, tick=start, source="planner", reason=choice.reason)
            )

        plan = self.planner.plan(state, npc, choice, start, interval, tier, self._variant.get(npc, 0))
        intents.extend(plan)
        self._planned_through[npc] = start + max(1, interval)
        self.decisions.append(
            {
                "tick": tick,
                "npc": npc,
                "tier": tier,
                "goal": choice.goal_id,
                "content_type": choice.content_type,
                "preempted": choice.preempted,
                "create": bool(choice.create),
                "reason": choice.reason,
                "planned_through": self._planned_through[npc],
                "actions": [i.verb for i in intents],
            }
        )
        return intents

    def _legacy(self, npc: str, npc_state: dict[str, Any], tick: int) -> list[Intent]:
        rng = PRNG(self.seed, tick, f"mock_reasoner:{npc}")
        if rng.chance(0.12):
            return []
        stamina = float(npc_state.get("stamina", 100.0))
        exits = sorted(str(x) for x in (npc_state.get("exits") or []))
        if exits and stamina > 20 and rng.chance(0.45):
            dest = exits[rng.randint(0, len(exits) - 1)]
            return [
                Intent(
                    actor=npc,
                    verb="move",
                    args={"to": dest},
                    tick=tick,
                    source="mock_reasoner",
                    reason=f"规则桩：顺路去{dest}",
                )
            ]
        if rng.chance(0.55):
            return [
                Intent(
                    actor=npc,
                    verb="cultivate",
                    args={},
                    tick=tick,
                    source="mock_reasoner",
                    reason="规则桩：闲来修炼",
                )
            ]
        return []

    def observe(self, event) -> None:
        """运行期反馈：计划动作被拒 → 下次 replan 切换变体。"""
        if event.outcome != "rejected" or event.source != "planner":
            return
        npc = event.actor
        self._last_reject[npc] = event.reason
        self._variant[npc] = min(3, self._variant.get(npc, 0) + 1)

    def replan(self, npc_state: dict[str, Any], world_view: dict[str, Any]) -> list[Intent]:
        npc = str(npc_state.get("id", ""))
        tick = int(world_view.get("tick", 0))
        state: WorldState | None = world_view.get("state")
        content = world_view.get("content") or self.content
        if state is None or content is None or self.selector is None or self.planner is None:
            return []
        choice = self.selector.choose(state, npc, tick)
        intent = self.planner.alternative(
            state,
            npc,
            choice,
            tick,
            self._variant.get(npc, 0),
            self._last_reject.get(npc, "unknown"),
        )
        if intent is None:
            return []
        self.replan_count += 1
        self.decisions.append(
            {
                "tick": tick,
                "npc": npc,
                "tier": str(world_view.get("tier", "")),
                "goal": choice.goal_id if choice else "",
                "kind": "replan",
                "variant": self._variant.get(npc, 0),
                "reason": intent.reason,
                "actions": [intent.verb],
            }
        )
        return [intent]
