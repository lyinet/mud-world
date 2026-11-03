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
from .cost import estimate_tokens

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
    persona_tokens: int = 0

    def text(self) -> str:
        return f"{self.system}\n{self.user}"


# 每张人设卡的默认 token 预算（300–500 区间，按 4 字符≈1 token 估算）。
PERSONA_TOKEN_BUDGET = 450
PERSONA_PRIORITY = (
    "一句话性格",
    "说话风格",
    "在意的东西",
    "遇事反应倾向",
    "日常锚点",
    "张力",
)


def _split_persona(text: str) -> tuple[list[str], list[dict[str, Any]]]:
    preamble: list[str] = []
    sections: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            cur = {"title": line[3:].strip(), "lines": []}
            sections.append(cur)
        elif cur is None:
            preamble.append(line)
        else:
            cur["lines"].append(line)
    return preamble, sections


def _priority_rank(title: str) -> tuple[int, str]:
    for i, key in enumerate(PERSONA_PRIORITY):
        if key in title:
            return i, title
    return len(PERSONA_PRIORITY), title


def compose_persona(text: str, budget_tokens: int = PERSONA_TOKEN_BUDGET) -> tuple[str, int]:
    """按章节优先级把整张人设卡压进 token 预算。

    顺序：一句话性格 > 说话风格与台词 > 在意的东西 > 遇事反应倾向 > 日常锚点 > 张力。
    超预算时优先丢弃低优先级章节；单章节超长则截断并加省略号。
    """
    budget_chars = max(1, int(budget_tokens)) * 4
    preamble, sections = _split_persona(text)
    out: list[str] = []
    used = 0
    head = "\n".join(preamble).strip()
    if head:
        if len(head) > budget_chars:
            head = head[: max(0, budget_chars - 1)].rstrip() + "…"
        out.append(head)
        used += len(head) + 2  # 章节间以 "\n\n" 连接
    for sec in sorted(sections, key=lambda s: _priority_rank(str(s["title"]))):
        body = "\n".join(sec["lines"]).strip()
        block = f"## {sec['title']}\n{body}".strip()
        remaining = budget_chars - used
        if remaining <= 1:
            break
        if len(block) > remaining:
            block = block[: max(0, remaining - 1)].rstrip() + "…"
            out.append(block)
            used += len(block) + 2
            break
        out.append(block)
        used += len(block) + 2
    composed = "\n\n".join(out)
    return composed, estimate_tokens(composed)


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
    persona_budget_tokens: int = PERSONA_TOKEN_BUDGET,
) -> Prompt:
    raw = content.personas.get(npc, "") if content is not None else ""
    persona, persona_tokens = compose_persona(raw, persona_budget_tokens) if raw else ("", 0)
    summary = build_state_summary(content, state, npc, tick, tier, recent_events)
    user = "角色设定：\n" + (persona or "（无设定，按身份与当前状态行事）") + "\n\n当前状态：\n"
    user += json.dumps(summary, ensure_ascii=False, sort_keys=True)
    return Prompt(system=SYSTEM_PROMPT, user=user, persona_tokens=persona_tokens)


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


def extract_intent_payload(text: str) -> tuple[Any, dict[str, Any]]:
    """只做提取、不改语义：剥掉 markdown 围栏/外层包装，并归一常见同义参数名。

    返回 (payload, meta)；meta 记录提取动作，供报告与测试核对原始输出仍被保留。
    不做任何 schema 放宽，未知字段/动词仍由 validate_intent_json 拒绝。
    """
    raw = str(text or "")
    info: dict[str, Any] = {"stripped_fence": False, "unwrapped": False, "args_aliases": 0}
    candidate = raw.strip()
    if candidate.startswith("```"):
        start = candidate.find("\n")
        end = candidate.rfind("```")
        if start != -1 and end != -1 and end > start:
            candidate = candidate[start + 1 : end].strip()
            info["stripped_fence"] = True
    # 有些网关会再包一层 {"data": {...}} / {"result": {...}}。
    try:
        payload = json.loads(candidate)
    except (TypeError, ValueError):
        payload = None
    if isinstance(payload, dict) and "actions" not in payload:
        for key in ("data", "result", "output"):
            inner = payload.get(key)
            if isinstance(inner, dict) and "actions" in inner:
                payload = inner
                info["unwrapped"] = True
                break
    if isinstance(payload, dict) and isinstance(payload.get("actions"), list):
        # 只做参数名提取/同义名归一，不补造缺失参数、不改动作语义。
        aliases_by_verb = {
            "move": ("destination", "target_location", "to_location"),
            "work": ("location", "workplace", "place"),
            "open_shop": ("location", "workplace", "place"),
            "found_sect": ("location", "workplace", "place"),
        }
        canonical = {"move": "to", "work": "at", "open_shop": "at", "found_sect": "at"}
        for item in payload["actions"]:
            if not isinstance(item, dict):
                continue
            verb = item.get("verb")
            if verb not in aliases_by_verb:
                continue
            args = item.get("args")
            if not isinstance(args, dict):
                continue
            for alias in aliases_by_verb[verb]:
                if alias in args:
                    if canonical[verb] not in args:
                        args[canonical[verb]] = args[alias]
                        info["args_aliases"] += 1
                    del args[alias]
    return (payload if payload is not None else candidate), info


def parse_llm_output(
    text: str,
    tick: int,
    allowed_verbs: Iterable[str] = NPC_VERBS,
    max_actions: int = 4,
) -> list[Intent]:
    payload, _meta = extract_intent_payload(text)
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError) as exc:
            raise SchemaError("invalid_json", str(exc)[:80]) from exc
    return validate_intent_json(payload, tick, allowed_verbs, max_actions)
