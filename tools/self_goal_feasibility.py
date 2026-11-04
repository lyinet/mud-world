"""零成本验证自提案目标：R 基线 / competent 双跑 / scenarios 覆盖。

用法：
    PYTHONPATH=src .venv/bin/python tools/self_goal_feasibility.py \
        --out reports/self-goal-stage0.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.sim.headless import RunConfig, run_headless  # noqa: E402

SELF_ENV = (
    "WSIM_SELF_GOAL_MODE",
    "WSIM_SELF_GOAL_EVERY_DAYS",
    "WSIM_SELF_GOAL_MAX_WINDOW_DAYS",
    "WSIM_SELF_GOAL_TRACE",
)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _run(run_id: str, mode: str, args, out_root: Path) -> tuple[dict, Path]:
    saved = {k: os.environ.pop(k, None) for k in SELF_ENV}
    trace = out_root / run_id / "self_goal_proposals.jsonl"
    if mode:
        os.environ["WSIM_SELF_GOAL_MODE"] = mode
        os.environ["WSIM_SELF_GOAL_EVERY_DAYS"] = str(args.every_days)
        os.environ["WSIM_SELF_GOAL_MAX_WINDOW_DAYS"] = str(args.max_window_days)
        os.environ["WSIM_SELF_GOAL_TRACE"] = str(trace)
    try:
        cfg = RunConfig(
            days=args.days,
            seed=args.seed,
            npc_limit=args.npc_limit,
            out_root=out_root,
            run_id=run_id,
        )
        summary = run_headless(ROOT, cfg)
    finally:
        for key, value in saved.items():
            os.environ.pop(key, None)
            if value is not None:
                os.environ[key] = value
    return summary, out_root / run_id


def _trace_stats(path: Path) -> dict:
    if not path.exists():
        return {
            "proposals": 0,
            "accepted": 0,
            "rejected": 0,
            "pass_rate": 0.0,
            "proposal_reasons": {},
            "activation_reasons": {},
            "activations": 0,
            "completions": 0,
            "pending_expired": 0,
            "goal_expired": 0,
            "feasibility_rejects_without_equation": 0,
            "rows": [],
        }
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    props = [r for r in rows if r["kind"] == "proposal"]
    accepted = [r for r in props if r.get("accepted")]
    rejected = [r for r in props if not r.get("accepted")]
    act_rej = [r for r in rows if r["kind"] == "activation_rejected"]
    return {
        "proposals": len(props),
        "accepted": len(accepted),
        "rejected": len(rejected),
        "pass_rate": (len(accepted) / len(props)) if props else 0.0,
        "proposal_reasons": dict(sorted(Counter(r["reason"] for r in rejected).items())),
        "activation_reasons": dict(sorted(Counter(r["reason"] for r in act_rej).items())),
        "activations": sum(1 for r in rows if r["kind"] == "activation"),
        "completions": sum(1 for r in rows if r["kind"] == "completion"),
        "pending_expired": sum(1 for r in rows if r["kind"] == "pending_expired"),
        "goal_expired": sum(1 for r in rows if r["kind"] == "goal_expired"),
        "feasibility_rejects_without_equation": sum(
            1 for r in rejected if not str(r.get("equation") or "").strip()
        ),
        "rows": rows,
    }


def _hash_bundle(summary: dict, run_dir: Path) -> dict:
    events = run_dir / "events.jsonl"
    trace = run_dir / "self_goal_proposals.jsonl"
    return {
        "state_final": summary["hashes"]["state_final"],
        "event_final": summary["hashes"]["event_final"],
        "events_jsonl_sha256": _sha256(events) if events.exists() else "",
        "trace_sha256": _sha256(trace) if trace.exists() else "",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--npc-limit", type=int, default=0, help="0 表示全部 31 NPC")
    ap.add_argument("--every-days", type=int, default=3)
    ap.add_argument("--max-window-days", type=int, default=3)
    ap.add_argument("--out", type=Path, default=ROOT / "reports" / "self-goal-stage0.json")
    args = ap.parse_args()

    out_root = ROOT / "runs"
    tag = f"{args.days}d-s{args.seed}"
    runs = {
        "rule": (f"sg-rule-{tag}", ""),
        "competent_a": (f"sg-competent-{tag}-a", "competent"),
        "competent_b": (f"sg-competent-{tag}-b", "competent"),
        "scenarios": (f"sg-scenarios-{tag}", "scenarios"),
    }
    summaries: dict[str, dict] = {}
    dirs: dict[str, Path] = {}
    for name, (run_id, mode) in runs.items():
        print(f"[run] {name}: {run_id} mode={mode or 'off'}", flush=True)
        summaries[name], dirs[name] = _run(run_id, mode, args, out_root)

    hashes = {name: _hash_bundle(summaries[name], dirs[name]) for name in runs}
    deterministic = all(
        hashes["competent_a"][k] == hashes["competent_b"][k]
        for k in ("state_final", "event_final", "events_jsonl_sha256", "trace_sha256")
    )
    stage0 = _trace_stats(dirs["competent_a"] / "self_goal_proposals.jsonl")
    stage1 = _trace_stats(dirs["scenarios"] / "self_goal_proposals.jsonl")

    k1_keys = ("k1_rate", "k1_rate_in_scope", "k1_rate_created_only", "k1_rate_in_scope_created_only")
    result = {
        "config": {
            "days": args.days,
            "seed": args.seed,
            "npc_limit": args.npc_limit or "all",
            "every_days": args.every_days,
            "max_window_days": args.max_window_days,
            "content_hash": summaries["rule"]["content_hash"],
            "enable_llm": False,
        },
        "hashes": hashes,
        "deterministic": deterministic,
        "k1": {
            "rule": {k: summaries["rule"]["k1"][k] for k in k1_keys},
            "competent": {k: summaries["competent_a"]["k1"][k] for k in k1_keys},
            "delta_k1_rate_in_scope": (
                summaries["competent_a"]["k1"]["k1_rate_in_scope"]
                - summaries["rule"]["k1"]["k1_rate_in_scope"]
            ),
            "rule_goals_progressed_in_scope": summaries["rule"]["k1"]["k1_goals_progressed_in_scope"],
            "competent_goals_progressed_in_scope": summaries["competent_a"]["k1"]["k1_goals_progressed_in_scope"],
        },
        "stage0": {k: v for k, v in stage0.items() if k != "rows"},
        "stage1": {k: v for k, v in stage1.items() if k != "rows"},
        "criteria": {
            "pass_rate_ge_60": stage0["pass_rate"] >= 0.60,
            "all_rejects_have_equation": stage0["feasibility_rejects_without_equation"] == 0,
            "deterministic_rerun": deterministic,
        },
        "run_dirs": {name: str(dirs[name]) for name in runs},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("deterministic", "stage0", "stage1", "criteria")}, ensure_ascii=False, indent=2))
    print(f"[out] {args.out}")
    return 0 if all(result["criteria"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
