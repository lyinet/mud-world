"""P0-2：grounding 数量校验六个回归用例。

全部走 `parse_intent_response`（生产意图解析路径），不直接调私有函数。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from wsim.agent.intent_model import load_intent_catalog, parse_intent_response  # noqa: E402


def _parse(text, content, state, utterance):
    return parse_intent_response(
        text, load_intent_catalog(ROOT, content.verbs), content, state,
        "shen_yanzhou", utterance=utterance,
    )


def _buy(item: str, qty, seller: str = "集市") -> str:
    return json.dumps(
        {"意图": "购买", "物品": item, "数量": qty, "卖家": seller},
        ensure_ascii=False,
    )


def test_cross_clause_number_is_not_reused(real_content, real_state):
    """上一分句的“三个人”不能给下一分句的米背书 3 斗。"""
    res = _parse(
        _buy("米", "3斗"),
        real_content, real_state,
        utterance="先来三个人，再去集市买两斗米",
    )
    assert res.status == "ok"
    assert "qty" not in res.args
    assert {"field": "qty", "raw": "3斗"} in res.blocked
    assert res.dropped_fields == ["qty"]


def test_people_count_is_not_item_quantity(real_content, real_state):
    """“三个人要买米”里的 3 是人，不是 3 份米。"""
    res = _parse(
        _buy("米", 3),
        real_content, real_state,
        utterance="有三个人要去集市买米",
    )
    assert "qty" not in res.args
    assert {"field": "qty", "raw": 3} in res.blocked


def test_incompatible_unit_is_blocked(real_content, real_state):
    """原文“两斗”，模型给“2 斤”：数值相同、量词不兼容，必须丢弃。"""
    res = _parse(
        _buy("米", "2斤"),
        real_content, real_state,
        utterance="去集市买两斗米",
    )
    assert "qty" not in res.args
    assert {"field": "qty", "raw": "2斤"} in res.blocked


def test_liang_is_normalized_to_two(real_content, real_state):
    res = _parse(
        _buy("炊饼", "俩"),
        real_content, real_state,
        utterance="去集市买俩炊饼",
    )
    assert res.status == "ok"
    assert res.args["qty"] == 2
    assert res.normalizations["qty"]["value"] == 2


def test_half_is_normalized_to_one(real_content, real_state):
    """“半斤”在整数库存里向上取 1 份，仍必须有原文依据。"""
    res = _parse(
        _buy("米", "半斤"),
        real_content, real_state,
        utterance="去集市买半斤米",
    )
    assert res.status == "ok"
    assert res.args["qty"] == 1
    assert res.normalizations["qty"] == {"value": 1, "unit": "斤", "source": "半斤"}


def test_dropped_optional_field_is_recorded(real_content, real_state):
    """原文没有数量，模型补的 qty=5 被丢弃，并写入 dropped_fields。"""
    res = _parse(
        _buy("米", 5),
        real_content, real_state,
        utterance="去集市买米",
    )
    assert res.status == "ok"
    assert "qty" not in res.args
    assert {"field": "qty", "raw": 5} in res.blocked
    assert "qty" in res.dropped_fields
