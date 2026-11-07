"""xkx 支线任务：接取（ask）与交付（give）。

契约：
- 进度只写世界状态 `WorldState.xkx_quests`（经 `xkx_quest_upsert` Effect），
  前端与 shell 只读，不在客户端判完成；
- 交付前由后端逐项校验：任务是否存在、是否已接取、目标 NPC 是否在场、
  背包是否真有交付物，任何一项不满足都返回明确原因码；
- 动态任务奖励走 `PRNG(seed, seq, salt)`，取数顺序固定为
  exp → pot 除数 → score，测试锁死；
- 本模块只返回 Effect，不直接改 state。
"""
from __future__ import annotations

from typing import Any

from ..core.models import Effect, Intent
from ..world.state import WorldState
from .registry import Action


def _player_id(intent: Intent) -> str:
    pid = str(intent.args.get("player_id") or "").strip()
    if pid.startswith("player:"):
        return pid
    actor = str(intent.actor or "").strip()
    return actor if actor.startswith("player:") else ""


def _quest_key(pid: str, qid: str) -> str:
    return f"{pid}|{qid}"


def _quests(content: Any) -> dict[str, dict]:
    return dict(getattr(content, "xkx_quests", {}) or {})


def _giver_quests(content: Any, npc: str, text: str) -> list[dict]:
    """按发放 NPC + 话题筛选任务；话题命中要求原文出现在台词里。"""
    raw = str(text or "").strip()
    matched = []
    for qid in sorted(_quests(content)):
        quest = _quests(content)[qid]
        giver = quest.get("giver") or {}
        if str(giver.get("id") or "") != npc:
            continue
        topic = str(giver.get("topic") or "")
        if topic and raw and topic not in raw and raw not in topic:
            continue
        matched.append(quest)
    return matched


def _deliver_quest(content: Any, npc: str, item: str) -> dict | None:
    """交付目标 + 物品唯一确定一个任务；多个候选按 id 排序取第一个。"""
    for qid in sorted(_quests(content)):
        quest = _quests(content)[qid]
        target = quest.get("target") or {}
        required = quest.get("required_item") or {}
        if str(target.get("id") or "") != npc:
            continue
        if str(required.get("id") or "") != item:
            continue
        return quest
    return None


def _at_npc(state: WorldState, pid: str, npc: str) -> str:
    """校验 NPC 与玩家同处一室；返回空串表示通过。"""
    ent = state.entities.get(pid)
    npc_ent = state.entities.get(npc)
    if npc_ent is None:
        return "target_not_found"
    if ent is None or npc_ent.location != ent.location:
        return "not_here"
    return ""


def _record(state: WorldState, pid: str, qid: str) -> dict | None:
    return state.xkx_quests.get(_quest_key(pid, qid))


def _dynamic_reward(quest: dict, rng) -> dict[str, int]:
    """原版 `questd.lpc` 动态奖励，取数顺序不可调换。

    exp = 100 + random(400)
    pot = exp / (random(10) + 1)
    score = 50 + random(50)
    """
    reward = quest.get("reward") or {}
    exp_conf = reward.get("exp") or {}
    pot_conf = reward.get("pot") or {}
    score_conf = reward.get("score") or {}
    exp = int(exp_conf.get("base", 0)) + int(rng.randint(0, int(exp_conf.get("random", 0)) - 1))
    divisor = int(rng.randint(0, int(pot_conf.get("random", 0)) - 1)) + int(pot_conf.get("plus", 1))
    potential = exp // divisor
    score = int(score_conf.get("base", 0)) + int(rng.randint(0, int(score_conf.get("random", 0)) - 1))
    return {"exp": int(exp), "potential": int(potential), "score": int(score)}


class XkxQuestAcceptAction(Action):
    """问话接取：等价原版 `ask <npc> about <topic>`。"""

    verb = "xkx_quest_accept"

    def __init__(self, content: Any = None) -> None:
        self.content = content

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        pid = _player_id(intent)
        if not pid:
            return ["player_required"]
        ent = state.entities.get(pid)
        if ent is None:
            return ["player_not_entered"]
        if ent.status == "dead":
            return ["player_dead"]
        npc = str(intent.args.get("npc") or "").strip()
        if not npc:
            return ["target_required"]
        reason = _at_npc(state, pid, npc)
        if reason:
            return [reason]
        quests = _giver_quests(self.content, npc, str(intent.args.get("topic") or ""))
        if not quests:
            return ["quest_giver_none"]
        qid = str(quests[0]["id"])
        record = _record(state, pid, qid)
        if record is not None and record.get("status") == "completed":
            return ["quest_already_completed"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        pid = _player_id(intent)
        npc = str(intent.args.get("npc") or "").strip()
        quest = _giver_quests(self.content, npc, str(intent.args.get("topic") or ""))[0]
        qid = str(quest["id"])
        if _record(state, pid, qid) is not None:
            return []
        effects: list[Effect] = [
            Effect(
                "xkx_quest_upsert",
                {
                    "actor": pid,
                    "quest": qid,
                    "status": "accepted",
                    "step": "asked",
                    "tick": int(intent.tick),
                    "talked_tick": int(intent.tick),
                },
            )
        ]
        required = quest.get("required_item") or {}
        if required.get("id"):
            effects.append(
                Effect(
                    "item_create",
                    {"owner": pid, "item": str(required["id"]), "qty": 1},
                )
            )
        return effects


class XkxQuestDeliverAction(Action):
    """交付：`give <item> to <npc>`。完成后奖励在同一事务里结算。"""

    verb = "xkx_quest_deliver"
    aliases = ("give",)

    def __init__(self, content: Any = None) -> None:
        self.content = content

    def _resolve(self, state: WorldState, intent: Intent):
        return _deliver_quest(
            self.content,
            str(intent.args.get("npc") or "").strip(),
            str(intent.args.get("item") or "").strip(),
        )

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        pid = _player_id(intent)
        if not pid:
            return ["player_required"]
        ent = state.entities.get(pid)
        if ent is None:
            return ["player_not_entered"]
        if ent.status == "dead":
            return ["player_dead"]
        npc = str(intent.args.get("npc") or "").strip()
        item = str(intent.args.get("item") or "").strip()
        if not npc:
            return ["target_required"]
        if not item:
            return ["quest_item_required"]
        reason = _at_npc(state, pid, npc)
        if reason:
            return [reason]
        quest = self._resolve(state, intent)
        if quest is None:
            return ["quest_not_found"]
        qid = str(quest["id"])
        record = _record(state, pid, qid)
        if record is not None and record.get("status") == "completed":
            return ["quest_already_completed"]
        # 动态任务没有问话前置（原版直接把物品交给 owner 即结算）；
        # 问话支线必须先接取，否则即使背包里有同名物品也不认。
        if not quest.get("dynamic") and record is None:
            return ["quest_not_accepted"]
        if int(ent.inventory.get(item, 0)) <= 0:
            return ["quest_item_missing"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        pid = _player_id(intent)
        quest = self._resolve(state, intent)
        qid = str(quest["id"])
        item = str(intent.args.get("item") or "").strip()
        reward = quest.get("reward") or {}
        effects: list[Effect] = [
            Effect("item_consume", {"owner": pid, "item": item, "qty": 1})
        ]

        reward_record = {"exp": 0, "potential": 0, "score": 0}
        if quest.get("dynamic"):
            reward_record = _dynamic_reward(quest, rng)
            if reward_record["exp"] > 0:
                effects.append(
                    Effect("combat_exp_add", {"entity": pid, "delta": reward_record["exp"]})
                )
            if reward_record["potential"] > 0:
                effects.append(
                    Effect(
                        "potential_add",
                        {"entity": pid, "delta": reward_record["potential"]},
                    )
                )
            if reward_record["score"] > 0:
                effects.append(
                    Effect("score_add", {"entity": pid, "delta": reward_record["score"]})
                )
        else:
            exp = int(reward.get("exp") or 0)
            if exp > 0:
                reward_record["exp"] = exp
                effects.append(Effect("combat_exp_add", {"entity": pid, "delta": exp}))
            for reward_item in reward.get("items") or []:
                effects.append(
                    Effect(
                        "item_create",
                        {"owner": pid, "item": str(reward_item), "qty": 1},
                    )
                )

        effects.append(
            Effect(
                "xkx_quest_upsert",
                {
                    "actor": pid,
                    "quest": qid,
                    "status": "completed",
                    "step": "delivered",
                    "tick": int(intent.tick),
                    "implicit": bool(quest.get("dynamic")),
                    "reward": reward_record,
                },
            )
        )
        return effects
