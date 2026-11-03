"""任务系统（P2-B）：内容包模板 -> 发布 / 接单 / 交付，全部走事件日志。"""
from __future__ import annotations

from ..core.clock import TICKS_PER_DAY
from ..core.models import Effect, Intent, Reject
from ..world.state import WorldState
from .common import memory, require_entity, wallet
from .registry import Action


class SystemQuestPostAction(Action):
    """每日发布：每个模板最多同时存在一条 open/accepted 任务。"""

    verb = "system_quest_post"

    def __init__(self, content):
        self.content = content

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        effects: list[Effect] = []
        qcfg = self.content.economy.get("quests") or {}
        expired_cap = int(qcfg.get("expired_cap_per_template", 3))
        expired_cap_global = int(qcfg.get("expired_cap_global", 20))
        min_samples = int(qcfg.get("min_samples_for_rate", 10))
        min_rate = float(qcfg.get("min_completion_rate", 0.2))
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
            # 可行性预检：giver 连报酬都拿不出，就不发这张单。
            if int(reward.get("money", 0)) > state.account_balance(wallet(giver)):
                continue
            giver_item = str(tpl.get("giver_item") or "")
            if giver_item:
                need = int(tpl.get("giver_item_qty", 1))
                if state.entities[giver].inventory.get(giver_item, 0) < need:
                    continue
            # 历史完成率过低（样本足够时）不再发单，避免模板空转占满委托列表。
            stats = state.quest_stats.get(tid) or {}
            created = int(stats.get("created", 0))
            completed = int(stats.get("completed", 0))
            if created >= min_samples and completed / created < min_rate:
                continue
            retained_expired = sum(1 for q in existing if q.status == "expired")
            if retained_expired >= expired_cap:
                continue
            if sum(1 for q in state.quests.values() if q.status == "expired") >= expired_cap_global:
                continue
            settlement = tpl.get("settlement") or {}
            qid = f"quest:{tid}:{intent.tick}"
            if qid in state.quests:
                continue
            effects.append(
                Effect(
                    "quest_create",
                    {
                        "id": qid,
                        "template_id": tid,
                        "giver": giver,
                        "target_npc": str(tpl.get("target_npc", "")),
                        "target_location": str(tpl.get("target_location", "")),
                        "verb": str(tpl["verb"]),
                        "args": dict(tpl.get("intent_args") or {}),
                        "reward_money": int(reward.get("money", 0)),
                        "reward_relation": float(reward.get("relation", 0.0)),
                        "deadline_tick": intent.tick + int(tpl.get("deadline_days", 1)) * TICKS_PER_DAY,
                        "created_tick": intent.tick,
                        "settlement_amount": int(settlement.get("amount", 0)),
                    },
                )
            )
        return effects


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
        giver = state.entities.get(quest.giver)
        if giver is None or giver.status != "alive":
            return ["giver_missing"]
        if ent.location != giver.location:
            return ["not_at_giver"]
        if ent.escort_id is not None:
            return ["busy_on_escort"]
        if ent.id.startswith("player:"):
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
            Effect("quest_accept", {"id": qid, "actor": ent.id, "tick": intent.tick}),
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
        if intent.tick > quest.deadline_tick:
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
        effects.append(Effect("quest_complete", {"id": quest.id, "tick": intent.tick}))
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
        for qid in sorted(state.quests):
            q = state.quests[qid]
            if q.status == "accepted" and intent.tick > q.deadline_tick:
                effects.append(Effect("quest_fail", {"id": qid, "tick": intent.tick}))
                if q.acceptor in state.entities:
                    effects.extend(
                        memory(state, q.acceptor, intent.tick, f"任务「{q.template_id}」超时未交付", 0.4)
                    )
            elif q.status == "open" and intent.tick > q.deadline_tick:
                # 无人接单且已过期：关闭模板占位，让后续 system_quest_post 能重发。
                # 不写 NPC 记忆（没有接单人就没人需要解释）。
                effects.append(Effect("quest_fail", {"id": qid, "tick": intent.tick}))
        # 过期/失败记录保留 7 天后清理；quest_stats 保留历史，不受清理影响。
        retention = int((self.content.economy.get("quests") or {}).get("expired_retention_days", 7))
        stale = [
            qid
            for qid, q in sorted(state.quests.items())
            if q.status in ("expired", "failed")
            and q.completed_tick >= 0
            and intent.tick - q.completed_tick > retention * TICKS_PER_DAY
        ]
        if stale:
            effects.append(Effect("quest_prune", {"ids": stale}))
        return effects
