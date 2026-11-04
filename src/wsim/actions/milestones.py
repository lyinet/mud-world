"""P4 里程碑链与维护型目标。

两个系统动作（日界执行）：
- `system_milestone`：一级内容目标完成后，为该 NPC 续接二级/三级目标；链走完后
  追加二级经营目标（雇伙计/收徒/置产）。只在一级内容目标全部“触达”后才启动，
  因此不会改变 K1 的内容目标分母与推进率。
- `system_maintenance`：店铺月租、门派月费、技艺生疏、关系衰减。完成不是终点，
  完成后按月/按旬继续产生义务。

确定性：全部按 id 排序遍历；不使用 random / 墙钟 / 外部数据。
"""
from __future__ import annotations

from ..core.models import Effect, Intent
from ..world.state import WorldState
from .common import organization_at, payment_with_shortfall, wallet
from .registry import Action

CONTENT_ALIVE = ("alive",)
TOUCHED_PROGRESS = 0.1
REPUTATION_KINDS = ("friend", "family", "master", "disciple", "ally", "marriage")


def _goal_key(npc: str, content_goal_id: str) -> str:
    return f"{npc}:{content_goal_id}"


def _last_tick(goal) -> int:
    return int(goal.progress_log[-1][0]) if goal.progress_log else int(goal.created_tick)


class _MilestoneBook:
    """从 economy.milestones 读取链与二级目标配置。"""

    def __init__(self, content):
        cfg = (content.economy.get("milestones") or {})
        self.chains: dict[str, list[dict]] = cfg.get("chains") or {}
        self.secondary: dict[str, dict] = cfg.get("secondary") or {}
        self.active_cap: int = int(cfg.get("active_cap", 0) or 0)

    @staticmethod
    def reputation(state: WorldState, npc: str) -> float:
        out = 0.0
        for rel in state.relations.values():
            if rel.status != "active" or npc not in (rel.a, rel.b):
                continue
            if rel.kind in REPUTATION_KINDS:
                out += max(0.0, float(rel.affection))
        return out


class SystemMilestoneAction(Action):
    verb = "system_milestone"

    def __init__(self, content):
        self.content = content
        self.book = _MilestoneBook(content)

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        tick = int(intent.tick)
        day = int(intent.args.get("day", 0))
        effects: list[Effect] = []
        # 归档“已轮转离开但仍在 active”的基础目标：它们已推进过（>=10%），
        # 保留 active 只会让开放目标数虚高，不影响 K1 的推进判定。
        # 只在最后两天清理，避免抢走 NPC 本来会回头完成的目标。
        if day >= 8:
            for gid in sorted(state.goals):
                sg = state.goals[gid]
                if sg.status != "active" or sg.source != "npc":
                    continue
                ent = state.entities.get(sg.npc)
                if ent is not None and ent.goal_id == gid:
                    continue
                if day >= 9 or float(sg.progress) >= TOUCHED_PROGRESS:
                    effects.append(Effect("goal_abandon", {"id": gid}))
        active_milestones = sum(
            1 for g in state.goals.values() if g.status == "active" and g.source == "milestone"
        )
        for npc in sorted(self.content.npcs, key=lambda n: str(n["id"])):
            eid = str(npc["id"])
            ent = state.entities.get(eid)
            if ent is None or ent.status != "alive":
                continue
            goals = sorted(
                [g for g in (npc.get("goals") or []) if str(g.get("id"))],
                key=lambda g: (float(g.get("priority", 9)), str(g.get("id"))),
            )
            eligible_types = set(self.book.chains) | set(self.book.secondary)
            base = [g for g in goals if str(g.get("type")) in eligible_types]
            if not base:
                continue
            # 已经有里程碑/备资目标在跑：一天只推进一个目标，不叠加。
            active = state.goals.get(ent.goal_id) if ent.goal_id else None
            if active is not None and active.status == "active" and active.source == "milestone":
                continue
            # 一级门槛：该 NPC 的全部基础目标都要“完成”（含 gain_status 等非链类型），
            # 否则宁可不启动里程碑，避免抢走基础轮转的产能、拉低完成目标数。
            if not all(self._completed(state, eid, str(g["id"])) for g in goals):
                continue
            if self.book.active_cap and active_milestones >= self.book.active_cap:
                continue
            # 选最早完成、且链还没走完的那条基础目标。
            candidates = []
            for g in base:
                sg = state.goals.get(_goal_key(eid, str(g["id"])))
                if sg is not None and sg.status == "completed":
                    candidates.append((_last_tick(sg), str(g["id"]), g))
            for _, base_id, g in sorted(candidates, key=lambda x: (x[0], x[1])):
                emitted = self._advance_one(state, eid, base_id, str(g.get("type")), tick)
                if emitted:
                    effects.extend(emitted)
                    active_milestones += 1
                    break
        return effects

    def _completed(self, state: WorldState, npc: str, goal_id: str) -> bool:
        sg = state.goals.get(_goal_key(npc, goal_id))
        return sg is not None and sg.status == "completed"

    def _touched(self, state: WorldState, npc: str, goal_id: str) -> bool:
        sg = state.goals.get(_goal_key(npc, goal_id))
        if sg is None:
            return False
        return sg.status in ("completed", "abandoned") or float(sg.progress) >= TOUCHED_PROGRESS

    def _advance_one(self, state: WorldState, npc: str, base_id: str, gtype: str, tick: int) -> list[Effect]:
        stages = list(self.book.chains.get(gtype) or [])
        done = self._completed_stages(state, npc, base_id, stages)
        if len(done) < len(stages):
            stage = stages[len(done)]
            gate_ok, prep = self._gate(state, npc, stage)
            if not gate_ok:
                return self._prep_goal(state, npc, base_id, prep, tick)
            return self._create(state, npc, base_id, stage, stage_number=int(stage.get("stage", len(done) + 2)),
                                key=f"ms{int(stage.get('stage', len(done) + 2))}", tick=tick)
        sec = self.book.secondary.get(gtype)
        if not sec:
            return []
        cycle = self._secondary_cycle(state, npc, base_id, str(sec.get("id", "sec")))
        if not bool(sec.get("repeat", False)) and cycle > 1:
            return []
        gate_ok, prep = self._gate(state, npc, sec)
        if not gate_ok:
            return self._prep_goal(state, npc, base_id, prep, tick)
        return self._create(
            state, npc, base_id, sec, stage_number=0, key=f"{sec.get('id', 'sec')}:{cycle}", tick=tick
        )

    def _completed_stages(self, state: WorldState, npc: str, base_id: str, stages: list[dict]) -> list[int]:
        out: list[int] = []
        for stage in stages:
            num = int(stage.get("stage", 0))
            sg = state.goals.get(_goal_key(npc, f"{base_id}:ms{num}"))
            if sg is not None and sg.status == "completed":
                out.append(num)
        return out

    def _secondary_cycle(self, state: WorldState, npc: str, base_id: str, sec_id: str) -> int:
        count = 0
        for gid, sg in state.goals.items():
            if sg.npc != npc:
                continue
            cid = str(sg.content_goal_id)
            if cid.startswith(f"{base_id}:{sec_id}") and sg.status == "completed":
                count += 1
        return count + 1

    def _gate(self, state: WorldState, npc: str, item: dict) -> tuple[bool, dict]:
        need_money = int(item.get("require_money") or 0)
        need_rep = float(item.get("require_reputation") or 0)
        if need_money > 0:
            have = int(state.account_balance(wallet(npc)))
            if have < need_money:
                return False, {"kind": "money", "need": need_money, "have": have}
        if need_rep > 0:
            have = _MilestoneBook.reputation(state, npc)
            if have < need_rep:
                return False, {"kind": "reputation", "need": need_rep, "have": have}
        return True, {}

    def _prep_goal(self, state: WorldState, npc: str, base_id: str, prep: dict, tick: int) -> list[Effect]:
        idx = 1
        for sg in state.goals.values():
            if sg.npc == npc and str(sg.content_goal_id).startswith(f"{base_id}:prep:"):
                idx += 1
        if prep.get("kind") == "money":
            ctype, kind = "earn_money", "wealth"
            target = max(10.0, float(prep.get("need", 10) - prep.get("have", 0)))
            label = "备资"
        else:
            ctype, kind = "build_relation", "bond"
            target = max(5.0, float(prep.get("need", 5) - prep.get("have", 0)))
            label = "攒人情"
        cgid = f"{base_id}:prep:{idx}"
        return [
            Effect(
                "goal_create",
                {
                    "id": _goal_key(npc, cgid),
                    "npc": npc,
                    "kind": kind,
                    "target": target,
                    "created_tick": tick,
                    "source": "milestone",
                    "content_goal_id": cgid,
                    "content_type": ctype,
                },
            ),
            Effect(
                "memory_add",
                {
                    "npc": npc,
                    "tick": tick,
                    "text": f"开始{label}：为下一阶段准备{('资金' if kind == 'wealth' else '人情')}（目标 {target:g}）",
                    "importance": 0.35,
                },
            ),
        ]

    def _create(
        self,
        state: WorldState,
        npc: str,
        base_id: str,
        item: dict,
        *,
        stage_number: int,
        key: str,
        tick: int,
    ) -> list[Effect]:
        cgid = f"{base_id}:{key}" if stage_number else f"{base_id}:{key}"
        goal_id = _goal_key(npc, cgid)
        if goal_id in state.goals:
            return []
        effects: list[Effect] = []
        # 归档已触达但未完成的基础目标，避免“旧目标仍开着”把开放目标数越堆越高。
        for gid in sorted(state.goals):
            sg = state.goals[gid]
            if sg.npc != npc or sg.status != "active" or sg.source != "npc":
                continue
            if float(sg.progress) >= TOUCHED_PROGRESS:
                effects.append(Effect("goal_abandon", {"id": gid}))
        effects.append(
            Effect(
                "goal_create",
                {
                    "id": goal_id,
                    "npc": npc,
                    "kind": str(item.get("kind", "wealth")),
                    "target": float(item.get("target", 1.0)),
                    "created_tick": tick,
                    "source": "milestone",
                    "content_goal_id": cgid,
                    "content_type": str(item.get("content_type", "")),
                },
            )
        )
        effects.append(
            Effect(
                "memory_add",
                {
                    "npc": npc,
                    "tick": tick,
                    "text": f"目标进阶：{str(item.get('label') or key)}（{goal_id}）",
                    "importance": 0.4,
                },
            )
        )
        return effects


class SystemMaintenanceAction(Action):
    """完成后的维护义务：月租、月费、技艺生疏、关系衰减。"""

    verb = "system_maintenance"

    def __init__(self, content):
        self.content = content

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        cfg = self.content.economy.get("maintenance") or {}
        day_no = int(intent.args.get("day", int(state.tick) // 144)) + 1
        tick = int(intent.tick)
        effects: list[Effect] = []
        if day_no % max(1, int(cfg.get("rent_period_days", 10))) == 0:
            effects.extend(self._rent(state, cfg, day_no, tick))
        if day_no % max(1, int(cfg.get("sect_dues_period_days", 10))) == 0:
            effects.extend(self._dues(state, cfg, day_no, tick))
        if day_no % max(1, int(cfg.get("skill_decay_period_days", 5))) == 0:
            effects.extend(self._skill_decay(state, cfg))
        if day_no % max(1, int(cfg.get("relation_decay_period_days", 5))) == 0:
            effects.extend(self._relation_decay(state, cfg))
        return effects

    def _rent(self, state: WorldState, cfg: dict, day_no: int, tick: int) -> list[Effect]:
        upkeep = (self.content.economy.get("shop_opening") or {}).get("monthly_upkeep") or {}
        effects: list[Effect] = []
        for biz in sorted(state.businesses.values(), key=lambda b: b.id):
            if not biz.owner:
                continue
            owner = state.entities.get(biz.owner)
            if owner is None or owner.status != "alive":
                continue
            amount = int(upkeep.get(biz.kind, 0))
            if amount <= 0:
                continue
            landlord = next(
                (b for b in sorted(state.business_at(biz.location), key=lambda x: x.id) if b.id != biz.id),
                None,
            )
            payee = landlord.id if landlord is not None else None
            if payee is None:
                org = organization_at(state, biz.location)
                payee = org.id if org is not None else None
            if payee is None:
                continue
            paid = min(amount, int(state.account_balance(wallet(biz.owner))))
            if paid >= amount:
                effects.extend(
                    self._obligation_goal(
                        owner,
                        f"rent:{biz.id}:{day_no}",
                        kind="wealth",
                        amount=amount,
                        label=f"交月租（{biz.id}）",
                        tick=tick,
                    )
                )
            effects.extend(
                payment_with_shortfall(
                    state,
                    biz.owner,
                    payee,
                    amount,
                    debt_id=f"debt:rent:{biz.id}:{day_no}",
                    created_tick=tick,
                    note="shop_rent",
                    transfer_reason="shop_rent",
                )
            )
        return effects

    def _dues(self, state: WorldState, cfg: dict, day_no: int, tick: int) -> list[Effect]:
        amount = int(cfg.get("sect_dues", 0))
        effects: list[Effect] = []
        if amount <= 0:
            return effects
        for fac in sorted(state.factions.values(), key=lambda f: f.id):
            for member in sorted(fac.members):
                ent = state.entities.get(member)
                if ent is None or ent.status != "alive":
                    continue
                paid = min(amount, int(state.account_balance(wallet(member))))
                if paid <= 0:
                    continue
                if paid >= amount:
                    effects.extend(
                        self._obligation_goal(
                            ent,
                            f"dues:{fac.id}:{day_no}",
                            kind="wealth",
                            amount=amount,
                            label=f"交月费（{fac.name}）",
                            tick=tick,
                        )
                    )
                effects.extend(
                    payment_with_shortfall(
                        state,
                        member,
                        fac.id,
                        amount,
                        debt_id=f"debt:dues:{fac.id}:{member}:{day_no}",
                        created_tick=tick,
                        note="sect_dues",
                        transfer_reason="sect_dues",
                    )
                )
        return effects

    def _obligation_goal(
        self,
        ent,
        key: str,
        *,
        kind: str,
        amount: int,
        label: str,
        tick: int,
    ) -> list[Effect]:
        """维护义务也记成目标：当月足额缴纳即当场完成，并恢复原有目标指针。"""
        prev = ent.goal_id or ""
        goal_id = f"{ent.id}:{key}"
        effects = [
            Effect(
                "goal_create",
                {
                    "id": goal_id,
                    "npc": ent.id,
                    "kind": kind,
                    "target": float(amount),
                    "created_tick": tick,
                    "source": "maintenance",
                    "content_goal_id": key,
                    "content_type": "maintenance",
                },
            ),
            Effect("goal_progress", {"id": goal_id, "kind": kind, "delta": float(amount)}),
        ]
        if prev:
            effects.append(Effect("set_field", {"entity": ent.id, "field": "goal_id", "value": prev}))
        return effects

    def _skill_decay(self, state: WorldState, cfg: dict) -> list[Effect]:
        amount = float(cfg.get("skill_decay", 0))
        top_n = int(cfg.get("decay_top_n", 5))
        if amount <= 0:
            return []
        candidates = [
            e
            for e in state.alive_entities()
            if not str(e.id).startswith("player:") and e.skills
        ]
        candidates.sort(key=lambda e: (int(e.last_action_tick), e.id))
        effects: list[Effect] = []
        for ent in candidates[:top_n]:
            skill = max(sorted(ent.skills), key=lambda k: (float(ent.skills.get(k, 0.0)), k))
            value = float(ent.skills.get(skill, 0.0))
            if value <= 0.0:
                continue
            delta = -min(amount, value)
            effects.append(Effect("skill", {"entity": ent.id, "skill": skill, "delta": delta}))
        return effects

    def _relation_decay(self, state: WorldState, cfg: dict) -> list[Effect]:
        amount = float(cfg.get("relation_decay", 0))
        top_n = int(cfg.get("decay_top_n", 5))
        if amount <= 0:
            return []
        rows = [
            r
            for r in state.relations.values()
            if r.status == "active" and float(r.affection) > 0
        ]
        rows.sort(key=lambda r: (str(r.a), str(r.b), str(r.kind)))
        effects: list[Effect] = []
        for rel in rows[:top_n]:
            effects.append(
                Effect(
                    "relation_upsert",
                    {
                        "a": rel.a,
                        "b": rel.b,
                        "kind": rel.kind,
                        "affection_delta": -min(amount, float(rel.affection)),
                    },
                )
            )
        return effects
