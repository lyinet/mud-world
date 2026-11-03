"""Prompt 构造器 + Intent JSON schema（P3 预备，离线可用）。

用途：把 `content/personas/*.md`、当前状态摘要（目标/关系/债务/地点/近期事件）
拼成一次推理请求；把模型返回的 JSON 校验成 `Intent`。校验失败必须由调用方回退
到 MockReasoner，并记录 `schema_reject`。

本模块不联网、不读墙钟；所有输入都来自内容包与事件投影，便于回放与审计。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

from ..actions.common import wallet
from ..core.clock import TICKS_PER_DAY, day_of
from ..core.models import Intent
from ..world.state import WorldState

# NPC 可执行的 17 个动词（含 P2-B 新增 4 个）。system_* 不允许由模型提议。
NPC_VERBS = (
    "move",
    "cultivate",
    "work",
    "consume",
    "buy",
    "sell",
    "trade",
    "apprentice",
    "join_sect",
    "marry",
    "open_shop",
    "escort",
    "found_sect",
    "recruit",
    "quest_accept",
    "quest_deliver",
    "debt_settle",
)

INTENT_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "wsim_intent_batch_v1",
    "type": "object",
    "required": ["actions"],
    "additionalProperties": False,
    "properties": {
        "actions": {
            "type": "array",
            "minItems": 1,
            "maxItems": 4,
            "items": {
                "type": "object",
                "required": ["verb", "reason"],
                "additionalProperties": False,
                "properties": {
                    "verb": {"type": "string", "enum": list(NPC_VERBS)},
                    "args": {"type": "object"},
                    "reason": {"type": "string", "minLength": 1, "maxLength": 200},
                },
            },
        }
    },
}


class SchemaError(ValueError):
    """模型输出不符合 schema；调用方必须回退 MockReasoner 并记 schema_reject。"""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}:{detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Prompt:
    system: str
    user: str

    def text(self) -> str:
        return f"{self.system}\n{self.user}"


SYSTEM_PROMPT = (
    "你是多 NPC 世界模拟器中的单个角色决策器。只允许输出 JSON，不要解释。"
    "输出格式：{\"actions\":[{\"verb\":\"<动词>\",\"args\":{},\"reason\":\"<一句话动机>\"}]}。"
    "动词必须在给定列表内；reason 必须具体、可审计；一次最多 4 个动作。"
    "不要输出系统动词（system_*）、不要编造地点/物品/人物 id。"
)


def _brief_goal(state: WorldState, npc: str) -> dict[str, Any]:
    ent = state.entities.get(npc)
    goal = state.goals.get(ent.goal_id) if ent is not None and ent.goal_id else None
    if goal is None:
        return {}
    return {
        "id": goal.id,
        "content_type": goal.content_type,
        "kind": goal.kind,
        "progress": round(float(goal.progress), 4),
        "target": goal.target,
        "status": goal.status,
    }


def _brief_relations(state: WorldState, npc: str, limit: int = 5) -> list[dict[str, Any]]:
    rels = [r for r in state.relations.values() if npc in (r.a, r.b)]
    rels.sort(key=lambda r: (-float(r.affection), r.key))
    out = []
    for r in rels[:limit]:
        other = r.b if r.a == npc else r.a
        out.append(
            {
                "other": other,
                "kind": r.kind,
                "affection": round(float(r.affection), 2),
                "trust": round(float(r.trust), 2),
            }
        )
    return out


def _brief_debts(state: WorldState, npc: str, limit: int = 5) -> list[dict[str, Any]]:
    debts = [
        d for d in state.debts.values()
        if d.debtor == npc and d.status == "active" and d.amount > 0
    ]
    debts.sort(key=lambda d: (-d.amount, d.id))
    return [
        {"id": d.id, "creditor": d.creditor, "amount": d.amount, "long_term": d.long_term}
        for d in debts[:limit]
    ]


def build_state_summary(
    content,
    state: WorldState,
    npc: str,
    tick: int,
    tier: str,
    recent_events: Iterable[str] = (),
) -> dict[str, Any]:
    ent = state.entities.get(npc)
    if ent is None:
        return {"npc": npc, "tick": tick, "tier": tier, "missing": True}
    cash = state.account_balance(wallet(npc))
    loc = content.locations.get(ent.location) or {}
    row = next((n for n in content.npcs if str(n.get("id")) == npc), {})
    return {
        "npc": npc,
        "name": str(row.get("name") or npc),
        "tick": tick,
        "day": day_of(tick),
        "tier": tier,
        "role": ent.role,
        "location": ent.location,
        "location_name": str(loc.get("name") or ent.location),
        "exits": sorted(str(x) for x in (loc.get("exits") or [])),
        "status": ent.status,
        "stamina": round(float(ent.stamina), 2),
        "cash": cash,
        "skills": {k: round(float(v), 3) for k, v in sorted((ent.skills or {}).items())},
        "goal": _brief_goal(state, npc),
        "relations": _brief_relations(state, npc),
        "debts": _brief_debts(state, npc),
        "recent_events": list(recent_events)[-5:],
        "allowed_verbs": list(NPC_VERBS),
    }


def build_prompt(
    content,
    state: WorldState,
    npc: str,
    tick: int,
    tier: str,
    recent_events: Iterable[str] = (),
) -> Prompt:
    persona = content.personas.get(npc, "") if content is not None else ""
    summary = build_state_summary(content, state, npc, tick, tier, recent_events)
    user = "角色设定：\n" + (persona.strip() or "（无设定，按身份与当前状态行事）") + "\n\n当前状态：\n"
    user += json.dumps(summary, ensure_ascii=False, sort_keys=True)
    return Prompt(system=SYSTEM_PROMPT, user=user)


def _validate_args(args: Any) -> dict[str, Any]:
    if args is None:
        return {}
    if not isinstance(args, dict):
        raise SchemaError("bad_args_type")
    out: dict[str, Any] = {}
    for key, value in args.items():
        if not isinstance(key, str) or not key:
            raise SchemaError("bad_args_key")
        if isinstance(value, (dict, list, tuple, set)):
            raise SchemaError("nested_args", key)
        out[key] = value
    return out


def validate_intent_json(
    payload: Any,
    tick: int,
    allowed_verbs: Iterable[str] = NPC_VERBS,
    max_actions: int = 4,
) -> list[Intent]:
    """校验并转换为 Intent；任何不符合 schema 的输入抛 SchemaError。"""
    allowed = set(allowed_verbs)
    if not isinstance(payload, dict):
        raise SchemaError("not_object")
    if "actions" not in payload:
        raise SchemaError("missing_actions")
    actions = payload.get("actions")
    if not isinstance(actions, list):
        raise SchemaError("actions_not_list")
    if not actions:
        raise SchemaError("empty_actions")
    if len(actions) > max_actions:
        raise SchemaError("too_many_actions", str(len(actions)))
    intents: list[Intent] = []
    for idx, item in enumerate(actions):
        if not isinstance(item, dict):
            raise SchemaError("action_not_object", str(idx))
        extra = set(item) - {"verb", "args", "reason"}
        if extra:
            raise SchemaError("unknown_action_fields", ",".join(sorted(extra)))
        verb = item.get("verb")
        if not isinstance(verb, str) or not verb:
            raise SchemaError("missing_verb", str(idx))
        if verb not in allowed:
            raise SchemaError("unknown_verb", verb)
        reason = item.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise SchemaError("missing_reason", str(idx))
        if len(reason) > 200:
            raise SchemaError("reason_too_long", str(idx))
        args = _validate_args(item.get("args"))
        intents.append(
            Intent(
                actor="",  # 由调用方按 npc 回填
                verb=verb,
                args=args,
                tick=tick,
                source="llm",
                reason=reason.strip(),
            )
        )
    return intents


def parse_llm_output(
    text: str,
    tick: int,
    allowed_verbs: Iterable[str] = NPC_VERBS,
    max_actions: int = 4,
) -> list[Intent]:
    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise SchemaError("invalid_json", str(exc)[:80]) from exc
    return validate_intent_json(payload, tick, allowed_verbs, max_actions)
