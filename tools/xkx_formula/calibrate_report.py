#!/usr/bin/env python3
"""把 probe_calibration.py 采集到的原始战斗文本解析成“实测分布 + 预测对比”。

只读采集 JSON，不联网、不改任何参照版文件。

用法：
  python3 tools/xkx_formula/calibrate_report.py \
      --input tools/xkx_formula/calibration/scene_shusheng_10rounds.json \
      --target 书生 --ap 1 --dp 1 --pp 1

输出：
  - 我方每次攻击的 闪避/招架/命中 计数与比例（Wilson 95% 置信区间）
  - 公式预测值（dodge=dp/(ap+dp)，parry=(1-dodge)*pp/(ap+pp)，hit=余下）
  - 偏差（百分点）
  - 命中伤害文本分桶分布（原版只暴露分桶，不打印具体数值）
"""

from __future__ import annotations

import argparse
import json
import re
import sys

ACTION_VERBS = r"(?:挥拳攻击|往|提起拳头往|对准)"
DODGE_KEYS = ("偏了几寸", "躲开", "闪了开去", "避开", "闪身", "身子一侧")
PARRY_KEYS = ("格开", "挡开", "架开")


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def classify_result(line: str) -> str | None:
    line = line.strip()
    if not line:
        return None
    if any(k in line for k in PARRY_KEYS):
        return "parry"
    if any(k in line for k in DODGE_KEYS):
        return "dodge"
    if line.startswith("结果"):
        return "hit"
    if line.startswith("但是"):
        # 兜底：未被上述关键词覆盖的躲避描述
        return "dodge"
    return None


def damage_bucket(line: str) -> str:
    if "没有造成任何伤害" in line:
        return "0"
    if any(k in line for k in ("轻轻地碰到", "轻轻地抓破", "轻轻地划破",
                               "轻轻地刺破", "退了半步，毫发无损")):
        return "1-9"
    if any(k in line for k in ("造成一处瘀青", "造成一处瘀伤",
                               "抓出一道细长血痕", "划出一道细长血痕",
                               "刺出一个创口")):
        return "10-19"
    if any(k in line for k in ("肿了一块老高", "痛得弯下腰去",
                               "抓出一道长长的血口", "划出一道伤口",
                               "刺入了")):
        return "20-39"
    if any(k in line for k in ("吃了不小的亏", "受了点内伤",
                               "血淋淋的伤口", "划出一道血淋淋",
                               "刺进")):
        return "40-79"
    return ">=80/未知"


def parse_attacks(text: str, actor_prefix: str) -> tuple[dict, dict]:
    """返回 (结果计数, 我方命中伤害分桶计数)。

    actor_prefix 为“你”时只统计我方行动；为对手名时统计对手行动（交叉验证）。
    """
    lines = [ln.strip() for ln in text.splitlines()]
    action_re = re.compile(r"^" + actor_prefix + ACTION_VERBS + r".*！$")
    counts = {"dodge": 0, "parry": 0, "hit": 0, "unpaired": 0}
    buckets: dict[str, int] = {}
    for i, line in enumerate(lines):
        if not action_re.match(line):
            continue
        result = None
        for j in range(i + 1, min(i + 3, len(lines))):
            cand = lines[j]
            if not cand:
                continue
            if cand.startswith("但是") or cand.startswith("结果"):
                result = cand
                break
            # 动作行之间不应插入另一条动作行
            if action_re.match(cand):
                break
        if result is None:
            counts["unpaired"] += 1
            continue
        kind = classify_result(result)
        if kind is None:
            counts["unpaired"] += 1
            continue
        counts[kind] += 1
        if kind == "hit":
            b = damage_bucket(result)
            buckets[b] = buckets.get(b, 0) + 1
    return counts, buckets


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--target", default="书生", help="对手中文名/显示名")
    ap.add_argument("--ap", type=int, default=1)
    ap.add_argument("--dp", type=int, default=1)
    ap.add_argument("--pp", type=int, default=1)
    ap.add_argument("--summary-out", default="")
    ap.add_argument("--per-round", action="store_true",
                    help="逐轮打印，观察对手挨打涨经验导致的命中率漂移")
    args = ap.parse_args()

    with open(args.input, encoding="utf-8") as fh:
        data = json.load(fh)

    fight_texts = [st.get("plain", "") for st in data.get("steps", [])
                   if st.get("step", "").startswith(("fight#", "fight "))]
    text = "\n".join(fight_texts)
    if not text.strip():
        print("输入里没有 fight# 记录", file=sys.stderr)
        return 2

    ours, our_buckets = parse_attacks(text, "你")
    theirs, their_buckets = parse_attacks(text, args.target)

    n = ours["dodge"] + ours["parry"] + ours["hit"]
    m = theirs["dodge"] + theirs["parry"] + theirs["hit"]

    # 公式预测（整数除法前的概率）
    pred_dodge = args.dp / (args.ap + args.dp)
    pred_parry = (1 - pred_dodge) * args.pp / (args.ap + args.pp)
    pred_hit = 1 - pred_dodge - pred_parry

    def rates(c: dict) -> dict[str, float]:
        total = c["dodge"] + c["parry"] + c["hit"]
        if total == 0:
            return {"dodge": 0.0, "parry": 0.0, "hit": 0.0}
        return {k: c[k] / total for k in ("dodge", "parry", "hit")}

    summary = {
        "input": args.input,
        "ap": args.ap, "dp": args.dp, "pp": args.pp,
        "ours": {"counts": ours, "n": n, "rates": rates(ours),
                 "ci95": {k: wilson(ours[k], n) for k in ("dodge", "parry", "hit")}},
        "theirs": {"counts": theirs, "n": m, "rates": rates(theirs),
                   "ci95": {k: wilson(theirs[k], m) for k in ("dodge", "parry", "hit")}},
        "prediction": {"dodge": pred_dodge, "parry": pred_parry, "hit": pred_hit},
        "damage_buckets_ours": our_buckets,
        "damage_buckets_theirs": their_buckets,
    }
    # 逐轮明细：把每一轮我方攻击计数与“轮前经验”一起落盘，便于复算 ap 漂移。
    exp_history: list[int] = []
    for st in data.get("steps", []):
        if st.get("step", "").startswith("hp#"):
            match = re.search(r"经\s*验\s*】\s*([0-9]+)", st.get("plain", ""))
            exp_history.append(int(match.group(1)) if match else -1)
    rounds: list[dict] = []
    idx = 0
    for st in data.get("steps", []):
        if not st.get("step", "").startswith(("fight#", "fight ")):
            continue
        if st.get("refused"):
            continue
        c, _b = parse_attacks(st.get("plain", ""), "你")
        nn = c["dodge"] + c["parry"] + c["hit"]
        label = st["step"].replace("fight#", "").replace("fight ", "").strip()
        prev_exp = exp_history[idx - 1] if idx > 0 and idx - 1 < len(exp_history) else 0
        rounds.append({"round": label, "n": nn, "dodge": c["dodge"],
                       "parry": c["parry"], "hit": c["hit"],
                       "exp_at_start": prev_exp})
        idx += 1
    if rounds:
        summary["rounds"] = rounds

    print(f"输入：{args.input}")
    print(f"对手：{args.target}   ap={args.ap} dp={args.dp} pp={args.pp}")
    print(f"公式预测：dodge={pred_dodge:.4f}  parry={pred_parry:.4f}  hit={pred_hit:.4f}")
    print()
    for label, c, nn in (("我方攻击", ours, n), ("对手攻击", theirs, m)):
        if nn == 0:
            print(f"{label}: 无样本")
            continue
        r = rates(c)
        print(f"{label}: N={nn}  dodge={c['dodge']} ({r['dodge']:.4f})  "
              f"parry={c['parry']} ({r['parry']:.4f})  hit={c['hit']} ({r['hit']:.4f})  "
              f"unpaired={c['unpaired']}")
        print(f"        95%CI dodge=[{wilson(c['dodge'], nn)[0]:.4f},{wilson(c['dodge'], nn)[1]:.4f}]  "
              f"parry=[{wilson(c['parry'], nn)[0]:.4f},{wilson(c['parry'], nn)[1]:.4f}]  "
              f"hit=[{wilson(c['hit'], nn)[0]:.4f},{wilson(c['hit'], nn)[1]:.4f}]")
    print()
    print(f"我方命中伤害分桶：{our_buckets}")
    print(f"对手命中伤害分桶：{their_buckets}")

    if args.per_round:
        print()
        print("逐轮（对手每挨一击有 random(max_qi+qi)<damage 的概率 +1 combat_exp，"
              "会抬高其 dp/pp，故需分轮观察漂移）：")
        print(f"{'轮':>3} {'N':>4} {'dodge':>6} {'parry':>6} {'hit':>5} "
              f"{'命中率':>7} {'累计命中':>8}")
        cum_hits = 0
        for st in data.get("steps", []):
            if not st.get("step", "").startswith(("fight#", "fight ")):
                continue
            c, _b = parse_attacks(st.get("plain", ""), "你")
            nn = c["dodge"] + c["parry"] + c["hit"]
            if nn == 0:
                continue
            cum_hits += c["hit"]
            hr = c["hit"] / nn
            label = st["step"].replace("fight#", "").replace("fight ", "").strip()
            print(f"{label:>3} {nn:>4} {c['dodge']:>6} {c['parry']:>6} "
                  f"{c['hit']:>5} {hr:>7.4f} {cum_hits:>8}")

    if args.summary_out:
        with open(args.summary_out, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2)
        print(f"\n[summary] {args.summary_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
