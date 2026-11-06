"""社交/组织动词：apprentice（仅拜师）、join_sect（入会/入门派）、marry、talk。"""
from __future__ import annotations

from ..agent import dialogue_runtime
from ..agent.dialogue import PLAYER_NAME, compose_talk, place_names
from ..core.clock import TICKS_PER_DAY
from ..core.models import Effect, Intent, Reject
from ..world.state import WorldState
from .business import is_shop_hire, shop_hire_effects, shop_hire_preconditions
from .common import (
    alive,
    arg_int,
    arg_str,
    explicit_goal_id,
    goal_progress,
    max_skill,
    memory,
    organization_at,
    require_entity,
    wallet,
)
from .registry import Action

# 这些 source 代表玩家侧输入；缺身份时必须拒绝，不能回退到 actor。
PLAYER_SOURCES = frozenset({"player", "proposal", "test"})


def _offline_player_target(state: WorldState, eid: str) -> bool:
    """目标为离线玩家时，NPC 不得主动影响（P3-E）。"""
    if not eid.startswith("player:"):
        return False
    ent = state.entities.get(eid)
    return ent is not None and ent.presence != "online"


def _npc_towards_player(ent, target) -> bool:
    return not str(ent.id).startswith("player:") and str(target.id).startswith("player:")


def _clamp_delta(requested: float, tick_used: float, day_used: float) -> float:
    """同时受「单 tick ±2」与「单日 ±8」余量约束。"""
    lower = max(-2.0 - tick_used, -8.0 - day_used)
    upper = min(2.0 - tick_used, 8.0 - day_used)
    if lower > upper:
        return 0.0
    return max(lower, min(upper, requested))


def _talk_context(state: WorldState, npc_id: str) -> tuple[int, str, str]:
    """取该 NPC 的对话轮次与上一轮原文；只读状态，不写任何字段。

    turn 由 NPC memory 中 `talk:npc` 条数推导，不新增世界状态字段，避免
    state_hash / golden 基线漂移；memory 本身是确定性、可回放的事件产物。
    """
    mem = state.memories.get(npc_id) or []
    turns = sum(1 for e in mem if str(e.get("source") or "") == "talk:npc")
    last_player = ""
    last_npc = ""
    for entry in reversed(mem):
        source = str(entry.get("source") or "")
        text = str(entry.get("text") or "")
        if source == "talk:npc" and not last_npc:
            last_npc = text.removeprefix("我答：").strip()
        elif source == "talk:player" and not last_player:
            last_player = text.split("说：", 1)[-1].strip()
        if last_player and last_npc:
            break
    return turns, last_player, last_npc


def _speaker_id(state: WorldState, intent: Intent) -> str:
    """对话发起者身份。

    - 优先 `args.player_id`（必须是 `player:` 命名空间，避免冒用 NPC id）；
    - 否则玩家会话身份（`actor` 以 `player:` 开头）；
    - 玩家来源的 intent 若两者都没有，返回空串（调用方必须拒绝）；
    - NPC/规划器由服务端调度，`actor` 本身就是服务端会话身份，
      但必须已注册，防止伪造未知实体。
    """
    pid = str(intent.args.get("player_id") or "").strip()
    if pid:
        return pid if pid.startswith("player:") else ""
    actor = str(intent.actor or "").strip()
    if actor.startswith("player:"):
        return actor
    if intent.source in PLAYER_SOURCES:
        return ""
    if actor and actor in state.entities:
        return actor
    return ""


class TalkAction(Action):
    """玩家发言落地：写 NPC 记忆 + 小幅关系变化；不产生任何钱变动。

    P3-C：关系增量与回复必须由服务端计算。调用方传入的
    affection_delta / trust_delta / reply 一律丢弃，并记 spoofed_arg 审计。
    """

    verb = "talk"

    def __init__(self, content, dialogue=None):
        self.content = content
        # 可选：AsyncDialogueQueue。未注入时保持纯模板路径（golden/离线 deterministic）。
        self.dialogue = dialogue

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        pid = _speaker_id(state, intent)
        if not pid:
            return ["missing_player_id"]
        target = alive(state, arg_str(intent, "target"))
        # 新玩家必须先 player_enter；只有旧 run 的 player:visitor 保留懒创建兼容。
        if pid != "player:visitor" and pid not in state.entities:
            return ["player_not_entered"]
        speaker = state.entities.get(pid)
        if speaker is not None and speaker.presence != "online":
            return ["player_offline"]
        if speaker is not None and speaker.location != target.location:
            return ["not_together"]
        message = intent.args.get("message")
        if not isinstance(message, str) or not message.strip():
            return ["bad_message"]
        if len(message) > 200:
            return ["bad_message"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        pid = _speaker_id(state, intent)
        if not pid:
            raise Reject("missing_player_id")
        target = alive(state, arg_str(intent, "target"))
        message = str(intent.args["message"]).strip()
        tick = int(intent.tick)
        day = tick // TICKS_PER_DAY

        # 服务端按人设卡重新生成回复与情感增量；调用方字段全部忽略。
        persona = self.content.personas.get(target.id, "") if self.content is not None else ""
        places = place_names(self.content) if self.content is not None else {}
        turns, last_player, last_npc = _talk_context(state, target.id)
        server = compose_talk(
            persona, target.id, target.name, target.role, message, tick,
            turn=turns, last_message=last_player, last_reply=last_npc, places=places,
        )
        # 红线：状态/事件只写确定性模板回复；模型台词仅进旁路与 HTTP 响应。
        reply = str(server["reply"])
        if self.dialogue is not None:
            summary = {
                "name": target.name,
                "role": target.role,
                "location": target.location,
                "faction": target.faction or "",
                "stamina": round(float(target.stamina), 1),
                "affection": round(state.affection_between(pid, target.id), 1),
                "turn": turns,
                "last_message": last_player,
                "last_reply": last_npc,
            }
            try:
                dlg = self.dialogue.reply_sync(
                    npc=target.id,
                    player_text=message,
                    day=day,
                    tick=tick,
                    persona=persona,
                    state_summary=summary,
                    timeout_s=8.0,
                )
                if dlg.ok and dlg.text.strip():
                    dialogue_runtime.remember(
                        pid, target.id, tick, message, dlg.text.strip()
                    )
            except Exception:  # noqa: BLE001 - 对话失败不得影响动作落地
                pass
        req_aff = float(server["affection_delta"])
        req_trust = float(server["trust_delta"])
        # 配偶专属回应：确定性台词按世界日轮换，覆盖模板/模型回复。
        if (
            pid.startswith("player:")
            and target.spouse == pid
            and pid in state.entities
            and str(state.entities[pid].spouse or "") == target.id
        ):
            pname = state.entities[pid].name
            spouse_lines = (
                f"{pname}，我温着汤呢，忙完早些回来。",
                f"我把{pname}的换洗衣裳叠在柜头了，出门别又忘了带伞。",
                f"{pname}，米缸见底了，今日该缴租，你回来顺路去一趟。",
            )
            reply = spouse_lines[day % len(spouse_lines)]

        ledger = dict(state.talk_ledger.get(target.id) or {})
        day_aff = float(ledger.get("day_affection", 0.0))
        day_trust = float(ledger.get("day_trust", 0.0))
        tick_aff = float(ledger.get("tick_affection", 0.0))
        tick_trust = float(ledger.get("tick_trust", 0.0))
        if int(ledger.get("day", day)) != day:
            day_aff = day_trust = 0.0
            tick_aff = tick_trust = 0.0
        if int(ledger.get("last_tick", tick)) != tick:
            tick_aff = tick_trust = 0.0

        d_aff = _clamp_delta(req_aff, tick_aff, day_aff)
        d_trust = _clamp_delta(req_trust, tick_trust, day_trust)

        effects: list[Effect] = []
        spoofed = [
            field
            for field in ("affection_delta", "trust_delta", "reply")
            if field in intent.args
        ]
        if spoofed:
            effects.append(
                Effect(
                    "audit_log",
                    {
                        "kind": "spoofed_arg",
                        "actor": pid,
                        "tick": tick,
                        "fields": spoofed,
                        "note": "服务端忽略调用方传入的关系增量/回复",
                    },
                )
            )

        if pid not in state.entities:
            if pid != "player:visitor":
                raise Reject("player_not_entered")
            raw_name = intent.args.get("player_name") or intent.args.get("name")
            player_name = str(raw_name or PLAYER_NAME).strip() or PLAYER_NAME
            effects.append(
                Effect(
                    "create_entity",
                    {
                        "id": pid,
                        "name": player_name,
                        "role": "visitor",
                        "location": target.location,
                        "stamina": 100.0,
                        "max_stamina": 100.0,
                    },
                )
            )
        player_name = (
            state.entities[pid].name if pid in state.entities
            else str(intent.args.get("player_name") or intent.args.get("name") or PLAYER_NAME).strip()
            or PLAYER_NAME
        )
        if wallet(pid) not in state.accounts:
            effects.append(Effect("create_account", {"account": wallet(pid), "balance": 0}))

        effects.append(
            Effect(
                "memory_add",
                {
                    "npc": target.id,
                    "tick": tick,
                    "text": f"{player_name}说：{message}",
                    "importance": 0.4,
                    "source": "talk:player",
                },
            )
        )
        effects.append(
            Effect(
                "memory_add",
                {
                    "npc": target.id,
                    "tick": tick,
                    "text": f"我答：{reply}",
                    "importance": 0.3,
                    "source": "talk:npc",
                },
            )
        )

        existing = state.relation(pid, target.id, "acquaintance")
        relation = {
            "a": pid,
            "b": target.id,
            "kind": "acquaintance",
        }
        if existing is None:
            relation["affection"] = max(-100.0, min(100.0, d_aff))
            relation["trust"] = max(-100.0, min(100.0, 40.0 + d_trust))
        else:
            relation["affection_delta"] = d_aff
            relation["trust_delta"] = d_trust
        effects.append(Effect("relation_upsert", relation))
        effects.append(
            Effect(
                "talk_ledger",
                {
                    "npc": target.id,
                    "day": day,
                    "day_affection": day_aff + d_aff,
                    "day_trust": day_trust + d_trust,
                    "last_tick": tick,
                    "tick_affection": tick_aff + d_aff,
                    "tick_trust": tick_trust + d_trust,
                },
            )
        )
        effects.append(Effect("set_field", {"entity": target.id, "field": "last_action_tick", "value": tick}))
        effects.append(Effect("set_field", {"entity": pid, "field": "last_action_tick", "value": tick}))
        # 在线状态由 player_enter / heartbeat 维护，talk 不再副作用式上线。
        effects.append(Effect("set_field", {"entity": pid, "field": "last_seen_tick", "value": tick}))
        return effects


class ApprenticeAction(Action):
    verb = "apprentice"

    def __init__(self, content):
        self.content = content

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        target = alive(state, arg_str(intent, "target"))
        arg_int(intent, "duration_days", 7, minimum=7, maximum=365)
        if _offline_player_target(state, target.id):
            return ["target_offline"]
        if target.id == ent.id:
            return ["self_apprentice"]
        if target.location != ent.location:
            return ["not_together"]
        if ent.master is not None:
            return ["already_has_master"]
        if target.master == ent.id:
            return ["cycle"]
        cur = target
        seen = {ent.id}
        while cur.master:
            if cur.master in seen:
                return ["cycle"]
            seen.add(cur.master)
            nxt = state.entities.get(cur.master)
            if nxt is None:
                break
            cur = nxt
        if max_skill(target) < 0.5:
            return ["master_not_qualified"]
        if max_skill(target) < max_skill(ent) + 0.1:
            return ["master_not_qualified"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        target = alive(state, arg_str(intent, "target"))
        return [
            Effect(
                "relation_upsert",
                {
                    "a": target.id,
                    "b": ent.id,
                    "kind": "master",
                    "affection": 5.0,
                    "affection_delta": 5.0,
                    "trust_delta": 5.0,
                },
            ),
            Effect(
                "relation_upsert",
                {
                    "a": ent.id,
                    "b": target.id,
                    "kind": "disciple",
                    "affection": 5.0,
                    "affection_delta": 5.0,
                    "trust_delta": 5.0,
                },
            ),
            Effect("add_disciple", {"master": target.id, "disciple": ent.id}),
            Effect("set_field", {"entity": ent.id, "field": "master", "value": target.id}),
            Effect("set_field", {"entity": ent.id, "field": "last_action_tick", "value": intent.tick}),
            *goal_progress(state, ent.id, "bond", 5.0, intent.tick, goal_id=explicit_goal_id(intent)),
            *memory(state, ent.id, intent.tick, f"拜{target.name}为师", 0.6),
            *memory(state, target.id, intent.tick, f"收{ent.name}为徒", 0.6),
        ]


class JoinSectAction(Action):
    verb = "join_sect"

    def __init__(self, content):
        self.content = content

    def _faction(self, state: WorldState, target: str):
        if target in state.factions:
            return state.factions[target]
        if target in state.locations:
            return organization_at(state, target)
        return None

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        target = arg_str(intent, "target")
        dues = arg_int(intent, "dues", 0, minimum=0, maximum=100000)
        fac = self._faction(state, target)
        if fac is None:
            return ["unknown_sect"]
        if fac.location and fac.location != ent.location:
            return ["not_at_sect"]
        if ent.faction is not None and ent.faction != fac.id:
            return ["already_in_faction"]
        if ent.id in fac.members:
            return ["already_member"]
        if state.account_balance(wallet(ent.id)) < dues:
            return ["insufficient_money"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        target = arg_str(intent, "target")
        dues = arg_int(intent, "dues", 0, minimum=0, maximum=100000)
        fac = self._faction(state, target)
        if fac is None:
            raise Reject("unknown_sect")
        effects: list[Effect] = [Effect("faction_join", {"entity": ent.id, "faction": fac.id})]
        if dues > 0:
            effects.append(Effect("transfer", {"src": wallet(ent.id), "dst": fac.treasury_account, "amount": dues, "reason": "sect_dues"}))
        if fac.leader and fac.leader != ent.id:
            for x, y in ((ent.id, fac.leader), (fac.leader, ent.id)):
                effects.append(
                    Effect(
                        "relation_upsert",
                        {"a": x, "b": y, "kind": "ally", "affection": 3.0, "affection_delta": 3.0},
                    )
                )
        effects.extend(
            [
                Effect("set_field", {"entity": ent.id, "field": "last_action_tick", "value": intent.tick}),
                *goal_progress(state, ent.id, "faction", 1.0, intent.tick, goal_id=explicit_goal_id(intent)),
                *memory(state, ent.id, intent.tick, f"加入{fac.name}", 0.5),
            ]
        )
        return effects


class RecruitAction(Action):
    """真实招揽：同地点、面对面把对方拉进门派（sworn 关系）。

    开宗立派前没有 faction，recruit 先建立 sworn 关系；found_sect 只认这些
    由真实动作建立的成员，不再靠开局关系一次性凑数。
    """

    verb = "recruit"

    def __init__(self, content):
        self.content = content

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        if is_shop_hire(intent):
            return shop_hire_preconditions(self.content, state, intent)
        ent = require_entity(state, intent.actor)
        target = alive(state, arg_str(intent, "target"))
        if _offline_player_target(state, target.id):
            return ["target_offline"]
        if _npc_towards_player(ent, target):
            return ["player_consent_required"]
        if target.id == ent.id:
            return ["self_recruit"]
        if target.location != ent.location:
            return ["not_together"]
        if target.faction and str(target.faction).startswith("sect:"):
            return ["target_in_sect"]
        if any(
            r.kind == "sworn" and r.status == "active" and r.a == ent.id and r.b == target.id
            for r in state.relations.values()
        ):
            return ["already_sworn"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        if is_shop_hire(intent):
            return shop_hire_effects(self.content, state, intent)
        ent = require_entity(state, intent.actor)
        target = alive(state, arg_str(intent, "target"))
        current = state.affection_between(ent.id, target.id)
        delta = max(1.0, 30.0 - current)
        effects: list[Effect] = [
            Effect(
                "relation_upsert",
                {"a": ent.id, "b": target.id, "kind": "sworn", "affection": 30.0,
                 "affection_delta": delta, "trust": 45.0},
            ),
            Effect(
                "relation_upsert",
                {"a": target.id, "b": ent.id, "kind": "sworn", "affection": 30.0,
                 "affection_delta": delta, "trust": 45.0},
            ),
            Effect("set_field", {"entity": ent.id, "field": "last_action_tick", "value": intent.tick}),
            # 招人只是进度，不能把 found_sect 目标提前判成完成：
            # 5 次招揽才累计 0.5，真正的 1.0 由 found_sect 动作给出。
            *goal_progress(state, ent.id, "faction", 0.1, intent.tick, goal_id=explicit_goal_id(intent)),
            *memory(state, ent.id, intent.tick, f"招揽{target.name}入伙", 0.5),
            *memory(state, target.id, intent.tick, f"答应与{ent.name}共举门派", 0.4),
        ]
        if ent.faction:
            effects.insert(2, Effect("faction_join", {"entity": target.id, "faction": ent.faction}))
        return effects


class MarryAction(Action):
    verb = "marry"

    def __init__(self, content):
        self.content = content

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        target = alive(state, arg_str(intent, "target"))
        if _offline_player_target(state, target.id):
            return ["target_offline"]
        if _npc_towards_player(ent, target):
            return ["player_consent_required"]
        if target.id == ent.id:
            return ["self_marriage"]
        if target.location != ent.location:
            return ["not_together"]
        if ent.spouse is not None or target.spouse is not None:
            return ["already_married"]
        for e in (ent, target):
            if any(
                r.kind == "marriage" and r.status == "active" and e.id in (r.a, r.b)
                for r in state.relations.values()
            ):
                return ["already_married"]
        if state.affection_between(ent.id, target.id) < 60.0:
            return ["affection_too_low"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        target = alive(state, arg_str(intent, "target"))
        return [
            Effect("marry", {"a": ent.id, "b": target.id}),
            Effect("set_field", {"entity": ent.id, "field": "last_action_tick", "value": intent.tick}),
            Effect("set_field", {"entity": target.id, "field": "last_action_tick", "value": intent.tick}),
            *goal_progress(state, ent.id, "bond", 10.0, intent.tick, goal_id=explicit_goal_id(intent)),
            *goal_progress(state, target.id, "bond", 10.0, intent.tick),
            *memory(state, ent.id, intent.tick, f"与{target.name}成婚", 0.9),
            *memory(state, target.id, intent.tick, f"与{ent.name}成婚", 0.9),
        ]
