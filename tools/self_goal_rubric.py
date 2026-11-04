"""提案理由 rubric 的可操作性验证（机器可判定版，不做拟人性结论）。

本轮把两个“人评”维度改到可执行：
  1. 处境贴合      —— 机器规则：objects 非空=5；无 objects 但有 preconditions=4；否则 1。
  2. 目标具体可执行 —— 机器规则：target/capacity ≤0.85=5；≤1.0=3；>1.0 或缺失=1。
  3. 与目标的因果链 —— 机器规则：purpose 必须出现「现状数值 → 目标数值」，
                       且 (目标数值-现状数值) 与 target 的误差在容差内；否则 2。
  4. 是否像本人会说 —— **待评**：无真实模型输出前不评分，不参与分歧率。

两个“打分者”是两套独立实现：A 直接读 `capacity_units`，B 从 `equation`
字符串里解析 capacity；因果链 A 用单条正则，B 先切箭头再取两侧数字。
规则同源、实现独立，用来验证 rubric 是否真的可被不同实现一致执行。

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
SCORED = ["处境贴合", "目标具体可执行", "与目标的因果链"]
PENDING = "是否像本人会说"
RATIO_ACCEPT = 0.85
ARROW = re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:→|->)\s*(-?\d+(?:\.\d+)?)")
NUM = re.compile(r"-?\d+(?:\.\d+)?")


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _tol(target: float) -> float:
    return max(5e-4, 0.005 * abs(target))


def _spread(rows: list[dict], n: int) -> list[dict]:
    if n <= 0:
        return []
    if len(rows) <= n:
        return rows
    step = (len(rows) - 1) / (n - 1)
    return [rows[int(round(i * step))] for i in range(n)]


def _pick(acc: list[dict], rej: list[dict], n_total: int = 20, n_rej: int = 6) -> list[dict]:
    """分层抽样：优先 14 通过 + 6 拒绝；某一类不足时用另一类补足 20 条。"""
    n_rej = min(n_rej, len(rej))
    n_acc = n_total - n_rej
    if len(acc) < n_acc:
        n_rej = min(len(rej), n_total - len(acc))
        n_acc = n_total - n_rej
    return _spread(acc, n_acc) + _spread(rej, n_rej)


def _ratio_from_capacity(p: dict) -> float | None:
    cap = p.get("capacity_units")
    target = float((p.get("proposal") or {}).get("target") or 0.0)
    if cap is None or float(cap) <= 0:
        return None
    return target / float(cap)


# ---------- 打分者 A：直接读结构化字段 ----------
def _a_ground(p: dict) -> int:
    prop = p["proposal"]
    if prop.get("objects"):
        return 5
    if prop.get("preconditions"):
        return 4
    return 1


def _a_feasible(p: dict) -> int:
    if not p.get("accepted", True):
        return 1
    ratio = _ratio_from_capacity(p)
    if ratio is None:
        return 1
    if ratio <= RATIO_ACCEPT + 1e-9:
        return 5
    if ratio <= 1.0 + 1e-9:
        return 3
    return 1


def _a_chain(p: dict) -> int:
    prop = p["proposal"]
    purpose = str(prop.get("purpose") or "")
    target = float(prop.get("target") or 0.0)
    m = ARROW.search(purpose)
    if not m:
        return 2
    cur, res = float(m.group(1)), float(m.group(2))
    return 5 if abs((res - cur) - target) <= _tol(target) else 2


# ---------- 打分者 B：只从文本/算式解析，独立实现 ----------
def _b_ground(p: dict) -> int:
    # 与 A 同规则，但走“长度存在性”而非真值判断。
    if len(p["proposal"].get("objects") or []) > 0:
        return 5
    if len(p["proposal"].get("preconditions") or []) > 0:
        return 4
    return 1


def _b_capacity(p: dict) -> float | None:
    eq = str(p.get("equation") or "")
    m = re.search(r"capacity=[^=]*=\s*(\d+(?:\.\d+)?)", eq)
    if m:
        return float(m.group(1))
    cap = p.get("capacity_units")
    return float(cap) if cap is not None else None


def _b_feasible(p: dict) -> int:
    if not p.get("accepted", True):
        return 1
    cap = _b_capacity(p)
    target = float((p.get("proposal") or {}).get("target") or 0.0)
    if cap is None or cap <= 0:
        return 1
    ratio = target / cap
    if ratio <= RATIO_ACCEPT + 1e-9:
        return 5
    if ratio <= 1.0 + 1e-9:
        return 3
    return 1


def _b_chain(p: dict) -> int:
    purpose = str((p.get("proposal") or {}).get("purpose") or "")
    target = float(p["proposal"].get("target") or 0.0)
    if "→" not in purpose and "->" not in purpose:
        return 2
    token = "→" if "→" in purpose else "->"
    left, right = purpose.split(token, 1)
    lnums = NUM.findall(left)
    rnums = NUM.findall(right)
    if not lnums or not rnums:
        return 2
    cur, res = float(lnums[-1]), float(rnums[0])
    return 5 if abs((res - cur) - target) <= _tol(target) else 2


def _score(p: dict) -> tuple[dict, dict]:
    a = {
        "处境贴合": _a_ground(p),
        "目标具体可执行": _a_feasible(p),
        "与目标的因果链": _a_chain(p),
        PENDING: "待评",
    }
    b = {
        "处境贴合": _b_ground(p),
        "目标具体可执行": _b_feasible(p),
        "与目标的因果链": _b_chain(p),
        PENDING: "待评",
    }
    return a, b


def _example(purpose: str, target: float, capacity: float, accepted: bool = True) -> dict:
    eq = f"capacity=3×0.0050={capacity:.4f} >= target={target:.4f}" if accepted else \
        f"capacity=3×0.0050={capacity:.4f} < target={target:.4f}"
    return {
        "proposal": {"purpose": purpose, "target": target, "objects": ["x"], "preconditions": []},
        "capacity_units": capacity,
        "equation": eq,
        "accepted": accepted,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", type=Path,
                    default=ROOT / "runs" / "sg-competent-10d-s42-a" / "self_goal_proposals.jsonl")
    ap.add_argument("--extra-trace", type=Path, default=None,
                    help="额外提案日志（用于补足拒绝样例，例如 scenarios 臂）")
    ap.add_argument("--out", type=Path, default=ROOT / "reports" / "self-goal-rubric.json")
    args = ap.parse_args()

    proposals = [r for r in _rows(args.trace) if r["kind"] == "proposal"]
    extra = [r for r in _rows(args.extra_trace) if r["kind"] == "proposal"] if args.extra_trace else []
    acc = sorted([p for p in proposals if p["accepted"]], key=lambda p: (p["tick"], p["npc"]))
    rej = sorted(
        [p for p in proposals + extra if not p["accepted"]], key=lambda p: (p["tick"], p["npc"])
    )
    sample = _pick(acc, rej)
    scored = []
    for p in sample:
        a, b = _score(p)
        row = {
            "proposal_id": p["proposal"]["proposal_id"],
            "npc": p["npc"],
            "tick": p["tick"],
            "accepted": p["accepted"],
            "goal_type": p["proposal"]["goal_type"],
            "purpose": p["proposal"].get("purpose", ""),
            "target": p["proposal"]["target"],
            "capacity": p.get("capacity_units"),
            "ratio": round(_ratio_from_capacity(p), 4) if _ratio_from_capacity(p) is not None else None,
            "scorer_a": a,
            "scorer_b": b,
            "diff": {d: a[d] - b[d] for d in SCORED},
        }
        scored.append(row)

    dim_stats = {}
    total_cells = 0
    total_disagree = 0
    for d in SCORED:
        disagree = [s for s in scored if abs(s["scorer_a"][d] - s["scorer_b"][d]) >= 1]
        total_cells += len(scored)
        total_disagree += len(disagree)
        dim_stats[d] = {
            "mean_a": round(sum(s["scorer_a"][d] for s in scored) / len(scored), 3) if scored else 0.0,
            "mean_b": round(sum(s["scorer_b"][d] for s in scored) / len(scored), 3) if scored else 0.0,
            "disagreements": len(disagree),
            "rate": round(len(disagree) / len(scored), 4) if scored else 0.0,
            "max_abs_gap": max(
                (abs(s["scorer_a"][d] - s["scorer_b"][d]) for s in scored), default=0
            ),
            "items": [s["proposal_id"] for s in disagree],
        }

    examples = [
        ("旧：攒142文件盘缠", _example("攒142文件盘缠", 142.0, 453.96)),
        ("旧：把0.440的手艺再精进到0.014", _example("把0.440的手艺再精进到0.014", 0.0135, 0.015)),
        ("新：手艺 0.4400 → 0.4526", _example("手艺 0.4400 → 0.4526", 0.0126, 0.015)),
        ("新：盘缠 796.00 → 830.22", _example("盘缠 796.00 → 830.22", 34.2216, 40.74)),
        ("拒：超预算（比例合格但动作数超）", _example("盘缠 100 → 114", 14.0, 15.0, accepted=False)),
    ]
    example_scores = []
    for label, p in examples:
        a, b = _score(p)
        example_scores.append({"label": label, "scorer_a": a, "scorer_b": b})

    result = {
        "corpus_proposals": len(proposals),
        "sample_size": len(scored),
        "sample_accepted": sum(1 for s in scored if s["accepted"]),
        "sample_rejected": sum(1 for s in scored if not s["accepted"]),
        "rubric": {
            "处境贴合": "5=objects 非空；4=无 objects 但有 preconditions；1=两者都空",
            "目标具体可执行": f"1=校验器拒绝（任何原因）；5=放行且 target/capacity<={RATIO_ACCEPT:.2f}；3=放行但 ratio 在 ({RATIO_ACCEPT:.2f},1.0]（仅历史样本）",
            "与目标的因果链": "5=purpose 含「现状数值 → 目标数值」且差值等于 target（容差 max(5e-4, 0.5%)）；否则 2",
            PENDING: "待评：无真实模型输出前不评分，不参与分歧率",
        },
        "divergence": {
            "scored_dimensions": SCORED,
            "pending_dimension": PENDING,
            "cells": total_cells,
            "disagreements": total_disagree,
            "rate": round(total_disagree / total_cells, 4) if total_cells else 0.0,
            "target_rate": 0.15,
            "pass": (total_disagree / total_cells) < 0.15 if total_cells else False,
        },
        "dim_stats": dim_stats,
        "examples": example_scores,
        "items": scored,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(
        {k: result[k] for k in ("corpus_proposals", "sample_size", "sample_accepted", "sample_rejected", "divergence")},
        ensure_ascii=False,
    ))
    for d in SCORED:
        st = dim_stats[d]
        print(f"{d}: A={st['mean_a']} B={st['mean_b']} 分歧={st['disagreements']}/{len(scored)}")
    print(f"{PENDING}: 待评 {len(scored)}/{len(scored)}（不参与分歧率）")
    return 0 if result["divergence"]["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
