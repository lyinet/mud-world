"""意图 + 诉求对象二维路由回归：评审点名的错配必须改道。"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from wsim.agent.dialogue import compose_talk

ROOT = Path(__file__).resolve().parents[1]
R2_INPUTS = ROOT / "runs" / "a2-dialogue-30" / "eval_raw.jsonl"

# 评审点名的 7 条错配：原文、旧骨架、应改走的新骨架族。
MISMATCH_CASES = (
    ("柳三娘做的绣品，你可买过？", "price", "price.other"),
    ("今日可有新鲜的猪下水？", "price", "price.offal"),
    ("我想买半斤瘦肉，怎么切最好？", "price", "price.cut"),
    ("我想盘个铺子，要多少本钱？", "price", "price.capital"),
    ("你觉得读书人靠得住吗？", "gossip.person", "gossip.type"),
    ("最近夜里可有怪声？", "chitchat.safety", "chitchat.sound"),
    ("你家里还有些什么人？", "chitchat.daily", "chitchat.family"),
)

GENERIC_PERSON_CASES = (
    "你觉得读书人靠得住吗？",
    "你觉得书生为人如何？",
    "货郎这行人公道吗？",
)


def _family(skeleton: str) -> str:
    """去掉末位 a/b 变体，得到骨架族名。"""
    return skeleton[:-2] if skeleton.endswith((".a", ".b")) else skeleton


def _reply(real_content, message: str, turn: int = 0, tick: int = 100):
    persona = real_content.personas["duan_wudou"]
    return compose_talk(
        persona, "duan_wudou", "段五斗", "屠户", message, tick, turn=turn
    )


@pytest.mark.parametrize("message,old_family,new_family", MISMATCH_CASES)
def test_review_mismatch_is_rehomed(real_content, message, old_family, new_family):
    out = _reply(real_content, message)
    assert _family(out["skeleton"]) != old_family, out["skeleton"]
    assert _family(out["skeleton"]) == new_family, out["skeleton"]


@pytest.mark.parametrize("message", GENERIC_PERSON_CASES)
def test_generic_person_uses_type_skeleton(real_content, message):
    out = _reply(real_content, message)
    assert out["intent"] == "gossip"
    assert _family(out["skeleton"]) == "gossip.type", out["skeleton"]


def test_named_person_stays_on_person_skeleton(real_content):
    assert _family(_reply(real_content, "金掌柜为人如何？")["skeleton"]) == (
        "gossip.person.reputation"
    )
    assert _family(_reply(real_content, "你和孟九娘熟吗？")["skeleton"]) == (
        "gossip.person.relation"
    )


def test_r2_sample_skeleton_reuse_at_most_three(real_content):
    rows = [
        json.loads(line)
        for line in R2_INPUTS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 30
    skeletons = [
        _reply(real_content, row["message"], tick=int(row["tick"]))["skeleton"]
        for row in rows
    ]
    counts = Counter(skeletons)
    assert max(counts.values()) <= 3, counts


def test_same_object_still_rotates_by_turn(real_content):
    first = _reply(real_content, "今日肉价如何？", turn=0)
    second = _reply(real_content, "今日肉价如何？", turn=1)
    assert first["skeleton"] != second["skeleton"]
    assert first["reply"] != second["reply"]
