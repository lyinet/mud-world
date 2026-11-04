"""自提案代理指标：SGR（自主完成率）/ STC（类型覆盖）/ SGC（目标链）。

全部可从事件日志（events.jsonl）与提案日志（self_goal_proposals.jsonl）复算，
不依赖 summary，也不调用模型。

用法：
    PYTHONPATH=src .venv/bin/python tools/self_goal_metrics.py \
        --rule runs/sg-rule-10d-s42 --self runs/sg-competent-10d-s42-a \
        --out reports/self-goal-metrics.json
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TICKS_PER_DAY = 144

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


def _event_metrics(run_dir: Path) -> dict:
    """事件层面：独立额度动作数、进度落点、内容目标进度事件数。"""
    events = _events(run_dir)
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


def _trace_metrics(trace_path: Path) -> dict:
    rows = _rows(trace_path)
    props = {
        str((r.get("proposal") or {}).get("proposal_id") or ""): r
        for r in rows
        if r["kind"] == "proposal" and r.get("accepted")
    }
    acts = [r for r in rows if r["kind"] == "activation"]
    comps = [r for r in rows if r["kind"] == "completion"]
    expired = [r for r in rows if r["kind"] == "goal_expired"]
    act_ids = {r["goal_id"] for r in acts}
    comp_ids = {r["goal_id"] for r in comps}

    def _day(tick: int) -> int:
        return int(tick) // TICKS_PER_DAY + 1

    # SGR：激活过（=真正创建了）的自目标里完成的比例。
    sgr = (len(comp_ids) / len(act_ids)) if act_ids else 0.0

    # 基线难度：校验器放行时的 target / capacity_units，越高越接近“额度上限”。
    difficulties = []
    for r in acts:
        prop = props.get(r["goal_id"])
        cap = float(prop.get("capacity_units") or 0.0) if prop else 0.0
        target = float((r.get("proposal") or {}).get("target") or 0.0)
        if cap > 0:
            difficulties.append(target / cap)
    difficulties.sort()
    diff_median = difficulties[len(difficulties) // 2] if difficulties else 0.0

    # STC：激活目标的类型分布 + 覆盖数。
    type_of = {r["goal_id"]: str((r.get("proposal") or {}).get("goal_type") or "") for r in acts}
    type_dist = Counter(type_of.get(g, "") for g in act_ids)
    type_dist.pop("", None)

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

    # 前 5 天 / 后 5 天分段（按激活/完成发生的天数划分）。
    def _seg(first: bool) -> dict:
        lo, hi = (1, 5) if first else (6, 10)
        a = [r for r in acts if lo <= _day(r["tick"]) <= hi]
        c = [r for r in comps if lo <= _day(r["tick"]) <= hi]
        e = [r for r in expired if lo <= _day(r["tick"]) <= hi]
        return {
            "activations": len(a),
            "completions": len(c),
            "expired": len(e),
            "sgr": round(len(c) / len(a), 4) if a else 0.0,
            "types": dict(sorted(Counter(type_of.get(r["goal_id"], "") for r in a).items())),
        }

    return {
        "activations": len(act_ids),
        "completions": len(comp_ids),
        "expired": len({r["goal_id"] for r in expired}),
        "sgr": round(sgr, 4),
        "baseline_difficulty": {
            "n": len(difficulties),
            "mean": round(sum(difficulties) / len(difficulties), 4) if difficulties else 0.0,
            "median": round(diff_median, 4),
            "min": round(difficulties[0], 4) if difficulties else 0.0,
            "max": round(difficulties[-1], 4) if difficulties else 0.0,
        },
        "stc": {
            "distinct_types": len(type_dist),
            "distribution": dict(sorted(type_dist.items())),
            "share_of_earn_money": round(type_dist.get("earn_money", 0) / max(1, len(act_ids)), 4),
        },
        "sgc": {
            "completed": len(comps),
            "next_within_3d": chain_any,
            "next_within_3d_related": chain_related,
            "rate_any": round(chain_any / n_comp, 4),
            "rate_related": round(chain_related / n_comp, 4),
            "examples": chain_examples[:8],
        },
        "segments": {"first_5d": _seg(True), "last_5d": _seg(False)},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rule", type=Path, default=ROOT / "runs" / "sg-rule-10d-s42")
    ap.add_argument("--self", dest="self_run", type=Path, default=ROOT / "runs" / "sg-competent-10d-s42-a")
    ap.add_argument("--out", type=Path, default=ROOT / "reports" / "self-goal-metrics.json")
    args = ap.parse_args()

    rule_ev = _event_metrics(args.rule)
    self_ev = _event_metrics(args.self_run)
    self_tr = _trace_metrics(args.self_run / "self_goal_proposals.jsonl")
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
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
