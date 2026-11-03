"""意图理解槽（Xing4.0-29B-Intent 兼容）：类别 + 参数抽取 → 引擎 Intent。

设计要点（对齐上游实测经验）：
- 功能表（config/intents.yaml）显式列出每个意图的必填字段与取值来源；
- 缺字段不猜：返回 needs_clarification，由上层追问或回退规则调度；
- 未知 category / 非白名单 verb 直接拒绝，不落地；
- 中文参数值（数量词、地名、人名、物品名）映射到内容包 id，映射失败视为缺失；
- destructive 意图需要二次确认。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

from ..core.models import Intent

CN_NUM = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9,
}
CN_UNIT = {"十": 10, "百": 100, "千": 1000, "万": 10000}

STATUSES = ("ok", "needs_clarification", "needs_confirmation", "reject")

# 意图模板字段 -> 引擎参数名。只有 move 的 `destination` 与引擎 `to` 不同名。
ENGINE_FIELD_ALIAS = {"move": {"destination": "to"}}

# 上游可能返回中文键名；这里归一成模板字段名。P3-D 起同时接受英文键名。
FIELD_ALIASES = {
    "地点": "destination", "目的地": "destination", "去往": "destination",
    "地点编号": "destination", "位置": "destination",
    "人物": "target", "对象": "target", "目标": "target", "目标人物": "target",
    "门派": "target", "组织": "target",
    "物品": "item", "东西": "item", "货物": "item", "商品": "item",
    "数量": "qty", "份数": "qty",
    "卖家": "from", "商家": "from", "店铺": "from", "卖方": "from",
    "买家": "to", "买主": "to", "买方": "to",
    "时辰": "hours", "时长": "hours", "小时": "hours",
    "天数": "duration_days",
    "路线": "route", "行程": "route",
    "货物价值": "cargo_value", "货值": "cargo_value",
    "费用": "fee", "报酬": "fee",
    "任务": "quest", "债务": "debt", "技能": "skill",
    "店名": "name", "名字": "name", "名称": "name",
    "类型": "shop_type", "店铺类型": "shop_type",
    "宗旨": "doctrine", "会费": "dues",
    "说的话": "message", "内容": "message", "回复": "reply",
    # P3-D：实测 Xing 会在中英文键名之间漂移，这里把英文键也归一到同一内部结构。
    "goods": "item", "good": "item", "product": "item", "thing": "item",
    "items": "item", "item_name": "item",
    "quantity": "qty", "amount": "qty", "count": "qty", "num": "qty", "number": "qty",
    "place": "destination", "location": "destination", "dest": "destination",
    "where": "destination", "target_location": "destination",
    "person": "target", "npc": "target", "who": "target", "target_person": "target",
    "seller": "from", "vendor": "from", "from_shop": "from",
    "buyer": "to", "receiver": "to", "to_person": "to",
    "duration": "duration_days", "days": "duration_days",
    "period": "hours", "duration_hours": "hours",
    "text": "message", "words": "message", "utterance": "message", "message_text": "message",
    "path": "route", "route_plan": "route",
    "value": "cargo_value", "cargo": "cargo_value",
    "cost": "fee", "price": "fee", "payment": "fee",
    "quest_name": "quest", "debt_id": "debt", "skill_name": "skill",
    "shop_name": "name", "sect_name": "name",
    "store_type": "shop_type", "kind": "shop_type",
    "purpose": "doctrine", "dues_amount": "dues",
    "reply_text": "reply", "answer": "reply",
}

# 顶层键名：类别容器与参数容器在两种语言下都认。
TOP_ALIASES = {
    "category": "category", "intent": "category", "intent_type": "category",
    "type": "category", "action": "category", "verb": "category",
    "意图": "category", "意图类别": "category", "意图类型": "category",
    "类别": "category", "类型": "category", "动作": "category",
    "args": "args", "arguments": "args", "params": "args", "parameters": "args",
    "slots": "args", "slot": "args", "参数": "args", "字段": "args",
    "槽位": "args", "参数值": "args",
    "needs_clarification": "needs_clarification", "clarification": "needs_clarification",
    "missing": "needs_clarification", "缺少": "needs_clarification",
}

NUMERIC_FIELDS = {
    "qty", "hours", "duration_days", "cargo_value", "fee", "money_delta", "dues",
}

# 生成型字段不是从原话里“抽取”的，而是模型替 NPC 说的话；不参与原文 grounding。
GENERATIVE_FIELDS = {"reply"}

# 中文数量词后面的常见量词；用于把“两斗”归一为 (2, "斗")。
CN_UNITS = (
    "斗", "升", "石", "斤", "文", "贯", "个", "只", "头", "匹", "袋", "包",
    "瓶", "坛", "串", "块", "张", "本", "份", "枚", "担", "碗", "壶", "盘",
    "件", "套", "车", "船", "人", "名", "位", "打",
)
NUMBER_RE = re.compile(r"[0-9]+(?:\.[0-9]+)?|[零一二两三四五六七八九十百千万]+")
_UNIT_ALT = "|".join(sorted((re.escape(u) for u in CN_UNITS), key=len, reverse=True))

# 中文口语数量词：模型/用户都可能只说这些，不带阿拉伯数字。
SPECIAL_QTY = {"俩": 2, "半": 1, "一打": 12, "若干": 1, "一些": 1, "几": 1}
# 同义量词组：个/块/枚/只 可在同一语境互换；斗与斤绝不互换。
UNIT_GROUPS: dict[str, set[str]] = {
    "个": {"个", "块", "枚", "只"}, "块": {"个", "块", "枚", "只"},
    "枚": {"个", "块", "枚", "只"}, "只": {"个", "块", "枚", "只"},
    "人": {"人", "名", "位"}, "名": {"人", "名", "位"}, "位": {"人", "名", "位"},
}
CLAUSE_SPLIT_RE = re.compile(r"[，,。；;！!？?\n]+")


def _field_name(raw: str) -> str:
    key = str(raw or "").strip()
    return FIELD_ALIASES.get(key, FIELD_ALIASES.get(key.lower(), key))


def _top_key(raw: str) -> str:
    key = str(raw or "").strip()
    if key in TOP_ALIASES:
        return TOP_ALIASES[key]
    return TOP_ALIASES.get(key.lower(), "")


def _map_engine_fields(verb: str, fields: dict[str, Any]) -> dict[str, Any]:
    alias = ENGINE_FIELD_ALIAS.get(verb, {})
    out: dict[str, Any] = {}
    for key, value in fields.items():
        out[alias.get(key, key)] = value
    return out


def _declared_ok(verb: str, field: str, declared: set[str]) -> bool:
    if field in declared:
        return True
    return ENGINE_FIELD_ALIAS.get(verb, {}).get(field) in declared


@dataclass(frozen=True)
class IntentTemplate:
    category: str
    verb: str
    required: tuple[str, ...]
    optional: tuple[str, ...] = ()
    source: dict[str, str] = field(default_factory=dict)
    destructive: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "verb": self.verb,
            "required": list(self.required),
            "optional": list(self.optional),
            "source": dict(self.source),
            "destructive": self.destructive,
        }


@dataclass
class IntentCatalog:
    templates: list[IntentTemplate]

    @property
    def by_category(self) -> dict[str, IntentTemplate]:
        return {t.category: t for t in self.templates}

    @property
    def by_verb(self) -> dict[str, IntentTemplate]:
        return {t.verb: t for t in self.templates}

    def validate_against_verbs(self, verbs: dict[str, dict[str, Any]]) -> None:
        """功能表必须与 verbs.yaml 对齐：动词存在、字段不脱节。"""
        errors: list[str] = []
        for t in self.templates:
            meta = verbs.get(t.verb)
            if meta is None:
                errors.append(f"{t.verb}: verbs.yaml 缺少该动词")
                continue
            declared = set((meta.get("args") or {}).keys())
            fields = set(t.required) | set(t.optional)
            if not t.required:
                errors.append(f"{t.verb}: required 不能为空")
            unknown = {f for f in fields if not _declared_ok(t.verb, f, declared)}
            if unknown:
                errors.append(f"{t.verb}: intents.yaml 字段 {sorted(unknown)} 不在 verbs.yaml")
            mapped = {
                ENGINE_FIELD_ALIAS.get(t.verb, {}).get(f, f) for f in fields
            }
            missing = declared - mapped
            if missing:
                errors.append(f"{t.verb}: verbs.yaml 字段 {sorted(missing)} 未进功能表")
            for name in t.required:
                if not _declared_ok(t.verb, name, declared):
                    errors.append(f"{t.verb}: required 字段 {name} 未在 verbs.yaml 声明")
        if errors:
            raise ValueError("intent 功能表与 verbs.yaml 不一致: " + "; ".join(errors))


def load_intent_catalog(root: Path | str, verbs: dict[str, dict[str, Any]] | None = None) -> IntentCatalog:
    path = Path(root) / "config" / "intents.yaml"
    raw = []
    if yaml is not None and path.is_file():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    templates = [
        IntentTemplate(
            category=str(row.get("category", "")).strip(),
            verb=str(row.get("verb", "")).strip(),
            required=tuple(str(x) for x in (row.get("required") or [])),
            optional=tuple(str(x) for x in (row.get("optional") or [])),
            source={str(k): str(v) for k, v in (row.get("source") or {}).items()},
            destructive=bool(row.get("destructive", False)),
        )
        for row in raw
        if isinstance(row, dict) and row.get("category") and row.get("verb")
    ]
    catalog = IntentCatalog(templates=templates)
    if verbs is not None:
        catalog.validate_against_verbs(verbs)
    return catalog


def build_intent_prompt(
    catalog: IntentCatalog,
    utterance: str,
    state_summary: dict[str, Any] | None = None,
    history: list[str] | None = None,
) -> tuple[str, str]:
    """按文章的调用模板：①说话内容+前几轮 ②支持意图 ③每个意图的字段。"""
    system = (
        "你是意图理解模型，只做意图分类与参数抽取，不做决策。"
        "只输出 JSON：{\"category\":\"<类别>\",\"args\":{\"<字段>\":\"<原文值>\"}}。"
        "信息没说全时不要猜，输出 {\"needs_clarification\":[<缺失字段>]}。"
        "用户说“去某地”但没给目的地时，只能从【当前可到达的相邻地点】里选；"
        "无法确定就只缺 destination，不要猜。"
        "否定句（如“不去”“别给我”）视为不执行，输出 {\"category\":\"none\",\"args\":{}}。"
        "未知意图输出 {\"category\":\"unknown\",\"args\":{}}。"
        "不要改写原文参数值。"
    )
    lines: list[str] = ["【前几轮对话】"]
    for h in (history or [])[-4:]:
        lines.append(f"- {h}")
    lines += ["", "【用户/上层说的话】", utterance.strip(), "", "【系统支持哪些意图】"]
    lines.append("、".join(t.category for t in catalog.templates))
    lines += ["", "【每个意图要填哪些字段】"]
    for t in catalog.templates:
        req = "、".join(t.required)
        opt = "、".join(t.optional) or "无"
        lines.append(f"- {t.category}（{t.verb}）：必填 {req}；选填 {opt}")
        for k, v in t.source.items():
            lines.append(f"  - {k} 来源：{v}")
    if state_summary:
        ctx = {
            "npc": state_summary.get("npc"),
            "location": state_summary.get("location"),
            "exits": state_summary.get("exits"),
            "reachable_locations": state_summary.get("reachable_locations"),
            "previous_location": state_summary.get("previous_location"),
            "cash": state_summary.get("cash"),
            "inventory": state_summary.get("inventory"),
            "nearby_npcs": [
                {"id": n.get("id"), "name": n.get("name")}
                for n in (state_summary.get("nearby_npcs") or [])
            ],
            "goal": state_summary.get("goal"),
        }
        lines += ["", "【当前上下文】", json.dumps(ctx, ensure_ascii=False, sort_keys=True)]
        prev = state_summary.get("previous_location") or {}
        cur = {
            "id": state_summary.get("location"),
            "name": state_summary.get("location_name"),
        }
        lines += [
            "",
            "【你刚才在哪】",
            f"- 上一句时你在 {prev.get('name') or cur['name']}（{prev.get('id') or cur['id']}）",
        ]
        lines += ["", "【当前可到达的相邻地点（只可从这些里选 destination）】"]
        for row in state_summary.get("reachable_locations") or []:
            lines.append(f"- {row.get('name') or row.get('id')}（{row.get('id')}）")
        if not state_summary.get("reachable_locations"):
            lines.append("- 无相邻地点；需要目的地时输出 needs_clarification，只缺 destination")
    return system, "\n".join(lines)


def _strip_payload(text: str) -> Any:
    """剥离模型输出里的 ```json 围栏与前后杂文本，尽量拿到 JSON 对象。

    实测 Xing 会在裸 JSON / ```json 围栏 / 围栏前后带解释文字之间漂移，
    因此这里按“围栏内容 → 原文 → 首个 { 到末个 }”三级回退。
    """
    raw = str(text or "").strip().lstrip("\ufeff")
    if not raw:
        return raw
    fenced = re.search(r"```[a-zA-Z0-9_-]*\s*(.*?)```", raw, re.S)
    source = fenced.group(1).strip() if fenced else raw
    for candidate in (source, raw):
        try:
            return json.loads(candidate)
        except (TypeError, ValueError):
            pass
        start, end = candidate.find("{"), candidate.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(candidate[start:end + 1])
            except (TypeError, ValueError):
                pass
    return raw


def _cn_number(token: str) -> int | None:
    token = token.strip()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    total = 0
    section = 0
    numeral = 0
    seen = False
    for ch in token:
        if ch in CN_NUM:
            numeral = CN_NUM[ch]
            seen = True
        elif ch in CN_UNIT:
            unit = CN_UNIT[ch]
            if unit >= 10000:
                section = (section + numeral) * unit
                total += section
                section = 0
            else:
                section += (numeral or 1) * unit
            numeral = 0
            seen = True
        else:
            break
    if not seen:
        return None
    return total + section + numeral


def parse_quantity_unit(value: Any) -> tuple[int | None, str]:
    """数量词归一：返回 (数值, 量词)。"两斗" -> (2, "斗")，"2瓶" -> (2, "瓶")。

    P0-2：补 俩/半/一打/若干/一些/几；"半斤" 在整数库存里向上取 1 份。
    """
    if isinstance(value, bool):
        return None, ""
    if isinstance(value, (int, float)):
        return int(value), ""
    text = str(value).strip()
    special = re.match(r"(一打|俩|半|若干|一些|几)", text)
    if special:
        number = SPECIAL_QTY[special.group(1)]
        rest = text[special.end():].lstrip()
    else:
        match = NUMBER_RE.search(text)
        if not match:
            return None, ""
        number = _cn_number(match.group(0))
        if number is None:
            return None, ""
        rest = text[match.end():].lstrip()
    unit_match = re.match(rf"(?:{_UNIT_ALT})", rest)
    unit = unit_match.group(0) if unit_match else ""
    if unit == "打":
        number *= 12
    return number, unit


def parse_quantity(value: Any) -> int | None:
    return parse_quantity_unit(value)[0]


def _norm(text: Any) -> str:
    return str(text or "").strip().strip("\"'“”")


def _resolve_location(value: Any, content) -> str:
    raw = _norm(value)
    if not raw:
        return ""
    if raw in content.locations:
        return raw
    for lid, row in content.locations.items():
        names = {str(lid), str(row.get("name") or "")}
        if raw in names:
            return str(lid)
    for lid, row in content.locations.items():
        name = str(row.get("name") or "")
        if name and (raw in name or name in raw):
            return str(lid)
    return ""


def _resolve_npc(value: Any, content, state, npc: str) -> str:
    raw = _norm(value)
    if not raw:
        return ""
    if raw in state.entities:
        return raw
    for eid, ent in state.entities.items():
        if eid == npc or ent.status != "alive":
            continue
        if raw == ent.name:
            return eid
    for eid, ent in state.entities.items():
        if eid == npc or ent.status != "alive":
            continue
        if raw and (raw in ent.name or ent.name in raw):
            return eid
    return ""


def _resolve_item(value: Any, content) -> str:
    raw = _norm(value)
    if not raw:
        return ""
    if raw in content.items:
        return raw
    for iid, row in content.items.items():
        if raw == str(row.get("name") or ""):
            return str(iid)
    for iid, row in content.items.items():
        name = str(row.get("name") or "")
        if name and (raw in name or name in raw):
            return str(iid)
    return ""


def _resolve_sect(value: Any, content) -> str:
    raw = _norm(value)
    if raw in content.sects:
        return raw
    if raw.startswith("sect:"):
        return raw
    for sid, row in content.sects.items():
        if raw == str(row.get("name") or ""):
            return str(sid)
    return ""


def _resolve_skill(value: Any, content, state=None) -> str:
    raw = _norm(value)
    skills: set[str] = {s for row in content.npcs for s in (row.get("skills") or {})}
    # 内容包的人设把技能写在实体上（genesis 后 state.entities[npc].skills），
    # content.npcs 本身通常没有 skills 字段；两处都收，避免 cultivate 永远解析失败。
    if state is not None:
        for ent in state.entities.values():
            skills.update((getattr(ent, "skills", None) or {}).keys())
    skills = sorted(skills)
    if raw in skills:
        return raw
    for s in skills:
        if raw in s or s in raw:
            return s
    return ""


def _resolve_by_field(field: str, value: Any, content, state, npc: str) -> Any:
    if field in ("to", "at", "from", "destination") or field.endswith("location"):
        return _resolve_location(value, content)
    if field == "target":
        return _resolve_npc(value, content, state, npc) or _resolve_sect(value, content)
    if field == "item":
        return _resolve_item(value, content)
    if field == "skill":
        return _resolve_skill(value, content, state)
    if field == "route":
        rows = value if isinstance(value, list) else [x for x in re.split(r"[、,，>→-]", str(value)) if x.strip()]
        out = [_resolve_location(x, content) for x in rows]
        return out if out and all(out) and len(out) >= 2 else []
    if field in ("offer", "want"):
        out = []
        rows = value if isinstance(value, list) else [str(value)]
        for row in rows:
            if isinstance(row, dict):
                item = _resolve_item(row.get("item"), content)
                qty = parse_quantity(row.get("qty", 1)) or 1
            else:
                item = _resolve_item(row, content)
                qty = parse_quantity(row) or 1
            if item:
                out.append({"item": item, "qty": qty})
        return out
    if field in ("qty", "hours", "duration_days", "cargo_value", "fee", "money_delta", "dues"):
        return parse_quantity(value)
    if field == "shop_type":
        mapping = {"摊位": "stall", "摊": "stall", "店铺": "storefront", "店": "storefront",
                   "分号": "branch", "分店": "branch"}
        raw = _norm(value)
        return mapping.get(raw, raw if raw in ("stall", "storefront", "branch") else "")
    if field == "quest" or field == "debt":
        return _norm(value)
    if field == "quest_verb":
        return _norm(value)
    return _norm(value)


def _collect_args(value: Any) -> dict[str, Any]:
    """把 args 容器（dict / [{key,value}] / [{...}]）摊平成字段名 -> 原值。"""
    if isinstance(value, list):
        merged: dict[str, Any] = {}
        for row in value:
            if isinstance(row, dict):
                if "key" in row and "value" in row:
                    merged[_field_name(row["key"])] = row["value"]
                else:
                    merged.update({_field_name(k): v for k, v in row.items()})
        return merged
    if isinstance(value, dict):
        return {_field_name(k): v for k, v in value.items()}
    return {}


def _normalize_payload(
    payload: dict[str, Any], allowed: set[str]
) -> tuple[str, dict[str, Any], list[str]]:
    """P3-D 适配层：中英文顶层键 -> category/args；白名单外键丢弃并记 unknown。

    先收参数容器，再收被模型摊平到顶层的已知字段；未知键（含未知英文键）只记录
    键名，不进入 args，避免“多出来的字段”被当成参数落地。
    """
    unknown: list[str] = []
    category = ""
    args: dict[str, Any] = {}
    for raw_key, value in payload.items():
        top = _top_key(raw_key)
        if top == "category":
            category = _norm(value)
        elif top == "args":
            args.update(_collect_args(value))
    for raw_key, value in payload.items():
        top = _top_key(raw_key)
        if top:
            continue
        field = _field_name(raw_key)
        if field in allowed:
            args[field] = value
        else:
            unknown.append(str(raw_key))
    # 参数容器里也可能混入未知字段（例如模型顺手加了 reason）。
    for key in list(args):
        if key not in allowed:
            unknown.append(str(key))
            args.pop(key)
    return category, args, sorted(set(unknown))


def _ground_string(value: Any, corpus: str) -> bool:
    raw = _norm(value)
    return bool(raw) and raw in corpus


def _ground_number(value: Any, corpus: str) -> bool:
    """数字归一后必须能在原文/上下文里找到同一个数（"两斗" 与 2 等价）。"""
    number, _unit = parse_quantity_unit(value)
    if number is None:
        return False
    tokens = {_cn_number(m) for m in NUMBER_RE.findall(corpus)}
    return number in tokens


def _clauses(text: str) -> list[str]:
    return [c.strip() for c in CLAUSE_SPLIT_RE.split(str(text or "")) if c.strip()]


def _quantity_tokens(text: str) -> list[tuple[int, str, str]]:
    """抽取一段文本中的数量词：(数值, 量词, 原文片段)。"""
    pattern = re.compile(
        rf"(一打|俩|半|若干|一些|几|[0-9]+(?:\.[0-9]+)?|[零一二两三四五六七八九十百千万]+)"
        rf"({_UNIT_ALT})?"
    )
    out: list[tuple[int, str, str]] = []
    for match in pattern.finditer(text):
        token = match.group(1)
        unit = match.group(2) or ""
        raw = match.group(0)
        # “三个人”会被切成 (三, 个)，但这里计的是人；把紧随其后的
        # 人/名/位 归为人数单位，避免给物品数量背书（P0-2）。
        tail = text[match.end():match.end() + 1]
        if tail in ("人", "名", "位"):
            unit = tail
            raw += tail
        if token in SPECIAL_QTY:
            number = SPECIAL_QTY[token]
        else:
            number = _cn_number(token)
        if number is None:
            continue
        if unit == "打":
            number *= 12
        out.append((number, unit, raw))
    return out


PEOPLE_UNITS = {"人", "名", "位"}


def _units_compatible(a: str, b: str) -> bool:
    # P0-2：人数单位不能给物品数量背书（“三个人买米”不能推出 qty=3）。
    # 人物数量单位与物品数量单位语义不同，任一侧出现人数单位即不兼容；
    # 即使两侧都是“人/名/位”，也仍然不能作为物品 qty 的依据。
    if a in PEOPLE_UNITS or b in PEOPLE_UNITS:
        return False
    if not a or not b:
        return True
    return b in UNIT_GROUPS.get(a, {a})


def _item_aliases(item_raw: Any, content) -> set[str]:
    raw = _norm(item_raw)
    if not raw:
        return set()
    aliases = {raw}
    resolved = _resolve_item(raw, content) if content is not None else ""
    if resolved:
        aliases.add(resolved)
        row = content.items.get(resolved) or {}
        name = str(row.get("name") or "")
        if name:
            aliases.add(name)
    return aliases


def _ground_quantity(value: Any, utterance: str, item_raw: Any, content) -> bool:
    """P0-2：数量必须与目标物品同分句、且量词兼容；不允许跨分句借数字。"""
    number, unit = parse_quantity_unit(value)
    if number is None:
        return False
    clauses = _clauses(utterance)
    aliases = _item_aliases(item_raw, content)
    if aliases:
        targets = [c for c in clauses if any(a in c for a in aliases)]
    else:
        targets = clauses
    if not targets:
        return False
    for clause in targets:
        for token_number, token_unit, _raw in _quantity_tokens(clause):
            if token_number == number and _units_compatible(unit, token_unit):
                return True
    return False


def _ground_value(
    field: str,
    value: Any,
    corpus: str,
    number_corpus: str = "",
    raw_args: dict[str, Any] | None = None,
    content=None,
) -> bool:
    """参数值是否能在原文/本轮上下文里找到字符串或数字依据。

    字符串可回指「本轮上下文」（如相邻地点名）；数量必须回指用户原文中
    与目标物品同分句的数量词，避免跨分句/人数给编造的数量背书。
    """
    numbers = number_corpus or corpus
    raw_args = raw_args or {}
    if field in GENERATIVE_FIELDS:
        return bool(_norm(value))
    if field in ("offer", "want"):
        rows = value if isinstance(value, list) else [value]
        if not rows:
            return False
        for row in rows:
            if isinstance(row, dict):
                item, qty = row.get("item"), row.get("qty")
            else:
                item, qty = row, None
            if not _ground_string(item, corpus):
                return False
            if qty is not None and not _ground_quantity(qty, numbers, item, content):
                return False
        return True
    if field == "route":
        rows = value if isinstance(value, list) else [value]
        return bool(rows) and all(_ground_string(x, corpus) for x in rows)
    if field == "qty":
        return _ground_quantity(value, numbers, raw_args.get("item"), content)
    if field in NUMERIC_FIELDS:
        return _ground_number(value, numbers)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _ground_number(value, numbers)
    if isinstance(value, list):
        return all(_ground_string(x, corpus) for x in value)
    return _ground_string(value, corpus)


@dataclass
class IntentResolution:
    status: str
    category: str = ""
    verb: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    reason: str = ""
    destructive: bool = False
    unknown_keys: list[str] = field(default_factory=list)
    blocked: list[dict[str, Any]] = field(default_factory=list)
    dropped_fields: list[str] = field(default_factory=list)
    normalizations: dict[str, dict[str, Any]] = field(default_factory=dict)
    grounding: bool = False

    def intent(self, actor: str, tick: int) -> Intent | None:
        if self.status != "ok" or not self.verb:
            return None
        return Intent(actor=actor, verb=self.verb, args=dict(self.args), tick=tick, source="intent")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status, "category": self.category, "verb": self.verb,
            "args": self.args, "missing": self.missing, "reason": self.reason,
            "destructive": self.destructive, "unknown_keys": self.unknown_keys,
            "blocked": self.blocked, "dropped_fields": self.dropped_fields,
            "normalizations": self.normalizations,
            "grounding": self.grounding,
        }


def parse_intent_response(
    text: str,
    catalog: IntentCatalog,
    content,
    state,
    npc: str,
    confirm: bool = False,
    utterance: str = "",
    context_text: str = "",
) -> IntentResolution:
    payload = _strip_payload(text)
    if not isinstance(payload, dict):
        return IntentResolution(status="reject", reason="invalid_json")
    grounding = bool(_norm(utterance))
    corpus = "\n".join(x for x in (_norm(utterance), str(context_text or "")) if x)
    number_corpus = _norm(utterance)
    if payload.get("needs_clarification"):
        missing = payload.get("needs_clarification")
        if isinstance(missing, dict):
            missing = missing.get("missing") or list(missing.values())
        if not isinstance(missing, list):
            missing = [str(missing)]
        return IntentResolution(
            status="needs_clarification",
            missing=[_field_name(x) for x in missing],
            reason="model_needs_clarification",
            grounding=grounding,
        )
    category, unknown_keys = "", []
    # 先定类别，才能知道该模板允许哪些字段（未知键按模板白名单判定）。
    for raw_key, value in payload.items():
        if _top_key(raw_key) == "category":
            category = _norm(value)
            break
    template = catalog.by_category.get(category) or catalog.by_verb.get(category)
    if template is not None and category != template.category:
        category = template.category
    if not category or category in ("none", "unknown"):
        return IntentResolution(status="reject", category=category, reason="unknown_category",
                                grounding=grounding)
    if category in ("needs_clarification", "clarification", "clarify", "missing"):
        # 实测模型也会把 needs_clarification 当 category 返回，缺失字段塞在 args 的 null 里。
        raw_args = payload.get("args") or {}
        missing = [k for k, v in (raw_args or {}).items() if v in (None, "", [], {})]
        return IntentResolution(
            status="needs_clarification", category="",
            missing=[_field_name(x) for x in missing] or ["unknown"],
            reason="model_needs_clarification_category", grounding=grounding,
        )
    if template is None:
        return IntentResolution(status="reject", category=category, reason="unknown_category",
                                grounding=grounding)
    allowed = set(template.required) | set(template.optional)
    _cat, raw_args, unknown_keys = _normalize_payload(payload, allowed)
    args: dict[str, Any] = {}
    missing: list[str] = []
    blocked: list[dict[str, Any]] = []
    dropped: list[str] = []
    normalizations: dict[str, dict[str, Any]] = {}
    for name in template.required + template.optional:
        if name not in raw_args:
            if name in template.required:
                missing.append(name)
            continue
        raw_value = raw_args.get(name)
        if grounding and not _ground_value(
            name, raw_value, corpus, number_corpus, raw_args, content
        ):
            # 实测模型会在缺字段时自行编造（"去买点米" → 数量=1、卖家="买家"）。
            # 找不到原文依据一律按缺失处理，绝不填充；可选字段记入 dropped_fields。
            blocked.append({"field": name, "raw": raw_value})
            if name in template.required:
                missing.append(name)
            else:
                dropped.append(name)
            continue
        value = _resolve_by_field(name, raw_value, content, state, npc)
        if value == "" or value is None or value == []:
            if name in template.required:
                missing.append(name)
            else:
                dropped.append(name)
            continue
        if name in NUMERIC_FIELDS:
            number, unit = parse_quantity_unit(raw_value)
            if number is not None:
                normalizations[name] = {
                    "value": number,
                    "unit": unit,
                    "source": _norm(raw_value),
                }
        args[name] = value
    if missing:
        reason = "grounding_missing_required" if blocked else "missing_required"
        return IntentResolution(status="needs_clarification", category=category, verb=template.verb,
                                args=args, missing=missing, reason=reason,
                                destructive=template.destructive, unknown_keys=unknown_keys,
                                blocked=blocked, dropped_fields=dropped,
                                normalizations=normalizations, grounding=grounding)
    args = _map_engine_fields(template.verb, args)
    if template.destructive and not confirm:
        return IntentResolution(status="needs_confirmation", category=category, verb=template.verb,
                                args=args, reason="destructive_requires_confirmation",
                                destructive=True, unknown_keys=unknown_keys, blocked=blocked,
                                dropped_fields=dropped, normalizations=normalizations,
                                grounding=grounding)
    return IntentResolution(status="ok", category=category, verb=template.verb, args=args,
                            destructive=template.destructive, unknown_keys=unknown_keys,
                            blocked=blocked, dropped_fields=dropped,
                            normalizations=normalizations, grounding=grounding)
