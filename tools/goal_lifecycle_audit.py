#!/usr/bin/env python3
"""目标生命周期审计：只用既有 run 的 events.jsonl / summary.json，不调用模型。

口径（详见 reports/goal-lifecycle-audit.md）：
- 生命周期从 goal_create 的 created_tick 起算；
- 存活 tick = 该目标作为 NPC 当前目标的 tick 并集（激活 tick 计入，被切换 tick 不计，
  完成 tick 计入）；
- 切换 = 当前目标 A 被另一个目标 B 激活替换；完成/放弃不算“切换”；
- 丢弃率 = 至少被切换/放弃一次且最终未完成的目标 / 全部已创建目标；
- 沉积进度 = 未完成且被切换/放弃过的目标的期末归一化进度之和；
- 反事实增量 = 若最后一次离开后一直保持该目标、按该目标自身“每活跃 tick 进度”外推
  到 run 结束的额外进度（只是情景估算，不是事实）。

用法：
  python tools/goal_lifecycle_audit.py runs/a3-model2-10d-31npc \
      runs/a3-rule-10d-31npc-a3 --out /tmp/goal-audit.json
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _origin(args: dict[str, Any], verb: str) -> str:
    if verb == "genesis":
        return "planner"
    src = str(args.get("goal_source") or "")
    if src == "model":
        return "model"
    if src in ("content", "urgent"):
        return "planner"
    return "system"


def _switch_type(prev_origin: str | None, new_origin: str) -> str:
    if prev_origin is None:
        return "activation"
    if prev_origin == "model" and new_origin == "model":
        return "model_internal"
    if prev_origin == "planner" and new_origin == "model":
        return "model_over_planner"
    if prev_origin == "model" and new_origin == "planner":
        return "planner_over_model"
    if prev_origin == "planner" and new_origin == "planner":
        return "planner_internal"
    return "other"


def audit_run(run_dir: Path) -> dict[str, Any]:
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    events = sorted(_load_jsonl(run_dir / "events.jsonl"), key=lambda e: int(e.get("seq", 0)))
    days = int(summary.get("days") or 10)
    run_end = days * 144  # 半开区间右端；tick 0..run_end-1
    k1_rows = summary.get("k1", {}).get("rows") or []
    k1_by_id = {str(r["id"]): r for r in k1_rows}

    goals: dict[str, dict[str, Any]] = {}
    current: dict[str, str | None] = defaultdict(lambda: None)
    current_origin: dict[str, str | None] = defaultdict(lambda: None)
    open_interval: dict[str, tuple[str, int] | None] = defaultdict(lambda: None)
    pending_abandon: dict[str, int | None] = defaultdict(lambda: None)
    intervals: dict[str, list[tuple[int, int]]] = defaultdict(list)
    activations: list[dict[str, Any]] = []
    activation_ticks: dict[str, list[int]] = defaultdict(list)
    switches: list[dict[str, Any]] = []
    same_goal_reselect = 0

    def close_interval(npc: str, end_tick: int) -> None:
        opened = open_interval[npc]
        if opened is None:
            return
        gid, start = opened
        if end_tick > start:
            intervals[gid].append((start, end_tick))
        open_interval[npc] = None

    def activate(npc: str, gid: str, tick: int, origin: str, reason: str) -> None:
        nonlocal same_goal_reselect
        prev = current[npc]
        prev_origin = current_origin[npc]
        if prev is None:
            kind = "activation"
        elif prev == gid:
            kind = "reselect"
            same_goal_reselect += 1
        else:
            close_interval(npc, tick)
            kind = _switch_type(prev_origin, origin)
            switches.append(
                {
                    "tick": tick,
                    "npc": npc,
                    "from": prev,
                    "to": gid,
                    "from_origin": prev_origin,
                    "to_origin": origin,
                    "type": kind,
                    "from_progress": round(float(goals.get(prev, {}).get("progress", 0.0)), 6),
                    "reason": reason[:120],
                }
            )
            if prev in goals:
                goals[prev]["departures"] += 1
                goals[prev]["last_departure_tick"] = tick
        if prev is None or prev != gid or open_interval[npc] is None:
            if prev != gid:
                open_interval[npc] = (gid, tick)
        current[npc] = gid
        current_origin[npc] = origin
        pending_abandon[npc] = None
        activation_ticks[gid].append(tick)
        activations.append({"tick": tick, "npc": npc, "goal": gid, "origin": origin, "kind": kind, "reason": reason[:120]})

    for event in events:
        tick = int(event.get("tick", 0))
        verb = str(event.get("verb", ""))
        args = event.get("args") or {}
        reason = str(event.get("reason", ""))
        for eff in event.get("effects") or []:
            op = str(eff.get("op", ""))
            data = eff.get("data") or {}
            if op == "goal_create":
                gid = str(data["id"])
                npc = str(data["npc"])
                if gid not in goals:
                    goals[gid] = {
                        "id": gid,
                        "npc": npc,
                        "kind": str(data.get("kind", "")),
                        "content_type": str(data.get("content_type", "")),
                        "content_goal_id": str(data.get("content_goal_id", "")),
                        "source": str(data.get("source", "")),
                        "target": float(data.get("target", 1.0)),
                        "created_tick": int(data.get("created_tick", tick)),
                        "progress": 0.0,
                        "status": "active",
                        "completed_tick": None,
                        "abandoned_tick": None,
                        "departures": 0,
                        "last_departure_tick": None,
                        "first_origin": None,
                        "last_origin": None,
                    }
                g = goals[gid]
                g["status"] = "active"
                if g["first_origin"] is None:
                    g["first_origin"] = _origin(args, verb)
                g["last_origin"] = _origin(args, verb)
                activate(npc, gid, tick, _origin(args, verb), reason)
            elif op == "set_field" and str(data.get("field")) == "goal_id":
                npc = str(data.get("entity"))
                value = data.get("value")
                new_gid = str(value) if value not in (None, "") else None
                if new_gid is None:
                    close_interval(npc, tick)
                    current[npc] = None
                    current_origin[npc] = None
                else:
                    origin = _origin(args, verb)
                    if new_gid in goals:
                        goals[new_gid]["last_origin"] = origin
                    activate(npc, new_gid, tick, origin, reason)
            elif op == "goal_progress":
                gid = str(data["id"])
                g = goals.get(gid)
                if g is None or g["status"] != "active":
                    continue
                g["progress"] = min(1.0, max(0.0, float(g["progress"]) + float(data.get("delta", 0.0))))
                if g["progress"] >= 1.0:
                    g["status"] = "completed"
                    g["completed_tick"] = tick
                    npc = g["npc"]
                    if current[npc] == gid:
                        close_interval(npc, tick + 1)
                        current[npc] = None
                        current_origin[npc] = None
            elif op == "goal_abandon":
                gid = str(data["id"])
                g = goals.get(gid)
                if g is not None and g["status"] == "active":
                    g["status"] = "abandoned"
                    g["abandoned_tick"] = tick
                    pending_abandon[g["npc"]] = tick
        # 同一事件若只放弃、不激活新目标，则在此收口。
        for npc, abandoned_tick in list(pending_abandon.items()):
            if abandoned_tick is not None and abandoned_tick == tick and current[npc] is not None:
                if goals.get(current[npc], {}).get("status") == "abandoned":
                    close_interval(npc, tick)
        for npc in list(current):
            if current[npc] is not None and goals.get(current[npc], {}).get("status") in ("completed", "abandoned"):
                current[npc] = None
                current_origin[npc] = None
                pending_abandon[npc] = None

    for npc in list(open_interval):
        if open_interval[npc] is not None:
            close_interval(npc, run_end)

    # 生命周期与状态汇总
    rows: list[dict[str, Any]] = []
    for gid, g in goals.items():
        active_ticks = sum(end - start for start, end in intervals.get(gid, []))
        if g["status"] == "completed":
            end_tick = int(g["completed_tick"])
        elif g["status"] == "abandoned":
            end_tick = int(g["abandoned_tick"])
        else:
            end_tick = max((end - 1 for _, end in intervals.get(gid, [])), default=int(g["created_tick"]))
        lifecycle_ticks = max(0, end_tick - int(g["created_tick"]) + 1)
        k1 = k1_by_id.get(gid)
        rows.append(
            {
                **g,
                "end_tick": end_tick,
                "active_ticks": active_ticks,
                "lifecycle_ticks": lifecycle_ticks,
                "activations": len(activation_ticks.get(gid, [])),
                "reactivated": len(activation_ticks.get(gid, [])) > 1,
                "in_k1_scope": k1 is not None,
                "k1_progressed": bool(k1 and k1.get("progressed")),
                "k1_completed": bool(k1 and k1.get("completed")),
            }
        )

    # 与 K1 状态对照，验证事件重建是否可靠
    mismatches = []
    for row in rows:
        k1 = k1_by_id.get(row["id"])
        if not k1:
            continue
        if abs(float(row["progress"]) - float(k1.get("progress", 0.0))) > 1e-6:
            mismatches.append({"id": row["id"], "field": "progress", "events": row["progress"], "k1": k1.get("progress")})
        if str(row["status"]) != str(k1.get("status")):
            mismatches.append({"id": row["id"], "field": "status", "events": row["status"], "k1": k1.get("status")})

    status_counts = Counter(r["status"] for r in rows)
    completions = [r for r in rows if r["status"] == "completed"]
    departed = [r for r in rows if r["departures"] > 0]
    dropped = [r for r in rows if r["departures"] > 0 and r["status"] != "completed"]
    total_progress = sum(float(r["progress"]) for r in rows)
    stranded = sum(float(r["progress"]) for r in dropped)
    stranded_share = stranded / total_progress if total_progress else 0.0

    # 反事实：最后一次离开后按自身每活跃 tick 进度外推到 run 结束。
    extra_total = 0.0
    extra_rows = []
    for r in dropped:
        active_ticks = int(r["active_ticks"])
        progress = float(r["progress"])
        if active_ticks <= 0 or progress <= 0 or r["last_departure_tick"] is None:
            continue
        rate = progress / active_ticks
        remaining = max(0, run_end - int(r["last_departure_tick"]))
        projected = min(1.0, progress + rate * remaining)
        extra = max(0.0, projected - progress)
        if extra > 0:
            extra_total += extra
            extra_rows.append(
                {
                    "id": r["id"],
                    "npc": r["npc"],
                    "progress": round(progress, 6),
                    "active_ticks": active_ticks,
                    "rate_per_tick": round(rate, 6),
                    "last_departure_tick": r["last_departure_tick"],
                    "extra_progress_estimate": round(extra, 6),
                }
            )
    extra_share = extra_total / total_progress if total_progress else 0.0

    switch_types = Counter(s["type"] for s in switches)
    for s in switches:
        later = [t for t in activation_ticks.get(s["from"], []) if t > s["tick"]]
        s["from_returned"] = bool(later)
    roundtrip = sum(1 for s in switches if s["from_returned"])
    type_roundtrip: dict[str, dict[str, int]] = {}
    for typ in switch_types:
        group = [s for s in switches if s["type"] == typ]
        type_roundtrip[typ] = {"total": len(group), "returned": sum(1 for s in group if s["from_returned"])}
    departure_dist = Counter(min(int(r["departures"]), 3) for r in rows)
    k1_rows_created = [r for r in rows if r["in_k1_scope"] and r["created_tick"] is not None]
    k1_by_origin: dict[str, dict[str, Any]] = {}
    for origin in ("model", "planner", "system"):
        group = [r for r in k1_rows_created if r["first_origin"] == origin]
        if not group:
            continue
        passed = sum(1 for r in group if r["k1_progressed"])
        k1_by_origin[origin] = {
            "created": len(group),
            "passed": passed,
            "pass_rate": round(passed / len(group), 6),
            "zero_progress": sum(1 for r in group if float(r["progress"]) <= 0.0),
        }
    first_goal_rows = [r for r in k1_rows_created if str(r["content_goal_id"]).endswith("_1")]
    first_goal = {
        "count": len(first_goal_rows),
        "passed": sum(1 for r in first_goal_rows if r["k1_progressed"]),
        "zero_progress": sum(1 for r in first_goal_rows if float(r["progress"]) <= 0.0),
        "avg_active_ticks": round(statistics.fmean([r["active_ticks"] for r in first_goal_rows]), 3) if first_goal_rows else 0.0,
        "avg_progress": round(statistics.fmean([float(r["progress"]) for r in first_goal_rows]), 6) if first_goal_rows else 0.0,
    }
    switch_npcs = len({s["npc"] for s in switches})
    active_means = [r["active_ticks"] for r in rows]
    lifecycle_means = [r["lifecycle_ticks"] for r in rows]
    npc_count = int(summary.get("npc_count") or 0)
    switch_rate = len(switches) / max(1, npc_count * days)
    top_lost = sorted(
        (
            {
                "id": r["id"],
                "npc": r["npc"],
                "content_type": r["content_type"],
                "status": r["status"],
                "progress": round(float(r["progress"]), 6),
                "departures": r["departures"],
                "active_ticks": r["active_ticks"],
                "last_origin": r["last_origin"],
            }
            for r in dropped
            if r["progress"] > 0
        ),
        key=lambda x: (-x["progress"], -x["departures"], x["id"]),
    )

    by_origin = {}
    for origin in ("model", "planner", "system"):
        group = [r for r in rows if r["first_origin"] == origin]
        by_origin[origin] = {
            "created": len(group),
            "completed": sum(1 for r in group if r["status"] == "completed"),
            "abandoned": sum(1 for r in group if r["status"] == "abandoned"),
            "progressed_only": sum(1 for r in group if r["status"] == "active"),
            "progress_sum": round(sum(float(r["progress"]) for r in group), 6),
        }

    return {
        "run": str(run_dir),
        "days": days,
        "npcs": npc_count,
        "run_end_tick": run_end,
        "k1": {
            "passed": summary.get("k1", {}).get("goals_progressed"),
            "total": summary.get("k1", {}).get("goals_total"),
            "rate": summary.get("k1", {}).get("k1_rate"),
        },
        "goals_created": len(rows),
        "goals": {
            "status": {
                "completed": status_counts.get("completed", 0),
                "abandoned": status_counts.get("abandoned", 0),
                "progressed_only": status_counts.get("active", 0),
            },
            "completion_rate": len(completions) / len(rows) if rows else 0.0,
            "progress_gt_10pct": sum(1 for r in rows if float(r["progress"]) > 0.1),
            "progress_gt_10pct_rate": sum(1 for r in rows if float(r["progress"]) > 0.1) / len(rows) if rows else 0.0,
            "total_final_progress": round(total_progress, 6),
            "avg_active_ticks": round(statistics.fmean(active_means), 3) if active_means else 0.0,
            "median_active_ticks": round(statistics.median(active_means), 3) if active_means else 0.0,
            "avg_lifecycle_ticks": round(statistics.fmean(lifecycle_means), 3) if lifecycle_means else 0.0,
            "departed_goals": len(departed),
            "dropped_goals": len(dropped),
            "drop_rate": len(dropped) / len(rows) if rows else 0.0,
            "switch_count": len(switches),
            "switch_rate_per_npc_day": round(switch_rate, 4),
            "switch_roundtrip": roundtrip,
            "switch_one_way": len(switches) - roundtrip,
            "switch_roundtrip_rate": round(roundtrip / len(switches), 6) if switches else 0.0,
            "departure_distribution": {
                "0": departure_dist.get(0, 0),
                "1": departure_dist.get(1, 0),
                "2": departure_dist.get(2, 0),
                "3+": departure_dist.get(3, 0),
            },
            "zero_progress_goals": sum(1 for r in rows if float(r["progress"]) <= 0.0),
            "switched_goals": len({s["from"] for s in switches}),
            "switch_npcs": switch_npcs,
            "same_goal_reselect": same_goal_reselect,
            "departures_per_goal": round(len(switches) / len(rows), 4) if rows else 0.0,
            "stranded_progress": round(stranded, 6),
            "stranded_share": round(stranded_share, 6),
            "counterfactual_extra_progress": round(extra_total, 6),
            "counterfactual_extra_share": round(extra_share, 6),
        },
        "switch_types": dict(sorted(switch_types.items())),
        "switch_type_roundtrip": type_roundtrip,
        "k1_by_origin": k1_by_origin,
        "first_goal": first_goal,
        "by_origin": by_origin,
        "validation": {
            "k1_rows": len(k1_by_id),
            "event_goals_in_k1": sum(1 for r in rows if r["in_k1_scope"]),
            "mismatches": mismatches[:20],
            "mismatch_count": len(mismatches),
        },
        "top_lost_goals": top_lost[:15],
        "counterfactual_rows": sorted(extra_rows, key=lambda x: -x["extra_progress_estimate"])[:15],
        "per_goal": sorted(rows, key=lambda r: (r["npc"], r["created_tick"], r["id"])),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", nargs="+", help="run 目录（需含 events.jsonl 与 summary.json）")
    parser.add_argument("--out", type=Path, default=None, help="输出 JSON 路径；缺省打印到 stdout")
    args = parser.parse_args()
    payload = [audit_run(Path(p)) for p in args.runs]
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
