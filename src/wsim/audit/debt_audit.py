"""K6 债务独立审计。

不信任 `WorldState` 的汇总，也不使用“应收合计 == 应付合计”这种同额加减的恒真校验。
从事件日志逐事件、逐账户重建资金流，再把每笔债务的金额与来源事件独立核对。
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from ..actions.common import living_cost, wallet
from ..core.content import Content
from ..core.models import Event
from ..world.state import WorldState


def _resolve(party: str, mapping: dict[str, str], accounts: set[str]) -> str | None:
    if party in mapping:
        return mapping[party]
    if party in accounts:
        return party
    if party.startswith("wallet:") or party.startswith("treasury:") or party.startswith("cash:"):
        return party
    return None


def _expected_initial_debts(content: Content) -> dict[tuple[str, str], int]:
    out: dict[tuple[str, str], int] = {}
    for npc in content.npcs:
        nid = npc["id"]
        for rel in npc.get("relations") or []:
            if "amount" not in rel:
                continue
            kind = str(rel.get("type"))
            target = str(rel.get("target"))
            if kind == "creditor":
                out[(nid, target)] = int(rel["amount"])
            elif kind == "debtor":
                out[(target, nid)] = int(rel["amount"])
    return out


def debt_audit(
    events: list[Event],
    state: WorldState,
    content: Content,
    npc_ids: set[str] | None = None,
) -> dict[str, Any]:
    balances: dict[str, int] = {}
    mapping: dict[str, str] = {}
    declared: dict[str, dict[str, Any]] = {}
    settled_by_debt: dict[str, int] = {}
    orders: dict[str, dict[str, Any]] = {}
    expected_initial = _expected_initial_debts(content)
    roles = {npc["id"]: npc["role"] for npc in content.npcs}
    cut = float((content.economy.get("escort") or {}).get("agency_cut", 0.30))

    mismatches: list[dict[str, Any]] = []
    settle_mismatches: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    negative: list[dict[str, Any]] = []
    double_entry_violations: list[dict[str, Any]] = []
    wallet_credit_without_debit: list[dict[str, Any]] = []
    mint_outside_genesis: list[dict[str, Any]] = []
    origin_checks = 0
    origin_expected_sum = 0
    settle_event_count = 0
    unvalidated_notes: Counter[str] = Counter()

    for e in events:
        if e.outcome != "ok":
            continue
        deltas: Counter[str] = Counter()
        minted_this = 0
        burned_this = 0
        created_here: list[dict[str, Any]] = []
        settled_here: list[dict[str, Any]] = []

        for eff in e.effects:
            op = eff.get("op")
            d = eff.get("data") or {}
            if op == "create_account":
                balances.setdefault(str(d["account"]), int(d.get("balance", 0)))
            elif op == "create_entity":
                entity = str(d["id"])
                mapping[entity] = wallet(entity)
                balances.setdefault(mapping[entity], 0)
            elif op == "create_business":
                bid = str(d["id"])
                account = str(d.get("cash_account", ""))
                if account:
                    mapping[bid] = account
                    balances.setdefault(account, 0)
            elif op == "create_faction":
                fid = str(d["id"])
                account = str(d.get("treasury_account", ""))
                if account:
                    mapping[fid] = account
                    balances.setdefault(account, 0)
            elif op == "mint":
                acc = str(d["account"])
                amount = int(d["amount"])
                balances[acc] = balances.get(acc, 0) + amount
                deltas[acc] += amount
                minted_this += amount
            elif op == "burn":
                acc = str(d["account"])
                amount = int(d["amount"])
                balances[acc] = balances.get(acc, 0) - amount
                deltas[acc] -= amount
                burned_this += amount
            elif op == "transfer":
                src, dst = str(d["src"]), str(d["dst"])
                amount = int(d["amount"])
                balances[src] = balances.get(src, 0) - amount
                balances[dst] = balances.get(dst, 0) + amount
                deltas[src] -= amount
                deltas[dst] += amount
            elif op == "debt_create":
                did = str(d["id"])
                declared[did] = {
                    "id": did,
                    "creditor": str(d["creditor"]),
                    "debtor": str(d["debtor"]),
                    "amount": int(d["amount"]),
                    "note": str(d.get("note", "")),
                    "long_term": bool(d.get("long_term", False)),
                    "created_tick": int(d.get("created_tick", e.tick)),
                    "created_seq": e.seq,
                    "origin_verb": e.verb,
                    "origin_actor": e.actor,
                }
                created_here.append(declared[did])
            elif op == "debt_increase":
                # 工资欠条续欠：同一债务金额累加，必须同步到 declared 才能与 state 对账。
                did = str(d["id"])
                debt = declared.get(did)
                if debt is None:
                    settle_mismatches.append({"seq": e.seq, "debt": did, "reason": "increase_unknown_debt"})
                else:
                    debt["amount"] += int(d["amount"])
            elif op == "debt_settle":
                did = str(d["id"])
                settled_here.append({"id": did, "amount": int(d["amount"]), "seq": e.seq})
            elif op == "escort_order_create":
                orders[str(d["id"])] = {"fee": int(d.get("fee", 0))}
            elif op == "escort_settle":
                pass

        # 债务结算：按债务两端账户检查实际增减，而不是相信 amount
        for st in settled_here:
            settle_event_count += 1
            debt = declared.get(st["id"])
            if debt is None:
                settle_mismatches.append({"seq": e.seq, "debt": st["id"], "reason": "unknown_debt"})
                continue
            src = _resolve(debt["debtor"], mapping, set(balances))
            dst = _resolve(debt["creditor"], mapping, set(balances))
            if src is None or dst is None:
                unresolved.append({"seq": e.seq, "debt": st["id"], "src": src, "dst": dst})
                continue
            amount = st["amount"]
            before_src = balances.get(src, 0)
            before_dst = balances.get(dst, 0)
            balances[src] = before_src - amount
            balances[dst] = before_dst + amount
            deltas[src] -= amount
            deltas[dst] += amount
            if before_src < amount:
                settle_mismatches.append(
                    {"seq": e.seq, "debt": st["id"], "reason": "debtor_insufficient", "balance": before_src, "amount": amount}
                )
            if amount <= 0:
                settle_mismatches.append({"seq": e.seq, "debt": st["id"], "reason": "non_positive"})
            remaining = declared[st["id"]]["amount"] - settled_by_debt.get(st["id"], 0)
            if amount > remaining:
                settle_mismatches.append(
                    {"seq": e.seq, "debt": st["id"], "reason": "over_settle", "amount": amount, "remaining": remaining}
                )
            settled_by_debt[st["id"]] = settled_by_debt.get(st["id"], 0) + amount

        # 来源核对：每笔债务金额由同事件或内容包独立复算
        if created_here:
            transfers = [
                (str(x.get("data", {}).get("src", "")), str(x.get("data", {}).get("dst", "")),
                 int(x.get("data", {}).get("amount", 0)), str(x.get("data", {}).get("reason", "")))
                for x in e.effects
                if x.get("op") == "transfer"
            ]
            for debt in created_here:
                note = debt["note"]
                amount = debt["amount"]
                expected: int | None = None
                if note == "initial_debt":
                    expected = expected_initial.get((debt["creditor"], debt["debtor"]))
                elif note == "unpaid_wage" and e.verb == "work":
                    hours = int((e.args or {}).get("hours", 8))
                    gross = max(1, int(round(content.wage_for(roles.get(e.actor, "")) * hours / 8.0)))
                    paid = sum(
                        amt for src, dst, amt, reason in transfers
                        if dst == wallet(e.actor) and reason == "wage"
                    )
                    expected = gross - paid
                elif note.startswith("living_") and e.verb == "system_living":
                    kind = note[len("living_"):]
                    cost = living_cost(content, roles.get(e.actor, "")).get(kind, 0)
                    paid = sum(
                        amt for src, dst, amt, reason in transfers
                        if src == wallet(e.actor) and reason == note
                    )
                    expected = cost - paid
                elif note == "escort_fee" and e.verb == "system_escort_arrive":
                    oid = debt["id"][len("debt:escort:"):] if debt["id"].startswith("debt:escort:") else ""
                    order = orders.get(oid)
                    if order is not None:
                        share = max(1, order["fee"] - int(order["fee"] * cut))
                        paid = sum(
                            amt for src, dst, amt, reason in transfers
                            if dst == wallet(debt["creditor"]) and reason == "escort_fee"
                        )
                        expected = share - paid
                else:
                    unvalidated_notes[note] += 1

                origin_checks += 1
                if expected is None:
                    mismatches.append(
                        {"seq": e.seq, "debt": debt["id"], "note": note, "reason": "origin_unavailable"}
                    )
                else:
                    origin_expected_sum += expected
                    if expected != amount:
                        mismatches.append(
                            {
                                "seq": e.seq,
                                "debt": debt["id"],
                                "note": note,
                                "expected": expected,
                                "actual": amount,
                                "reason": "origin_mismatch",
                            }
                        )

        # 逐事件复式平衡：正变动 + 销毁 == 负变动 + 发行
        positive = sum(v for v in deltas.values() if v > 0)
        negative_sum = -sum(v for v in deltas.values() if v < 0)
        if positive + burned_this != negative_sum + minted_this:
            double_entry_violations.append(
                {
                    "seq": e.seq,
                    "verb": e.verb,
                    "credits": positive,
                    "debits": negative_sum,
                    "minted": minted_this,
                    "burned": burned_this,
                }
            )
        for acc, delta in deltas.items():
            if minted_this == 0 and acc.startswith("wallet:") and delta > 0 and negative_sum < delta:
                wallet_credit_without_debit.append(
                    {"seq": e.seq, "verb": e.verb, "account": acc, "increase": delta, "total_debits": negative_sum}
                )
        if minted_this > 0 and e.verb != "genesis":
            mint_outside_genesis.append(
                {"seq": e.seq, "verb": e.verb, "amount": minted_this}
            )

        for acc, value in balances.items():
            if value < 0:
                negative.append({"seq": e.seq, "account": acc, "balance": value})

    # 与状态投影对账
    state_keys = set(state.accounts)
    rebuilt_keys = set(balances)
    projection_diff = []
    for key in sorted(state_keys | rebuilt_keys):
        if int(state.accounts.get(key, 0)) != int(balances.get(key, 0)):
            projection_diff.append(
                {"account": key, "state": int(state.accounts.get(key, 0)), "rebuilt": int(balances.get(key, 0))}
            )

    # 债务声明金额 - 已结算 == 期末状态金额
    remaining_mismatch = []
    for did, debt in declared.items():
        remaining = debt["amount"] - settled_by_debt.get(did, 0)
        st = state.debts.get(did)
        if st is None:
            remaining_mismatch.append({"debt": did, "reason": "missing_in_state", "remaining": remaining})
        elif int(st.amount) != remaining:
            remaining_mismatch.append(
                {"debt": did, "state": int(st.amount), "rebuilt": remaining, "status": st.status}
            )
    for did in state.debts:
        if did not in declared:
            remaining_mismatch.append({"debt": did, "reason": "missing_in_events"})

    declared_total = sum(d["amount"] for d in declared.values())
    settled_total = sum(settled_by_debt.values())
    active_total = declared_total - settled_total
    by_note: dict[str, dict[str, int]] = {}
    for d in declared.values():
        row = by_note.setdefault(d["note"], {"count": 0, "declared": 0, "settled": 0, "active": 0})
        row["count"] += 1
        row["declared"] += d["amount"]
        row["settled"] += settled_by_debt.get(d["id"], 0)
        row["active"] += d["amount"] - settled_by_debt.get(d["id"], 0)

    checks = {
        "reconstruction_matches_state": {"ok": not projection_diff, "mismatches": projection_diff},
        "event_double_entry": {"ok": not double_entry_violations, "violations": double_entry_violations},
        "debt_settle_credit_equals_debit": {"ok": not settle_mismatches, "mismatches": settle_mismatches},
        "wallet_credit_without_payer_debit": {
            "ok": not wallet_credit_without_debit,
            "violations": wallet_credit_without_debit,
        },
        "no_mint_outside_genesis": {"ok": not mint_outside_genesis, "violations": mint_outside_genesis},
        "debt_amount_matches_origin": {
            "ok": not mismatches,
            "checked": origin_checks,
            "expected_total": origin_expected_sum,
            "mismatches": mismatches,
        },
        "declared_total_equals_origin_total": {
            "ok": declared_total == origin_expected_sum,
            "declared": declared_total,
            "origin_expected": origin_expected_sum,
        },
        "declared_equals_remaining": {"ok": not remaining_mismatch, "mismatches": remaining_mismatch},
        "no_negative_balances": {"ok": not negative, "violations": negative},
    }
    ok = all(v["ok"] for v in checks.values()) and not unresolved

    return {
        "status": "pass" if ok else "fail",
        "debts_declared": len(declared),
        "declared_total": declared_total,
        "settled_events": settle_event_count,
        "settled_total": settled_total,
        "active_total": active_total,
        "by_note": dict(sorted(by_note.items())),
        "unvalidated_notes": dict(unvalidated_notes),
        "unresolved": unresolved,
        "checks": checks,
        "rebuilt_accounts_sum": sum(balances.values()),
        "state_accounts_sum": sum(int(v) for v in state.accounts.values()),
    }
