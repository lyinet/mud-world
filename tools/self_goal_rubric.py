"""提案理由 rubric 的可操作性验证（不做拟人性结论）。

4 个维度，各 1~5 分：
  1. 处境贴合      —— 提案是否锚在当前处境（地点/雇主/库存/技能余量）
  2. 目标具体可执行 —— target 是否落在独立额度可达范围内
  3. 与目标的因果链 —— purpose 是否把“现状 → 动作 → 结果”讲通
  4. 是否像本人会说 —— 措辞是否是这个人会说的话

做法：从提案日志里按“accepted / rejected”分层各抽若干条，用两套**互相独立**
的确定性打分规则（A 严格复算、B 读者直觉）各打一遍，统计分歧。
stub 文本是模板生成的，所以本工具**不**给出拟人性结论，只验证 rubric 能不能
被两个打分者稳定执行、以及哪条规则需要补锚点。

用法：
    PYTHONPATH=src .venv/bin/python tools/self_goal_rubric.py \
        --trace runs/sg-competent-10d-s42-a/self_goal_proposals.jsonl \
        --out reports/self-goal-rubric.json
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIMS = ["处境贴合", "目标具体可执行", "与目标的因果链", "是否像本人会说"]
NUM = re.compile(r"\d+(?:\.\d+)?")


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _pick(proposals: list[dict], n_accepted: int = 14, n_rejected: int = 6) -> list[dict]:
    """确定性分层抽样：两类各自按 (tick, npc) 排序后等距取。"""
    acc = sorted([p for p in proposals if p["accepted"]], key=lambda p: (p["tick"], p["npc"]))
    rej = sorted([p for p in proposals if not p["accepted"]], key=lambda p: (p["tick"], p["npc"]))

    def spread(rows: list[dict], n: int) -> list[dict]:
        if len(rows) <= n:
            return rows
        step = (len(rows) - 1) / (n - 1)
        return [rows[int(round(i * step))] for i in range(n)]

    return spread(acc, n_accepted) + spread(rej, n_rejected)


def _scorer_a(p: dict) -> dict:
    """严格：只认可复算的事实。"""
    prop = p["proposal"]
    objects = prop.get("objects") or []
    prec = prop.get("preconditions") or []
    cap = float(p.get("capacity_units") or 0.0)
    target = float(prop.get("target") or 0.0)
    ratio = (target / cap) if cap > 0 else 0.0
    purpose = str(prop.get("purpose") or "")
    nums = NUM.findall(purpose)

    ground = 3 if objects else 1
    ground = min(5, ground + 2) if prec else ground

    if 0.5 <= ratio <= 0.95:
        feasible = 5
    elif 0.2 <= ratio < 0.5:
        feasible = 3
    else:
        feasible = 1

    # 因果链：purpose 要同时给出“现状”和“结果”，且两个数与 target 对得上。
    chain = 1
    if len(nums) >= 2:
        cur, res = float(nums[0]), float(nums[1])
        chain = 5 if abs((res - cur) - target) <= max(1e-9, 0.02 * abs(target)) else 4
    elif len(nums) == 1:
        chain = 2

    human = 5 if ("我" in purpose and len(purpose) <= 30) else (3 if len(purpose) <= 30 else 1)
    return dict(zip(DIMS, [ground, feasible, chain, human]))


def _scorer_b(p: dict) -> dict:
    """宽松：按“读者第一眼能不能懂”打分，故意不用 capacity 复算。"""
    prop = p["proposal"]
    objects = prop.get("objects") or []
    prec = prop.get("preconditions") or []
    target = float(prop.get("target") or 0.0)
    days = int(prop.get("deadline_days") or 0)
    acts = int(prop.get("expected_actions") or 0)
    purpose = str(prop.get("purpose") or "")
    nums = NUM.findall(purpose)

    ground = 5 if objects else (3 if prec else 1)
    feasible = 5 if (target > 0 and days > 0 and acts > 0) else (3 if target > 0 else 1)
    if nums and len(nums) >= 2:
        chain = 5
    elif purpose:
        chain = 4
    else:
        chain = 1
    human = 5 if len(purpose) <= 40 else (3 if len(purpose) <= 60 else 1)
    return dict(zip(DIMS, [ground, feasible, chain, human]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", type=Path,
                    default=ROOT / "runs" / "sg-competent-10d-s42-a" / "self_goal_proposals.jsonl")
    ap.add_argument("--out", type=Path, default=ROOT / "reports" / "self-goal-rubric.json")
    args = ap.parse_args()

    proposals = [r for r in _rows(args.trace) if r["kind"] == "proposal"]
    sample = _pick(proposals)
    scored = []
    for p in sample:
        a, b = _scorer_a(p), _scorer_b(p)
        scored.append({
            "proposal_id": p["proposal"]["proposal_id"],
            "npc": p["npc"],
            "tick": p["tick"],
            "accepted": p["accepted"],
            "goal_type": p["proposal"]["goal_type"],
            "purpose": p["proposal"].get("purpose", ""),
            "target": p["proposal"]["target"],
            "capacity": p.get("capacity_units"),
            "scorer_a": a,
            "scorer_b": b,
            "diff": {d: a[d] - b[d] for d in DIMS},
        })

    dim_stats = {}
    for d in DIMS:
        da = [s["scorer_a"][d] for s in scored]
        db = [s["scorer_b"][d] for s in scored]
        disagree = [s for s in scored if abs(s["scorer_a"][d] - s["scorer_b"][d]) >= 1]
        dim_stats[d] = {
            "mean_a": round(sum(da) / len(da), 3),
            "mean_b": round(sum(db) / len(db), 3),
            "disagreements": len(disagree),
            "max_abs_gap": max((abs(s["scorer_a"][d] - s["scorer_b"][d]) for s in scored), default=0),
            "items": [s["proposal_id"] for s in disagree],
        }

    result = {
        "corpus_proposals": len(proposals),
        "sample_size": len(scored),
        "sample_accepted": sum(1 for s in scored if s["accepted"]),
        "rubric": {
            "处境贴合": "1=无地点/无依据；3=有真实地点；5=地点+可执行前置条件都锚在当前状态",
            "目标具体可执行": "1=target 超出独立额度或小到没有意义；3=占用额度 20%~50%；5=占用 50%~95%",
            "与目标的因果链": "1=没有因果描述；2=只有现状或只有结果；4=现状→结果但数值对不上；5=两者都对得上 target",
            "是否像本人会说": "1=模板腔且过长；3=简短但无自我指涉；5=第一人称、短句、像本人说的",
        },
        "dim_stats": dim_stats,
        "items": scored,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("corpus_proposals", "sample_size", "sample_accepted")}, ensure_ascii=False))
    for d in DIMS:
        st = dim_stats[d]
        print(f"{d}: A={st['mean_a']} B={st['mean_b']} 分歧={st['disagreements']}/{len(scored)} maxgap={st['max_abs_gap']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
