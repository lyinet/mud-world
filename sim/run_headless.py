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
    p.add_argument("--llm-max-tokens", type=int, default=4096)
    p.add_argument("--llm-temperature", type=float, default=0.0)
    p.add_argument("--budget-daily-tokens", type=int, default=None)
    p.add_argument("--budget-monthly-tokens", type=int, default=None)
    p.add_argument("--budget-daily-cost", type=float, default=None)
    p.add_argument("--budget-monthly-cost", type=float, default=None)
    p.add_argument("--budget-soft-ratio", type=float, default=None)
    p.add_argument("--budget-soft-cost", type=float, default=None, help="run 级成本软阈值（美元）")
    p.add_argument("--budget-hard-cost", type=float, default=None, help="run 级成本硬闸（美元，超限中止）")
    p.add_argument(
        "--enable-strategy",
        action="store_true",
        help="启用 A4 战略层（每 NPC 每 N 游戏日一份计划，替代 tick 级 LLM 目标选择器）",
    )
    p.add_argument("--strategy-every-days", type=int, default=3)
    p.add_argument("--strategy-horizon-days", type=int, default=3)
    p.add_argument("--strategy-fake", action="store_true", help="强制确定性 fake 端点（零网络成本）")
    p.add_argument(
        "--goal-policy",
        action="store_true",
        help="启用目标切换策略（承诺窗口 + 滞回阈值）；默认关闭以保留旧行为",
    )
    p.add_argument("--goal-commit-ticks", type=int, default=0, help="目标承诺窗口（tick，1 tick=10 分钟）")
    p.add_argument("--goal-hysteresis-delta", type=float, default=0.0, help="滞回阈值 Δ")
    p.add_argument(
        "--override-guard-v2",
        action="store_true",
        help="启用覆盖护栏 v2（保护高预期完成概率的规则目标）",
    )
    p.add_argument("--override-high-probability", type=float, default=0.55, help="高概率保护阈值")
    p.add_argument(
        "--type-quota",
        action="store_true",
        help="启用目标类型配额（build_shop / earn_money / advance_skill 下限）",
    )
    p.add_argument("--type-quota-floor", type=int, default=1, help="每 NPC 核心类型下限")
    args = p.parse_args()

    budget: dict[str, object] = {}
    for key, value in (
        ("daily_tokens", args.budget_daily_tokens),
        ("monthly_tokens", args.budget_monthly_tokens),
        ("daily_cost", args.budget_daily_cost),
        ("monthly_cost", args.budget_monthly_cost),
        ("soft_ratio", args.budget_soft_ratio),
        ("soft_cost", args.budget_soft_cost),
        ("hard_cost", args.budget_hard_cost),
    ):
        if value is not None:
            budget[key] = value

    cfg = RunConfig(
        days=args.days,
        seed=args.seed,
        npc_limit=args.npc,
        out_root=Path(args.out) if Path(args.out).is_absolute() else ROOT / args.out,
        run_id=args.run_id or None,
        with_players=args.with_players,
        dump_state=args.dump_state,
        enable_llm=args.enable_llm,
        llm_budget=budget or None,
        llm_max_tokens=args.llm_max_tokens,
        llm_temperature=args.llm_temperature,
        goal_policy_enabled=args.goal_policy,
        goal_commit_ticks=args.goal_commit_ticks,
        goal_hysteresis_delta=args.goal_hysteresis_delta,
        override_guard_v2=args.override_guard_v2,
        override_high_probability=args.override_high_probability,
        type_quota_enabled=args.type_quota,
        type_quota_floor=args.type_quota_floor,
        strategy_enabled=args.enable_strategy,
        strategy_every_days=args.strategy_every_days,
        strategy_horizon_days=args.strategy_horizon_days,
        strategy_fake=args.strategy_fake,
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
        "k1_rate_in_scope": s["k1"]["k1_rate_in_scope"],
        "k1_rate_in_scope_created_only": s["k1"]["k1_rate_in_scope_created_only"],
        "k1_rate_in_scope_excluding_one_shot": s["k1"]["k1_rate_in_scope_excluding_one_shot"],
        "k1_scope": {
            "npcs": s["k1"]["k1_scope_npcs"],
            "goals_out_of_run": s["k1"]["k1_goals_out_of_run"],
            "goals_in_scope": s["k1"]["goals_total"] - s["k1"]["k1_goals_out_of_run"],
            "goals_progressed_in_scope": s["k1"]["k1_goals_progressed_in_scope"],
        },
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
        "decisions": s.get("decisions", {}),
        "goal_policy": s.get("goal_policy", {}),
        "k1_detail": s.get("k1", {}),
        "model_agency": s.get("model_agency", {}),
        "budget": s.get("budget", {}),
        "strategy": s.get("strategy", {}),
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
