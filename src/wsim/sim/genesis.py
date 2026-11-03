"""创世：把内容包投影成事件。

只在 genesis 铸币：NPC 初始资产 + 地点金库 float（内容包 money_supply 声明的总量）。
之后所有资金流动只能是 transfer / burn，禁止日常增发。
"""
from __future__ import annotations

import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..actions.common import goal_kind, goal_target, organization_at
from ..core.content import Content
from ..core.models import Effect
from ..world.state import WorldState


@dataclass
class GenesisResult:
    effects: list[Effect]
    mismatches: list[str] = field(default_factory=list)
    initial_npc_money: int = 0
    initial_location_float: int = 0
    debts: int = 0
    long_term_debts: int = 0


def _stable_index(key: str, n: int) -> int:
    return sum(ord(c) for c in key) % n if n else 0


def _load_role_workplace(root: Path | None) -> dict[str, str]:
    if root is None:
        return {}
    p = root / "config" / "role_workplace.yaml"
    if not p.exists():
        return {}
    try:
        import yaml

        with p.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return {str(k): str(v) for k, v in (data.get("roles") or {}).items()}
    except Exception as exc:  # 配置缺失不应阻塞
        warnings.warn(f"读取 {p} 失败: {exc}")
        return {}


def _assign_home(loc_id: str, content: Content, key: str) -> str:
    exits = sorted(content.locations[loc_id].get("exits") or [])
    if exits:
        return exits[_stable_index(key, len(exits))]
    return loc_id


def _pick_business(state: WorldState, location: str, key: str) -> str | None:
    ids = sorted(b.id for b in state.business_at(location))
    if not ids:
        return None
    return ids[_stable_index(key, len(ids))]


def build_genesis(
    content: Content,
    npcs: list[dict[str, Any]],
    root: Path | None = None,
    state: WorldState | None = None,
) -> GenesisResult:
    """构建创世事件。

    state 用于就业分配时查询已创建商业体；若为 None 则只返回创世基础效果。
    就业分配依赖商业体，因此调用方应先 apply 基础效果，或传入空 state 并让
    build_genesis 先创建商业体再回填就业（这里采用后者：一次性效果顺序创建，
    就业通过 business_at 的临时投影计算）。
    """
    eco = content.economy
    supply = eco.get("money_supply") or {}
    loc_float = supply.get("location_float") or {}
    stock = int(eco.get("business_stock", 2000))
    role_map = _load_role_workplace(root)
    # 内容层扩展职业：workplace 由 content/additions/roles.yaml 声明，优先于引擎配置。
    for rid, meta in (getattr(content, "roles", {}) or {}).items():
        if meta.get("workplace"):
            role_map[str(rid)] = str(meta["workplace"])
    mismatches: list[str] = []
    effects: list[Effect] = []

    npc_by_id = {n["id"]: n for n in npcs}
    loc_ids = sorted(content.locations)

    # 1) 地点常设组织（用于 join_sect target=location）。
    #    若内容包为某地点声明了门派，则用门派实例替换默认行会，避免同地两个公共组织。
    declared_sects: dict[str, dict[str, Any]] = {}
    for sid in sorted(getattr(content, "sects", {}) or {}):
        sect = content.sects[sid]
        if sect.get("location") in content.locations:
            declared_sects[str(sect["location"])] = sect
    for lid in loc_ids:
        sect = declared_sects.get(lid)
        if sect is not None:
            fid = f"sect:{sect['id']}"
            name = str(sect["name"])
        else:
            fid = f"org:{lid}"
            name = f"{content.locations[lid]['name']}·行会"
        treasury = f"treasury:{fid}"
        effects.append(Effect("create_account", {"account": treasury}))
        effects.append(
            Effect(
                "create_faction",
                {
                    "id": fid,
                    "name": name,
                    "location": lid,
                    "founder": "",
                    "treasury_account": treasury,
                },
            )
        )


    # 2) 地点商业体 + 区域结算金库
    # 内容包 payroll_rules 规定工资从“NPC 所属地点的金库”支付，掌柜可先行垫付。
    # P1 只有一个区域，且实测按地点拆分会造成结构性欠薪：书院/镖局/河渡/药铺/城隍庙
    # 的工资总额远超本地服务收入，而集市/客栈现金充裕。因此 P1 用单一区域结算金库
    # `treasury:region` 汇总所有商业体现金，作为“掌柜垫付 + 区域清算”的简化实现。
    # 地点 float 仍按内容包总数 mint 一次（不是新增货币），只是不再按 service 切分。
    region_treasury = "treasury:region"
    effects.append(Effect("create_account", {"account": region_treasury}))
    business_ids: dict[str, list[str]] = {}
    float_total = 0
    for lid in loc_ids:
        services = sorted(set(content.locations[lid].get("services") or []))
        amount_total = int(loc_float.get(lid, 0))
        float_total += amount_total
        business_ids[lid] = []
        for service in services:
            bid = f"biz:{lid}:{service}"
            effects.append(
                Effect(
                    "create_business",
                    {
                        "id": bid,
                        "name": f"{content.locations[lid]['name']}·{service}",
                        "location": lid,
                        "kind": service,
                        "cash_account": region_treasury,
                    },
                )
            )
            if service in content.items and stock > 0:
                effects.append(
                    Effect("item_create", {"owner": bid, "item": service, "qty": stock, "reason": "genesis_stock"})
                )
            business_ids[lid].append(bid)
        if not services:
            mismatches.append(f"location_without_services:{lid}")
    if float_total > 0:
        effects.append(
            Effect(
                "mint",
                {"account": region_treasury, "amount": float_total, "reason": "genesis_location_float"},
            )
        )

    # 3) 师徒关系（内容包口径 master = owner 是 target 的师父）
    master_of: dict[str, str] = {}
    disciples: dict[str, list[str]] = defaultdict(list)
    for npc in npcs:
        for rel in npc.get("relations") or []:
            target = rel["target"]
            if target not in npc_by_id:
                continue
            if rel["type"] == "master":
                master_of[target] = npc["id"]
                disciples[npc["id"]].append(target)
            elif rel["type"] == "disciple":
                master_of[npc["id"]] = target
                disciples[target].append(npc["id"])

    # 4a) NPC 实体/账户/资产（必须全部先创建，师徒 effect 才能引用）
    npc_money_total = 0
    npc_home: dict[str, str] = {}
    npc_work: dict[str, str] = {}
    for npc in npcs:
        nid = npc["id"]
        acc = f"wallet:{nid}"
        work_loc = role_map.get(npc["role"])
        if work_loc not in content.locations:
            work_loc = loc_ids[_stable_index(nid, len(loc_ids))]
        home = npc.get("home") if npc.get("home") in content.locations else _assign_home(work_loc, content, nid)
        npc_home[nid] = home
        npc_work[nid] = work_loc
        skill = 0.30 if nid in master_of else (0.65 if disciples.get(nid) else 0.40 + _stable_index(npc["role"], 15) / 100.0)
        money = int(npc["assets"].get("money", 0))
        npc_money_total += money

        effects.append(Effect("create_account", {"account": acc}))
        effects.append(
            Effect(
                "create_entity",
                {
                    "id": nid,
                    "name": npc["name"],
                    "role": npc["role"],
                    "location": home,
                    "home": home,
                    "stamina": 100.0,
                    "skills": {content_role_skill(content, npc["role"]): skill},
                    "personality": npc.get("personality") or {},
                },
            )
        )
        if money > 0:
            effects.append(Effect("mint", {"account": acc, "amount": money, "reason": "genesis_endowment"}))
        for item in npc["assets"].get("items") or []:
            if item["qty"] > 0:
                effects.append(
                    Effect("item_create", {"owner": nid, "item": item["id"], "qty": item["qty"], "reason": "genesis_carry"})
                )

    # 4b) 就业 / 初始目标 / 师徒字段
    for npc in npcs:
        nid = npc["id"]
        work_loc = npc_work[nid]
        bid = _pick_business_temp(business_ids.get(work_loc) or [], nid)
        if bid:
            effects.append(Effect("set_employment", {"entity": nid, "business": bid}))
        else:
            mismatches.append(f"npc_without_workplace:{nid}")
        goals = sorted(npc.get("goals") or [], key=lambda g: (float(g.get("priority", 9)), str(g.get("id", ""))))
        if goals:
            g = goals[0]
            kind = goal_kind(g["type"])
            if g["type"] not in ("earn_money", "advance_skill", "build_relation", "build_shop", "gain_status", "join_sect", "found_sect"):
                mismatches.append(f"unknown_goal_type:{nid}:{g['type']}")
            effects.append(
                Effect(
                    "goal_create",
                    {
                        "id": f"{nid}:{g['id']}",
                        "npc": nid,
                        "kind": kind,
                        "target": goal_target(content, kind),
                        "created_tick": 0,
                        "source": "npc",
                        "content_goal_id": g["id"],
                        "content_type": g["type"],
                    },
                )
            )
        if nid in disciples:
            for d in sorted(set(disciples[nid])):
                effects.append(Effect("add_disciple", {"master": nid, "disciple": d}))
        if nid in master_of:
            effects.append(Effect("set_field", {"entity": nid, "field": "master", "value": master_of[nid]}))

    # 5) 初始关系（有向；保留 amount/long_term）
    for npc in npcs:
        nid = npc["id"]
        for rel in npc.get("relations") or []:
            target = rel["target"]
            if target not in npc_by_id:
                mismatches.append(f"relation_target_missing:{nid}->{target}")
                continue
            data = {
                "a": nid,
                "b": target,
                "kind": rel["type"],
                "affection": float(rel["value"]),
                "trust": 40.0,
                "status": "active",
            }
            if "amount" in rel and rel["type"] in ("creditor", "debtor"):
                data["debt"] = int(rel["amount"])
            effects.append(Effect("relation_upsert", data))

    # 6) 初始债务账本（借出/借入只记一条 canonical Debt；审计展开为应收/应付两条）
    pairs: dict[tuple[str, str], dict[str, Any]] = {}
    for npc in npcs:
        for rel in npc.get("relations") or []:
            if rel["type"] not in ("creditor", "debtor"):
                continue
            target = rel["target"]
            if target not in npc_by_id:
                continue
            creditor, debtor = (npc["id"], target) if rel["type"] == "creditor" else (target, npc["id"])
            amount = rel.get("amount")
            row = pairs.setdefault((creditor, debtor), {"amounts": set(), "long_term": False, "declared_by": set()})
            if amount is None:
                mismatches.append(f"debt_amount_missing:{npc['id']}->{target}:{rel['type']}")
            else:
                row["amounts"].add(int(amount))
            row["long_term"] = bool(row["long_term"] or rel.get("long_term"))
            row["declared_by"].add(npc["id"])
    debt_total = 0
    long_term_total = 0
    for (creditor, debtor), row in sorted(pairs.items()):
        amounts = sorted(row["amounts"])
        if not amounts:
            continue
        if len(amounts) > 1:
            mismatches.append(f"debt_amount_mismatch:{creditor}->{debtor}:{amounts}")
        amount = max(amounts)
        if amount <= 0:
            continue
        did = f"debt:init:{creditor}:{debtor}"
        effects.append(
            Effect(
                "debt_create",
                {
                    "id": did,
                    "creditor": creditor,
                    "debtor": debtor,
                    "amount": amount,
                    "long_term": row["long_term"],
                    "created_tick": 0,
                    "note": "initial_debt",
                },
            )
        )
        debt_total += 1
        if row["long_term"]:
            long_term_total += 1

    declared_total = int(supply.get("initial_npc_total", npc_money_total))
    if declared_total != npc_money_total:
        mismatches.append(f"money_supply_npc_total:{declared_total}!={npc_money_total}")
    if int(supply.get("initial_location_float_total", float_total)) != float_total:
        mismatches.append(f"money_supply_float_total:{supply.get('initial_location_float_total')}!={float_total}")

    return GenesisResult(
        effects=effects,
        mismatches=mismatches,
        initial_npc_money=npc_money_total,
        initial_location_float=float_total,
        debts=debt_total,
        long_term_debts=long_term_total,
    )


def content_role_skill(content: Content, role: str) -> str:
    table = content.economy.get("skill_by_role") or {}
    return str(table.get(role, role))


def _pick_business_temp(ids: list[str], key: str) -> str | None:
    if not ids:
        return None
    return sorted(ids)[_stable_index(key, len(ids))]
