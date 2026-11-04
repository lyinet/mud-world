"""R3b：意图 × 对象 × 具体诉求的细分路由回归。"""
from __future__ import annotations

import json
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

import pytest

from wsim.agent.dialogue import compose_talk

ROOT = Path(__file__).resolve().parents[1]
R3_INPUTS = ROOT / "reports" / "evidence" / "blind-eval-r3" / "inputs-40.jsonl"
R2_RUN = ROOT / "runs" / "a2-dialogue-30" / "eval_raw.jsonl"
R2_EVIDENCE = ROOT / "reports" / "evidence" / "a2-dialogue-30" / "eval_raw.jsonl"
R2_RAW = R2_RUN if R2_RUN.exists() else R2_EVIDENCE
NO_R2_INPUTS = pytest.mark.skipif(not R2_RAW.exists(), reason="缺 a2-dialogue-30 评测输入")

# 40 条新增输入在收细路由后应落到的骨架族。
EXPECTED_FAMILIES = {
    1: "gossip.person.history.trade",
    2: "credit.origin",
    3: "gossip.person.history.post",
    4: "gossip.person.exam",
    5: "gossip.person.craft",
    6: "gossip.person.relation.duration",
    7: "gossip.person.whereabouts",
    8: "price.bargain",
    9: "price.bulk",
    10: "price.mix",
    11: "price.extra",
    12: "price.compare",
    13: "price.regular",
    14: "price.credit_diff",
    15: "help.schedule",
    16: "help.status",
    17: "help.search",
    18: "help.referral",
    19: "help.escort",
    20: "help.progress",
    21: "help.alternative",
    22: "rejection.borrow",
    23: "rejection.change_mind",
    24: "credit.collateral",
    25: "rejection.question",
    26: "rejection.trust",
    27: "price.concession",
    28: "credit.defer",
    29: "credit.interest",
    30: "credit.repay",
    31: "credit.ledger",
    32: "credit.owe",
    33: "credit.interest_regular",
    34: "directions.landmark",
    35: "directions.inn_lamp",
    36: "directions.ferry",
    37: "directions.avoid_patrol",
    38: "price.goods",
    39: "directions.well",
    40: "directions.temple",
}


def _family(skeleton: str) -> str:
    return skeleton[:-2] if skeleton.endswith((".a", ".b")) else skeleton


def _rows(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _reply(real_content, message: str, tick: int) -> dict:
    npc = next(n for n in real_content.npcs if str(n["id"]) == "duan_wudou")
    return compose_talk(
        real_content.personas["duan_wudou"],
        "duan_wudou",
        str(npc.get("name") or ""),
        str(npc.get("role") or ""),
        message,
        tick,
    )


def _r3_replies(real_content) -> list[str]:
    return [
        _reply(real_content, str(row["prompt"]), 6 * int(row["id"]))["reply"]
        for row in _rows(R3_INPUTS)
    ]


def _merged_replies(real_content) -> list[str]:
    r2 = [
        _reply(real_content, str(row["message"]), int(row["tick"]))["reply"]
        for row in _rows(R2_RAW)
    ]
    return r2 + _r3_replies(real_content)


@pytest.mark.parametrize("rid,family", sorted(EXPECTED_FAMILIES.items()))
def test_r3_input_routes_to_specific_demand(real_content, rid, family):
    row = next(row for row in _rows(R3_INPUTS) if int(row["id"]) == rid)
    out = _reply(real_content, str(row["prompt"]), 6 * rid)
    assert _family(out["skeleton"]) == family, out["skeleton"]


@NO_R2_INPUTS
def test_merged_70_skeleton_reuse_at_most_two(real_content):
    skeletons = [
        _reply(real_content, str(row["message"]), int(row["tick"]))["skeleton"]
        for row in _rows(R2_RAW)
    ] + [
        _reply(real_content, str(row["prompt"]), 6 * int(row["id"]))["skeleton"]
        for row in _rows(R3_INPUTS)
    ]
    counts = Counter(skeletons)
    assert len(skeletons) == 70
    assert max(counts.values()) <= 2, counts


@NO_R2_INPUTS
def test_merged_70_has_no_duplicate_or_over_60_reply(real_content):
    replies = _merged_replies(real_content)
    assert len(replies) == 70
    assert len(set(replies)) == 70
    over_limit: list[tuple[int, int, float]] = []
    worst = 0.0
    for left in range(len(replies)):
        for right in range(left + 1, len(replies)):
            ratio = SequenceMatcher(None, replies[left], replies[right]).ratio()
            worst = max(worst, ratio)
            if ratio > 0.6:
                over_limit.append((left + 1, right + 1, round(ratio, 4)))
    assert worst < 0.6, worst
    assert over_limit == [], over_limit
