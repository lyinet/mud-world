#!/usr/bin/env python3
"""无头模拟入口。

用法：
  .venv/bin/python sim/run_headless.py --days 10 --seed 42 --npc 0
  .venv/bin/python sim/run_headless.py --days 72 --seed 42 --npc 0
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.sim.headless import RunConfig, run_headless  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=10)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--npc", type=int, default=0, help="0 表示使用全部 NPC")
    p.add_argument("--out", default="runs")
    p.add_argument("--run-id", default="")
    p.add_argument("--with-players", type=int, default=0, choices=[0, 1])
    p.add_argument("--dump-state", action="store_true", help="额外写出 state_final.json（大文件）")
    p.add_argument(
        "--enable-llm",
        action="store_true",
        help="启用真实 LLM（还需 LLM_BASE_URL/LLM_API_KEY/LLM_MODEL；默认关闭，测试禁止联网）",
    )
    args = p.parse_args()

    cfg = RunConfig(
        days=args.days,
        seed=args.seed,
        npc_limit=args.npc,
        out_root=Path(args.out) if Path(args.out).is_absolute() else ROOT / args.out,
        run_id=args.run_id or None,
        with_players=args.with_players,
        dump_state=args.dump_state,
        enable_llm=args.enable_llm,
    )
    s = run_headless(ROOT, cfg)
    out = {
        "run_id": s["run_id"],
        "seed": s["seed"],
        "days": s["days"],
        "npc_count": s["npc_count"],
        "hashes": s["hashes"],
        "events": {
            "total": s["events"]["total"],
            "rejected": s["events"]["rejected"],
            "illegal_rate": s["events"]["illegal_rate"],
        },
        "k6_conservation_error": s["k6"]["conservation"]["conservation_error"],
        "k6_illegal_rate": s["k6"]["illegal"]["illegal_rate"],
        "k6_relation_conflict_rate": s["k6"]["relations_strong_ties"]["conflict_rate"],
        "k1_rate": s["k1"]["k1_rate"],
        "k1_comparison_basis": s["k1"]["comparison_basis"],
        "k1_rate_created_only": s["k1"]["k1_rate_created_only"],
        "k1_rate_excluding_one_shot": s["k1"]["k1_rate_excluding_one_shot"],
        "k1_goals": f"{s['k1']['goals_progressed']}/{s['k1']['goals_total']}",
        "k1_created": f"{s['k1']['goals_created']}/{s['k1']['goals_total']}",
        "k1_one_shot": {
            "created": s["k1"]["one_shot_created"],
            "progressed": s["k1"]["one_shot_progressed"],
            "share_of_progressed": s["k1"]["one_shot_share_of_progressed"],
            "by_type": s["k1"]["one_shot_by_type"],
        },
        "k1_not_created": s["k1"]["not_created"],
        "k3_status": s["k3"]["status"],
        "k3_metric": s["k3"]["metric"],
        "k3_spec_version": s["k3"]["spec_version"],
        "k3_verb_layer": s["k3"]["verb_layer"],
        "k3_object_layer": s["k3"]["object_layer"],
        "k3_v10_compat": s["k3"]["v10_compat"],
        "k3_sensitivity_excluding_new_verbs": s["k3"]["sensitivity_excluding_new_verbs"],
        "k3_folds": s["k3"]["folds"],
        "k3_reference_v2_thresholds": s["k3"]["reference_v2_thresholds"],
        "verb_counts": s["k3"]["verb_counts"],
        "economy": {
            "npc_net_per_day": s["economy"]["npc_net_per_day"],
            "npc_operating_net_per_day": s["economy"]["operating_net_per_day"],
            "npc_net_worth_per_day": s["economy"]["npc_net_worth_per_day"],
            "business_book_value": s["economy"]["business_book_value"],
            "npc_capital_out_per_day": s["economy"]["capital_out_per_day"],
            "npc_net_change": s["economy"]["npc_net_change"],
            "ideal_net_per_npc_day": s["economy"]["ideal_net_per_npc_day"],
            "minted_total": s["economy"]["minted_total"],
            "burned_total": s["economy"]["burned_total"],
            "ious_count": s["economy"]["ious_count"],
            "ious_total": s["economy"]["ious_total"],
            "business_cash_now": s["economy"]["business_cash_now"],
            "cash_delta_breakdown": s["economy"]["cash_delta_breakdown"],
        },
        "wage_chain": {
            "totals": s["wages"]["totals"],
            "reconciliation": s["wages"]["reconciliation"],
        },
        "debt_audit": {
            "status": s["k6"]["debt_audit"]["status"],
            "declared_total": s["k6"]["debt_audit"]["declared_total"],
            "settled_total": s["k6"]["debt_audit"]["settled_total"],
            "active_total": s["k6"]["debt_audit"]["active_total"],
            "checks": {
                key: value.get("ok")
                for key, value in s["k6"]["debt_audit"]["checks"].items()
            },
        },
        "cost": s["cost"],
        "llm": s["llm"],
        "verdict": s["verdict"],
        "content_audit": {
            "issues": s["content_audit"]["issues"],
            "defaults_used": s["content_audit"]["defaults_used"],
            "cooldown_field_present": s["content_audit"]["cooldown_field_present"],
        },
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"out: {cfg.out_root / s['run_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
