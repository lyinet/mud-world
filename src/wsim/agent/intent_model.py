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

# 上游可能返回中文键名；这里归一成模板字段名。
FIELD_ALIASES = {
    "地点": "destination", "目的地": "destination", "去往": "destination",
    "地点编号": "destination", "位置": "destination",
    "人物": "target", "对象": "target", "目标": "target", "目标人物": "target",
    "门派": "target", "组织": "target",
    "物品": "item", "东西": "item", "货物": "item",
    "数量": "qty", "份数": "qty",
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
}


def _field_name(raw: str) -> str:
    key = str(raw or "").strip()
    return FIELD_ALIASES.get(key, key)


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
            "cash": state_summary.get("cash"),
            "inventory": state_summary.get("inventory"),
            "nearby_npcs": [
                {"id": n.get("id"), "name": n.get("name")}
                for n in (state_summary.get("nearby_npcs") or [])
            ],
            "goal": state_summary.get("goal"),
        }
        lines += ["", "【当前上下文】", json.dumps(ctx, ensure_ascii=False, sort_keys=True)]
    return system, "\n".join(lines)


def _strip_payload(text: str) -> Any:
    raw = str(text).strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
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


def parse_quantity(value: Any) -> int | None:
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    match = re.search(r"[0-9]+|[零一二两三四五六七八九十百千万]+", text)
    if not match:
        return None
    return _cn_number(match.group(0))


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


def _resolve_skill(value: Any, content) -> str:
    raw = _norm(value)
    skills = sorted({s for row in content.npcs for s in (row.get("skills") or {})})
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
        return _resolve_skill(value, content)
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


@dataclass
class IntentResolution:
    status: str
    category: str = ""
    verb: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    reason: str = ""
    destructive: bool = False

    def intent(self, actor: str, tick: int) -> Intent | None:
        if self.status != "ok" or not self.verb:
            return None
        return Intent(actor=actor, verb=self.verb, args=dict(self.args), tick=tick, source="intent")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status, "category": self.category, "verb": self.verb,
            "args": self.args, "missing": self.missing, "reason": self.reason,
            "destructive": self.destructive,
        }


def parse_intent_response(
    text: str,
    catalog: IntentCatalog,
    content,
    state,
    npc: str,
    confirm: bool = False,
) -> IntentResolution:
    payload = _strip_payload(text)
    if not isinstance(payload, dict):
        return IntentResolution(status="reject", reason="invalid_json")
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
        )
    category = _norm(payload.get("category"))
    if not category or category in ("none", "unknown"):
        return IntentResolution(status="reject", category=category, reason="unknown_category")
    template = catalog.by_category.get(category)
    if template is None:
        return IntentResolution(status="reject", category=category, reason="unknown_category")
    raw_args = payload.get("args") or {}
    if isinstance(raw_args, list):
        merged: dict[str, Any] = {}
        for row in raw_args:
            if isinstance(row, dict):
                if "key" in row and "value" in row:
                    merged[_field_name(row["key"])] = row["value"]
                else:
                    merged.update({_field_name(k): v for k, v in row.items()})
        raw_args = merged
    if not isinstance(raw_args, dict):
        return IntentResolution(status="reject", category=category, verb=template.verb,
                                reason="invalid_args")
    raw_args = {_field_name(k): v for k, v in raw_args.items()}
    args: dict[str, Any] = {}
    missing: list[str] = []
    for name in template.required + template.optional:
        if name not in raw_args:
            if name in template.required:
                missing.append(name)
            continue
        value = _resolve_by_field(name, raw_args.get(name), content, state, npc)
        if value == "" or value is None or value == []:
            if name in template.required:
                missing.append(name)
            continue
        args[name] = value
    if missing:
        return IntentResolution(status="needs_clarification", category=category, verb=template.verb,
                                args=args, missing=missing, reason="missing_required",
                                destructive=template.destructive)
    args = _map_engine_fields(template.verb, args)
    if template.destructive and not confirm:
        return IntentResolution(status="needs_confirmation", category=category, verb=template.verb,
                                args=args, reason="destructive_requires_confirmation",
                                destructive=True)
    return IntentResolution(status="ok", category=category, verb=template.verb, args=args,
                            destructive=template.destructive)
