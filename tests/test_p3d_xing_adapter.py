"""P3-D：真实 Xing 意图槽适配层（剥围栏 / 中英键归一 / 原文 grounding / 重试限额）。

这些测试不联网：所有模型返回都来自固定字符串或本地假 transport。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from wsim.agent.intent_model import (  # noqa: E402
    load_intent_catalog,
    parse_intent_response,
)
from wsim.agent.provider import (  # noqa: E402
    Completion,
    ProviderClient,
    ProviderConfig,
    ProviderError,
    load_model_slots,
    make_replay_transport,
    prompt_sha,
)


def _catalog(content):
    return load_intent_catalog(ROOT, content.verbs)


def _parse(text, content, state, npc="shen_yanzhou", utterance="", context=""):
    return parse_intent_response(
        text, _catalog(content), content, state, npc,
        utterance=utterance, context_text=context,
    )


# ---- 1. 剥围栏 --------------------------------------------------------------
def test_strip_fence_and_prose(real_content, real_state):
    for text in (
        '{"category":"去某地","args":{"地点":"客栈"}}',
        '```json\n{"category":"去某地","args":{"地点":"客栈"}}\n```',
        '```\n{"category":"去某地","args":{"地点":"客栈"}}\n```',
        '好的，结果如下：\n```json\n{"category":"去某地","args":{"地点":"客栈"}}\n```\n以上。',
        '我理解你的意图：{"category":"去某地","args":{"地点":"客栈"}} 完毕',
    ):
        res = _parse(text, real_content, real_state)
        assert res.status == "ok", text
        assert res.args == {"to": "inn"}


def test_category_needs_clarification_variant(real_content, real_state):
    res = _parse(
        '{"category":"needs_clarification","args":{"destination":null}}',
        real_content, real_state, utterance="去买点米",
    )
    assert res.status == "needs_clarification"
    assert res.missing == ["destination"]
    assert res.reason == "model_needs_clarification_category"


def test_invalid_json_rejected(real_content, real_state):
    res = _parse("完全不是 JSON", real_content, real_state)
    assert res.status == "reject"
    assert res.reason == "invalid_json"


# ---- 2. 中英文键名归一 / unknown_key ----------------------------------------
def test_zh_and_en_keys_normalize_to_same_structure(real_content, real_state):
    zh = _parse(
        '{"意图":"购买","参数":{"物品":"米","卖家":"集市","数量":"两斗"}}',
        real_content, real_state,
    )
    en = _parse(
        '{"intent":"buy","args":{"item":"米","seller":"集市","quantity":"两斗"}}',
        real_content, real_state,
    )
    assert zh.status == en.status == "ok"
    assert zh.args == en.args == {"item": "rice", "from": "market", "qty": 2}


def test_unknown_keys_dropped_and_recorded(real_content, real_state):
    res = _parse(
        '{"category":"去某地","args":{"location":"客栈","备注":"模型自己加的"},"reason":"x"}',
        real_content, real_state,
    )
    assert res.status == "ok"
    assert "备注" in res.unknown_keys and "reason" in res.unknown_keys
    assert set(res.args) == {"to"}


# ---- 3. 原文 grounding（核心）----------------------------------------------
def test_grounding_blocks_fabricated_qty_and_seller(real_content, real_state):
    """实测模型会把“去买点米”补成 数量=1、卖家="买家"；两者都必须被拦下。"""
    text = json.dumps(
        {"意图": "购买", "物品": "米", "数量": 1, "卖家": "买家"},
        ensure_ascii=False,
    )
    res = _parse(text, real_content, real_state, utterance="去买点米")
    assert res.status == "needs_clarification"
    assert res.missing == ["from"]
    blocked = {row["field"]: row["raw"] for row in res.blocked}
    assert blocked == {"qty": 1, "from": "买家"}
    assert "qty" not in res.args and "from" not in res.args
    assert res.reason == "grounding_missing_required"


def test_grounding_accepts_values_backed_by_utterance(real_content, real_state):
    text = json.dumps(
        {"意图": "购买", "物品": "米", "数量": "两斗", "卖家": "集市"},
        ensure_ascii=False,
    )
    res = _parse(text, real_content, real_state, utterance="去集市买两斗米")
    assert res.status == "ok"
    assert res.args == {"item": "rice", "from": "market", "qty": 2}
    assert res.normalizations["qty"] == {"value": 2, "unit": "斗", "source": "两斗"}


def test_grounding_blocks_number_not_in_utterance(real_content, real_state):
    text = json.dumps(
        {"意图": "购买", "物品": "米", "数量": 1, "卖家": "集市"},
        ensure_ascii=False,
    )
    res = _parse(
        text, real_content, real_state,
        utterance="去集市买两斗米",
        context='{"tick": 1, "cash": 100}',
    )
    # 原文是“两斗”，模型给的 1 没有原文依据：数量被丢弃，且上下文里的 1 不能背书。
    assert {"field": "qty", "raw": 1} in res.blocked
    assert "qty" not in res.args


def test_grounding_can_use_context_entity_names(real_content, real_state):
    text = json.dumps({"category": "去某地", "args": {"location": "客栈"}}, ensure_ascii=False)
    res = _parse(
        text, real_content, real_state,
        utterance="去刚才说的地方",
        context='{"reachable_locations":[{"id":"inn","name":"客栈"}]}',
    )
    assert res.status == "ok"
    assert res.args == {"to": "inn"}


def test_grounding_disabled_without_utterance_keeps_legacy_callers(real_content, real_state):
    res = _parse(
        json.dumps({"category": "去某地", "args": {"地点": "客栈"}}, ensure_ascii=False),
        real_content, real_state,
    )
    assert res.status == "ok"
    assert res.grounding is False


def test_generative_reply_exempt_from_grounding(real_content, real_state):
    text = json.dumps(
        {"category": "对话", "args": {"对象": "白露生", "说的话": "找白露生闲聊几句",
                                     "回复": "嗯，知道了。"}},
        ensure_ascii=False,
    )
    res = _parse(text, real_content, real_state, utterance="找白露生闲聊几句")
    assert res.status == "ok"
    assert res.blocked == []
    assert res.args["reply"] == "嗯，知道了。"


# ---- 4. 限额与重试 ---------------------------------------------------------
def test_429_retries_twice_with_25s_backoff():
    sleeps: list[float] = []

    def _always_429(_cfg, _system, _user):
        raise ProviderError("http_429", "429")

    cfg = ProviderConfig(
        base_url="http://127.0.0.1:1/v1", key="k", model="m", offline=False,
        max_retries=2, timeout_s=60,
    )
    client = ProviderClient(cfg, transport=_always_429, sleep_fn=sleeps.append)
    try:
        client.complete("s", "u")
    except ProviderError as exc:
        assert exc.kind == "http_429"
    else:
        raise AssertionError("429 用尽重试后必须失败")
    assert sleeps == [25.0, 25.0]
    stats = client.stats_snapshot()
    assert stats["http_429"] == 3
    assert stats["retries"] == 2
    assert stats["calls"] == 1 and stats["attempts"] == 3


def test_429_then_success_records_latency_and_usage():
    sleeps: list[float] = []
    state = {"n": 0}

    def _flaky(_cfg, _system, _user):
        state["n"] += 1
        if state["n"] < 3:
            raise ProviderError("http_429", "429")
        return Completion(text='{"category":"none"}', usage={"prompt_tokens": 10, "completion_tokens": 5})

    client = ProviderClient(
        ProviderConfig(base_url="http://127.0.0.1:1/v1", key="k", model="m",
                       offline=False, max_retries=2),
        transport=_flaky, sleep_fn=sleeps.append,
    )
    text = client.complete("s", "u")
    assert text == '{"category":"none"}'
    assert sleeps == [25.0, 25.0]
    assert client.last_usage == {"prompt_tokens": 10, "completion_tokens": 5}
    assert client.last_latency_s >= 0.0


def test_http_4xx_not_retried():
    def _bad(_cfg, _system, _user):
        raise ProviderError("http_4xx", "400")

    sleeps: list[float] = []
    client = ProviderClient(
        ProviderConfig(base_url="http://127.0.0.1:1/v1", key="k", model="m",
                       offline=False, max_retries=2),
        transport=_bad, sleep_fn=sleeps.append,
    )
    try:
        client.complete("s", "u")
    except ProviderError as exc:
        assert exc.kind == "http_4xx"
    else:
        raise AssertionError("4xx 不应重试")
    assert sleeps == []


def test_intent_slot_limits_from_config():
    slots = load_model_slots(ROOT)
    intent = slots["intent"]
    assert intent["base_url"] == "http://127.0.0.1:3425/v1"
    assert intent["model"] == "openrouter/xingchen-agi/xing4.0-29b-a4b:free"
    assert int(intent["max_tokens"]) == 1500
    assert float(intent["timeout_s"]) == 60.0
    assert int(intent["max_retries"]) == 2


# ---- 5. 录制回放 -----------------------------------------------------------
def test_recorded_intent_output_replays_deterministically(tmp_path):
    system, user = "sys", "user"
    record = tmp_path / "records.jsonl"
    record.write_text(
        json.dumps({
            "prompt_sha": prompt_sha(system, user),
            "utterance": "去买点米",
            "output": '```json\n{"意图":"购买","物品":"米","数量":1,"卖家":"买家"}\n```',
            "usage": {"prompt_tokens": 7, "completion_tokens": 3},
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    client = ProviderClient(
        ProviderConfig(base_url="replay://local", key="k", model="recorded", offline=False),
        transport=make_replay_transport(record),
    )
    text = client.complete(system, user)
    assert "买家" in text
    assert client.last_usage == {"prompt_tokens": 7, "completion_tokens": 3}
