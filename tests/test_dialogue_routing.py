"""意图 + 诉求对象二维路由回归：评审点名的错配必须改道。"""
from __future__ import annotations

import json
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

import pytest

from wsim.agent.dialogue import TEMPLATES, compose_talk

ROOT = Path(__file__).resolve().parents[1]
R2_RUN = ROOT / "runs" / "a2-dialogue-30" / "eval_raw.jsonl"
R2_EVIDENCE = ROOT / "reports" / "evidence" / "a2-dialogue-30" / "eval_raw.jsonl"
R2_INPUTS = R2_RUN if R2_RUN.exists() else R2_EVIDENCE
NO_R2_INPUTS = pytest.mark.skipif(not R2_INPUTS.exists(), reason="缺 a2-dialogue-30 评测输入")

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

# 第三轮评审点名的串线：外乡人路线与招人入伙不能再共用“传言”骨架。
ROUTING_REGRESSIONS = (
    ("最近可有外乡人经过？", "gossip.event", "gossip.passersby"),
    ("听说有人在招人入伙，你知道吗？", "gossip.event", "gossip.recruit"),
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


def _r2_replies(real_content) -> list[str]:
    rows = [
        json.loads(line)
        for line in R2_INPUTS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 30
    return [_reply(real_content, row["message"], tick=int(row["tick"]))["reply"] for row in rows]


@pytest.mark.parametrize("message,old_family,new_family", MISMATCH_CASES)
def test_review_mismatch_is_rehomed(real_content, message, old_family, new_family):
    out = _reply(real_content, message)
    assert _family(out["skeleton"]) != old_family, out["skeleton"]
    assert _family(out["skeleton"]) == new_family, out["skeleton"]


@pytest.mark.parametrize("message,old_family,new_family", ROUTING_REGRESSIONS)
def test_review_routing_crossing_is_fixed(real_content, message, old_family, new_family):
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
        "gossip.person.relation.closeness"
    )


@NO_R2_INPUTS
def test_r2_sample_skeleton_reuse_at_most_three(real_content):
    skeletons = [
        _reply(real_content, json.loads(line)["message"], tick=int(json.loads(line)["tick"]))[
            "skeleton"
        ]
        for line in R2_INPUTS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    counts = Counter(skeletons)
    assert max(counts.values()) <= 3, counts


def test_templates_and_replies_have_no_topic_brackets():
    bodies = [body for variants in TEMPLATES.values() for body in variants]
    bad = [body for body in bodies if "「" in body or "」" in body]
    assert bad == [], bad


@NO_R2_INPUTS
def test_r2_replies_contain_no_topic_brackets(real_content):
    replies = _r2_replies(real_content)
    assert all("「" not in reply and "」" not in reply for reply in replies)


@NO_R2_INPUTS
def test_r2_no_reply_pair_above_60_percent(real_content):
    replies = _r2_replies(real_content)
    worst = 0.0
    over_limit: list[tuple[int, int, float]] = []
    for left in range(len(replies)):
        for right in range(left + 1, len(replies)):
            ratio = SequenceMatcher(None, replies[left], replies[right]).ratio()
            worst = max(worst, ratio)
            if ratio > 0.6:
                over_limit.append((left + 1, right + 1, round(ratio, 4)))
    assert worst < 0.6, worst
    assert over_limit == [], over_limit


def test_same_object_still_rotates_by_turn(real_content):
    first = _reply(real_content, "今日肉价如何？", turn=0)
    second = _reply(real_content, "今日肉价如何？", turn=1)
    assert first["skeleton"] != second["skeleton"]
    assert first["reply"] != second["reply"]
