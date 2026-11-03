"""内容加载：优先读 content/ 下的 YAML，缺失时用 fixtures/default 的 JSON 兜底。

content 由另一个会话维护，本模块只读，绝不写入。
schema（只读这几个字段，多余字段保留但不解释）：
- npcs.yaml: id / name / role / personality{traits[], speech_style} /
             goals[{id,type,priority}] / assets{money, items[{id,qty}]} /
             relations[{target,type,value}]
- locations.yaml: id / name / exits[] / services[]
- items.yaml: id / name / category / base_price
- verbs.yaml: name / args / cooldown / allowed_roles
- economy.yaml: 物价、工资、税率等参数（自由 dict）
"""
from __future__ import annotations

import json
import sys
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .canonical import hash_obj

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None


DEFAULT_ECONOMY = {
    "wage_default": 20,
    "price_markup": 1.0,
    "tax_rate": 0.01,
    "stamina_move": 5,
    "stamina_work": 12,
    "stamina_cultivate": 4,
    "stamina_recover": 40,
    "food_stamina": 25,
    "starting_business_cash": 300,
    "business_stock": 2000,
    "sell_ratio": 0.6,
}


@dataclass
class Content:
    npcs: list[dict[str, Any]] = field(default_factory=list)
    locations: dict[str, dict[str, Any]] = field(default_factory=dict)
    items: dict[str, dict[str, Any]] = field(default_factory=dict)
    verbs: dict[str, dict[str, Any]] = field(default_factory=dict)
    economy: dict[str, Any] = field(default_factory=dict)
    roles: dict[str, dict[str, Any]] = field(default_factory=dict)
    sects: dict[str, dict[str, Any]] = field(default_factory=dict)
    manifest_version: str = ""
    sources: dict[str, str] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        return hash_obj(
            {
                "npcs": self.npcs,
                "locations": self.locations,
                "items": self.items,
                "verbs": self.verbs,
                "economy": self.economy,
                "roles": self.roles,
                "sects": self.sects,
                "manifest_version": self.manifest_version,
            }
        )

    def sect_at(self, location: str) -> dict[str, Any] | None:
        for sid in sorted(self.sects):
            sect = self.sects[sid]
            if str(sect.get("location", "")) == location:
                return sect
        return None

    def item_price(self, item_id: str) -> int:
        item = self.items.get(item_id, {})
        base = int(item.get("base_price", 10))
        return max(1, int(round(base * float(self.economy.get("price_markup", 1.0)))))

    def wage_for(self, role: str) -> int:
        table = self.economy.get("wages_daily") or {}
        if role in table:
            return int(table[role])
        table2 = self.economy.get("wage_by_role") or {}
        if role in table2:
            return int(table2[role])
        return int(self.economy.get("wage_default", 20))

    def allowed_roles(self, verb: str) -> list[str] | None:
        meta = self.verbs.get(verb) or {}
        roles = meta.get("allowed_roles")
        if not roles:
            return None
        return list(roles)


def _read_yaml(path: Path) -> Any:
    if yaml is None:
        raise RuntimeError("PyYAML not installed")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_one(
    content_dir: Path,
    fixture_dir: Path,
    filename: str,
    fixture_name: str,
    allow_fixture: bool = True,
    use_content: bool = True,
) -> tuple[Any, str]:
    p = content_dir / filename
    if use_content and p.exists():
        try:
            data = _read_yaml(p)
            if data:
                return data, f"content/{filename}"
            warnings.warn(f"{p} 为空，使用 fixture 兜底")
        except Exception as exc:  # 坏 YAML 不应阻塞模拟
            warnings.warn(f"读取 {p} 失败({exc})，使用 fixture 兜底")
    if not allow_fixture:
        raise RuntimeError(f"缺少或无法读取 {p}（fixture 已禁用）")
    f = fixture_dir / fixture_name
    if not f.exists():
        return None, "missing"
    return _read_json(f), f"fixture/{fixture_name}"


def _normalize_npc(raw: dict[str, Any], idx: int) -> dict[str, Any]:
    npc = dict(raw or {})
    npc["id"] = str(npc.get("id") or f"npc.unknown{idx}")
    npc["name"] = str(npc.get("name") or npc["id"])
    npc["role"] = str(npc.get("role") or "wanderer")
    personality = npc.get("personality") or {}
    npc["personality"] = {
        "traits": list(personality.get("traits") or []),
        "speech_style": str(personality.get("speech_style") or ""),
    }
    goals = []
    for g in npc.get("goals") or []:
        if not isinstance(g, dict):
            continue
        goals.append(
            {
                "id": str(g.get("id") or f"goal.{npc['id']}.{len(goals)}"),
                "type": str(g.get("type") or "wealth"),
                "priority": float(g.get("priority", 1.0)),
            }
        )
    npc["goals"] = goals
    assets = npc.get("assets") or {}
    items = []
    for it in assets.get("items") or []:
        if not isinstance(it, dict):
            continue
        items.append({"id": str(it.get("id")), "qty": max(0, int(it.get("qty", 0)))})
    npc["assets"] = {"money": max(0, int(assets.get("money", 0))), "items": items}
    rels = []
    for r in npc.get("relations") or []:
        if not isinstance(r, dict) or not r.get("target"):
            continue
        item = {
            "target": str(r["target"]),
            "type": str(r.get("type") or "friend"),
            "value": float(r.get("value", 0.0)),
        }
        if "amount" in r:
            item["amount"] = max(0, int(r["amount"]))
        if r.get("long_term"):
            item["long_term"] = True
        rels.append(item)
    npc["relations"] = rels
    npc["home"] = raw.get("home") or raw.get("location") or None
    return npc


def _normalize_location(raw: dict[str, Any], idx: int) -> tuple[str, dict[str, Any]]:
    loc = dict(raw or {})
    lid = str(loc.get("id") or f"loc.unknown{idx}")
    return lid, {
        "id": lid,
        "name": str(loc.get("name") or lid),
        "exits": [str(x) for x in (loc.get("exits") or [])],
        "services": [str(x) for x in (loc.get("services") or [])],
    }


def load_content(
    root: Path | None = None,
    min_npcs: int = 0,
    allow_fixture: bool = True,
    use_content: bool = True,
) -> Content:
    """加载内容包。

    use_content=False 时只读 fixtures/default（仅测试使用）；生产模拟必须
    use_content=True 且 allow_fixture=False，避免静默回退掩盖内容包缺失。
    """
    root = root or Path(__file__).resolve().parents[3]
    content_dir = root / "content"
    fixture_dir = root / "fixtures" / "default"
    manifest_mode = use_content and (content_dir / "manifest.yaml").exists()

    if manifest_mode:
        from .content_manifest import load_content_packages

        loaded = load_content_packages(content_dir)
        raw_npcs = loaded.data["npcs"]
        raw_locs = loaded.data["locations"]
        raw_items = loaded.data["items"]
        raw_verbs = loaded.data["verbs"]
        raw_eco = loaded.data["economy"]
        raw_roles = loaded.data["roles"]
        raw_sects = loaded.data["sects"]
        src_n = loaded.sources.get("npcs", "content/npcs.yaml")
        src_l = loaded.sources.get("locations", "content/locations.yaml")
        src_i = loaded.sources.get("items", "content/items.yaml")
        src_v = loaded.sources.get("verbs", "content/verbs.yaml")
        src_e = loaded.sources.get("economy", "content/economy.yaml")
        content = Content(
            sources={
                "npcs": src_n,
                "locations": src_l,
                "items": src_i,
                "verbs": src_v,
                "economy": src_e,
                "roles": loaded.sources.get("roles", ""),
                "sects": loaded.sources.get("sects", ""),
            },
            manifest_version=loaded.manifest_version,
        )
    else:
        raw_npcs, src_n = _load_one(content_dir, fixture_dir, "npcs.yaml", "npcs.json", allow_fixture, use_content)
        raw_locs, src_l = _load_one(content_dir, fixture_dir, "locations.yaml", "locations.json", allow_fixture, use_content)
        raw_items, src_i = _load_one(content_dir, fixture_dir, "items.yaml", "items.json", allow_fixture, use_content)
        raw_verbs, src_v = _load_one(content_dir, fixture_dir, "verbs.yaml", "verbs.json", allow_fixture, use_content)
        raw_eco, src_e = _load_one(content_dir, fixture_dir, "economy.yaml", "economy.json", allow_fixture, use_content)
        raw_roles, raw_sects = [], []
        content = Content(
            sources={"npcs": src_n, "locations": src_l, "items": src_i, "verbs": src_v, "economy": src_e}
        )

    # NPC：支持 list 或 {npcs: [...]} 两种顶层形态
    if isinstance(raw_npcs, dict):
        raw_npcs = raw_npcs.get("npcs") or []
    for i, npc in enumerate(raw_npcs or []):
        if isinstance(npc, dict):
            content.npcs.append(_normalize_npc(npc, i))

    # 若 content 数量不足，用 fixture 中不冲突的条目补齐（保证 30 NPC 规模可跑）
    if min_npcs and len(content.npcs) < min_npcs:
        if not allow_fixture:
            raise RuntimeError(f"content NPC 数量不足: {len(content.npcs)} < {min_npcs}")
        fixture_npcs = _read_json(fixture_dir / "npcs.json")
        if isinstance(fixture_npcs, dict):
            fixture_npcs = fixture_npcs.get("npcs") or []
        existing = {n["id"] for n in content.npcs}
        for i, npc in enumerate(fixture_npcs):
            if len(content.npcs) >= min_npcs:
                break
            if not isinstance(npc, dict):
                continue
            norm = _normalize_npc(npc, i)
            if norm["id"] in existing:
                continue
            existing.add(norm["id"])
            content.npcs.append(norm)
        if src_n.startswith("content/"):
            content.sources["npcs"] += f"+fixture(补齐至{min_npcs})"

    if isinstance(raw_locs, dict):
        raw_locs = raw_locs.get("locations") or []
    for i, loc in enumerate(raw_locs or []):
        if isinstance(loc, dict):
            lid, norm = _normalize_location(loc, i)
            content.locations[lid] = norm

    if isinstance(raw_items, dict) and "items" in raw_items:
        raw_items = raw_items["items"]
    for i, item in enumerate(raw_items or []):
        if not isinstance(item, dict):
            continue
        iid = str(item.get("id") or f"item.unknown{i}")
        content.items[iid] = {
            "id": iid,
            "name": str(item.get("name") or iid),
            "category": str(item.get("category") or "misc"),
            "base_price": max(1, int(item.get("base_price", 10))),
        }

    if isinstance(raw_verbs, dict) and "verbs" in raw_verbs:
        raw_verbs = raw_verbs["verbs"]
    for verb in raw_verbs or []:
        if isinstance(verb, str):
            content.verbs[verb] = {"name": verb, "args": [], "cooldown": 0, "allowed_roles": []}
        elif isinstance(verb, dict) and verb.get("name"):
            content.verbs[str(verb["name"])] = dict(verb)

    content.economy = dict(DEFAULT_ECONOMY)
    if isinstance(raw_eco, dict):
        content.economy.update(raw_eco)

    # 扩展职业：数据里只写 wage / living_cost / skill / workplace，
    # 这里合并进 economy 的既有表，引擎仍按原字段读取，无需知道 roles.yaml 存在。
    for role in raw_roles or []:
        if not isinstance(role, dict) or not role.get("id"):
            continue
        rid = str(role["id"])
        content.roles[rid] = {
            "id": rid,
            "name": str(role.get("name") or rid),
            "wage": int(role.get("wage", 0)),
            "skill": str(role.get("skill") or rid),
            "workplace": str(role.get("workplace") or ""),
            "living_cost": {str(k): int(v) for k, v in (role.get("living_cost") or {}).items()},
        }
        content.economy.setdefault("wages_daily", {})[rid] = int(role.get("wage", 0))
        costs = content.economy.setdefault("living_costs", {})
        costs.setdefault("by_role", {})[rid] = dict(content.roles[rid]["living_cost"])
        content.economy.setdefault("skill_by_role", {})[rid] = content.roles[rid]["skill"]

    # 扩展门派：genesis 会按 location 实例化为公共组织（founder=""）。
    for sect in raw_sects or []:
        if not isinstance(sect, dict) or not sect.get("id"):
            continue
        sid = str(sect["id"])
        content.sects[sid] = {
            "id": sid,
            "name": str(sect.get("name") or sid),
            "location": str(sect.get("location") or ""),
        }

    if not content.locations:
        raise RuntimeError("没有可用的 locations 数据（content/locations.yaml 与 fixture 均缺失）")
    if not content.npcs:
        raise RuntimeError("没有可用的 npcs 数据（content/npcs.yaml 与 fixture 均缺失）")

    _repair_exits(content)
    return content


def _repair_exits(content: Content) -> None:
    """丢弃指向不存在地点的出口，并补齐反向边，避免路径单向导致死锁。"""
    for lid, loc in content.locations.items():
        loc["exits"] = [x for x in loc["exits"] if x in content.locations and x != lid]
    for lid, loc in content.locations.items():
        for other in loc["exits"]:
            if lid not in content.locations[other]["exits"]:
                content.locations[other]["exits"].append(lid)
        loc["exits"] = sorted(set(loc["exits"]))


def describe_sources(content: Content) -> str:
    return ", ".join(f"{k}={v}" for k, v in sorted(content.sources.items()))


if __name__ == "__main__":  # pragma: no cover
    c = load_content(Path(sys.argv[1]) if len(sys.argv) > 1 else None, min_npcs=30)
    print(f"npcs={len(c.npcs)} locations={len(c.locations)} items={len(c.items)} verbs={len(c.verbs)}")
    print(describe_sources(c))
