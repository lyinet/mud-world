#!/usr/bin/env python3
"""A4 战略层审计：PCR / PIR / LHGA / CDC / CEG / PEG / K1。

口径见 specs/a4-strategist/tasks.md：
- PCR：合法计划条目在到期前完成的比例（另给剔除一次性目标的口径）；
- PIR：曾被规则层真正执行的计划条目中，在完成/到期前被替换、放弃或迁出的比例；
- LHGA：每 NPC 每 10 游戏日完成目标数；
- CDC：相邻两天「成功、非 system」动词分布余弦相似度均值；
- CEG：真实战略层成本 / (本 arm 完成目标数 - R 臂完成目标数)；
- PEG：合法计划条目中，规则层从未安排过动作的比例。

只读 runs/<id> 下文件，不写世界状态。
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ONE_SHOT_TYPES = {"join_sect", "found_sect", "build_shop"}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _summary(run: Path) -> dict[str, Any]:
    path = run / "summary.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _goal_timeline(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """从事件流重建目标生命周期：创建/进度/放弃/完成 tick。"""
    goals: dict[str, dict[str, Any]] = {}
    for ev in events:
        tick = int(ev.get("tick", 0))
        args = ev.get("args") or {}
        for eff in ev.get("effects") or []:
            op = eff.get("op")
            data = eff.get("data") or {}
            if op == "goal_create":
                gid = str(data.get("id") or "")
                if not gid:
                    continue
                goals.setdefault(gid, {
                    "npc": str(data.get("npc") or ev.get("actor") or ""),
                    "target": float(data.get("target") or 0.0),
                    "created_tick": int(data.get("created_tick", tick) or tick),
                    "progress": 0.0,
                    "progress_tick": None,
                    "abandoned_tick": None,
                    "abandon_by_type": "",
                    "content_type": str(data.get("content_type") or ""),
                })
            elif op == "goal_progress":
                gid = str(data.get("id") or "")
                if gid and gid in goals:
                    goals[gid]["progress"] += float(data.get("delta") or 0.0)
                    if goals[gid]["progress_tick"] is None:
                        goals[gid]["progress_tick"] = tick
            elif op == "goal_abandon":
                gid = str(data.get("id") or "")
                if gid and gid in goals:
                    goals[gid]["abandoned_tick"] = tick
                    new_type = str(args.get("content_type") or "")
                    if new_type:
                        goals[gid]["abandon_by_type"] = new_type
    for gid, g in goals.items():
        target = float(g.get("target") or 0.0)
        if target > 0 and g["progress"] >= target * 0.999:
            g["completed_tick"] = g["progress_tick"]
        else:
            g["completed_tick"] = None
    return goals


def _plans(strategy_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_npc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in strategy_rows:
        if row.get("type") == "plan":
            by_npc[str(row.get("npc") or "")].append(row)
    for rows in by_npc.values():
        rows.sort(key=lambda r: int(r.get("created_tick", 0)))
    return by_npc


def _consumptions(strategy_rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in strategy_rows:
        if row.get("type") == "consumption":
            out[(str(row.get("plan_id")), str(row.get("goal_id")))] = row
    return out


def _deviations(strategy_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in strategy_rows if r.get("type") == "deviation"]


def plan_metrics(run: Path) -> dict[str, Any]:
    events = _load_jsonl(run / "events.jsonl")
    strategy_rows = _load_jsonl(run / "strategy.jsonl")
    goals = _goal_timeline(events)
    plans_by_npc = _plans(strategy_rows)
    cons = _consumptions(strategy_rows)
    devs = _deviations(strategy_rows)
    run_end = int(_summary(run).get("days", 0)) * 1440

    # 同一 NPC 重复出现的同一目标只计一次（spec M1 的分母口径）。
    entries: dict[tuple[str, str], dict[str, Any]] = {}
    for npc, plans in plans_by_npc.items():
        for idx, plan in enumerate(plans):
            if not plan.get("valid"):
                continue
            next_created = (
                int(plans[idx + 1]["created_tick"]) if idx + 1 < len(plans) else None
            )
            created = int(plan.get("created_tick", 0))
            expires = int(plan.get("expires_tick", created + 3 * 1440))
            for entry in plan.get("entries") or []:
                gid = str(entry.get("goal_id") or "")
                key = (npc, gid)
                row = entries.get(key)
                if row is None:
                    row = {
                        "npc": npc,
                        "goal_id": gid,
                        "content_type": str(entry.get("content_type") or ""),
                        "first_created": created,
                        "last_expires": expires,
                        "plan_ids": [],
                        "next_created": next_created,
                        "consumed": False,
                    }
                    entries[key] = row
                else:
                    row["last_expires"] = max(int(row["last_expires"]), expires)
                    if next_created is not None:
                        row["next_created"] = max(int(row.get("next_created") or 0), next_created)
                row["plan_ids"].append(str(plan.get("plan_id") or ""))
                if (plan.get("plan_id"), gid) in cons and cons[(plan.get("plan_id"), gid)].get("scheduled"):
                    row["consumed"] = True

    entries_total = len(entries)
    entries_one_shot = sum(1 for e in entries.values() if e["content_type"] in ONE_SHOT_TYPES)
    completed = 0
    completed_one_shot = 0
    consumed_total = 0
    consumed_completed = 0
    interrupted = 0
    pending = 0
    expired_unfinished = 0
    peg_miss = 0
    causes: Counter[str] = Counter()
    samples: list[dict[str, Any]] = []

    for key in sorted(entries):
        row = entries[key]
        gid = row["goal_id"]
        ctype = row["content_type"]
        goal = goals.get(gid) or {}
        done_tick = goal.get("completed_tick")
        abandoned_tick = goal.get("abandoned_tick")
        first_created = int(row["first_created"])
        last_expires = int(row["last_expires"])
        if row["consumed"]:
            consumed_total += 1
        else:
            peg_miss += 1
        if done_tick is not None and first_created <= int(done_tick) <= last_expires:
            completed += 1
            if ctype in ONE_SHOT_TYPES:
                completed_one_shot += 1
            if row["consumed"]:
                consumed_completed += 1
            continue
        interrupted_here = False
        cause = ""
        if abandoned_tick is not None and first_created <= int(abandoned_tick) <= last_expires:
            interrupted_here = True
            new_type = str(goal.get("abandon_by_type") or "")
            cause = "urgent_rule" if new_type.startswith("urgent_") else "rule_other"
        if interrupted_here and row["consumed"]:
            interrupted += 1
            causes[cause or "unknown"] += 1
            if len(samples) < 12:
                samples.append({
                    "npc": row["npc"],
                    "plan_ids": row["plan_ids"],
                    "goal_id": gid,
                    "content_type": ctype,
                    "cause": cause,
                })
        elif row["consumed"] and last_expires <= run_end and done_tick is None:
            expired_unfinished += 1
        elif last_expires > run_end:
            pending += 1

    # 规则层短暂迁出计划的偏差（没有 abandon 事件）按前置不可行/回退分类。
    for dev in devs:
        reason = str(dev.get("reason") or "")
        if reason.startswith("infeasible") or reason.startswith("fallback"):
            causes["prereq_infeasible_or_fallback"] = causes.get("prereq_infeasible_or_fallback", 0) + 1

    return {
        "entries_total": entries_total,
        "entries_one_shot": entries_one_shot,
        "completed": completed,
        "completed_one_shot": completed_one_shot,
        "pcr": completed / entries_total if entries_total else 0.0,
        "pcr_ex_one_shot": (
            (completed - completed_one_shot) / (entries_total - entries_one_shot)
            if entries_total - entries_one_shot > 0 else 0.0
        ),
        "consumed_total": consumed_total,
        "consumed_completed": consumed_completed,
        "interrupted": interrupted,
        "pir": interrupted / entries_total if entries_total else 0.0,
        "pir_denominator": "all_plan_entries_unique",
        "executed_entries": consumed_total,
        "pir_causes": dict(sorted(causes.items())),
        "interruption_samples": samples,
        "peg": peg_miss / entries_total if entries_total else 0.0,
        "peg_entries": peg_miss,
        "expired_unfinished": expired_unfinished,
        "pending_at_run_end": pending,
        "plans_valid": sum(1 for rows in plans_by_npc.values() for p in rows if p.get("valid")),
        "plans_invalid": sum(1 for rows in plans_by_npc.values() for p in rows if not p.get("valid")),
        "plans_total": sum(len(rows) for rows in plans_by_npc.values()),
    }


def lhga(run: Path) -> dict[str, Any]:
    summary = _summary(run)
    days = max(1, int(summary.get("days", 0) or 0))
    npcs = max(1, int(summary.get("npc_count", 0) or 0))
    completed = int((summary.get("k1") or {}).get("goals_completed", 0) or 0)
    per_10d = completed / npcs * (10.0 / days) if npcs else 0.0
    return {"completed": completed, "npcs": npcs, "days": days, "lhga_per_npc_10d": per_10d}


def cdc(run: Path) -> dict[str, Any]:
    events = _load_jsonl(run / "events.jsonl")
    by_day_npc: dict[tuple[int, str], Counter[str]] = defaultdict(Counter)
    for ev in events:
        if ev.get("outcome") != "ok" or ev.get("source") == "system":
            continue
        verb = str(ev.get("verb") or "")
        if not verb or verb.startswith("system_"):
            continue
        by_day_npc[(int(ev.get("day", 0)), str(ev.get("actor") or ""))][verb] += 1
    sims: list[float] = []
    days = sorted({d for d, _ in by_day_npc})
    npcs = sorted({n for _, n in by_day_npc})
    for npc in npcs:
        for a, b in zip(days, days[1:]):
            va = by_day_npc.get((a, npc))
            vb = by_day_npc.get((b, npc))
            if not va or not vb:
                continue
            common = set(va) & set(vb)
            dot = sum(va[k] * vb[k] for k in common)
            na = math.sqrt(sum(v * v for v in va.values()))
            nb = math.sqrt(sum(v * v for v in vb.values()))
            if na and nb:
                sims.append(dot / (na * nb))
    return {
        "cdc": sum(sims) / len(sims) if sims else 0.0,
        "pairs": len(sims),
        "npcs": len(npcs),
        "days": len(days),
    }


def cost_stats(run: Path) -> dict[str, Any]:
    rows = _load_jsonl(run / "cost.jsonl")
    strat = [r for r in rows if r.get("slot") == "strategy"]
    total = sum(float(r.get("estimated_cost", 0.0) or 0.0) for r in strat)
    tokens_in = sum(int(r.get("input_tokens", 0) or 0) for r in strat)
    tokens_out = sum(int(r.get("output_tokens", 0) or 0) for r in strat)
    calls = sum(int(r.get("calls", 0) or 0) for r in strat)
    days = max(1, int(_summary(run).get("days", 0) or 0))
    return {
        "strategy_cost": total,
        "strategy_calls": calls,
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "cost_per_call": total / calls if calls else 0.0,
        "extrapolated_10d": total * (10.0 / days),
        "extrapolated_72d": total * (72.0 / days),
    }


def ceg(run: Path, baseline: Path) -> dict[str, Any]:
    summary = _summary(run)
    base = _summary(baseline)
    new_completed = int((summary.get("k1") or {}).get("goals_completed", 0) or 0)
    base_completed = int((base.get("k1") or {}).get("goals_completed", 0) or 0)
    cost = cost_stats(run)["strategy_cost"]
    new_goals = new_completed - base_completed
    days = max(1, int(summary.get("days", 0) or 0))
    return {
        "completed_new": new_completed,
        "completed_baseline": base_completed,
        "new_goals": new_goals,
        "cost": cost,
        "ceg": cost / new_goals if new_goals > 0 else None,
        "new_goals_10d": new_goals * (10.0 / days),
    }


def k1_guard(run: Path, baseline: Path) -> dict[str, Any]:
    summary = _summary(run)
    base = _summary(baseline)
    k1 = float((summary.get("k1") or {}).get("k1_rate", 0.0) or 0.0)
    base_k1 = float((base.get("k1") or {}).get("k1_rate", 0.0) or 0.0)
    return {"k1": k1, "baseline_k1": base_k1, "floor": base_k1 - 0.02, "pass": k1 >= base_k1 - 0.02}


def audit(run: Path, baseline: Path) -> dict[str, Any]:
    return {
        "run": str(run),
        "baseline": str(baseline),
        "plan": plan_metrics(run),
        "lhga": lhga(run),
        "cdc": cdc(run),
        "cost": cost_stats(run),
        "ceg": ceg(run, baseline),
        "k1": k1_guard(run, baseline),
        "summary_k1": (_summary(run).get("k1") or {}).get("k1_rate"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run", help="runs/<run_id>")
    ap.add_argument("--baseline", default="runs/a3-rule-10d-31npc-frozen")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    result = audit(Path(args.run), Path(args.baseline))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        p = result["plan"]
        print(f"run={args.run}")
        print(f"PCR={p['pcr']:.2%} (ex_one_shot={p['pcr_ex_one_shot']:.2%}) entries={p['entries_total']} "
              f"completed={p['completed']}")
        print(f"PIR={p['pir']:.2%} interrupted={p['interrupted']}/{p['consumed_total']} causes={p['pir_causes']}")
        print(f"PEG={p['peg']:.2%} ({p['peg_entries']} 条从未被规则层安排)")
        print(f"LHGA={result['lhga']['lhga_per_npc_10d']:.3f} /NPC/10d")
        print(f"CDC={result['cdc']['cdc']:.4f} pairs={result['cdc']['pairs']}")
        print(f"K1={result['k1']['k1']:.2%} floor={result['k1']['floor']:.2%} pass={result['k1']['pass']}")
        print(f"cost=${result['cost']['strategy_cost']:.4f} calls={result['cost']['strategy_calls']}")
        print(f"CEG={result['ceg']['ceg']} new_goals={result['ceg']['new_goals']}")
        print(f"plans valid={p['plans_valid']} invalid={p['plans_invalid']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
