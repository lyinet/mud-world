"""汇总审计结果与内容包对账。"""
from __future__ import annotations

from ..actions.common import goal_kind
from ..core.content import Content
from ..world.state import WorldState


NPC_VERBS = {
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
}

# P2-B：由引擎意图层/插件提供、不要求内容包声明的动词。
ENGINE_ONLY_ALLOWED = {
    "quest_accept", "quest_deliver", "recruit", "debt_settle",
    # P3-C 玩家最小切片：由 UI/会话层直接 propose，不属于 NPC 决策动词。
    "player_enter", "player_presence", "player_leave", "player_endowment",
    # 玩家婚姻闭环：由会话层/服务端事件链驱动，不在内容包 verbs 中声明。
    "marriage_propose", "marriage_accept", "marriage_reject", "marriage", "marriage_end",
    "spouse_time",
    # 人物四面板（78876e5）：玩家 UI 直驱的装备/物品动作，不参与 NPC 决策，
    # 无需内容包声明 allowed_roles；改代码不改 content_hash，避免触发内容哈希迁移。
    "equip", "unequip", "use_item",
}


def content_audit(content: Content, registry, genesis_mismatches: list[str] | None = None) -> dict:
    issues: list[str] = list(genesis_mismatches or [])
    npc_roles = {n["role"] for n in content.npcs}
    wages = content.economy.get("wages_daily") or {}
    living = (content.economy.get("living_costs") or {}).get("by_role") or {}
    for role in sorted(npc_roles):
        if role not in wages:
            issues.append(f"role_missing_wage:{role}")
        if role not in living:
            issues.append(f"role_missing_living_cost:{role}")

    content_verbs = set(content.verbs.keys())
    # P2：插件 verb / 意图层内核 verb 也是引擎可执行动词，但不必由内容包声明；
    # system_* 同样属于内核内部动作。其余未声明的引擎动词仍然算内容/代码不一致。
    engine_verbs = {v for v in registry.verbs() if not v.startswith("system_")}
    unknown_engine = engine_verbs - content_verbs - ENGINE_ONLY_ALLOWED
    if content_verbs - engine_verbs or unknown_engine:
        issues.append(
            "verbs_mismatch:content_only="
            + ",".join(sorted(content_verbs - engine_verbs))
            + ";engine_only="
            + ",".join(sorted(unknown_engine))
        )
    for verb, meta in sorted(content.verbs.items()):
        for role in (meta.get("allowed_roles") or []):
            if role != "*" and role not in npc_roles:
                issues.append(f"verb_allowed_role_unknown:{verb}:{role}")

    for lid, loc in sorted(content.locations.items()):
        for service in loc.get("services") or []:
            if service not in content.items:
                issues.append(f"location_service_missing_item:{lid}:{service}")

    goal_types = set()
    goal_kind_counts: dict[str, int] = {}
    debt_rels = 0
    debt_amounts = 0
    long_term = 0
    homes_declared = 0
    for npc in content.npcs:
        for g in npc.get("goals") or []:
            goal_types.add(g["type"])
            kind = goal_kind(g["type"])
            goal_kind_counts[kind] = goal_kind_counts.get(kind, 0) + 1
        if npc.get("home") in content.locations:
            homes_declared += 1
        for rel in npc.get("relations") or []:
            if rel["type"] in ("creditor", "debtor"):
                debt_rels += 1
                if "amount" in rel:
                    debt_amounts += 1
                if rel.get("long_term"):
                    long_term += 1

    goal_targets = content.economy.get("goal_targets") or {}
    defaults_used = {
        "home": {
            "content_declared": homes_declared,
            "content_total": len(content.npcs),
            "defaulted": len(content.npcs) - homes_declared,
            "fallback": "工作地点相邻地点 + id 哈希（确定性）",
        },
        "goal_targets": {
            "declared_kinds": sorted(goal_targets),
            "used_kinds": sorted(goal_kind_counts),
            "defaulted_kinds": sorted(k for k in goal_kind_counts if k not in goal_targets),
            "defaulted_goals": sum(v for k, v in goal_kind_counts.items() if k not in goal_targets),
            "fallback": "goal_kind 对应的内置默认 target",
        },
    }
    if homes_declared < len(content.npcs):
        issues.append(f"content_missing_home:{len(content.npcs) - homes_declared}/{len(content.npcs)}_use_default")
    if defaults_used["goal_targets"]["defaulted_kinds"]:
        issues.append(
            "economy_missing_goal_targets:"
            + ",".join(defaults_used["goal_targets"]["defaulted_kinds"])
            + "_use_default"
        )
    if debt_rels and debt_amounts < debt_rels:
        issues.append(f"debt_relations_missing_amount:{debt_rels - debt_amounts}/{debt_rels}")
    if not (content.economy.get("money_supply") or {}).get("sources"):
        pass
    else:
        issues.append("economy_declares_external_sources")

    return {
        "issues": issues,
        "npc_roles": sorted(npc_roles),
        "goal_types": sorted(goal_types),
        "content_verbs": sorted(content_verbs),
        "engine_npc_verbs": sorted(engine_verbs),
        "debt_relations": debt_rels,
        "debt_relations_with_amount": debt_amounts,
        "debt_relations_long_term": long_term,
        "defaults_used": defaults_used,
        "cooldown_field_present": any(
            "cooldown" in (meta or {}) for meta in content.verbs.values()
        ),
        "content_hash": content.content_hash,
        "sources": content.economy.get("money_supply", {}).get("sources", []),
    }


def cashflow_stats(events, npc_ids: set[str], days: int, state: WorldState | None = None) -> dict:
    """从事件流统计 NPC 钱包的现金流入/流出。

    - operating：经营现金流（工资、消费、生活、税、交易），排除开店铺/建门派等资本开支。
    - total：钱包余额变动（含资本开支与一次性费用）。
    只统计 wallet:<npc> 账户，不把商业体/门派金库算作 NPC 收入。

    debt_settle 是 Effect 内部的资金转移（不是 transfer op），必须在这里补记，
    否则“还债”会变成钱包余额变动却不在现金流里，造成对账缺口。
    """
    inflow: dict[str, int] = {}
    outflow: dict[str, int] = {}
    capital_out: dict[str, int] = {}
    capital_in: dict[str, int] = {}
    for e in events:
        if e.outcome != "ok" or e.verb == "genesis":
            continue
        for eff in e.effects:
            op = eff.get("op")
            data = eff.get("data") or {}
            if op == "transfer":
                src, dst, amount = data.get("src"), data.get("dst"), int(data.get("amount", 0))
                reason = str(data.get("reason", "transfer"))
                if isinstance(dst, str) and dst.startswith("wallet:") and dst[7:] in npc_ids:
                    bucket = capital_in if reason in ("shop_rent", "sect_registration_refund") else inflow
                    bucket[reason] = bucket.get(reason, 0) + amount
                if isinstance(src, str) and src.startswith("wallet:") and src[7:] in npc_ids:
                    bucket = capital_out if reason in ("shop_rent", "sect_registration") else outflow
                    bucket[reason] = bucket.get(reason, 0) + amount
            elif op == "burn":
                acc = data.get("account")
                amount = int(data.get("amount", 0))
                reason = str(data.get("reason", "burn"))
                if isinstance(acc, str) and acc.startswith("wallet:") and acc[7:] in npc_ids:
                    bucket = capital_out if reason in ("shop_rent", "sect_registration") else outflow
                    bucket[reason] = bucket.get(reason, 0) + amount
            elif op == "mint":
                acc = data.get("account")
                amount = int(data.get("amount", 0))
                if isinstance(acc, str) and acc.startswith("wallet:") and acc[7:] in npc_ids:
                    inflow["genesis_endowment"] = inflow.get("genesis_endowment", 0) + amount
            elif op == "debt_settle" and state is not None:
                debt = state.debts.get(str(data.get("id", "")))
                amount = int(data.get("amount", 0))
                if debt is None or amount <= 0:
                    continue
                reason = f"debt_settle:{debt.note}"
                if debt.creditor in npc_ids:
                    inflow[reason] = inflow.get(reason, 0) + amount
                if debt.debtor in npc_ids:
                    outflow[reason] = outflow.get(reason, 0) + amount
    gross_in = sum(inflow.values())
    gross_out = sum(outflow.values())
    cap_out = sum(capital_out.values())
    return {
        "inflow_by_reason": dict(sorted(inflow.items())),
        "outflow_by_reason": dict(sorted(outflow.items())),
        "capital_out_by_reason": dict(sorted(capital_out.items())),
        "inflow_total": gross_in,
        "outflow_total": gross_out,
        "capital_out_total": cap_out,
        "operating_net": gross_in - gross_out,
        "operating_net_per_npc_day": (gross_in - gross_out) / max(1, days * len(npc_ids)),
        "total_net": gross_in - gross_out - cap_out,
    }


def economy_stats(
    state: WorldState,
    content: Content,
    initial_npc_money: int,
    days: int,
    npc_count: int,
) -> dict:
    npc_wallets = {
        eid: state.account_balance(f"wallet:{eid}")
        for eid in sorted(state.entities)
    }
    npc_total = sum(npc_wallets.values())
    delta = npc_total - initial_npc_money
    per_npc_day = delta / max(1, npc_count * days)
    biz_total = sum(
        state.account_balance(acc)
        for acc in sorted({b.cash_account for b in state.businesses.values()})
    )
    shop_opening = content.economy.get("shop_opening") or {}
    business_book_value = sum(
        int(shop_opening.get(b.kind, 0))
        for b in state.businesses.values()
        if b.owner is not None
    )
    supply = content.economy.get("money_supply") or {}
    closure = content.economy.get("closure_model") or {}
    ideal = []
    for eid in sorted(state.entities):
        role = state.entities[eid].role
        cost = sum(int(v) for v in ((content.economy.get("living_costs") or {}).get("by_role") or {}).get(role, {}).values())
        ideal.append(content.wage_for(role) * (1 - float((content.economy.get("taxes") or {}).get("income", 0))) - cost)
    ideal_avg = sum(ideal) / len(ideal) if ideal else 0.0
    ious = [d for d in state.debts.values() if d.status == "active" and d.note == "unpaid_wage"]
    living_debts = [d for d in state.debts.values() if d.status == "active" and d.note.startswith("living_")]
    initial_debts = [d for d in state.debts.values() if d.status == "active" and d.note == "initial_debt"]
    return {
        "initial_npc_money": initial_npc_money,
        "initial_location_float": int(supply.get("initial_location_float_total", 0)),
        "declared_total_supply": int(supply.get("initial_npc_total", 0)) + int(supply.get("initial_location_float_total", 0)),
        "npc_money_now": npc_total,
        "npc_net_change": delta,
        "npc_net_per_day": per_npc_day,
        "business_book_value": business_book_value,
        "npc_net_worth_change": delta + business_book_value,
        "npc_net_worth_per_day": (delta + business_book_value) / max(1, npc_count * days),
        "business_cash_now": biz_total,
        "minted_total": state.minted_total,
        "burned_total": state.burned_total,
        "supply_now": sum(state.accounts.values()),
        "ideal_net_range": closure.get("expected_net_range"),
        "ideal_net_per_npc_day": ideal_avg,
        "ious_count": len(ious),
        "ious_total": sum(d.amount for d in ious),
        "living_debts_count": len(living_debts),
        "living_debts_total": sum(d.amount for d in living_debts),
        "initial_debts_count": len(initial_debts),
        "initial_debts_total": sum(d.amount for d in initial_debts),
        "npc_wallets": npc_wallets,
    }
