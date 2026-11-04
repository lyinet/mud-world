"""自提案代理指标：SGR（自主完成率）/ STC（类型覆盖）/ SGC（目标链）。

全部可从事件日志（events.jsonl）与提案日志（self_goal_proposals.jsonl）复算，
不依赖 summary，也不调用模型。

本轮新增两件事：
1. SGR 同时给 raw（分母=全部激活）与 mature（分母=截止日已过的激活），
   避免 10 天窗口把最后 3 天新目标算成“没完成”；
2. 执行归因：每个成熟目标排了几次自额度动作、其中几次真记到目标、
   进度兑现了多少，用来区分“目标定得更容易”与“执行跟上了”。

用法：
    PYTHONPATH=src .venv/bin/python tools/self_goal_metrics.py \
        --rule runs/sg-rule-10d-s42 --self runs/sg-competent-10d-s42-a \
        --prev-self runs/sg-competent-10d-s42-a --probe-types \
        --out reports/self-goal-metrics.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
TICKS_PER_DAY = 144

from wsim.sim.self_goal import SUPPORTED_TYPES  # noqa: E402

# 完成之后“下一个相关目标”的判定表：同类型，或明确的顺承关系。
SUCCESSOR = {
    "advance_skill": ("advance_skill", "earn_money", "build_shop"),
    "earn_money": ("earn_money", "build_shop", "advance_skill"),
    "build_relation": ("build_relation", "build_shop", "join_sect"),
    "build_shop": ("build_shop", "earn_money", "build_relation"),
    "join_sect": ("join_sect", "build_relation", "advance_skill"),
    "escort": ("escort", "earn_money"),
}


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _events(path: Path) -> list[dict]:
    return _rows(path / "events.jsonl")


def _is_self(goal_id: str) -> bool:
    return str(goal_id).startswith("self:")


def _end_tick(events: list[dict]) -> int:
    return max((int(ev.get("tick", 0)) for ev in events), default=0)


def _event_metrics(events: list[dict]) -> dict:
    """事件层面：独立额度动作数、进度落点、内容目标进度事件数。"""
    self_slot = 0
    self_slot_moves = 0
    self_slot_on_self = 0
    self_slot_on_content = 0
    content_progress = 0
    self_progress = 0
    content_actions = 0
    leaked_examples: list[dict] = []
    for ev in events:
        args = ev.get("args") or {}
        gid = str(args.get("_goal_id") or "")
        progresses = [
            e["data"]["id"]
            for e in (ev.get("effects") or [])
            if e.get("op") == "goal_progress" and e.get("data", {}).get("id")
        ]
        if gid:
            if ev.get("verb") == "move":
                self_slot_moves += 1
            else:
                self_slot += 1
            hit_self = [p for p in progresses if p == gid]
            hit_other = [p for p in progresses if p != gid]
            self_slot_on_self += 1 if hit_self else 0
            if hit_other:
                self_slot_on_content += 1
                if len(leaked_examples) < 5:
                    leaked_examples.append(
                        {"tick": ev.get("tick"), "actor": ev.get("actor"), "verb": ev.get("verb"),
                         "slot_goal": gid, "credited": hit_other}
                    )
        for pid in progresses:
            if _is_self(pid):
                self_progress += 1
            else:
                content_progress += 1
                if not gid:
                    content_actions += 1
    npcs = {ev.get("actor") for ev in events}
    days = max((int(ev.get("day", 0)) for ev in events), default=0) + 1
    return {
        "self_slot_events": self_slot,
        "self_slot_moves": self_slot_moves,
        "self_slot_progress_on_self": self_slot_on_self,
        "self_slot_progress_on_content": self_slot_on_content,
        "self_goal_progress_events": self_progress,
        "content_goal_progress_events": content_progress,
        "content_goal_progress_without_self_slot": content_actions,
        "content_progress_per_npc_day": round(content_progress / max(1, len(npcs) * days), 4),
        "npc_count": len(npcs),
        "days": days,
        "leak_examples": leaked_examples,
    }


def _progress_by_goal(events: list[dict]) -> dict[str, dict]:
    """每个自目标：排了几次动作、几次真记到目标、兑现进度（delta 累加，截到 1）。"""
    out: dict[str, dict] = {}
    for ev in events:
        gid = str((ev.get("args") or {}).get("_goal_id") or "")
        if not gid:
            continue
        row = out.setdefault(gid, {"scheduled": 0, "credited": 0, "delta": 0.0})
        if ev.get("verb") != "move":
            row["scheduled"] += 1
        for e in ev.get("effects") or []:
            if e.get("op") != "goal_progress":
                continue
            data = e.get("data") or {}
            if data.get("id") != gid:
                continue
            row["credited"] += 1
            row["delta"] += float(data.get("delta") or 0.0)
    for row in out.values():
        row["delta"] = min(1.0, round(row["delta"], 6))
    return out


def _percentiles(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "mean": 0.0, "median": 0.0, "min": 0.0, "max": 0.0, "p10": 0.0, "p90": 0.0}
    vals = sorted(values)
    idx = lambda p: vals[max(0, min(len(vals) - 1, int(round(p * (len(vals) - 1)))))]
    return {
        "n": len(vals),
        "mean": round(sum(vals) / len(vals), 4),
        "median": round(idx(0.5), 4),
        "min": round(vals[0], 4),
        "max": round(vals[-1], 4),
        "p10": round(idx(0.1), 4),
        "p90": round(idx(0.9), 4),
    }


def _trace_metrics(trace_path: Path, end_tick: int) -> dict:
    rows = _rows(trace_path)
    props = {
        str((r.get("proposal") or {}).get("proposal_id") or ""): r
        for r in rows
        if r["kind"] == "proposal" and r.get("accepted")
    }
    all_props = [r for r in rows if r["kind"] == "proposal"]
    acts = [r for r in rows if r["kind"] == "activation"]
    comps = [r for r in rows if r["kind"] == "completion"]
    expired = [r for r in rows if r["kind"] == "goal_expired"]
    act_ids = {r["goal_id"] for r in acts}
    comp_ids = {r["goal_id"] for r in comps}
    exp_ids = {r["goal_id"] for r in expired}

    def _day(tick: int) -> int:
        return int(tick) // TICKS_PER_DAY + 1

    # SGR raw：激活过（=真正创建了）的自目标里完成的比例。
    sgr_raw = (len(comp_ids) / len(act_ids)) if act_ids else 0.0

    # SGR mature：只看截止日已过的激活，避免右删失把最后几天的新目标算成失败。
    mature = [r for r in acts if int(r.get("deadline_tick") or 0) <= end_tick]
    mature_ids = {r["goal_id"] for r in mature}
    mature_done = len(mature_ids & comp_ids)
    mature_exp = len(mature_ids & exp_ids)
    mature_sgr = (mature_done / len(mature_ids)) if mature_ids else 0.0

    # 基线难度：校验器放行时的 target / capacity_units。
    difficulties = []
    needed = []
    for r in acts:
        prop = props.get(r["goal_id"])
        target = float((r.get("proposal") or {}).get("target") or 0.0)
        cap = float(prop.get("capacity_units") or 0.0) if prop else 0.0
        per = float(prop.get("per_action_units") or 0.0) if prop else 0.0
        if cap > 0:
            difficulties.append(target / cap)
        if per > 0:
            needed.append(max(1, int(math.ceil(target / per - 1e-9))))

    # 执行归因：成熟目标的动作排班 / 记账 / 兑现。
    per_goal = _progress_by_goal(_events(trace_path.parent))
    exec_rows = []
    for r in mature:
        row = per_goal.get(r["goal_id"], {"scheduled": 0, "credited": 0, "delta": 0.0})
        prop = props.get(r["goal_id"])
        target = float((r.get("proposal") or {}).get("target") or 0.0)
        per = float(prop.get("per_action_units") or 0.0) if prop else 0.0
        exec_rows.append(
            {
                "goal_id": r["goal_id"],
                "npc": r["npc"],
                "type": str((r.get("proposal") or {}).get("goal_type") or ""),
                "target": target,
                "actions_needed": max(1, int(math.ceil(target / per - 1e-9))) if per > 0 else 0,
                "scheduled": row["scheduled"],
                "credited": row["credited"],
                "realized": row["delta"],
                "completed": r["goal_id"] in comp_ids,
                "expired": r["goal_id"] in exp_ids,
            }
        )

    def _exec_agg(rows: list[dict]) -> dict:
        if not rows:
            return {"n": 0, "scheduled_mean": 0.0, "credited_mean": 0.0, "realized_mean": 0.0}
        return {
            "n": len(rows),
            "scheduled_mean": round(sum(r["scheduled"] for r in rows) / len(rows), 2),
            "credited_mean": round(sum(r["credited"] for r in rows) / len(rows), 2),
            "realized_mean": round(sum(r["realized"] for r in rows) / len(rows), 4),
        }

    done_rows = [r for r in exec_rows if r["completed"]]
    exp_rows = [r for r in exec_rows if r["expired"]]

    # STC：提案类型分布 + 激活类型分布 + 未覆盖类型。
    prop_types = Counter(str((r.get("proposal") or {}).get("goal_type") or "") for r in all_props)
    prop_types.pop("", None)
    type_of = {r["goal_id"]: str((r.get("proposal") or {}).get("goal_type") or "") for r in acts}
    type_dist = Counter(type_of.get(g, "") for g in act_ids)
    type_dist.pop("", None)
    uncovered = sorted(SUPPORTED_TYPES - set(type_dist))

    # SGC：完成一个自目标后 3 天内是否提出下一个自目标（以及是否相关）。
    by_npc: dict[str, list[tuple[int, str]]] = {}
    for r in acts:
        by_npc.setdefault(r["npc"], []).append((int(r["tick"]), r["goal_id"]))
    chain_any = 0
    chain_related = 0
    chain_examples = []
    for r in comps:
        ctick = int(r["tick"])
        done_type = type_of.get(r["goal_id"], "")
        nxt = [
            (t, g) for t, g in by_npc.get(r["npc"], [])
            if t > ctick and t <= ctick + 3 * TICKS_PER_DAY and g != r["goal_id"]
        ]
        nxt.sort()
        if nxt:
            chain_any += 1
            nt, ng = nxt[0]
            succ = SUCCESSOR.get(done_type, (done_type,))
            if type_of.get(ng, "") in succ:
                chain_related += 1
            chain_examples.append(
                {"npc": r["npc"], "done": r["goal_id"], "done_type": done_type,
                 "next": ng, "next_type": type_of.get(ng, ""), "gap_ticks": nt - ctick}
            )
    n_comp = len(comps) or 1

    def _seg(first: bool) -> dict:
        lo, hi = (1, 5) if first else (6, 10)
        a = [r for r in acts if lo <= _day(r["tick"]) <= hi]
        c = [r for r in comps if lo <= _day(r["tick"]) <= hi]
        e = [r for r in expired if lo <= _day(r["tick"]) <= hi]
        ma = [r for r in mature if lo <= _day(r["tick"]) <= hi]
        mc = [r for r in ma if r["goal_id"] in comp_ids]
        return {
            "activations": len(a),
            "completions": len(c),
            "expired": len(e),
            "sgr_raw": round(len(c) / len(a), 4) if a else 0.0,
            "mature_activations": len(ma),
            "mature_completions": len(mc),
            "sgr_mature": round(len(mc) / len(ma), 4) if ma else None,
            "types": dict(sorted(Counter(type_of.get(r["goal_id"], "") for r in a).items())),
        }

    return {
        "activations": len(act_ids),
        "completions": len(comp_ids),
        "expired": len(exp_ids),
        "sgr": round(sgr_raw, 4),
        "sgr_raw": round(sgr_raw, 4),
        "sgr_mature": round(mature_sgr, 4),
        "mature": {
            "activations": len(mature_ids),
            "completions": mature_done,
            "expired": mature_exp,
            "unresolved": len(mature_ids) - mature_done - mature_exp,
        },
        "baseline_difficulty": {
            **_percentiles(difficulties),
            "n_above_085": sum(1 for x in difficulties if x > 0.85 + 1e-9),
        },
        "actions_needed": {
            "distribution": dict(sorted(Counter(needed).items())),
            "median": _percentiles([float(x) for x in needed])["median"],
            "mean": _percentiles([float(x) for x in needed])["mean"],
        },
        "execution": {
            "completed": _exec_agg(done_rows),
            "expired": _exec_agg(exp_rows),
            "expired_scheduled_shortfall": sum(
                1 for r in exp_rows if r["scheduled"] < r["actions_needed"]
            ),
            "expired_credited_shortfall": sum(
                1 for r in exp_rows if r["credited"] < r["actions_needed"]
            ),
        },
        "stc": {
            "proposal_distribution": dict(sorted(prop_types.items())),
            "activation_distribution": dict(sorted(type_dist.items())),
            "distinct_types": len(type_dist),
            "uncovered_types": uncovered,
            "share_of_earn_money": round(type_dist.get("earn_money", 0) / max(1, len(act_ids)), 4),
        },
        "sgc": {
            "completed": len(comps),
            "next_within_3d": chain_any,
            "next_within_3d_related": chain_related,
            "rate_any": round(chain_any / n_comp, 4),
            "rate_related": round(chain_related / n_comp, 4),
            "examples": chain_examples[:8],
            "note": "stub 每 3 天固定发一条提案，而窗口也是 3 天；这个比例是发言节奏，不是意图延续证据。",
        },
        "segments": {"first_5d": _seg(True), "last_5d": _seg(False)},
    }


def _attribute(prev: dict | None, new: dict) -> dict:
    if prev is None:
        return {"available": False}
    d_mature = round(new["sgr_mature"] - prev["sgr_mature"], 4)
    d_raw = round(new["sgr_raw"] - prev["sgr_raw"], 4)
    d_diff = round(
        new["baseline_difficulty"]["mean"] - prev["baseline_difficulty"]["mean"], 4
    )
    need_prev = prev["actions_needed"]["median"]
    need_new = new["actions_needed"]["median"]
    if d_mature > 0.02 and abs(d_diff) <= 0.03 and need_new == need_prev:
        verdict = "执行/计划改善：难度与所需动作数几乎不变，成熟完成率上升"
    elif d_mature > 0.02 and d_diff < -0.03:
        verdict = "目标变容易：难度均值下降超过 3pp，成熟完成率随之上升"
    elif d_mature <= 0.02:
        verdict = "未回升：目标更容易或执行改善都不足以提高成熟完成率"
    else:
        verdict = "混合：难度与执行同时变化，需按明细判断"
    return {
        "available": True,
        "delta_sgr_raw": d_raw,
        "delta_sgr_mature": d_mature,
        "delta_difficulty_mean": d_diff,
        "actions_needed_median_prev": need_prev,
        "actions_needed_median_new": need_new,
        "verdict": verdict,
    }


def _type_probe() -> dict:
    """用创世状态探测 6 类目标：校验器是否会把“额度内目标”误拒。

    stub 只发 advance_skill / earn_money，所以另外 4 类从未进入日志。
    这个探针直接构造“内容目标值且 ≤0.8 capacity”的提案，回答
    “是 stub 没覆盖，还是校验器把可达提案拒了”。
    """
    from wsim.actions.common import goal_kind, goal_target
    from wsim.core.content import load_content
    from wsim.sim.genesis import build_genesis
    from wsim.sim.self_goal import FeasibilityValidator, parse_proposal
    from wsim.world.effects import apply_effects
    from wsim.world.state import WorldState

    content = load_content(ROOT, min_npcs=0, allow_fixture=False, use_content=True)
    state = WorldState(locations=content.locations)
    apply_effects(state, build_genesis(content, content.npcs, root=ROOT).effects)
    validator = FeasibilityValidator(content)

    out: dict[str, dict] = {}
    for ctype in sorted(SUPPORTED_TYPES):
        chosen = None
        blocked: list[str] = []
        for npc in sorted(state.entities):
            ent = state.entities[npc]
            if ent.status != "alive":
                continue
            objects = [ent.location]
            if ctype == "build_relation":
                peers = sorted(
                    e.id for e in state.entities.values()
                    if e.status == "alive" and e.id != npc and e.location == ent.location
                )
                if not peers:
                    blocked.append(f"{npc}:no_peer")
                    continue
                objects = [peers[0]]
            raw = {
                "npc": npc,
                "goal_type": ctype,
                "target": 0.01,
                "deadline_days": 3,
                "preconditions": [],
                "expected_actions": 3,
                "expected_money": 300 if ctype == "build_shop" else 0,
                "objects": objects,
                "purpose": "探针",
            }
            proposal, errs = parse_proposal(raw, npc, 0)
            if errs:
                blocked.append(f"{npc}:{errs[0]}")
                continue
            verdict = validator.validate(state, proposal, 0, "active")
            if not verdict.accepted or verdict.capacity_units <= 0:
                blocked.append(f"{npc}:{verdict.reason}")
                continue
            chosen = (npc, raw, verdict, objects)
            break
        if chosen is None:
            out[ctype] = {
                "npc": None,
                "accepted": False,
                "reason": "no_eligible_npc_in_genesis",
                "blocked_examples": blocked[:4],
            }
            continue
        npc, raw, verdict, objects = chosen
        cap = float(verdict.capacity_units)
        content_target = float(goal_target(content, goal_kind(ctype)))
        target = round(min(content_target, 0.8 * cap), 4)
        probe_raw = dict(raw, target=target)
        probe, errs = parse_proposal(probe_raw, npc, 0)
        if errs:
            out[ctype] = {"npc": npc, "accepted": False, "reason": errs[0]}
            continue
        v2 = validator.validate(state, probe, 0, "active")
        out[ctype] = {
            "npc": npc,
            "per_action_units": round(verdict.per_action_units, 4),
            "capacity_units": round(cap, 4),
            "content_target": content_target,
            "probe_target": target,
            "target_ratio": round(target / cap, 4) if cap else 0.0,
            "accepted": bool(v2.accepted),
            "reason": v2.reason,
            "equation": v2.equation,
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rule", type=Path, default=ROOT / "runs" / "sg-rule-10d-s42")
    ap.add_argument("--self", dest="self_run", type=Path, default=ROOT / "runs" / "sg-competent-10d-s42-a")
    ap.add_argument("--prev-self", type=Path, default=None, help="上一轮自提案臂（用于归因对照）")
    ap.add_argument("--probe-types", action="store_true", help="探测 6 类目标是否被校验器误拒")
    ap.add_argument("--out", type=Path, default=ROOT / "reports" / "self-goal-metrics.json")
    args = ap.parse_args()

    rule_events = _events(args.rule)
    self_events = _events(args.self_run)
    rule_ev = _event_metrics(rule_events)
    self_ev = _event_metrics(self_events)
    self_tr = _trace_metrics(args.self_run / "self_goal_proposals.jsonl", _end_tick(self_events))

    prev_tr = None
    if args.prev_self is not None:
        prev_events = _events(args.prev_self)
        prev_tr = _trace_metrics(
            args.prev_self / "self_goal_proposals.jsonl", _end_tick(prev_events)
        )

    result = {
        "rule_arm": {"events": rule_ev},
        "self_arm": {"events": self_ev, "trace": self_tr},
        "quota": {
            "self_actions_total": self_ev["self_slot_events"],
            "self_actions_credited_to_content": self_ev["self_slot_progress_on_content"],
            "self_actions_credited_to_self": self_ev["self_slot_progress_on_self"],
            "content_progress_rule": rule_ev["content_goal_progress_events"],
            "content_progress_self": self_ev["content_goal_progress_events"],
        },
    }
    if prev_tr is not None:
        result["comparison"] = {
            "prev": prev_tr,
            "new": self_tr,
            "attribution": _attribute(prev_tr, self_tr),
        }
    if args.probe_types:
        result["type_probe"] = _type_probe()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
