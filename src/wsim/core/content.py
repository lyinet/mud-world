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


def _day_value(values: Any, day: int) -> float:
    """按天取供需值；超出区间时沿用最后一天，避免 2 日内容被跑长 run 时归零。"""
    if not isinstance(values, (list, tuple)) or not values:
        return 1.0
    idx = min(max(0, int(day)), len(values) - 1)
    try:
        return float(values[idx])
    except (TypeError, ValueError):
        return 1.0


@dataclass
class Content:
    npcs: list[dict[str, Any]] = field(default_factory=list)
    locations: dict[str, dict[str, Any]] = field(default_factory=dict)
    items: dict[str, dict[str, Any]] = field(default_factory=dict)
    verbs: dict[str, dict[str, Any]] = field(default_factory=dict)
    economy: dict[str, Any] = field(default_factory=dict)
    roles: dict[str, dict[str, Any]] = field(default_factory=dict)
    sects: dict[str, dict[str, Any]] = field(default_factory=dict)
    quests: dict[str, dict[str, Any]] = field(default_factory=dict)
    # xkx 内容包的任务（`content/xkx/quests.json`，按 id 索引）。为空时
    # 不进 content_hash，旧内容包的 content hash 不变。
    xkx_quests: dict[str, dict[str, Any]] = field(default_factory=dict)
    escort_routes: dict[str, dict[str, Any]] = field(default_factory=dict)
    personas: dict[str, str] = field(default_factory=dict)
    # 技能元数据（xkx JSON 内容包）：只供成长线读取，不参与 content_hash，
    # 避免给旧内容包补字段时改写既有 run 的 content_hash。
    skills: list[dict[str, Any]] = field(default_factory=list)
    skills_stats: list[dict[str, Any]] = field(default_factory=list)
    manifest_version: str = ""
    sources: dict[str, str] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        payload = {
            "npcs": self.npcs,
            "locations": self.locations,
            "items": self.items,
            "verbs": self.verbs,
            "economy": self.economy,
            "roles": self.roles,
            "sects": self.sects,
            "quests": self.quests,
            "escort_routes": self.escort_routes,
            "personas": self.personas,
            "manifest_version": self.manifest_version,
        }
        if self.xkx_quests:
            payload["xkx_quests"] = self.xkx_quests
        return hash_obj(payload)

    def sect_at(self, location: str) -> dict[str, Any] | None:
        for sid in sorted(self.sects):
            sect = self.sects[sid]
            if str(sect.get("location", "")) == location:
                return sect
        return None

    def item_price(self, item_id: str, state: Any | None = None) -> int:
        """物品价格；传 state 时叠加供需/库存乘数（确定性，无随机）。

        不传 state 的调用（创世、只读展示、测试兜底）保持旧的静态定价，
        因此旧内容包在拿不到世界状态时行为不变。
        """
        item = self.items.get(item_id, {})
        base = int(item.get("base_price", 10))
        price = base * float(self.economy.get("price_markup", 1.0))
        if state is not None:
            price *= self.price_multiplier(item_id, state)
        return max(1, int(round(price)))

    def price_multiplier(self, item_id: str, state: Any) -> float:
        """供需乘数：日供需曲线（content price_model）+ 全城库存压力。

        - 日曲线：`(demand - supply) / (demand + supply)`，按 state.tick 换算天数取数组下标。
        - 库存压力：`(target - stock) / target`，stock 是该物品在所有商业体里的总库存，
          缺货抬价、压货降价；再做 ±1 截断，避免单个店铺把乘数拉爆。
        - 两者按 `stock_weight` 加权后乘以品类弹性，最后用 `max_change` 硬截断
          （默认 ±20%），这是防止价格失控的护栏。
        只读 state.tick / state.businesses[*].stock，不引入 random、墙钟或外部数据。
        """
        model = self.economy.get("price_model")
        if not isinstance(model, dict):
            return 1.0
        category = self._price_category(model, item_id)
        if category is None:
            return 1.0
        elasticity = float(category.get("elasticity", 0.0) or 0.0)
        day_span = max(1, int(model.get("ticks_per_day", 144)))
        day = max(0, int(getattr(state, "tick", 0)) // day_span)
        supply = _day_value(category.get("supply_by_day"), day)
        demand = _day_value(category.get("demand_by_day"), day)
        total = supply + demand
        day_curve = (demand - supply) / total if total > 0 else 0.0
        target_stock = float(self.economy.get("business_stock", 2000) or 0)
        stock = 0.0
        for biz in getattr(state, "businesses", {}).values():
            stock += float((biz.stock or {}).get(item_id, 0))
        stock_curve = (target_stock - stock) / target_stock if target_stock > 0 else 0.0
        stock_curve = max(-1.0, min(1.0, stock_curve))
        stock_weight = float(model.get("stock_weight", 0.5))
        raw = elasticity * (day_curve + stock_weight * stock_curve)
        cap = float(model.get("max_change", 0.20))
        return max(1.0 - cap, min(1.0 + cap, 1.0 + raw))

    def _price_category(self, model: dict[str, Any], item_id: str) -> dict[str, Any] | None:
        categories = model.get("categories")
        if not isinstance(categories, dict):
            return None
        for name in sorted(categories):
            cfg = categories[name]
            if isinstance(cfg, dict) and item_id in (cfg.get("items") or []):
                return cfg
        return None

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
    packs: list[Any] = []

    if manifest_mode:
        from .content_manifest import load_content_packages

        loaded = load_content_packages(content_dir)
        packs = loaded.packs
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

    # 基础门派（P2-B 起引擎真实读取）：sects.yaml 未在 manifest 声明，直接读。
    sp = content_dir / "sects.yaml"
    if use_content and sp.exists():
        raw_base = _read_yaml(sp) or {}
        for sect in (raw_base.get("sects") if isinstance(raw_base, dict) else raw_base) or []:
            if not isinstance(sect, dict) or not sect.get("id"):
                continue
            sid = str(sect["id"])
            loc = str(sect.get("seat") or sect.get("location") or "")
            entry = content.sects.setdefault(sid, {"id": sid, "name": str(sect.get("name") or sid)})
            entry.setdefault("name", str(sect.get("name") or sid))
            entry.setdefault("location", loc)
            for key in ("founder_candidate", "fallback_founder", "recruit_targets", "founding_cost",
                        "min_members", "min_money", "registration_fee", "min_reputation",
                        "min_relation_value", "dues", "activity_locations", "skill_directions"):
                if key in sect:
                    entry.setdefault(key, sect[key])
        content.sources["sects"] = "content/sects.yaml"

    if not content.locations:
        raise RuntimeError("没有可用的 locations 数据（content/locations.yaml 与 fixture 均缺失）")
    if not content.npcs:
        raise RuntimeError("没有可用的 npcs 数据（content/npcs.yaml 与 fixture 均缺失）")

    # 任务模板与镖路：P2 起直接读取内容包（不写 manifest，避免把只读内容包当扩展校验）。
    if use_content:
        qp = content_dir / "quests.yaml"
        if qp.exists():
            raw_q = _read_yaml(qp) or {}
            for q in (raw_q.get("quests") if isinstance(raw_q, dict) else raw_q) or []:
                if isinstance(q, dict) and q.get("id"):
                    content.quests[str(q["id"])] = dict(q)
            content.sources["quests"] = "content/quests.yaml"
        rp = content_dir / "escort_routes.yaml"
        if rp.exists():
            raw_r = _read_yaml(rp) or {}
            for r in (raw_r.get("escort_routes") if isinstance(raw_r, dict) else raw_r) or []:
                if isinstance(r, dict) and r.get("id"):
                    content.escort_routes[str(r["id"])] = dict(r)
            content.sources["escort_routes"] = "content/escort_routes.yaml"

        # 已开启扩展包的委托模板：与 base 直接合并，键名冲突时以包内为准。
        for pack in packs:
            qp = pack.file("quests")
            if qp is None or not qp.exists():
                continue
            raw_q = _read_yaml(qp) or {}
            for q in (raw_q.get("quests") if isinstance(raw_q, dict) else raw_q) or []:
                if isinstance(q, dict) and q.get("id"):
                    content.quests[str(q["id"])] = dict(q)
            source = f"content/{qp.relative_to(content_dir).as_posix()}"
            prior = content.sources.get("quests")
            content.sources["quests"] = f"{prior}+{source}" if prior else source

    # personas：整卡读入，供 Prompt 按 token 预算与章节优先级截断（见 agent/prompt.py）。
    # 已开启扩展包的卡放在 content/packs/<id>/personas/ 下，与 base 30 张合并。
    persona_dir = content_dir / "personas"
    persona_sources: list[str] = []
    if use_content and persona_dir.is_dir():
        for p in sorted(persona_dir.glob("*.md")):
            content.personas[p.stem] = p.read_text(encoding="utf-8").strip()
        if content.personas:
            persona_sources.append("content/personas/*.md")
    for pack in packs:
        pack_personas = pack.path / "personas"
        if not pack_personas.is_dir():
            continue
        found = False
        for p in sorted(pack_personas.glob("*.md")):
            content.personas[p.stem] = p.read_text(encoding="utf-8").strip()
            found = True
        if found:
            persona_sources.append(
                f"content/{pack_personas.relative_to(content_dir).as_posix()}/*.md"
            )
    if persona_sources:
        content.sources["personas"] = "+".join(persona_sources)

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
