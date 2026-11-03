"""P3 预备：Prompt 构造、Intent JSON schema、成本遥测、LLM 降级。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from wsim.agent.cost import CostTelemetry, estimate_tokens, load_cost_rates
from wsim.agent.prompt import (
    MODEL_VERBS,
    NPC_VERBS,
    SchemaError,
    build_prompt,
    parse_llm_output,
    validate_intent_json,
)
from wsim.agent.provider import LLMReasoner, ProviderClient, ProviderConfig
from wsim.sim.intent import MockReasoner

ROOT = Path(__file__).resolve().parents[1]


def _view(state, content, tick=10, tier="active"):
    return {"tick": tick, "tier": tier, "state": state, "content": content, "interval": 144}


def test_prompt_contains_persona_and_state_summary(real_content, real_state):
    npc = "bai_lusheng"
    prompt = build_prompt(real_content, real_state, npc, 10, "active", ("system_goal:ok",))
    assert real_content.personas[npc][:4] in prompt.user
    summary = json.loads(prompt.user.split("当前状态：\n", 1)[1])
    assert summary["npc"] == npc
    assert {"goal", "relations", "debts", "location", "recent_events"} <= set(summary)
    assert summary["allowed_verbs"] == list(MODEL_VERBS)
    assert summary["recent_events"] == ["system_goal:ok"]


def test_persona_full_card_keeps_dialogue_within_token_budget(real_content, real_state):
    npc = "bai_lusheng"
    raw = real_content.personas[npc]
    prompt = build_prompt(real_content, real_state, npc, 10, "active", ())
    quotes = [ln.strip().lstrip("> ").strip() for ln in raw.splitlines() if ln.strip().startswith(">")]
    assert quotes, "人设卡应含 > 台词"
    assert any(q in prompt.user for q in quotes), "整卡注入后应保留台词原文"
    # 不能只注入首行摘要：靠后的章节也必须在场。
    assert "遇事反应倾向" in prompt.user
    assert "与谁有张力" in prompt.user
    assert prompt.persona_tokens <= 450


def test_compose_persona_truncates_by_section_priority():
    from wsim.agent.prompt import compose_persona

    filler = "这一段用来占满预算。" * 40
    text = (
        "# 测试角色\n\n- 身份：杂役\n\n"
        f"## 一句话性格\n\n{filler}\n\n"
        f"## 说话风格\n\n> “先记账，后说话。”\n\n"
        f"## 日常锚点\n\n{filler}\n\n"
        f"## 在意的东西\n\n{filler}\n\n"
        f"## 与谁有张力\n\n{filler}\n"
    )
    composed, tokens = compose_persona(text, budget_tokens=300)
    assert "一句话性格" in composed
    assert "说话风格" in composed
    assert "先记账，后说话。" in composed
    assert "与谁有张力" not in composed  # 最低优先级被预算挤掉
    assert tokens <= 300
    assert composed.endswith("…")


def test_cost_telemetry_records_persona_tokens():
    telemetry = CostTelemetry(rates=load_cost_rates(ROOT))
    telemetry.record(
        tick=1, npc="n1", tier="active", prompt_text="a" * 400, output_text="b" * 40,
        model="mock", persona_tokens=321,
    )
    summary = telemetry.summary()
    assert summary["persona_tokens"] == 321
    assert summary["by_tier"]["active"]["persona_tokens"] == 321


def test_validate_intent_json_accepts_contract():
    payload = {"actions": [{"verb": "move", "args": {"to": "market"}, "reason": "去集市"}]}
    intents = validate_intent_json(payload, tick=12)
    assert intents[0].verb == "move"
    assert intents[0].tick == 12
    assert intents[0].reason == "去集市"
    assert intents[0].source == "llm"


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"actions": []}, "empty_actions"),
        ({"actions": [{"verb": "system_plan", "reason": "x"}]}, "unknown_verb"),
        ({"actions": [{"verb": "move", "args": {}}]}, "missing_reason"),
        ({"actions": [{"verb": "move", "args": {"to": {"x": 1}}, "reason": "x"}]}, "nested_args"),
        ({"actions": [{"verb": "move", "args": {}, "reason": "x", "extra": 1}]}, "unknown_action_fields"),
        ({}, "missing_actions"),
        ([], "not_object"),
        ({"actions": [{"verb": "move", "reason": "x"}] * 5}, "too_many_actions"),
    ],
)
def test_validate_intent_json_rejects(payload, code):
    with pytest.raises(SchemaError) as exc:
        validate_intent_json(payload, tick=1)
    assert exc.value.code == code


def test_parse_llm_output_rejects_invalid_json():
    with pytest.raises(SchemaError) as exc:
        parse_llm_output("这不是 JSON", tick=1)
    assert exc.value.code == "invalid_json"


def test_llm_reasoner_falls_back_on_schema_reject(real_content, real_state):
    cfg = ProviderConfig(base_url="https://example.invalid", key="k", model="m", offline=False)
    provider = ProviderClient(cfg, transport=lambda _c, _s, _u: "not json")
    telemetry = CostTelemetry(rates=load_cost_rates(ROOT))
    mock = MockReasoner(seed=42, content=real_content)
    reasoner = LLMReasoner(provider, mock, real_content, telemetry)
    intents = reasoner.decide({"id": "bai_lusheng"}, _view(real_state, real_content))
    assert reasoner.fallback_count == 1
    assert telemetry.schema_rejects.get("schema_reject") == 1
    rec = telemetry.records[-1]
    assert rec["schema_reject"] is True and rec["fallback"] is True


def test_llm_reasoner_accepts_valid_json(real_content, real_state):
    cfg = ProviderConfig(base_url="https://example.invalid", key="k", model="m", offline=False)
    text = json.dumps({"actions": [{"verb": "cultivate", "args": {"skill": "craft", "hours": 4}, "reason": "练手"}]})
    provider = ProviderClient(cfg, transport=lambda _c, _s, _u: text)
    telemetry = CostTelemetry(rates=load_cost_rates(ROOT))
    reasoner = LLMReasoner(provider, MockReasoner(seed=42, content=real_content), real_content, telemetry)
    intents = reasoner.decide({"id": "bai_lusheng"}, _view(real_state, real_content))
    assert [i.verb for i in intents][-1] == "cultivate"
    assert intents[-1].actor == "bai_lusheng"
    assert telemetry.schema_rejects.get("schema_reject") is None
    assert reasoner.calls == 1


def test_cost_telemetry_summary_by_tier():
    telemetry = CostTelemetry(rates=load_cost_rates(ROOT))
    telemetry.record(
        tick=1, npc="n1", tier="silent", prompt_text="a" * 400, output_text="b" * 40, model="mock"
    )
    telemetry.record(
        tick=2, npc="n1", tier="spotlight", prompt_text="a" * 800, output_text="b" * 80, model="mock"
    )
    summary = telemetry.summary()
    assert summary["calls"] == 2
    assert summary["input_tokens"] == 100 + 200
    assert set(summary["by_tier"]) == {"silent", "spotlight"}
    assert summary["by_tier"]["spotlight"]["estimated_cost"] > summary["by_tier"]["silent"]["estimated_cost"]
    assert estimate_tokens("abcde") == 2
    lines = [json.loads(x) for x in telemetry.to_jsonl().splitlines()]
    assert len(lines) == 2 and lines[0]["currency"] == "USD"
