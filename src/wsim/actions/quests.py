"""任务系统（P2-B）：内容包模板 -> 发布 / 接单 / 交付，全部走事件日志。"""
from __future__ import annotations

from ..core.clock import TICKS_PER_DAY, npc_tick_of
from ..core.models import Effect, Intent, Reject
from ..world.state import WorldState
from .common import (
    TREASURY_ACCOUNT,
    memory,
    quest_required_units,
    reputation_points,
    require_entity,
    wallet,
)
from .registry import Action


class SystemQuestPostAction(Action):
    """每日发布：每个模板最多同时存在一条 open/accepted 任务。

    在常规发布之外，每个“发布点”（有 giver 的地点）每世界日保底 1 张可接单：
    - 常规发布继续遵守模板完成率、过期上限、giver 现金等健康度门槛；
    - 保底单只在发布点当天没有“无声望门槛的 open 单”时补发，模板按世界日
      确定性轮转（`day % 候选数`，不引入随机数），优先采购/跑腿类；
    - 保底单跳过过期上限与完成率门槛（这两项用于防刷屏，不能反过来饿死供给），
      但仍要求 giver 存活、无同类在途、giver_item 有货；
    - giver 现金不足时由区域金库 `treasury:region` 通过真实 `transfer` 垫付差额，
      不 mint、不产生欠账。金库不足则放弃该点当日保底（无凭空造钱）。
    """

    verb = "system_quest_post"

    def __init__(self, content):
        self.content = content

    # ------------------------------------------------------------ 发布单元
    @staticmethod
    def _quest_effect(
        tpl: dict,
        tid: str,
        giver: str,
        tick: int,
        *,
        clock_tick: int | None = None,
        floor: bool = False,
        advance: int = 0,
    ) -> Effect:
        settlement = tpl.get("settlement") or {}
        # 截止时间是 NPC 时钟单位（显示与判定同源）；created_tick/id 仍是事件轴 tick。
        deadline_base = int(tick) if clock_tick is None else int(clock_tick)
        data = {
            "id": f"quest:{tid}:{tick}",
            "template_id": tid,
            "giver": giver,
            "target_npc": str(tpl.get("target_npc", "")),
            "target_location": str(tpl.get("target_location", "")),
            "verb": str(tpl["verb"]),
            "args": dict(tpl.get("intent_args") or {}),
            "reward_money": int((tpl.get("reward") or {}).get("money", 0)),
            "reward_relation": float((tpl.get("reward") or {}).get("relation", 0.0)),
            "deadline_tick": deadline_base + int(tpl.get("deadline_days", 1)) * TICKS_PER_DAY,
            "created_tick": tick,
            "settlement_amount": int(settlement.get("amount", 0)),
        }
        if floor:
            # 可审计字段：区分保底单，并记录当日金库垫付额（0 表示 giver 自有现金足够）。
            data["floor"] = True
            data["advance_money"] = advance
        return Effect("quest_create", data)

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        effects: list[Effect] = []
        qcfg = self.content.economy.get("quests") or {}
        expired_cap = int(qcfg.get("expired_cap_per_template", 3))
        expired_cap_global = int(qcfg.get("expired_cap_global", 20))
        min_samples = int(qcfg.get("min_samples_for_rate", 10))
        min_rate = float(qcfg.get("min_completion_rate", 0.2))
        created: set[str] = set()
        for tid in sorted(self.content.quests):
            tpl = self.content.quests[tid]
            giver = str(tpl.get("giver", ""))
            if giver not in state.entities or state.entities[giver].status != "alive":
                continue
            existing = [q for q in state.quests.values() if q.template_id == tid]
            if any(q.status in ("open", "accepted") for q in existing):
                continue
            if not bool(tpl.get("repeatable", True)) and existing:
                continue
            reward = tpl.get("reward") or {}
            # 可行性预检：giver 连报酬都拿不出，就不发这张单（保底路径另行垫付）。
            if int(reward.get("money", 0)) > state.account_balance(wallet(giver)):
                continue
            giver_item = str(tpl.get("giver_item") or "")
            if giver_item:
                need = int(tpl.get("giver_item_qty", 1))
                if state.entities[giver].inventory.get(giver_item, 0) < need:
                    continue
            # 历史完成率过低（样本足够时）不再发单，避免模板空转占满委托列表。
            stats = state.quest_stats.get(tid) or {}
            created_n = int(stats.get("created", 0))
            completed = int(stats.get("completed", 0))
            if created_n >= min_samples and completed / created_n < min_rate:
                continue
            # 过期 open 单已在日界“先下架再补发”中被 prune，这里统计的是仍在
            # 世界的过期记录（保留期内），不会再永久累计、封停模板。
            retained_expired = sum(1 for q in existing if q.status == "expired")
            if retained_expired >= expired_cap:
                continue
            if sum(1 for q in state.quests.values() if q.status == "expired") >= expired_cap_global:
                continue
            qid = f"quest:{tid}:{intent.tick}"
            if qid in state.quests:
                continue
            effects.append(
                self._quest_effect(
                    tpl, tid, giver, intent.tick, clock_tick=npc_tick_of(intent)
                )
            )
            created.add(tid)
        effects.extend(self._floor_posts(state, intent, created))
        return effects

    # ------------------------------------------------------------ 保底供给
    def _floor_posts(
        self, state: WorldState, intent: Intent, created: set[str]
    ) -> list[Effect]:
        """每个有存活 giver 的地点，当日至少一张对新手开放的可接单。"""
        day = int(intent.args.get("day", intent.tick // TICKS_PER_DAY))
        effects: list[Effect] = []
        treasury_left = state.account_balance(TREASURY_ACCOUNT)
        points: dict[str, list[str]] = {}
        for tid in sorted(self.content.quests):
            tpl = self.content.quests[tid]
            giver = str(tpl.get("giver", ""))
            ent = state.entities.get(giver)
            if ent is None or ent.status != "alive":
                continue
            # 没有声望门槛的模板才算“新手可接”，否则板面仍可能是 0 可接。
            if float(tpl.get("require_reputation", 0) or 0) > 0:
                continue
            points.setdefault(ent.location, []).append(tid)
        for loc in sorted(points):
            if self._has_acceptable_open(state, loc, created):
                continue
            candidates = [
                tid for tid in points[loc]
                if self._floor_eligible(state, tid, created, allow_inflight=False)
            ]
            if not candidates:
                # 次级兜底：该点所有模板都已在途（accepted）时，允许 repeatable
                # 模板再开一张，避免“都在送、板面为空”。同一 tick 仍只补一张。
                candidates = [
                    tid for tid in points[loc]
                    if self._floor_eligible(state, tid, created, allow_inflight=True)
                ]
            if not candidates:
                continue
            preferred = [
                tid for tid in candidates
                if str(self.content.quests[tid].get("category", "")) in ("procurement", "errand")
            ]
            rest = [tid for tid in candidates if tid not in preferred]
            for group in (preferred, rest):
                if not group:
                    continue
                posted = self._post_first_in_rotation(
                    state, intent.tick, day, group, created, effects, treasury_left,
                    clock_tick=npc_tick_of(intent),
                )
                if posted is not None:
                    treasury_left -= posted
                    break
        return effects

    def _has_acceptable_open(
        self, state: WorldState, loc: str, created: set[str]
    ) -> bool:
        for q in state.quests.values():
            if q.status != "open":
                continue
            tpl = self.content.quests.get(q.template_id) or {}
            if float(tpl.get("require_reputation", 0) or 0) > 0:
                continue
            giver = state.entities.get(q.giver)
            if giver is not None and giver.status == "alive" and giver.location == loc:
                return True
        for tid in created:
            tpl = self.content.quests.get(tid) or {}
            if float(tpl.get("require_reputation", 0) or 0) > 0:
                continue
            giver = state.entities.get(str(tpl.get("giver", "")))
            if giver is not None and giver.status == "alive" and giver.location == loc:
                return True
        return False

    def _floor_eligible(
        self, state: WorldState, tid: str, created: set[str], *, allow_inflight: bool
    ) -> bool:
        """保底候选：只排除客观不可发，不套用健康度上限。"""
        if tid in created:
            return False
        tpl = self.content.quests[tid]
        giver = str(tpl.get("giver", ""))
        existing = [q for q in state.quests.values() if q.template_id == tid]
        if bool(tpl.get("repeatable", True)):
            if any(q.status == "open" for q in existing):
                return False
            # accepted 在途是否算阻塞：常规候选算阻塞，兜底候选放行一张。
            if not allow_inflight and any(q.status == "accepted" for q in existing):
                return False
        elif existing:
            return False
        giver_item = str(tpl.get("giver_item") or "")
        if giver_item and state.entities[giver].inventory.get(giver_item, 0) < int(
            tpl.get("giver_item_qty", 1)
        ):
            return False
        return True

    def _post_first_in_rotation(
        self,
        state: WorldState,
        tick: int,
        day: int,
        group: list[str],
        created: set[str],
        effects: list[Effect],
        treasury_left: int,
        *,
        clock_tick: int | None = None,
    ) -> int | None:
        """确定性轮转：从 `day % n` 起按模板 id 顺序找第一张可发单。"""
        start = day % len(group)
        for tid in group[start:] + group[:start]:
            if tid in created:
                continue
            tpl = self.content.quests[tid]
            giver = str(tpl.get("giver", ""))
            reward = int((tpl.get("reward") or {}).get("money", 0))
            balance = state.account_balance(wallet(giver))
            advance = max(0, reward - balance)
            if advance > treasury_left:
                continue
            if advance > 0:
                qid = f"quest:{tid}:{tick}"
                effects.append(
                    Effect(
                        "transfer",
                        {
                            "src": TREASURY_ACCOUNT,
                            "dst": wallet(giver),
                            "amount": advance,
                            "reason": "quest_reward_advance",
                            "quest": qid,
                            "template": tid,
                            "day": day,
                        },
                    )
                )
            effects.append(
                self._quest_effect(
                    tpl, tid, giver, tick, clock_tick=clock_tick, floor=True, advance=advance
                )
            )
            created.add(tid)
            return advance
        return None


class QuestAcceptAction(Action):
    verb = "quest_accept"

    def __init__(self, content):
        self.content = content

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        qid = str(intent.args.get("quest") or "")
        quest = state.quests.get(qid)
        if quest is None:
            return ["quest_not_found"]
        if quest.status != "open":
            return ["quest_not_open"]
        if quest.deadline_tick and npc_tick_of(intent) > quest.deadline_tick:
            return ["quest_expired"]
        giver = state.entities.get(quest.giver)
        if giver is None or giver.status != "alive":
            return ["giver_missing"]
        if ent.location != giver.location:
            return ["not_at_giver"]
        if ent.escort_id is not None:
            return ["busy_on_escort"]
        if ent.id.startswith("player:"):
            tpl = self.content.quests.get(quest.template_id) or {}
            need = float(tpl.get("require_reputation", 0) or 0)
            if need > 0 and reputation_points(state, ent.id) < need:
                return ["reputation_too_low"]
            active = sum(
                1 for q in state.quests.values()
                if q.status == "accepted" and q.acceptor == ent.id
            )
            if active >= 2:
                return ["too_many_quests"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        qid = str(intent.args.get("quest"))
        quest = state.quests.get(qid)
        if quest is None:
            raise Reject("quest_not_found")
        giver = state.entities.get(quest.giver)
        if giver is None or ent.location != giver.location:
            raise Reject("not_at_giver")
        return [
            Effect("quest_accept", {"id": qid, "actor": ent.id, "tick": npc_tick_of(intent)}),
            # 玩家接单即建立可见进度（0 单位）；NPC 接单不写，保持无玩家世界哈希不变。
            *(
                [
                    Effect(
                        "quest_step",
                        {
                            "id": qid,
                            "actor": ent.id,
                            "units": 0,
                            "tick": intent.tick,
                            "verb": quest.verb,
                            "note": "已接单，等待完成目标动作",
                        },
                    )
                ]
                if ent.id.startswith("player:")
                else []
            ),
            *memory(state, ent.id, intent.tick, f"接下{giver.name}的任务「{quest.template_id}」", 0.4),
        ]


class QuestDeliverAction(Action):
    verb = "quest_deliver"

    def __init__(self, content):
        self.content = content

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        qid = str(intent.args.get("quest") or "")
        quest = state.quests.get(qid)
        if quest is None:
            return ["quest_not_found"]
        if quest.status != "accepted" or quest.acceptor != ent.id:
            return ["quest_not_accepted_by_actor"]
        if npc_tick_of(intent) > quest.deadline_tick:
            return ["quest_expired"]
        tpl = self.content.quests.get(quest.template_id) or {}
        giver = state.entities.get(quest.giver)
        at_target = ent.location == quest.target_location
        at_giver = giver is not None and ent.location == giver.location
        if not (at_target or at_giver):
            return ["not_at_target"]
        if str(tpl.get("category", "")) == "procurement":
            item = str(tpl.get("target_item") or "")
            if not item or ent.inventory.get(item, 0) < 1:
                return ["quest_item_missing"]
        # 玩家必须真的做完目标动作（NPC 任务链保持原语义）；判定口径与记进度一致。
        if ent.id.startswith("player:") and quest.progress_units < quest_required_units(quest):
            return ["quest_step_incomplete"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        quest = state.quests[str(intent.args.get("quest"))]
        giver = state.entities.get(quest.giver)
        if giver is None:
            raise Reject("giver_missing")
        tpl = self.content.quests.get(quest.template_id) or {}
        effects: list[Effect] = []
        if str(tpl.get("category", "")) == "procurement":
            effects.append(Effect("item_consume", {"owner": ent.id, "item": str(tpl["target_item"]), "qty": 1}))
        # 代收款（讨债）：先把手里的钱交回雇主，再领取跑腿钱。
        if quest.settlement_amount > 0:
            remit = min(quest.settlement_amount, state.account_balance(wallet(ent.id)))
            if remit > 0:
                effects.append(
                    Effect(
                        "transfer",
                        {
                            "src": wallet(ent.id),
                            "dst": wallet(giver.id),
                            "amount": remit,
                            "reason": "quest_settlement",
                            "quest": quest.id,
                        },
                    )
                )
        reward = quest.reward_money
        pay = min(reward, state.account_balance(wallet(giver.id)))
        if pay > 0:
            effects.append(Effect("transfer", {"src": wallet(giver.id), "dst": wallet(ent.id), "amount": pay,
                                               "reason": "quest_reward", "quest": quest.id}))
        if quest.reward_relation > 0:
            effects.append(
                Effect(
                    "relation_upsert",
                    {"a": ent.id, "b": giver.id, "kind": "friend",
                     "affection": min(100.0, quest.reward_relation * 5), "trust": 45.0},
                )
            )
        effects.append(Effect("quest_complete", {"id": quest.id, "tick": npc_tick_of(intent)}))
        effects.extend(
            memory(
                state,
                ent.id,
                intent.tick,
                f"交付「{quest.template_id}」，得报酬{pay}文" + (f"，代交{quest.settlement_amount}文" if quest.settlement_amount else ""),
                0.5,
            )
        )
        return effects


class SystemQuestFailAction(Action):
    verb = "system_quest_fail"

    def __init__(self, content):
        self.content = content

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        effects: list[Effect] = []
        clock = npc_tick_of(intent)
        for qid in sorted(state.quests):
            q = state.quests[qid]
            if q.status == "accepted" and clock > q.deadline_tick:
                effects.append(Effect("quest_fail", {"id": qid, "tick": clock}))
                if q.acceptor in state.entities:
                    effects.extend(
                        memory(state, q.acceptor, intent.tick, f"任务「{q.template_id}」超时未交付", 0.4)
                    )
            elif q.status == "open" and clock > q.deadline_tick:
                # 无人接单且已过期：标记 expired；在线玩家世界按日界补发前先下架，
                # 避免过期单留在板上、也避免它累积占满补发配额。
                # 不写 NPC 记忆（没有接单人就没人需要解释）；历史仍留在 quest_stats。
                effects.append(Effect("quest_fail", {"id": qid, "tick": clock}))
                if bool(intent.args.get("prune_expired", False)):
                    effects.append(Effect("quest_prune", {"ids": [qid]}))
        # 过期/失败记录保留 7 天后清理；quest_stats 保留历史，不受清理影响。
        retention = int((self.content.economy.get("quests") or {}).get("expired_retention_days", 7))
        stale = [
            qid
            for qid, q in sorted(state.quests.items())
            if q.status in ("expired", "failed")
            and q.completed_tick >= 0
            and clock - q.completed_tick > retention * TICKS_PER_DAY
        ]
        if stale:
            effects.append(Effect("quest_prune", {"ids": stale}))
        # completed 记录同样会永久累积：保留“7 个世界日”或“数量上限”，二者先触发。
        # 只清理投影内存（state.quests），事件历史一条不改；清理仍是确定性 Effect，
        # 回放同一事件流得到逐字节相同的 state_hash。
        # - retention：与经济包 expired_retention_days 同口径，7 世界日；
        # - cap 1000：保留期在“快时钟”（1 世界日≈1 秒）下先触发，上限只兜底慢时钟世界，
        #   1000 条约 1MB 量级，不影响读路径。
        # 无玩家世界不清理 completed，保证 state_hash 与旧基线逐字节一致
        # （同 state.py “无玩家世界哈希不变”的约定）。
        if state.players:
            qcfg = self.content.economy.get("quests") or {}
            keep_days = int(qcfg.get("completed_retention_days", 7))
            keep_max = int(qcfg.get("completed_max", 1000))
            completed = sorted(
                (q.completed_tick, qid)
                for qid, q in state.quests.items()
                if q.status == "completed" and q.completed_tick >= 0
            )
            # 时间维度命中项一定是排序后的前缀，可与数量上限取并集（取较长的前缀）。
            by_time = sum(1 for ctick, _ in completed if clock - ctick > keep_days * TICKS_PER_DAY)
            prune_n = max(by_time, len(completed) - keep_max)
            for start in range(0, prune_n, 512):
                ids = [qid for _, qid in completed[start:min(start + 512, prune_n)]]
                effects.append(Effect("quest_prune", {"ids": ids}))
        return effects
