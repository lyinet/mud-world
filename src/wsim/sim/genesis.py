"""创世：把内容包投影成事件。

只在 genesis 铸币：NPC 初始资产 + 地点金库 float（内容包 money_supply 声明的总量）。
之后所有资金流动只能是 transfer / burn，禁止日常增发。
"""
from __future__ import annotations

import copy
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..actions.common import goal_kind, goal_target, organization_at
from ..actions.equipment import item_apply, slot_for_item
from ..core.content import Content
from ..core.models import Effect
from ..world.state import WorldState
from .vitals import resolve_npc_vitals


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
    seed: int = 0,
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
        # 有发起人的门派不在创世时预置：必须由发起人用 found_sect 真实创立，
        # 否则 P2-B 的“三门派可被发起”会变成同名重复社团。
        if sect.get("founder_candidate"):
            continue
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
    # 额外实例复制规范实体已算好的静态数据；不按实例 id 重算（原版每个 clone
    # 都跑同一模板的 create()，属性与规范实例相同）。
    npc_entity_data: dict[str, dict[str, Any]] = {}
    npc_money: dict[str, int] = {}
    npc_items: dict[str, list[dict[str, Any]]] = {}
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

        entity_data: dict[str, Any] = {
            "id": nid,
            "name": npc["name"],
            "role": npc["role"],
            "location": home,
            "home": home,
            "stamina": 100.0,
            "skills": {content_role_skill(content, npc["role"]): skill},
            "personality": npc.get("personality") or {},
        }
        # 带内容战斗数据的 NPC（xkx 切片）落 max_hp/attrs/skills；
        # legacy 内容没有 vitals/attrs/skills 键，解析结果为 None，事件逐字节不变。
        npc_vitals = resolve_npc_vitals(npc, seed=seed, errors=mismatches)
        if npc_vitals is not None:
            entity_data.update(npc_vitals)
        # P4a：内容包显式标注的参考快照初始状态（如采集时被打晕的毒蛇/大狼狗）。
        # 不引入随机、不碰调度器；没有该键的 NPC（含旧九地点）事件不变。
        initial_state = str(npc.get("initial_state") or "")
        if initial_state and initial_state != "alive":
            entity_data["status"] = initial_state

        effects.append(Effect("create_account", {"account": acc}))
        effects.append(Effect("create_entity", entity_data))
        if money > 0:
            effects.append(Effect("mint", {"account": acc, "amount": money, "reason": "genesis_endowment"}))
        for item in npc["assets"].get("items") or []:
            if item["qty"] > 0:
                effects.append(
                    Effect("item_create", {"owner": nid, "item": item["id"], "qty": item["qty"], "reason": "genesis_carry"})
                )
        if npc_vitals is not None:
            # xkx 内容包的装备：作为“随身装备”挂在实体上（不进背包，避免被交易/
            # 拾取逻辑当成普通携带物），死亡时由战斗动作转成地面物品。
            # 只按内容 `equipment` 原样记录，不补造任何“掉落表”。
            equipment: list[str] = []
            # 穿戴快照：与玩家 `wear/wield` 的落地字段同源，创世即生效。
            # `equip_slot` 决定护具槽位；`slot == "wield"`/兵器类统一进 weapon 槽。
            equipped_slots: dict[str, str] = {}
            weapon_type = ""
            for row in npc.get("equipment") or []:
                ref = str((row or {}).get("ref") or "")
                if not ref:
                    continue
                equipment.append(ref)
                # 物品表缺这条装备时如实记录诊断，不编造重量/价格；落地时
                # loot_take 仍按物品表计算，查不到就是 0，不在这里补值。
                if ref not in content.items:
                    mismatches.append(f"unknown_equipment_item:{nid}:{ref}")
                    continue
                data = content.items.get(ref) or {}
                category = str(data.get("category") or "")
                equip_slot = str(data.get("equip_slot") or "")
                if str((row or {}).get("slot") or "") == "wield" or category == "weapon":
                    equipped_slots["weapon"] = ref
                    weapon_type = equip_slot
                else:
                    slot = slot_for_item(ref, category, equip_slot)
                    if slot:
                        equipped_slots[slot] = ref
            if equipment:
                entity_data["equipment"] = equipment
            if equipped_slots:
                entity_data["equipped"] = dict(sorted(equipped_slots.items()))
                equipped_apply: dict[str, int] = {}
                for item in equipped_slots.values():
                    for key, value in item_apply(content, item).items():
                        equipped_apply[key] = equipped_apply.get(key, 0) + int(value)
                if equipped_apply:
                    entity_data["equipped_apply"] = dict(sorted(equipped_apply.items()))
                if weapon_type:
                    entity_data["weapon_type"] = weapon_type
            # xkx 的 `set_temp("apply/...")`：只取 known=true 且 value 为整数的项，
            # 作为临时攻防落地。known=false（armor/is_riding/...）与无法求值的
            # 表达式一律不进数值，写诊断，不静默丢、不猜值。
            temp_apply: dict[str, int] = {}
            for row in npc.get("temp_apply") or []:
                if not isinstance(row, dict):
                    continue
                key = str(row.get("key") or "")
                if not key:
                    continue
                if row.get("known") is not True:
                    src = row.get("source") or {}
                    mismatches.append(
                        f"unknown_temp_apply:{nid}:{key}:{src.get('path', '')}:{src.get('line', '')}"
                    )
                    continue
                if row.get("source_kind") == "lpc_set":
                    # 原版 combatd 只读 query_temp("apply/…")；set("apply/…") 写的是
                    # 永久属性，未证实进入战斗。内容包保留原值供对账，但不落地为加成。
                    continue
                value = row.get("value")
                if isinstance(value, bool) or not isinstance(value, int):
                    mismatches.append(f"unresolved_temp_apply:{nid}:{key}")
                    continue
                temp_apply[key] = int(value)
            if temp_apply:
                entity_data["temp_apply"] = dict(sorted(temp_apply.items()))
        npc_entity_data[nid] = entity_data
        npc_money[nid] = money
        npc_items[nid] = list(npc["assets"].get("items") or [])

    # 4a1) 同模板多实例：原版房间 reset() 按 count 逐个 new()，每个 clone 是
    # 独立对象，属性与模板一致、位置就是本房间。这里按模板排序后追加事件，
    # 顺序固定；不调用 random，事件数不变（仍在同一个 genesis 事件里）。
    # legacy 内容 npc_instances 为空，本段零效果。
    for inst in getattr(content, "npc_instances", []) or []:
        ref = str(inst.get("template") or "")
        iid = str(inst.get("id") or "")
        room = str(inst.get("room") or "")
        base = npc_entity_data.get(ref)
        if base is None or room not in content.locations:
            mismatches.append(f"clone_without_canonical:{iid}")
            continue
        data = copy.deepcopy(base)
        data["id"] = iid
        data["location"] = room
        data["home"] = room
        data["template"] = ref
        data["instance"] = int(inst.get("instance") or 0)
        acc = f"wallet:{iid}"
        effects.append(Effect("create_account", {"account": acc}))
        effects.append(Effect("create_entity", data))
        money = npc_money.get(ref, 0)
        if money > 0:
            npc_money_total += money
            effects.append(Effect("mint", {"account": acc, "amount": money, "reason": "genesis_endowment"}))
        for item in npc_items.get(ref) or []:
            if item["qty"] > 0:
                effects.append(
                    Effect("item_create", {"owner": iid, "item": item["id"], "qty": item["qty"], "reason": "genesis_carry"})
                )

    # 4a2) 房间地面初始物品：只认内容显式给出的整数 count。
    # `count_expr: random(...)` 这类表达式一律跳过，不在创世里引入随机。
    # legacy locations 没有 `objects` 键，本段零效果。
    for lid in loc_ids:
        rows = [
            obj
            for obj in (content.locations[lid].get("objects") or [])
            if isinstance(obj, dict)
            and str(obj.get("kind") or "") == "item"
            and str(obj.get("ref") or "")
            and not isinstance(obj.get("count"), bool)
            and isinstance(obj.get("count"), int)
            and int(obj["count"]) > 0
        ]
        for obj in sorted(rows, key=lambda r: str(r["ref"])):
            qty = int(obj["count"])
            item_rec = (content.items or {}).get(str(obj["ref"])) or {}
            amount = item_rec.get("amount")
            if isinstance(amount, int) and amount > 0:
                # 原版 set_amount() 的堆叠语义：一件物品自带 N 个。
                qty *= amount
            effects.append(
                Effect(
                    "loot_create",
                    {
                        "location": lid,
                        "item": str(obj["ref"]),
                        "qty": qty,
                        "reason": "genesis_ground",
                    },
                )
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
