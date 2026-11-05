"""意图层：GoalSelector + Planner + MockReasoner v1。

约束（与 P2 框架层一致）：
- 确定性：只用 core.rng.PRNG（SplitMix64）或纯排序，不读墙钟、不用全局 random；
- 决策必须给出 reason（一句话动机），由 Writer 写入事件日志，审计可解释；
- 计划器在前置条件不满足时换路径（不是直接拒绝）；运行期被拒后由 replan 切换变体；
- 本模块只读 state，所有变更仍走 Intent -> Writer -> Effect。
"""
from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
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
    role_allowed,
    sell_price,
    skill_for,
    treasury_balance,
    treasury_guard_active,
    treasury_open_shop_buffer,
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
# 类型配额只保护这三类核心目标，避免模型覆盖导致系统性低于规则臂。
CORE_GOAL_TYPES = ("build_shop", "earn_money", "advance_skill")


@dataclass(frozen=True)
class GoalSwitchPolicy:
    """目标切换策略（默认全部关闭，便于与旧行为对照）。

    - commit_ticks：选定目标后至少保持的 tick 数（1 tick=10 分钟），0 表示不限制；
    - hysteresis_delta：模型目标评分必须超过当前目标评分 + delta 才允许覆盖；
    - override_guard_v2：覆盖护栏 v2，按“预期完成概率”保护规则目标；
    - type_quota：按 NPC 为 build_shop/earn_money/advance_skill 设类型下限；
    - 只有对应开关为真时生效；紧急/生存目标始终绕过承诺窗口。
    """

    enabled: bool = False
    commit_ticks: int = 0
    hysteresis_delta: float = 0.0
    override_guard_v2: bool = False
    override_high_probability: float = 0.55
    type_quota: bool = False
    type_quota_floor: int = 1


@dataclass(frozen=True)
class GoalChoice:
    npc: str
    goal_id: str
    content_goal_id: str
    content_type: str
    kind: str
    target: float
    source: str  # content / urgent / self
    reason: str
    create: bool = True
    preempted: bool = False
    abandon: str = ""
    # 次级目标：只建目标、不切 entity.goal_id，进度走独立额度。
    secondary: bool = False


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
        # A4 战略层（默认关闭）：计划只影响选目标，不影响动作生成。
        self.strategy_book = None
        self.strategy_mode = False
        self._strategy_obs: dict[str, tuple[str, float, int]] = {}
        self._content_goals = {
            str(n["id"]): sorted(
                n.get("goals") or [],
                key=lambda g: (float(g.get("priority", 9)), str(g.get("id", ""))),
            )
            for n in content.npcs
        }
        # 门派发起人：内容包 sects.yaml 的 founder_candidate 自动补一条 found_sect 目标。
        for sid in sorted(content.sects):
            sect = content.sects[sid]
            cand = str(sect.get("founder_candidate") or "")
            if not cand or cand not in self._content_goals:
                continue
            if any(str(g.get("type")) == "found_sect" and str(g.get("sect", sid)) == sid
                   for g in self._content_goals[cand]):
                continue
            self._content_goals[cand].insert(
                0,
                {
                    "id": f"found_{sid}",
                    "type": "found_sect",
                    "priority": 1,
                    "sect": sid,
                    "name": sect.get("name", sid),
                },
            )

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
        """按内容优先级轮转：当前目标已推进（progress >= 0.1）就转向下一个。

        只做“深度优先”会把后续目标全部堵死（K1 分母失效）；轮转保证每个
        NPC 在 72 天内能触达多条内容目标，同时仍只有一个 active 主目标。
        """
        active = self._active(state, npc)
        for g in self._content_goals.get(npc, []):
            gid = f"{npc}:{g['id']}"
            sg = state.goals.get(gid)
            if sg is not None and sg.status in ("completed", "abandoned"):
                continue
            if active is not None and active.id == gid and active.progress >= 0.1:
                # found_sect 不轮转：招人本身就是进度，轮转会让门派永远凑不齐人。
                if str(g.get("type")) == "found_sect":
                    return g
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
        persona = self.content.personas.get(npc, "") if self.content is not None else ""
        ptag = f"｜{persona[:26]}" if persona else ""
        active = self._active(state, npc)
        urgent = self._urgent(state, npc)
        last_urgent = self._urgent_last.get(npc, -10_000)
        cooldown_ok = tick - last_urgent >= self.preempt_cooldown_ticks
        plan = self._strategy_plan(state, npc, tick)

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
                    reason=f"{name}（{trait}{ptag}）：{motivation}，继续处理",
                    create=False,
                )
            if active is not None and active.content_type.startswith("urgent_") and not cooldown_ok:
                return self._as_choice(active, name, trait, "紧急目标冷却中，维持当前", create=False)
            if active is not None and active.content_type in CONTENT_GOAL_TYPES and not cooldown_ok:
                return self._as_choice(active, name, trait, "紧急目标冷却中，先做主线", create=False)

            gid = f"urgent:{npc}:{ctype}:{day_of(tick)}"
            self._urgent_last[npc] = tick
            strategy_keeps = bool(plan is not None and active is not None and active.id in plan.goal_ids)
            if strategy_keeps:
                # A4：紧急事件只暂停计划，不放弃计划目标；解除后回到原主目标。
                self.strategy_book.record_pause(plan, tick, ctype, active.id)
            choice = GoalChoice(
                npc=npc,
                goal_id=gid,
                content_goal_id="",
                content_type=ctype,
                kind=kind,
                target=float(goal_target(self.content, kind)),
                source="urgent",
                reason=f"{name}（{trait}{ptag}）：{motivation}，抢占主线",
                create=gid not in state.goals,
                preempted=active is not None,
                abandon=(
                    active.id
                    if active is not None
                    and active.id != gid
                    and active.content_type != "found_sect"
                    and not strategy_keeps
                    else ""
                ),
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

        # P4：里程碑接力。完成一级后由日界 system_milestone 建好下一段，
        # 次日保持该里程碑直到完成，不再被基础目标轮转打断。
        if active is not None and active.status == "active" and active.source == "milestone":
            return GoalChoice(
                npc=npc,
                goal_id=active.id,
                content_goal_id=active.content_goal_id,
                content_type=active.content_type,
                kind=active.kind,
                target=active.target,
                source="milestone",
                reason=f"{name}（{trait}{ptag}）：继续长期里程碑「{active.content_goal_id}」",
                create=False,
            )

        # 无紧急事件：先按战略计划选，计划耗尽/不存在时回到内容主线的轮转
        content_goal = None
        if plan is not None:
            content_goal = self._plan_goal(state, npc, plan, tick)
        plan_goal = content_goal is not None
        if content_goal is None:
            content_goal = self._next_content_goal(state, npc)
        if content_goal is None:
            return None
        gid = f"{npc}:{content_goal['id']}"
        label = GOAL_LABEL.get(str(content_goal.get("type")), content_goal.get("type"))
        plan_note = ""
        if plan_goal and plan is not None:
            plan_note = f"按{plan.horizon_days}日计划「{plan.rationale[:28]}」推进"
        if active is not None and active.id == gid:
            return GoalChoice(
                npc=npc,
                goal_id=gid,
                content_goal_id=str(content_goal["id"]),
                content_type=str(content_goal.get("type", "")),
                kind=goal_kind(str(content_goal.get("type", ""))),
                target=float(goal_target(self.content, goal_kind(str(content_goal.get("type", ""))))),
                source="content",
                reason=(
                    f"{name}（{trait}{ptag}）：{plan_note}「{label}」"
                    if plan_note
                    else f"{name}（{trait}{ptag}）：按内容目标推进「{label}」"
                ),
                create=False,
            )
        abandon = active.id if active is not None and active.content_type.startswith("urgent_") else ""
        reason = (
            f"{name}（{trait}{ptag}）：{plan_note}「{label}」"
            if plan_note
            else f"{name}（{trait}{ptag}）：急事已了，回到主线「{label}」"
        )
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

    def _strategy_plan(self, state: WorldState, npc: str, tick: int):
        """战略层开启且当前有未过期计划时返回计划；否则 None。"""
        if not self.strategy_mode or self.strategy_book is None:
            return None
        return self.strategy_book.active_plan(npc, tick)

    def _strategy_stalled(self, npc: str, tick: int, goal_id: str, progress: float) -> bool:
        """确定性停滞检测：同一已选目标 48 tick（4 游戏小时）无进度则视为前置不可行。"""
        prev = self._strategy_obs.get(npc)
        if prev is None or prev[0] != goal_id or progress > prev[1]:
            self._strategy_obs[npc] = (goal_id, progress, tick)
            return False
        return tick - prev[2] >= 48

    def _plan_goal(self, state: WorldState, npc: str, plan, tick: int):
        """在计划内轮转：当前目标推进到 10% 后换下一条，全部触达后留在当前条完成。

        - 当前目标已在计划里且 progress<0.1：维持，保证跨日承诺；
        - progress>=0.1：转到计划内下一条尚未推进的条目（不迁出计划）；
        - 计划内条目全部触达：剩余内容目标交回规则自由轮转，避免第 4 条被长期堵死。
        """
        active = self._active(state, npc)
        if active is not None and active.id in plan.goal_ids:
            if str(active.content_type).startswith("urgent_"):
                return {"id": active.content_goal_id, "type": active.content_type, "role": "active"}
            progress = float(active.progress)
            if progress < 0.1:
                if self._strategy_stalled(npc, tick, active.id, float(active.progress)):
                    for entry in plan.entries:
                        if entry.goal_id == active.id:
                            continue
                        goal = state.goals.get(entry.goal_id)
                        if goal is not None and goal.status in ("completed", "abandoned"):
                            continue
                        other = float(goal.progress) if goal is not None else 0.0
                        if other < 0.1:
                            if self.strategy_book is not None:
                                self.strategy_book.record_deviation(plan, tick, "infeasible_prereq", entry.goal_id)
                            return {"id": entry.content_goal_id, "type": entry.content_type, "role": entry.role}
                    # 计划内没有其它可推进条目：让规则轮转到计划外目标，避免整段卡死（K1 护栏）。
                    return None
                return {"id": active.content_goal_id, "type": active.content_type, "role": "active"}
            for entry in plan.entries:
                if entry.goal_id == active.id:
                    continue
                goal = state.goals.get(entry.goal_id)
                if goal is not None and goal.status in ("completed", "abandoned"):
                    continue
                other = float(goal.progress) if goal is not None else 0.0
                if other < 0.1:
                    if self.strategy_book is not None:
                        self.strategy_book.record_deviation(plan, tick, "in_plan_rotate", entry.goal_id)
                    return {"id": entry.content_goal_id, "type": entry.content_type, "role": entry.role}
            # 计划内条目都已触达 10%：剩余未完成内容目标交回规则自由轮转，
            # 防止“只许三条”把第 4 条目标彻底堵死（K1 护栏）。
            return None
        if active is not None and not str(active.content_type).startswith("urgent_"):
            progress = float(active.progress)
            if progress >= 0.1:
                # G1 兜底：合法计划应已把它纳入；这里只防状态在计划生效后变化。
                if self.strategy_book is not None:
                    self.strategy_book.record_deviation(plan, tick, "g1_preserve_active", active.id)
                return {"id": active.content_goal_id, "type": active.content_type, "role": "active"}
            # 计划外的当前目标尚未推进到 10%：先让它走完这一步，避免每 tick 来回换。
            return {"id": active.content_goal_id, "type": active.content_type, "role": "active"}
        for entry in plan.entries:
            goal = state.goals.get(entry.goal_id)
            if goal is not None and goal.status in ("completed", "abandoned"):
                continue
            other = float(goal.progress) if goal is not None else 0.0
            if other < 0.1:
                return {"id": entry.content_goal_id, "type": entry.content_type, "role": entry.role}
        return None

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


def _as_ids(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(x) for x in raw]


section_ids = _as_ids


class Planner:
    """把目标拆成可执行动作序列；routine（通勤/上工/吃饭）每天固定，目标动作按档位深度。

    计划窗口 = LOD 唤醒间隔（silent 一次计划 10 天，spotlight 一次 6 小时），
    因此推理次数随档位变化，但生活不会因为少推理而停摆。
    """

    def __init__(self, content, seed: int):
        self.content = content
        self.seed = seed
        self._stamina = 100.0
        # quest_id -> 上次尝试 tick（避免同一任务反复被拒）
        self._quest_attempt: dict[str, int] = {}
        # template_id -> 上次被接单的 tick：开放任务按最久未服务优先轮转，
        # 避免 procure_* / teach_* 这类前缀靠后的模板被字典序永久压尾。
        self._quest_served: dict[str, int] = {}
        # 因金库缓冲不足而延后的开店次数（由 MockReasoner 汇总到 summary）。
        self.postponed_open_shop = 0
        # 自提案独立额度：实际排上的自目标动作数 / 因当天没空位而放弃的次数。
        self.secondary_steps = 0
        self.secondary_steps_dropped = 0

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
        secondary: GoalChoice | None = None,
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
        # 自提案独立额度用自己的一份 memo：_goal_step 在里面记 opened/joined/
        # apprentice/married 等“本窗口已做过”，共享会把次日的**内容**动作压掉。
        secondary_memo: dict[str, Any] = {}
        self._stamina = float(ent.stamina)
        recover = float((self.content.economy.get("recovery") or {}).get("stamina_per_day", 40))
        label = GOAL_LABEL.get(choice.content_type, choice.content_type)
        name = ent.name

        def reason(text: str) -> str:
            persona = self.content.personas.get(ent.id, "") if self.content is not None else ""
            tag = f"｜{persona[:26]}" if persona else ""
            return f"{name}：{text}（目标：{label}{tag}）"

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

            # 任务优先：接单 -> 执行（交付由 headless 在动作成功后推进）。
            # 放在上工之前：清早 NPC 与发起人多半都在驻地，接单离规划时刻更近，
            # 避免“计划时发起人还在、执行时已走开”的 not_at_giver 静默失败。
            loc, cursor, cash = self._quest_step(state, intents, npc, loc, cursor, seg_end, cash, memo)

            employer = self._employer(state, ent)
            work_loc = employer.location if employer is not None else ""
            gross = self._wage_for_employment(employer, ent)
            reserve = int((self.content.economy.get("work") or {}).get("employer_reserve", 300))
            # 日工可连续上工（P2-B 复核 B6）：不再隔日。
            # NPC 雇主沿用原规则（现金 ≥ 日薪 + 储备），防止把商业体现金抽干；
            # 玩家雇主按玩家钱包 + 当日营收判断偿付能力，但**不因此罢工**：
            # 契约成立就出工，钱不够由日结 system_shop_settle 记欠薪债。
            if employer is not None and self._employer_is_player(employer):
                can_work = True
                work_note = (
                    "上工糊口"
                    if self._player_employer_can_pay(state, employer, gross)
                    else "上工糊口（店主现金紧，工资走欠薪）"
                )
            else:
                can_work = (
                    employer is not None
                    and state.account_balance(employer.cash_account) >= gross + reserve
                )
                work_note = "上工糊口"
            if work_loc and can_work:
                loc, cursor = self._move(state, intents, npc, loc, work_loc, cursor, seg_end, reason("去上工"))
                if loc == work_loc and cursor < seg_end:
                    before = cursor
                    cursor = self._add(intents, npc, "work", self._work_args(employer, loc, 8), cursor, seg_end, reason(work_note))
                    if cursor != before:
                        cash += self._net_wage(ent, gross)
            else:
                # 无雇主或雇主现金不足：留在原地练手艺；攒门派注册费的人优先去挣现钱。
                note = "雇主现金不足，先练手艺" if work_loc else "没有雇主，练手艺"
                cursor = self._add(
                    intents,
                    npc,
                    "cultivate",
                    {"skill": skill_for(self.content, ent.role), "hours": 4},
                    cursor,
                    seg_end,
                    reason(note),
                )

            # 午饭（一天一顿，减少无效往返；晚饭取消）
            loc, cursor, cash = self._meal(state, intents, npc, loc, cursor, seg_end, cash, "午饭")

            # 消费倾向：有余钱就买一件非口粮品，钱回流店铺；
            # 正在攒注册费的发起人不做可选消费，否则永远差最后几百文。
            saving_for_sect = (
                choice.content_type == "found_sect"
                and cash < int((self.content.economy.get("sect") or {}).get("founding_cost", 2500))
            )
            if not saving_for_sect:
                loc, cursor, cash = self._propensity_buy(state, intents, npc, loc, cursor, seg_end, cash, memo)

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

            # 自提案的独立额度：每天额外 1 次自目标深度动作，排在内容目标之后。
            # 内容目标的 depth 名额已经用满/排在前面，所以这里只会“不做”，
            # 不会回流挤占内容目标。
            if secondary is not None:
                if cursor >= seg_end:
                    self.secondary_steps_dropped += 1
                else:
                    before = len(intents)
                    loc, cursor, cash = self._goal_step(
                        state, intents, ent, secondary, loc, cursor, seg_end, 0, cash, secondary_memo
                    )
                    if len(intents) == before:
                        self.secondary_steps_dropped += 1
                    else:
                        self.secondary_steps += 1
                        for it in intents[before:]:
                            it.args.setdefault("_goal_id", secondary.goal_id)

            # 只在计划窗口的最后一天回家，避免每天往返
            if day == day_last:
                home = ent.home or ent.location
                loc, cursor = self._move(state, intents, npc, loc, home, cursor, seg_end, reason("回家"))

        return intents

    def _work_location(self, state: WorldState, ent) -> str:
        if ent.employed_at and ent.employed_at in state.businesses:
            return state.businesses[ent.employed_at].location
        return ""

    def _employer(self, state: WorldState, ent):
        if ent.employed_at and ent.employed_at in state.businesses:
            return state.businesses[ent.employed_at]
        return None

    def _gross_wage(self, ent) -> int:
        return max(1, int(round(self.content.wage_for(ent.role))))

    def _employer_is_player(self, employer) -> bool:
        """玩家开的店：owner 形如 player:<account>。NPC 店铺走既有规则。"""
        return bool(
            employer is not None
            and employer.owner
            and str(employer.owner).startswith("player:")
        )

    def _work_args(self, employer, at: str, hours: int) -> dict:
        """work 参数：玩家店铺额外带 `employer`，让事件能归因到具体店铺。

        NPC 雇主的 work 参数保持原样（只有 at/hours），无玩家雇佣的世界
        事件哈希逐字节不变。
        """
        args = {"at": at, "hours": hours}
        if self._employer_is_player(employer):
            args["employer"] = employer.id
        return args

    def _wage_for_employment(self, employer, ent) -> int:
        """雇工的实际日薪：玩家店铺优先读契约日薪，其余沿用岗位市场价。"""
        gross = self._gross_wage(ent)
        if self._employer_is_player(employer):
            wage = (getattr(employer, "employee_wages", None) or {}).get(ent.id)
            if wage:
                return max(1, int(wage))
        return gross

    def _player_employer_can_pay(self, state: WorldState, employer, gross: int) -> bool:
        """玩家雇主的偿付判断：玩家钱包实际余额 + 当前已入账的当日营收。

        刻意不套用 NPC 雇主的 2000 文储备（那是防 NPC 破产的设计）。
        判断结果只影响计划文案；即使付不出，雇工仍按契约出工，由日结记欠薪债。
        """
        wallet_bal = state.account_balance(f"wallet:{employer.owner}")
        today_revenue = int(getattr(employer, "day_revenue", 0))
        return wallet_bal + today_revenue >= gross

    def _net_wage(self, ent, gross: int | None = None) -> int:
        gross = self._gross_wage(ent) if gross is None else gross
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
        # 就近吃饭：当前地点有吃的就不动；没有才走，且不为一顿饭横穿全城。
        item = cheapest_food(self.content, loc, state, min_stock=1)
        if item is None:
            foods = set(food_locations(state, self.content))
            if not foods:
                return loc, cursor, cash
            food_loc = nearest_location(state, loc, foods)
            if food_loc is None:
                return loc, cursor, cash
            if len(path_between(state, loc, food_loc)) > 2:
                return loc, cursor, cash  # 太远，改日再吃（体力仍按日恢复）
            loc, cursor = self._move(state, intents, npc, loc, food_loc, cursor, end, f"{label}：去饭点")
            item = cheapest_food(self.content, loc, state, min_stock=1)
        if item is None:
            return loc, cursor, cash
        price = self.content.item_price(item, state)
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

    def _quest_step(
        self,
        state: WorldState,
        intents: list[Intent],
        npc: str,
        loc: str,
        cursor: int,
        end: int,
        cash: int,
        memo: dict[str, Any],
    ) -> tuple[str, int, int]:
        if memo.get("quest_executed") or cursor >= end:
            return loc, cursor, cash
        ent = state.entities[npc]
        quest_cfg = self.content.economy.get("quests") or {}
        limit = max(1, int(quest_cfg.get("max_concurrent", 2)))
        escort_limit = max(1, int(quest_cfg.get("max_concurrent_escort", 1)))
        max_hops = max(1, int(quest_cfg.get("accept_max_hops", 3)))
        accepted = sorted(
            (q for q in state.quests.values()
             if q.status == "accepted" and q.acceptor == npc
             and q.accepted_tick >= 0 and cursor - q.accepted_tick >= TICKS_PER_DAY),
            key=lambda q: q.id,
        )
        if accepted:
            q = accepted[0]
            last = self._quest_attempt.get(q.id, -TICKS_PER_DAY * 2)
            if cursor - last >= TICKS_PER_DAY:
                self._quest_attempt[q.id] = cursor
                memo["quest_executed"] = q.id
                return self._quest_execute(state, intents, npc, loc, cursor, end, cash, q)
        # 押镖单独立占位：不占普通任务的并发额度，避免长期被采购/跑腿挤掉。
        escort_accepted = [q for q in accepted if q.verb == "escort"]
        errand_accepted = [q for q in accepted if q.verb != "escort"]
        # 公平轮转：先服务最久没被接过的模板，平局再按 quest id 稳定排序。
        # 未服务过的模板用 -1 哨兵，保证它们整体排在已服务模板之前。
        open_quests = [q for q in state.quests.values() if q.status == "open"]
        open_quests.sort(key=lambda q: (self._quest_served.get(q.template_id, -1), q.id))
        if (len(errand_accepted) >= limit and len(escort_accepted) >= escort_limit) or memo.get(
            "quest_look"
        ):
            return loc, cursor, cash
        memo["quest_look"] = True
        for q in open_quests:
            if q.giver == npc or not self._quest_feasible(state, ent, q):
                continue
            if q.verb == "escort":
                if len(escort_accepted) >= escort_limit:
                    continue
            elif len(errand_accepted) >= limit:
                continue
            # 截止太近就不接，避免“接了也来不及交付”的必拒。
            if q.deadline_tick >= 0 and q.deadline_tick - cursor < TICKS_PER_DAY:
                continue
            giver = state.entities.get(q.giver)
            if giver is None or giver.status != "alive":
                continue
            if giver.location != loc:
                # 前置条件不满足（不在发起人处）：换路径——走近一点再谈，
                # 避免全城 NPC 为了接任务每天长途奔袭，把 move 占比重新推高。
                # 接单距离由内容包 economy.quests.accept_max_hops 给出（当前 3 跳）。
                path = path_between(state, loc, giver.location)
                if len(path) > max_hops:
                    continue
                loc, cursor = self._move(
                    state, intents, npc, loc, giver.location, cursor, end, "去找任务发起人"
                )
                if loc != giver.location or cursor >= end:
                    return loc, cursor, cash
            # 不再用“同地点最小 id 排他”：那会把 escort 任务永久卡在
            # 不合格的 NPC 身上（该地点最小 id 未必有押镖资格）。
            # 竞争由单写者事件顺序自然裁决，落败者最多产生少量 quest_not_open。
            last = self._quest_attempt.get(q.id, -TICKS_PER_DAY * 2)
            if cursor - last < TICKS_PER_DAY:
                continue
            self._quest_attempt[q.id] = cursor
            self._quest_served[q.template_id] = cursor
            cursor = self._add(
                intents, npc, "quest_accept", {"quest": q.id, "quest_verb": q.verb}, cursor, end,
                f"在{giver.name}处接下任务「{q.template_id}」",
            )
            memo["quest_executed"] = q.id
            return self._quest_execute(state, intents, npc, loc, cursor, end, cash, q)
        return loc, cursor, cash

    def _quest_feasible(self, state: WorldState, ent, quest) -> bool:
        """只接当前角色真能执行的任务，避免把必拒动作塞进计划。"""
        tpl = self.content.quests.get(quest.template_id) or {}
        if quest.verb == "escort":
            if not role_allowed(self.content, "escort", ent.role):
                return False
            path = [str(x) for x in (quest.args.get("route") or [])]
            if not path:
                return False
            for a, b in zip(path, path[1:]):
                if a not in state.locations or b not in state.locations[a].get("exits", []):
                    return False
            return True
        if quest.verb == "buy":
            at = str(quest.args.get("from") or quest.target_location)
            item = str(quest.args.get("item") or "")
            return any(b.stock.get(item, 0) >= 3 for b in state.business_at(at))
        if quest.verb == "work":
            at = str(quest.args.get("at") or quest.target_location)
            return bool([b for b in state.business_at(at) if b.kind != "service_room"])
        if quest.verb == "trade":
            target = state.entities.get(str(tpl.get("target_npc") or ""))
            if target is None or target.status != "alive":
                return False
            delta = int((quest.args or {}).get("money_delta") or 0)
            if delta > 0 and state.account_balance(wallet(target.id)) < delta:
                return False
            return True
        return True

    def _quest_execute(
        self,
        state: WorldState,
        intents: list[Intent],
        npc: str,
        loc: str,
        cursor: int,
        end: int,
        cash: int,
        quest,
    ) -> tuple[str, int, int]:
        ent = state.entities[npc]
        if cursor >= end:
            return loc, cursor, cash
        if quest.verb == "escort":
            path = [str(x) for x in (quest.args.get("route") or [])]
            if not path:
                return loc, cursor, cash
            if loc != path[0]:
                loc, cursor = self._move(state, intents, npc, loc, path[0], cursor, end, "去镖局接镖")
            if loc == path[0] and cursor < end and ent.escort_id is None:
                args = dict(quest.args)
                args["quest"] = quest.id
                for route in self.content.escort_routes.values():
                    if [str(x) for x in (route.get("route") or [])] == path:
                        args["route_id"] = str(route["id"])
                        break
                cursor = self._add(intents, npc, "escort", args, cursor, end, "押镖赶路")
            return loc, cursor, cash

        target_npc = state.entities.get(quest.target_npc) if quest.target_npc else None
        exec_loc = quest.target_location
        if target_npc is not None and target_npc.status == "alive":
            exec_loc = target_npc.location
        if quest.verb == "buy" or quest.verb == "work":
            exec_loc = str(quest.args.get("from") or quest.args.get("at") or quest.target_location)
        if quest.verb == "move":
            exec_loc = str(quest.args.get("to") or quest.target_location)
        if loc != exec_loc:
            loc, cursor = self._move(state, intents, npc, loc, exec_loc, cursor, end, "去办事")
        if loc == exec_loc and cursor < end:
            args = dict(quest.args)
            args["quest"] = quest.id
            if quest.verb == "trade" and quest.target_npc:
                args.setdefault("target", quest.target_npc)
            cursor = self._add(intents, npc, quest.verb, args, cursor, end, f"执行任务「{quest.template_id}」")
            post_loc = str(args.get("to") or exec_loc) if quest.verb == "move" else exec_loc
            giver = state.entities.get(quest.giver)
            if giver is not None and giver.status == "alive":
                loc, cursor = self._move(
                    state, intents, npc, post_loc, giver.location, cursor, end, "回去交付任务"
                )
                if loc == giver.location and cursor < end:
                    cursor = self._add(
                        intents, npc, "quest_deliver", {"quest": quest.id, "quest_verb": quest.verb},
                        cursor, end, "交付任务"
                    )
        return loc, cursor, cash

    def _propensity_buy(
        self,
        state: WorldState,
        intents: list[Intent],
        npc: str,
        loc: str,
        cursor: int,
        end: int,
        cash: int,
        memo: dict[str, Any],
    ) -> tuple[str, int, int]:
        """消费倾向：有余钱买一件非口粮品，钱回流店铺（非 top3 动作）。

        treasury_guard 期间暂停这类非紧急采购，把现金留给工资与已承诺镖单。
        """
        if memo.get("bought") or cursor >= end or cash < 120:
            return loc, cursor, cash
        if treasury_guard_active(state, self.content):
            return loc, cursor, cash
        best: tuple[int, str] | None = None
        for biz in state.business_at(loc):
            for iid, qty in sorted(biz.stock.items()):
                if qty < 3 or iid not in self.content.items:
                    continue
                if str(self.content.items[iid].get("category", "")) in ("food", "drink", "service", "meal"):
                    continue
                price = self.content.item_price(iid, state)
                if price <= 0 or price * 3 > cash:
                    continue
                if best is None or price < best[0]:
                    best = (price, iid)
        if best is None:
            return loc, cursor, cash
        price, iid = best
        before = cursor
        cursor = self._add(
            intents, npc, "buy", {"from": loc, "item": iid, "qty": 1},
            cursor, end, f"手头宽裕，添置{iid}",
        )
        if cursor != before:
            memo["bought"] = iid
            cash -= price + int(price * float((self.content.economy.get("taxes") or {}).get("transaction", 0.0)))
        return loc, cursor, cash

    def _recruit_target(self, state: WorldState, ent, sect_cfg: dict, members: list[str]):
        """优先按内容包 recruit_targets 选人，已结盟/已入派/已故者跳过。"""
        seen = set(members)
        for tid in section_ids(sect_cfg.get("recruit_targets")):
            other = state.entities.get(tid)
            if other is None or other.status != "alive" or other.id in seen:
                continue
            if other.faction and str(other.faction).startswith("sect:"):
                continue
            return other
        for rid in sorted(state.entities):
            other = state.entities[rid]
            if rid == ent.id or other.status != "alive" or rid in seen:
                continue
            if other.role == "official" or (other.faction and str(other.faction).startswith("sect:")):
                continue
            return other
        return None

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
        # 只有内容包指定的发起人能开对应门派；其他 NPC 的 found_sect 目标
        # 退化为“入会”，避免出现谢停云抢先开清议社这种冒名顶替。
        if ct == "found_sect" and not any(
            str(s.get("founder_candidate")) == npc for s in self.content.sects.values()
        ):
            ct = "join_sect"
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
            if ct == "urgent_debt":
                # 还款是插件动作（plugins/actions/debt_settle），证明插件层可用。
                if memo.get("debt_paid"):
                    return loc, cursor, cash
                for d in sorted(state.debts.values(), key=lambda x: (x.amount, x.id)):
                    if d.status != "active" or d.debtor != npc or d.amount <= 0:
                        continue
                    # 只还 system_debts 不会自动清偿的欠条（初始欠债/长期欠债），
                    # 避免与日终自动结算竞争产生 no_debt 必拒。
                    if d.note != "initial_debt" and not d.long_term:
                        continue
                    pay = min(int(d.amount), int(cash))
                    if pay <= 0:
                        break
                    before = cursor
                    cursor = self._add(
                        intents, npc, "debt_settle", {"debt": d.id, "amount": pay},
                        cursor, end, reason(f"还{d.creditor}的欠债{pay}文"),
                    )
                    if cursor != before:
                        memo["debt_paid"] = d.id
                        cash -= pay
                    return loc, cursor, cash
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
                        cash += sell_price(self.content, item, state)
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
            # P2-B：取消“加短工”——它会重复占用岗位并制造大量工资欠条。
            cursor = self._add(
                intents, npc, "cultivate",
                {"skill": skill_for(self.content, ent.role), "hours": 2},
                cursor, end, reason("加练手艺等机会"),
            )
            return loc, cursor, cash

        if ct == "urgent_recover":
            foods = set(food_locations(state, self.content))
            if foods:
                food_loc = nearest_location(state, loc, foods)
                if food_loc:
                    loc, cursor = self._move(state, intents, npc, loc, food_loc, cursor, end, reason("找吃的恢复体力"))
                item = cheapest_food(self.content, loc, state, min_stock=1)
                price = self.content.item_price(item, state) + int(
                    self.content.item_price(item, state) * float((self.content.economy.get("taxes") or {}).get("transaction", 0.0))
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
            # 金库不够「保底线 + 开店成本 + 该店 3 日预计工资」时延后，不生成 intent（不是拒绝）。
            if treasury_balance(state) < treasury_open_shop_buffer(self.content, cost, kind):
                self.postponed_open_shop += 1
                return loc, cursor, cash
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
                cash += sell_price(self.content, item, state)
            return loc, cursor, cash

        if ct in ("gain_status", "join_sect"):
            if any(str(s.get("founder_candidate")) == ent.id for s in self.content.sects.values()):
                # 门派发起人不去入别人的会：否则一旦 faction_join，found_sect 永远被
                # already_in_faction 挡住，门派就再也发不起来。
                return loc, cursor, cash
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
            sect_cfg = next(
                (s for s in self.content.sects.values() if s.get("founder_candidate") == ent.id),
                {},
            )
            if not sect_cfg:
                return loc, cursor, cash
            at = str(sect_cfg.get("location") or ent.home or ent.location)
            loc, cursor = self._move(state, intents, npc, loc, at, cursor, end, reason("回门派驻地"))
            min_rel = float((self.content.economy.get("sect") or {}).get("min_relation_value", 30))
            members = founding_members(state, ent, min_rel)
            need = max(5, int((self.content.economy.get("sect") or {}).get("min_members", 5)))
            cost = int((self.content.economy.get("sect") or {}).get(
                "founding_cost",
                max(int((self.content.economy.get("sect") or {}).get("registration_fee", 500)),
                    int((self.content.economy.get("sect") or {}).get("min_money", 2000))),
            ))
            if len(members) < need and not memo.get("recruited"):
                target = self._recruit_target(state, ent, sect_cfg, members)
                if target is not None:
                    loc, cursor = self._move(state, intents, npc, loc, target.location, cursor, end,
                                             reason(f"去招揽{target.name}"))
                    if loc == target.location and cursor < end:
                        cursor = self._add(
                            intents, npc, "recruit", {"target": target.id},
                            cursor, end, reason(f"邀{target.name}结盟（人手{len(members)}/{need}）"),
                        )
                        memo["recruited"] = target.id
                    return loc, cursor, cash
            if len(members) >= need and cash >= cost and cursor < end:
                cursor = self._add(
                    intents, npc, "found_sect",
                    {
                        "name": str(sect_cfg.get("name") or f"{ent.name}会"),
                        "at": loc,
                        "doctrine": "自守",
                        "sect_id": str(sect_cfg.get("id") or ""),
                    },
                    cursor, end, reason(f"凑齐{len(members)}人，纳{cost}文注册费开派"),
                )
                memo["founded"] = True
                cash -= cost
            elif len(members) >= need and cursor < end:
                # 人已齐、钱不够：先用能一笔补上缺口的贵重余物变现，再考虑白天的
                # 4 小时晚班（兼职）。两条都不靠发钱、不降门槛。
                gap = cost - cash
                item = self._best_sellable(ent, memo, state)
                if item is not None and sell_price(self.content, item, state) >= gap:
                    dest = self._sell_location(state, item, loc)
                    if dest:
                        loc, cursor = self._move(state, intents, npc, loc, dest, cursor, end,
                                                 reason(f"卖{item}凑注册费"))
                    if loc == dest and state.business_at(loc) and cursor < end:
                        cursor = self._add(
                            intents, npc, "sell", {"item": item, "qty": 1, "to": loc},
                            cursor, end, reason(f"卖{item}凑{cost}文注册费"),
                        )
                        self._mark_used(memo, item)
                        cash += sell_price(self.content, item, state)
                        return loc, cursor, cash
                employer = self._employer(state, ent)
                reserve = int((self.content.economy.get("work") or {}).get("employer_reserve", 300))
                half_shift = max(1, int(round(self._wage_for_employment(employer, ent) * 4 / 8.0)))
                player_employer = self._employer_is_player(employer)
                if (
                    employer is not None
                    and cursor < end
                    and (player_employer or state.account_balance(employer.cash_account) >= half_shift + reserve)
                ):
                    loc, cursor = self._move(state, intents, npc, loc, employer.location, cursor, end,
                                             reason(f"加班凑{cost}文注册费"))
                    if loc == employer.location and cursor < end:
                        cursor = self._add(
                            intents, npc, "work", self._work_args(employer, loc, 4),
                            cursor, end, reason(f"加班凑{cost}文注册费"),
                        )
                        cash += int(round(int(round(self._wage_for_employment(employer, ent) * 4 / 8.0)) * 0.9))
                        return loc, cursor, cash
            else:
                cursor = self._add(
                    intents, npc, "cultivate",
                    {"skill": skill_for(self.content, ent.role), "hours": 2},
                    cursor, end, reason(f"人手{len(members)}/{need}、现钱{cash}/{cost}，先攒资源"),
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

    def _best_sellable(self, ent, memo: dict[str, Any] | None = None, state: WorldState | None = None) -> str | None:
        """变现时优先卖最值钱的余物，避免先卖便宜货、越卖越穷。"""
        cands = [
            iid
            for iid in sorted(ent.inventory)
            if self._available(ent, iid, memo) > 0 and self.content.items.get(iid, {}).get("category") != "service"
        ]
        if not cands:
            return None
        return max(cands, key=lambda iid: (sell_price(self.content, iid, state), iid))

    def _sell_location(self, state: WorldState, item: str, loc: str) -> str | None:
        revenue = sell_price(self.content, item, state)
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
            if any(state.account_balance(b.cash_account) >= sell_price(self.content, item, state) for b in state.business_at(loc)):
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
            employer = state.businesses[ent.employed_at]
            return Intent(
                actor=npc, verb="work", args=self._work_args(employer, loc, 4),
                tick=tick, source="planner", reason=reason,
            )
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

    def __init__(self, seed: int = 42, content=None, telemetry=None, goal_policy: "GoalSwitchPolicy | None" = None):
        self.seed = seed
        self.content = content
        self.telemetry = telemetry
        self.selector = GoalSelector(content) if content is not None else None
        self.planner = Planner(content, seed) if content is not None else None
        self._planned_through: dict[str, int] = {}
        self._variant: dict[str, int] = {}
        self._last_reject: dict[str, str] = {}
        self.goal_policy = goal_policy
        # 目标承诺窗口的起点（决策 tick）；没有记录时回退到目标的 created_tick。
        self._goal_selected_tick: dict[str, int] = {}
        self.goal_switches: list[dict[str, Any]] = []
        self.goal_commitment_blocked = 0
        self.override_blocked_by_reason: dict[str, int] = {}
        self.override_blocked_examples: list[dict[str, Any]] = []
        self.quota_redirects = 0
        self.quota_redirect_examples: list[dict[str, Any]] = []
        self.decisions: list[dict[str, Any]] = []
        self.replan_count = 0
        self.postponed_open_shop = 0
        self.goal_override_skipped = 0
        # 自提案目标（默认关闭）：仅当环境变量显式指定 mode 时启用。
        self.self_goal_book = None
        self.self_secondary_steps = 0     # 独立额度实际排上的自目标动作数
        self.self_secondary_dropped = 0   # 当天没有空位、自目标动作被放弃的次数
        self._npc_index = {
            str(row.get("id")): i for i, row in enumerate(content.npcs or [])
        } if content is not None else {}
        mode = os.environ.get("WSIM_SELF_GOAL_MODE", "").strip().lower()
        if content is not None and mode in ("competent", "scenarios"):
            from .self_goal import SelfGoalLedger  # 延迟导入，避免启动期循环依赖

            def _env_int(name: str, default: int) -> int:
                raw = os.environ.get(name, "").strip()
                try:
                    return int(raw) if raw else default
                except ValueError:
                    return default

            self.self_goal_book = SelfGoalLedger(
                content,
                mode=mode,
                every_days=_env_int("WSIM_SELF_GOAL_EVERY_DAYS", 3),
                max_window_days=_env_int("WSIM_SELF_GOAL_MAX_WINDOW_DAYS", 3),
                trace_path=os.environ.get("WSIM_SELF_GOAL_TRACE", "").strip(),
            )
        # A4 战略层（默认关闭）：开启后 LLM 决策选择器被日界战略计划替代。
        self.strategy_book = None
        self.strategy_enabled = False
        # 内容目标优先级（1 最好，3 最差），用于确定性目标评分。
        self._priorities: dict[str, dict[str, float]] = {
            str(npc): {str(g.get("id", "")): float(g.get("priority", 5.0)) for g in goals}
            for npc, goals in (self.selector._content_goals.items() if self.selector is not None else [])
        }

    def enable_strategy(self, book) -> None:
        """接入 A4 战略计划台账；tick 级动作仍由本规则 planner 生成。"""
        self.strategy_book = book
        self.strategy_enabled = True
        if self.selector is not None:
            self.selector.strategy_book = book
            self.selector.strategy_mode = True

    def decide(
        self,
        npc_state: dict[str, Any],
        world_view: dict[str, Any],
        goal_override: str = "",
    ) -> list[Intent]:
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
        if self.self_goal_book is not None:
            # 自提案：按 NPC 自己的决策节奏提交，每 every_days 天一次。
            self.self_goal_book.maybe_issue(
                state, npc, tick, tier, self._npc_index.get(npc, 0)
            )
        current = self._active_goal_choice(state, npc)
        choice = self.selector.choose(state, npc, tick)
        choice_source = "planner"
        # A3：模型目标合法且可规划时直接采用；紧急/生存目标仍由规则抢占。
        # 开关化策略（默认关闭）：
        # - 承诺窗口：非紧急目标被选定后至少保持 commit_ticks 才允许切换；
        # - 滞回阈值：模型目标评分必须 > 当前目标评分 + delta 才允许覆盖。
        if goal_override and choice is not None and choice.source != "urgent":
            alt = self._choice_for_goal(state, npc, goal_override)
            if alt is not None:
                guard_reason = ""
                if self.goal_policy is not None and self.goal_policy.override_guard_v2:
                    allowed, guard_reason = self._override_guard_v2(state, npc, choice, alt)
                elif self.goal_policy is not None and self.goal_policy.enabled:
                    allowed = self._hysteresis_allows(state, current, alt)
                    guard_reason = "hysteresis" if not allowed else ""
                else:
                    allowed = self._override_not_worse(state, choice, alt, npc)
                    guard_reason = "rule_target_has_progress" if not allowed else ""
                if allowed:
                    choice = alt
                    choice_source = "model"
                else:
                    self.goal_override_skipped += 1
                    if guard_reason == "hysteresis":
                        self.goal_commitment_blocked += 1
                    self._record_switch(
                        tick, npc, current, alt, "model", "keep_blocked",
                        self._guard_reason_text(guard_reason),
                        state=state,
                        override_blocked_reason=guard_reason,
                    )
                    if (
                        guard_reason
                        and self.goal_policy is not None
                        and self.goal_policy.override_guard_v2
                    ):
                        self.override_blocked_by_reason[guard_reason] = (
                            self.override_blocked_by_reason.get(guard_reason, 0) + 1
                        )
                        if len(self.override_blocked_examples) < 8:
                            self.override_blocked_examples.append(
                                {
                                    "tick": int(tick),
                                    "npc": npc,
                                    "rule_goal": choice.goal_id,
                                    "model_goal": alt.goal_id,
                                    "reason": guard_reason,
                                    "rule_expected_probability": round(
                                        self._expected_completion_probability(state, npc, choice), 4
                                    ),
                                    "rule_progress": round(self._goal_progress(state, choice.goal_id), 4),
                                }
                            )
        # 自提案目标只拿独立额度：它是次级目标，不替换内容主线目标。
        self_choice: GoalChoice | None = None
        if self.self_goal_book is not None:
            self_abandon = self.self_goal_book.consume_expired(state, npc, start)
            if self_abandon and choice is not None:
                # 到期未完成：放弃自目标，内容目标照常继续。
                choice = replace(choice, abandon=self_abandon)
            self_choice = self.self_goal_book.choose(state, npc, start, tier)
        if choice is None:
            return []

        if self.goal_policy is not None and self.goal_policy.enabled:
            choice, choice_source = self._enforce_commitment(state, npc, tick, current, choice, choice_source)

        if self.goal_policy is not None and self.goal_policy.type_quota:
            choice, quota_note = self._apply_type_quota(state, npc, tick, current, choice, choice_source)
            if quota_note and choice_source == "model":
                choice_source = "planner"

        intents: list[Intent] = []
        active = state.entities[npc].goal_id if npc in state.entities else None
        if choice.create or active != choice.goal_id:
            if active and active != choice.goal_id:
                self._record_switch(
                    tick, npc, current, choice, choice_source, "switch", choice.reason, state=state
                )
            elif not active:
                self._record_switch(
                    tick, npc, None, choice, choice_source, "select", choice.reason, state=state
                )
            self._goal_selected_tick[npc] = tick
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

        # 次级自目标首次激活时建目标，但不切换 entity.goal_id（secondary=True）。
        if self_choice is not None and self_choice.create:
            intents.append(
                Intent(
                    actor=npc,
                    verb="system_goal",
                    tick=start,
                    source="planner",
                    args={
                        "goal": self_choice.goal_id,
                        "content_goal_id": self_choice.content_goal_id,
                        "content_type": self_choice.content_type,
                        "kind": self_choice.kind,
                        "target": self_choice.target,
                        "goal_source": "self",
                        "create": True,
                        "secondary": True,
                        "abandon": "",
                        "reason": self_choice.reason,
                    },
                    reason=self_choice.reason,
                )
            )

        plan = self.planner.plan(
            state, npc, choice, start, interval, tier,
            self._variant.get(npc, 0), secondary=self_choice,
        )
        self.postponed_open_shop += self.planner.postponed_open_shop
        self.planner.postponed_open_shop = 0
        self.self_secondary_steps += self.planner.secondary_steps
        self.self_secondary_dropped += self.planner.secondary_steps_dropped
        self.planner.secondary_steps = 0
        self.planner.secondary_steps_dropped = 0
        intents.extend(plan)
        if self.strategy_enabled and self.strategy_book is not None:
            strategy_plan = self.strategy_book.active_plan(npc, tick)
            if strategy_plan is not None:
                if choice.goal_id in strategy_plan.goal_ids:
                    actions = [i for i in plan if i.verb != "system_goal"]
                    self.strategy_book.record_consumption(
                        strategy_plan,
                        choice.goal_id,
                        tick,
                        scheduled=bool(actions),
                        actions=len(actions),
                    )
                elif choice.source != "urgent":
                    self.strategy_book.record_deviation(
                        strategy_plan, tick, "fallback_default_content", choice.goal_id
                    )
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
                "persona": (self.content.personas.get(npc, "") if self.content is not None else ""),
                "planned_through": self._planned_through[npc],
                "self_goal": self_choice.goal_id if self_choice is not None else "",
                "actions": [i.verb for i in intents],
            }
        )
        if self.telemetry is not None:
            # Mock 也产生成本遥测（确定性估算），便于以后与真实 Provider 对账。
            from ..agent.prompt import build_prompt  # 局部导入，避免包级循环依赖

            prompt = build_prompt(content, state, npc, tick, tier, ())
            output = json.dumps(
                {"actions": [{"verb": i.verb, "args": i.args, "reason": i.reason} for i in intents]},
                ensure_ascii=False,
                sort_keys=True,
            )
            self.telemetry.record(
                tick=tick,
                npc=npc,
                tier=tier,
                prompt_text=prompt.text(),
                output_text=output,
                model="mock",
                calls=1,
                persona_tokens=prompt.persona_tokens,
            )
        return intents

    # ---- 目标切换策略（承诺窗口 / 滞回 / 留痕）--------------------------------

    def _active_goal_choice(self, state: WorldState, npc: str) -> GoalChoice | None:
        ent = state.entities.get(npc)
        if ent is None or not ent.goal_id:
            return None
        goal = state.goals.get(ent.goal_id)
        if goal is None or goal.status != "active":
            return None
        return GoalChoice(
            npc=npc,
            goal_id=goal.id,
            content_goal_id=goal.content_goal_id,
            content_type=goal.content_type,
            kind=goal.kind,
            target=goal.target,
            source="urgent" if goal.content_type.startswith("urgent_") else "content",
            reason="保持当前目标",
            create=False,
        )

    def _goal_progress(self, state: WorldState, goal_id: str) -> float:
        goal = state.goals.get(goal_id)
        if goal is None or goal.target <= 0:
            return 0.0
        return min(1.0, max(0.0, float(goal.progress) / float(goal.target)))

    def _type_history_rate(self, state: WorldState, content_type: str) -> float:
        """同类型历史通过率（按当前 run 已创建目标，progress>0.1 视为通过）。

        早期样本少时用 0.5 先验 + alpha=4 平滑，保证确定性且不偏向某一类型。
        """
        created = [
            g for g in state.goals.values()
            if g.content_goal_id and g.content_type == content_type
        ]
        if not created:
            return 0.5
        passed = sum(1 for g in created if g.status == "completed" or self._goal_progress(state, g.id) > 0.1)
        alpha = 4.0
        return (passed + alpha * 0.5) / (len(created) + alpha)

    def _goal_prereq_ok(self, state: WorldState, npc: str, content_type: str) -> bool:
        ent = state.entities.get(npc)
        if ent is None:
            return False
        loc = ent.location
        if content_type == "build_relation":
            return any(
                other.id != npc and other.status == "alive" and other.location == loc
                for other in state.entities.values()
            )
        if content_type in ("earn_money", "build_shop"):
            return bool(state.business_at(loc))
        return True

    def _expected_completion_probability(self, state: WorldState, npc: str, choice: GoalChoice) -> float:
        """被覆盖目标的预期完成概率：类型历史通过率 × 前置可达性 + 当前进度加成。"""
        base = self._type_history_rate(state, choice.content_type)
        if not self._goal_prereq_ok(state, npc, choice.content_type):
            base *= 0.6
        progress = self._goal_progress(state, choice.goal_id)
        return min(0.99, base + (1.0 - base) * progress)

    def _guard_score(self, state: WorldState, npc: str, choice: GoalChoice) -> float:
        priority = self._priorities.get(npc, {}).get(choice.content_goal_id, 5.0)
        return self._expected_completion_probability(state, npc, choice) + 0.05 * (3.0 - priority)

    def _override_guard_v2(
        self, state: WorldState, npc: str, rule: GoalChoice, alt: GoalChoice
    ) -> tuple[bool, str]:
        """覆盖护栏 v2：保护高概率规则目标；放行时要求同类型且评分严格更高。"""
        if alt.goal_id == rule.goal_id:
            return True, ""
        if rule.source == "urgent" or rule.content_type.startswith("urgent_"):
            return False, "urgent_protected"
        threshold = (
            float(self.goal_policy.override_high_probability) if self.goal_policy is not None else 0.55
        )
        if self._expected_completion_probability(state, npc, rule) >= threshold:
            return False, "high_completion_probability"
        if alt.content_type != rule.content_type:
            return False, "type_mismatch"
        if self._guard_score(state, npc, alt) <= self._guard_score(state, npc, rule):
            return False, "score_not_strictly_higher"
        return True, ""

    @staticmethod
    def _guard_reason_text(reason: str) -> str:
        return {
            "high_completion_probability": "覆盖护栏 v2：规则目标预期完成概率高，禁止覆盖",
            "type_mismatch": "覆盖护栏 v2：模型目标与规则目标类型不同",
            "score_not_strictly_higher": "覆盖护栏 v2：模型目标评分未严格更高",
            "urgent_protected": "覆盖护栏 v2：紧急/生存目标受保护",
            "hysteresis": "滞回阈值未通过",
            "rule_target_has_progress": "规则目标已有进度，禁止覆盖",
            "type_quota": "类型配额补位",
        }.get(reason, reason or "覆盖被拒绝")

    def _deficit_types(self, state: WorldState, npc: str) -> list[str]:
        """返回该 NPC 尚未达到类型下限的核心目标类型（按固定顺序）。"""
        floor = int(self.goal_policy.type_quota_floor) if self.goal_policy is not None else 1
        rows = self.selector._content_goals.get(npc, []) if self.selector is not None else []
        owned = {str(r.get("type", "")) for r in rows if str(r.get("type", "")) in CORE_GOAL_TYPES}
        deficits = []
        for ctype in CORE_GOAL_TYPES:
            if ctype not in owned:
                continue
            activated = sum(
                1 for g in state.goals.values()
                if g.npc == npc and g.content_goal_id and g.content_type == ctype
            )
            if activated < floor:
                deficits.append(ctype)
        return deficits

    def _apply_type_quota(
        self,
        state: WorldState,
        npc: str,
        tick: int,
        current: GoalChoice | None,
        choice: GoalChoice,
        source: str,
    ) -> tuple[GoalChoice, str]:
        """选择目标时优先补足该 NPC 尚缺的核心类型（模型与规则轮转都适用）。"""
        if current is None or choice is None:
            return choice, ""
        if choice.source == "urgent" or choice.content_type.startswith("urgent_"):
            return choice, ""
        if choice.goal_id == current.goal_id:
            return choice, ""
        deficits = self._deficit_types(state, npc)
        if not deficits or choice.content_type in deficits:
            return choice, ""
        rows = self.selector._content_goals.get(npc, []) if self.selector is not None else []
        replacement = None
        for row in rows:
            ctype = str(row.get("type", ""))
            if ctype not in deficits:
                continue
            rid = str(row.get("id", ""))
            gid = f"{npc}:{rid}"
            sg = state.goals.get(gid)
            if sg is not None and sg.status in ("completed", "abandoned"):
                continue
            kind = goal_kind(ctype)
            replacement = GoalChoice(
                npc=npc,
                goal_id=gid,
                content_goal_id=rid,
                content_type=ctype,
                kind=kind,
                target=float(goal_target(self.content, kind)),
                source="content",
                reason=f"类型配额补位（{choice.content_type}→{ctype}）",
                create=sg is None,
            )
            break
        if replacement is None or replacement.goal_id == current.goal_id:
            return choice, ""
        self.quota_redirects += 1
        if len(self.quota_redirect_examples) < 8:
            self.quota_redirect_examples.append(
                {
                    "tick": int(tick),
                    "npc": npc,
                    "from_type": choice.content_type,
                    "to_type": replacement.content_type,
                    "to_goal": replacement.goal_id,
                    "deficits": deficits,
                }
            )
        return replacement, "type_quota"

    def _goal_score(self, state: WorldState, choice: GoalChoice) -> float:
        """确定性目标评分：内容优先级（1 最好）+ 已完成进度比例（0–1）。

        评分只用于滞回比较，不参与规划；同一状态 + 同一目标必得同一分数。
        紧急目标不参与比较，统一给 99。
        """
        if choice.source == "urgent" or choice.content_type.startswith("urgent_"):
            return 99.0
        goal = state.goals.get(choice.goal_id)
        ratio = 0.0
        if goal is not None and goal.target > 0:
            ratio = min(1.0, max(0.0, float(goal.progress) / float(goal.target)))
        priority = self._priorities.get(choice.npc, {}).get(choice.content_goal_id, 5.0)
        return (3.0 - priority) + ratio

    def _fmt_score(self, state: WorldState, choice: GoalChoice | None) -> str:
        return f"{self._goal_score(state, choice):.3f}" if choice is not None else "-inf"

    def _hysteresis_allows(
        self, state: WorldState, current: GoalChoice | None, alt: GoalChoice
    ) -> bool:
        if current is None or current.goal_id == alt.goal_id:
            return True
        # 生存/紧急目标优先，模型不得覆盖。
        if current.source == "urgent" or current.content_type.startswith("urgent_"):
            return False
        delta = float(self.goal_policy.hysteresis_delta) if self.goal_policy is not None else 0.0
        return self._goal_score(state, alt) > self._goal_score(state, current) + delta

    def _enforce_commitment(
        self,
        state: WorldState,
        npc: str,
        tick: int,
        current: GoalChoice | None,
        desired: GoalChoice,
        source: str,
    ) -> tuple[GoalChoice, str]:
        if current is None or desired.goal_id == current.goal_id:
            return desired, source
        # 紧急目标可以随时抢占；从紧急状态回到主线是规则已判定急事已了，也放行。
        if desired.source == "urgent" or desired.content_type.startswith("urgent_"):
            return desired, source
        if current.source == "urgent" or current.content_type.startswith("urgent_"):
            return desired, source
        goal = state.goals.get(current.goal_id)
        since = self._goal_selected_tick.get(npc)
        if since is None:
            since = goal.created_tick if goal is not None else tick
        elapsed = tick - since
        commit = int(self.goal_policy.commit_ticks) if self.goal_policy is not None else 0
        if elapsed < commit:
            self.goal_commitment_blocked += 1
            self._record_switch(
                tick, npc, current, desired, source, "keep_blocked",
                f"目标承诺窗口未满（{elapsed}/{commit} tick）",
                state=state,
            )
            return current, "planner"
        return desired, source

    def _record_switch(
        self,
        tick: int,
        npc: str,
        from_choice: GoalChoice | None,
        to_choice: GoalChoice,
        source: str,
        action: str,
        reason: str,
        *,
        state: WorldState,
        override_blocked_reason: str = "",
    ) -> None:
        """写一条可审计的目标切换/保持记录（确定性，不含墙钟/随机）。"""
        from_goal = from_choice.goal_id if from_choice is not None else ""
        from_progress = self._goal_progress(state, from_goal) if from_goal else None
        self.goal_switches.append(
            {
                "tick": int(tick),
                "npc": npc,
                "from_goal": from_goal,
                "to_goal": to_choice.goal_id,
                "source": source,
                "action": action,
                "reason": str(reason),
                "override_blocked_reason": override_blocked_reason,
                "from_progress": round(from_progress, 4) if from_progress is not None else None,
                "from_score": round(self._goal_score(state, from_choice), 4) if from_choice else None,
                "to_score": round(self._goal_score(state, to_choice), 4),
            }
        )

    def goal_switch_stats(self, horizon_tick: int) -> dict[str, Any]:
        """从留痕记录复算切换次数与目标平均存活 tick（按 NPC 分段）。"""
        rows = self.goal_switches
        switches = [r for r in rows if r["action"] == "switch"]
        attempts = [r for r in rows if r["action"] in ("switch", "keep_blocked")]
        blocked = [r for r in rows if r["action"] == "keep_blocked"]
        selections = [r for r in rows if r["action"] in ("select", "switch")]
        per_npc: dict[str, list[int]] = {}
        for r in selections:
            per_npc.setdefault(str(r["npc"]), []).append(int(r["tick"]))
        segments: list[int] = []
        closed: list[int] = []
        for npc, ticks in sorted(per_npc.items()):
            points = sorted(set(ticks))
            if not points:
                continue
            for a, b in zip(points, points[1:]):
                length = max(0, b - a)
                segments.append(length)
                closed.append(length)
            segments.append(max(0, int(horizon_tick) - points[-1]))
        by_source: dict[str, int] = {}
        for r in switches:
            by_source[str(r["source"])] = by_source.get(str(r["source"]), 0) + 1
        blocked_by_policy: dict[str, int] = {}
        for r in blocked:
            key = "hysteresis" if "滞回" in str(r["reason"]) else "commitment_window"
            blocked_by_policy[key] = blocked_by_policy.get(key, 0) + 1
        return {
            "switches": len(switches),
            "switch_attempts": len(attempts),
            "blocked": len(blocked),
            "blocked_by_policy": dict(sorted(blocked_by_policy.items())),
            "switches_by_source": dict(sorted(by_source.items())),
            "mean_survival_ticks": round(sum(segments) / len(segments), 2) if segments else 0.0,
            "mean_survival_ticks_uncensored": round(sum(closed) / len(closed), 2) if closed else 0.0,
            "segments": len(segments),
            "npcs": len(per_npc),
        }

    def _override_not_worse(self, state: WorldState, rule: GoalChoice, alt: GoalChoice, npc: str) -> bool:
        """启发式护栏：规则目标已有进度时不允许覆盖，避免摊薄累积型目标。"""
        if alt.goal_id == rule.goal_id:
            return True
        rule_prog = float(state.goals[rule.goal_id].progress) if rule.goal_id in state.goals else 0.0
        return rule_prog <= 0.0

    def _choice_for_goal(self, state: WorldState, npc: str, goal_override: str) -> GoalChoice | None:
        """把模型选择的目标 id 映射成规则可执行的目标；非法/已完成返回 None。

        A3：允许选择内容包里存在、但 world 里尚未创建的目标（create=True），
        这样模型才真正拥有“今天推哪个内容目标”的选择权。
        """
        raw = str(goal_override).strip()
        candidates = [raw] if ":" in raw else [f"{npc}:{raw}"]
        for gid in candidates:
            goal = state.goals.get(gid)
            if goal is not None:
                if goal.npc != npc or goal.status in ("completed", "abandoned"):
                    continue
                return GoalChoice(
                    npc=npc,
                    goal_id=goal.id,
                    content_goal_id=goal.content_goal_id,
                    content_type=goal.content_type,
                    kind=goal.kind,
                    target=goal.target,
                    source="model",
                    reason="模型选择今日目标",
                    # 已存在的目标只切换/保持，不重建，避免把进度清零。
                    create=False,
                )
            # 尚未创建：从内容包目标清单里找同 id 且类型可规划的目标。
            cid = gid.split(":", 1)[1] if ":" in gid else gid
            for row in self.selector._content_goals.get(npc, []) if self.selector is not None else []:
                if str(row.get("id")) != cid:
                    continue
                ctype = str(row.get("type", ""))
                if ctype not in CONTENT_GOAL_TYPES:
                    break
                kind = goal_kind(ctype)
                return GoalChoice(
                    npc=npc,
                    goal_id=f"{npc}:{cid}",
                    content_goal_id=cid,
                    content_type=ctype,
                    kind=kind,
                    target=float(goal_target(self.content, kind)),
                    source="model",
                    reason="模型选择今日目标",
                    create=True,
                )
        return None

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

    def replan(
        self,
        npc_state: dict[str, Any],
        world_view: dict[str, Any],
        timing: str = "replan",
    ) -> list[Intent]:
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
