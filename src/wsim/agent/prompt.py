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

# NPC 可执行的 17 个动词（与 config/intents.yaml 的功能表一一对应）。
# system_* 不允许由模型提议；recruit 是规划器动词，不在意图白名单内。
NPC_VERBS = (
    "move",
    "work",
    "consume",
    "cultivate",
    "trade",
    "buy",
    "sell",
    "apprentice",
    "marry",
    "join_sect",
    "found_sect",
    "open_shop",
    "escort",
    "quest_accept",
    "quest_deliver",
    "talk",
    "debt_settle",
)

# P3-B 分工：生存/生计动作由规则调度器负责，模型不得替换。
RULE_VERBS = (
    "move", "cultivate", "work", "consume", "buy", "sell",
    "open_shop", "escort", "quest_accept", "quest_deliver", "debt_settle",
    # recruit 只由规则规划器（开宗立派）生成，模型不得提名。
    "recruit",
)
# 模型只负责目标选择与社交动作。
MODEL_VERBS = ("trade", "apprentice", "join_sect", "marry", "found_sect", "talk")

# 模型动作清单：必填参数 + 前置条件摘要（提示用；真正的校验仍走动作注册表）。
ACTION_SPECS: dict[str, dict[str, Any]] = {
    "trade": {
        "args": "target, offer[], want[], money_delta",
        "pre": "target 必须同地点；offer 的物品必须自己持有；want 的物品必须对方持有；钱差不能超过双方余额；好感 >= -20",
    },
    "apprentice": {
        "args": "target, duration_days>=7",
        "pre": "target 必须同地点；自己还没有师父；target 最高技能 >= 自己最高技能 + 0.1 且 >= 0.5；不能成环",
    },
    "join_sect": {
        "args": "target",
        "pre": "target 必须是同地点的组织/门派；不能已在门派中",
    },
    "marry": {
        "args": "target",
        "pre": "target 必须同地点；双方都未婚；好感 >= 60",
    },
    "recruit": {
        "args": "target",
        "pre": "target 必须同地点；target 未入其他门派；不能已结拜",
    },
    "found_sect": {
        "args": "at, name",
        "pre": "必须在目标地点；需要足够的 sworn 成员；金库/资金满足门槛",
    },
    "talk": {
        "args": "target, message, reply",
        "pre": "target 必须存活；message/reply 非空且各自 <=200 字",
    },
}


def _precondition_hint(verb: str) -> str:
    spec = ACTION_SPECS.get(verb)
    return str(spec["pre"]) if spec else "按动作注册表校验"

INTENT_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "wsim_intent_batch_v1",
    "type": "object",
    "required": ["actions"],
    "additionalProperties": False,
    "properties": {
        "goal": {
            "type": ["string", "null"],
            "description": "可选：今天想推进的内容目标 id，必须来自 goal_candidates",
        },
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


# 台词输出硬约束（提示词侧；与 `agent/text_guard.py` 的确定性后处理配套）：
# 第一人称、无旁白、无 Markdown/动作括号、无固定口头禅。
REPLY_HARD_CONSTRAINTS = (
    "【台词硬约束】"
    "1) 全程第一人称“我”，只写这个角色此刻说出口的话；不写旁白，"
    "不写第三人称小说叙述（如“他自己默默……”或“段五斗咧嘴一笑：‘……’”）。"
    "2) 禁止 Markdown：不要出现 **加粗**、*斜体*、__下划线__、`代码`。"
    "3) 禁止【】标注；动作不要用（动作）或(动作)括起来。"
    "4) 动作只用自然口语短句（如“我把刀往案板上一搁”），不用括号动作标记。"
    "5) 不要固定口头禅或句尾口号（如“包在我身上”“斤两不会错”）；"
    "每次表达都贴合当前问题，同一意思换句式。"
)

DIALOGUE_SYSTEM_PROMPT = (
    "你在扮演一个武侠世界中的 NPC，只能用第一人称回答，保持人设，"
    "不要解释、不要复述问题、不要出戏；最多 80 个汉字，可带一句自然口语动作。"
    + REPLY_HARD_CONSTRAINTS
)

SYSTEM_PROMPT = (
    "你是多 NPC 世界模拟器中的单个角色决策器。只允许输出 JSON，不要解释。"
    "分工：移动/上工/吃饭/修炼/买卖/跑任务等生存与生计动作由规则调度器负责，"
    "你不得输出这些动词，也不得替换它们。你只负责："
    "(1) 从 goal_candidates 里选今天想推进的目标（goal 字段）；"
    "(2) 社交动作 trade / apprentice / join_sect / marry / found_sect / talk；"
    "(3) 计划失效后的重规划。"
    "输出格式：{\"goal\":\"<goal_id 或 null>\",\"actions\":[{\"verb\":\"<社交动词>\",\"args\":{},\"reason\":\"<一句话动机>\"}]}。"
    "动作必须满足该动作的前置条件；target 必须是 nearby_npcs 里的人；"
    "一次最多 4 个动作；reason 必须具体、可审计。不要输出系统动词（system_*）、不要编造地点/物品/人物 id。"
    "reason 用第一人称口语、贴合当前目标；不要 Markdown、不要第三人称旁白、不要固定口头禅。"
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


def _brief_nearby(state: WorldState, npc: str, limit: int = 5) -> list[dict[str, Any]]:
    ent = state.entities.get(npc)
    if ent is None:
        return []
    out = []
    for other in sorted(state.entities.values(), key=lambda e: e.id):
        if other.id == npc or other.location != ent.location or other.status != "alive":
            continue
        out.append(
            {
                "id": other.id,
                "name": other.name,
                "role": other.role,
                "cash": state.account_balance(wallet(other.id)),
                "affection": round(state.affection_between(npc, other.id), 2),
            }
        )
        if len(out) >= limit:
            break
    return out


def _brief_inventory(state: WorldState, npc: str, limit: int = 8) -> list[dict[str, Any]]:
    ent = state.entities.get(npc)
    if ent is None:
        return []
    rows = [{"item": k, "qty": int(v)} for k, v in sorted(ent.inventory.items()) if int(v) > 0]
    return rows[:limit]


def _goal_candidates(content, state: WorldState, npc: str, limit: int = 4) -> list[dict[str, Any]]:
    row = next((n for n in content.npcs if str(n.get("id")) == npc), {})
    out: list[dict[str, Any]] = []
    for g in sorted(row.get("goals") or [], key=lambda x: (float(x.get("priority", 9)), str(x.get("id", "")))):
        gid = f"{npc}:{g.get('id')}"
        goal = state.goals.get(gid)
        if goal is not None and goal.status in ("completed", "abandoned"):
            continue
        out.append(
            {
                "goal_id": gid,
                "content_goal_id": str(g.get("id", "")),
                "type": str(g.get("type", "")),
                "priority": g.get("priority"),
                "progress": round(float(goal.progress), 3) if goal is not None else 0.0,
            }
        )
        if len(out) >= limit:
            break
    return out


def action_catalog(allowed: Iterable[str] = MODEL_VERBS) -> list[dict[str, Any]]:
    """可执行动作清单：必填参数 + 前置条件摘要。"""
    out = []
    for verb in allowed:
        spec = ACTION_SPECS.get(verb, {})
        out.append({"verb": verb, "args": spec.get("args", ""), "preconditions": spec.get("pre", "")})
    return out


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
    loc = content.locations.get(ent.location) or state.locations.get(ent.location) or {}
    row = next((n for n in content.npcs if str(n.get("id")) == npc), {})
    hour = (tick % TICKS_PER_DAY) / 6.0
    exits = sorted(str(x) for x in (loc.get("exits") or []))
    reachable = []
    for lid in exits:
        lrow = state.locations.get(lid) or content.locations.get(lid) or {}
        reachable.append({"id": lid, "name": str(lrow.get("name") or lid)})
    return {
        "npc": npc,
        "name": str(row.get("name") or npc),
        "tick": tick,
        "day": day_of(tick),
        "hour": round(hour, 2),
        "day_ticks_left": TICKS_PER_DAY - (tick % TICKS_PER_DAY),
        "remaining_work_hours": round(max(0.0, 18.0 - hour), 2),
        "tier": tier,
        "role": ent.role,
        "location": ent.location,
        "location_name": str(loc.get("name") or ent.location),
        "exits": exits,
        "reachable_locations": reachable,
        "previous_location": {
            "id": ent.location,
            "name": str(loc.get("name") or ent.location),
        },
        "status": ent.status,
        "stamina": round(float(ent.stamina), 2),
        "cash": cash,
        "inventory": _brief_inventory(state, npc),
        "skills": {k: round(float(v), 3) for k, v in sorted((ent.skills or {}).items())},
        "goal": _brief_goal(state, npc),
        "goal_candidates": _goal_candidates(content, state, npc),
        "nearby_npcs": _brief_nearby(state, npc),
        "relations": _brief_relations(state, npc),
        "debts": _brief_debts(state, npc),
        "recent_events": list(recent_events)[-5:],
        "allowed_verbs": list(MODEL_VERBS),
        "action_catalog": action_catalog(MODEL_VERBS),
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


DECISION_SYSTEM_PROMPT = (
    "你是多 NPC 世界模拟器中的单个角色决策器。只输出 JSON，不要解释。"
    "分工：移动/上工/吃饭/修炼/买卖/跑任务/还债/巡视摆摊看病等日常生计动作由规则调度器负责，"
    "你不得指定这些动作，也不要给出动作参数。"
    "你只负责选定「今天要推动哪一个具体社交或目标动作」，并说清对谁做。"
    "只能从这些模型动作里选一个：trade（交易/叙旧）、apprentice（拜师）、join_sect（入会）、"
    "marry（成婚）、found_sect（开派）、talk（交谈）。"
    "goal 必须从 goal_candidates 里选一个 goal_id；没有合适目标填 null。"
    "target 必须是 nearby_npcs 里同地点的人；没有合适的同地点目标时用 talk。"
    "verb/target 的对应关系：build_relation → trade（有同地点的人时优先 trade，talk 不推进目标）；"
    "advance_skill → apprentice（附近有更高技能者）；join_sect/gain_status → join_sect；"
    "marry → marry；found_sect → found_sect。"
    "intent 必须是一句具体的「对谁做什么」，人物用 nearby_npcs 或状态里的名字，不要编造 id。"
    "不要写泛化的日常计划（如巡视、摆摊、看病、练手艺、赶路、打听消息），这些不会被执行。"
    '输出格式：{"goal":"<goal_id 或 null>","intent":"<一句中文具体动作>",'
    '"verb":"trade|apprentice|join_sect|marry|found_sect|talk","target":"<人物名字>"}。'
    "intent 用第一人称口语；不要 Markdown、不要第三人称旁白、"
    "不要（动作）/(动作)括号标记，不要固定口头禅。"
)


def build_decision_prompt(
    content,
    state: WorldState,
    npc: str,
    tick: int,
    tier: str,
    recent_events: Iterable[str] = (),
    persona_budget_tokens: int = PERSONA_TOKEN_BUDGET,
) -> Prompt:
    """决策槽 prompt：只让模型说「想做什么」，不产出 JSON 动作。

    世界状态（地点/相邻地点/现金/物品/可执行清单/时间）仍完整注入，
    便于模型给出具体、可被意图槽抽参数的句子。
    """
    raw = content.personas.get(npc, "") if content is not None else ""
    persona, persona_tokens = compose_persona(raw, persona_budget_tokens) if raw else ("", 0)
    summary = build_state_summary(content, state, npc, tick, tier, recent_events)
    user = "角色设定：\n" + (persona or "（无设定，按身份与当前状态行事）") + "\n\n当前状态：\n"
    user += json.dumps(summary, ensure_ascii=False, sort_keys=True)
    return Prompt(system=DECISION_SYSTEM_PROMPT, user=user, persona_tokens=persona_tokens)


def parse_decision_hint(text: str) -> tuple[str, str]:
    """从决策 JSON 里取 (verb, target)；缺失/非法返回空串，交给意图槽兜底。

    A3：决策模型直接给出可用动作时，可跳过意图槽调用（省一次网络与 token），
    只在 verb 属于模型动作且 target 可在状态里解析时才走快路径。
    """
    raw = str(text or "").strip()
    try:
        payload, _meta = extract_intent_payload(raw)
    except (TypeError, ValueError, AttributeError, SchemaError):
        return "", ""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return "", ""
    if not isinstance(payload, dict):
        return "", ""
    verb = str(payload.get("verb") or "").strip()
    target = str(payload.get("target") or "").strip()
    if verb not in MODEL_VERBS:
        return "", ""
    return verb, target


def parse_decision_payload(text: str) -> tuple[str, str, list[Intent]]:
    """解析决策槽输出：返回 (goal_id, 自然语言意图, 兼容旧格式的动作列表)。

    兼容三种形态：新版 {"goal","intent"}、旧版 {"goal","actions"}、纯文本一句话。
    旧版动作仍走原有 schema 校验；新版动作列表恒为空。
    """
    raw = str(text or "").strip()
    if not raw:
        raise SchemaError("empty_output")
    payload, _meta = extract_intent_payload(raw)
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            payload = None
    if isinstance(payload, dict) and isinstance(payload.get("actions"), list):
        intents = validate_intent_json(payload, tick=0, allowed_verbs=NPC_VERBS)
        goal = payload.get("goal")
        return (str(goal) if isinstance(goal, str) else "", "", intents)
    if isinstance(payload, dict):
        goal = payload.get("goal")
        intent = payload.get("intent", payload.get("utterance", ""))
        if isinstance(intent, str) and intent.strip():
            return (str(goal) if isinstance(goal, str) else "", intent.strip(), [])
        raise SchemaError("missing_intent")
    # 纯文本：整句作为意图，goal 留空。
    return "", raw[:300], []


def build_repair_prompt(base: Prompt, bad_output: str, error: str) -> Prompt:
    """校验失败后的一次修复重试：把错误原因回喂给模型，要求只输出合法 JSON。"""
    user = (
        base.user
        + "\n\n上一次输出：\n"
        + str(bad_output)[:1200]
        + "\n校验错误："
        + str(error)
        + "\n请修正后重新输出；只输出一个 JSON 对象，格式 "
        + '{"goal":<goal_id 或 null>,"actions":[{"verb":"...","args":{},"reason":"..."}]}。'
        + "不要输出被禁止的生存动词，不要输出解释或 markdown。"
    )
    return Prompt(system=base.system, user=user, persona_tokens=base.persona_tokens)


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


def parse_llm_payload(
    text: str,
    tick: int,
    allowed_verbs: Iterable[str] = NPC_VERBS,
    max_actions: int = 4,
) -> tuple[list[Intent], dict[str, Any]]:
    """解析 + 校验，并额外返回 goal 选择与提取元信息（不改 schema 语义）。"""
    payload, meta = extract_intent_payload(text)
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError) as exc:
            raise SchemaError("invalid_json", str(exc)[:80]) from exc
    intents = validate_intent_json(payload, tick, allowed_verbs, max_actions)
    goal = payload.get("goal") if isinstance(payload, dict) else None
    meta = dict(meta)
    meta["goal"] = str(goal) if isinstance(goal, str) else ""
    return intents, meta


def parse_llm_output(
    text: str,
    tick: int,
    allowed_verbs: Iterable[str] = NPC_VERBS,
    max_actions: int = 4,
) -> list[Intent]:
    intents, _meta = parse_llm_payload(text, tick, allowed_verbs, max_actions)
    return intents
