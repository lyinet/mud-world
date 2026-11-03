"""P3-B 混合决策：规则保底、模型社交、预检丢弃、修复重试、分槽成本与异步对话。"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from fake_intent_server import FakeIntentServer  # noqa: E402
from fake_llm_server import FakeLLMServer  # noqa: E402
from wsim.agent.budget import BudgetGuard, BudgetLimits  # noqa: E402
from wsim.agent.cost import CostTelemetry, load_cost_rates  # noqa: E402
from wsim.agent.dialogue_async import FALLBACK_BUDGET, AsyncDialogueQueue  # noqa: E402
from wsim.actions.registry import build_registry  # noqa: E402
from wsim.agent.provider import (  # noqa: E402
    Completion,
    LLMReasoner,
    ProviderClient,
    ProviderConfig,
)
from wsim.sim.headless import RunConfig, run_headless  # noqa: E402
from wsim.sim.intent import MockReasoner  # noqa: E402


def _cfg(**kw):
    base = dict(base_url="http://127.0.0.1:1/v1", key="k", model="m", offline=False, max_retries=0)
    base.update(kw)
    return ProviderConfig(**base)


def _view(real_content, real_state, tick=10):
    return {"tick": tick, "day": 0, "tier": "active", "state": real_state, "content": real_content}


def test_model_cannot_replace_rule_actions(real_content, real_state):
    raw = json.dumps(
        {"actions": [
            {"verb": "move", "args": {"to": "market"}, "reason": "模型想通勤"},
            {"verb": "talk", "args": {"target": "shi_gandang", "message": "近来可好", "reply": "尚可"}, "reason": "闲聊"},
        ]},
        ensure_ascii=False,
    )
    provider = ProviderClient(_cfg(), transport=lambda _c, _s, _u: raw)
    telemetry = CostTelemetry(rates=load_cost_rates(ROOT))
    reasoner = LLMReasoner(
        provider, MockReasoner(seed=42, content=real_content), real_content, telemetry,
        registry=build_registry(real_content, 42),
    )
    intents = reasoner.decide({"id": "shen_yanzhou"}, _view(real_content, real_state))
    assert reasoner.dropped_by_reason["rule_owned_survival"] >= 1
    assert any(i.verb == "talk" and i.source == "llm" for i in intents)
    assert not any(i.verb == "move" and i.source == "llm" for i in intents)
    assert reasoner.rule_actions > 0, "规则底稿必须始终保留生存动作"


def test_schema_repair_succeeds_once(real_content, real_state):
    calls = {"n": 0}

    def transport(_c, _s, _u):
        calls["n"] += 1
        if calls["n"] == 1:
            return '{"verb":"move"}'  # 缺 actions 列表 -> SchemaError
        return '{"actions":[{"verb":"cultivate","args":{"skill":"craft","hours":1},"reason":"修复后"}]}'

    provider = ProviderClient(_cfg(max_retries=0), transport=transport)
    reasoner = LLMReasoner(provider, MockReasoner(seed=42, content=real_content), real_content, None)
    reasoner.decide({"id": "shen_yanzhou"}, _view(real_content, real_state))
    assert reasoner.repair_attempted == 1
    assert reasoner.repair_succeeded == 1
    assert reasoner.final_fail == 0


def test_cost_summary_splits_slots():
    telemetry = CostTelemetry(rates=load_cost_rates(ROOT))
    telemetry.record(tick=1, npc="a", tier="active", prompt_text="x" * 40, output_text="y" * 20,
                     model="decision-m", slot="decision", input_tokens=10, output_tokens=5)
    telemetry.record(tick=1, npc="a", tier="active", prompt_text="x" * 40, output_text="y" * 20,
                     model="intent-m", slot="intent", input_tokens=10, output_tokens=5)
    s = telemetry.summary()
    assert set(s["by_slot"]) == {"decision", "intent"}
    assert s["by_slot"]["decision"]["calls"] == 1
    assert s["by_slot"]["intent"]["models"] == ["intent-m"]
    assert s["estimated_cost"] == sum(v["estimated_cost"] for v in s["by_slot"].values())


def test_intent_slot_records_null_tokens_when_upstream_has_no_usage():
    telemetry = CostTelemetry(rates=load_cost_rates(ROOT))
    telemetry.record(
        tick=1, npc="a", tier="active", prompt_text="x" * 40, output_text="y" * 20,
        model="intent-m", slot="intent", usage_source="missing", estimate_missing=False,
    )
    rec = telemetry.records[0]
    assert rec["input_tokens"] is None and rec["output_tokens"] is None
    assert rec["estimated_cost"] is None and rec["usage_source"] == "missing"
    s = telemetry.summary()
    assert s["by_slot"]["intent"]["calls"] == 1
    assert s["by_slot"]["intent"]["token_missing_calls"] == 1
    assert s["by_slot"]["intent"]["estimated_cost"] == 0.0
    assert s["token_missing_calls"] == 1


def test_budget_slots_are_independent():
    guard = BudgetGuard(BudgetLimits(daily_tokens=100, dialogue_daily_calls=2, intent_daily_calls=2))
    # 决策槽超限只影响决策
    for _ in range(3):
        guard.check(0, "active", "x" * 400, 100)
    assert guard.mocked_by_day[0] >= 1
    ok, _ = guard.check_dialogue(0)
    assert ok is True
    guard.record_dialogue(0)
    guard.record_dialogue(0)
    denied, reason = guard.check_dialogue(0)
    assert denied is False and reason == "dialogue_budget_exceeded"
    iok, ireason = guard.check_intent(0)
    assert iok is True
    guard.check_intent(0)
    denied_i, reason_i = guard.check_intent(0)
    assert denied_i is False and reason_i == "intent_budget_exceeded"


def test_complete_many_runs_concurrently():
    with FakeLLMServer(delay_s=0.25, seed=1) as srv:
        cfg = _cfg(base_url=srv.base_url, max_retries=0)
        client = ProviderClient(cfg)
        start = time.perf_counter()
        out = client.complete_many([("s", f"u{i}") for i in range(4)], concurrency=4)
        elapsed = time.perf_counter() - start
        assert all(r["text"] for r in out)
        assert elapsed < 0.8, f"4 并发请求耗时 {elapsed:.2f}s，批处理未生效"


def test_async_dialogue_does_not_block_tick():
    def transport(_c, _s, _u):
        time.sleep(0.25)
        return Completion(text="我听着。", usage={"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6})

    client = ProviderClient(_cfg(max_retries=0), transport=transport)
    queue = AsyncDialogueQueue(client)
    start = time.perf_counter()
    rid = queue.submit("shen_yanzhou", "近来可好？", day=0, tick=10)
    submit_elapsed = time.perf_counter() - start
    assert submit_elapsed < 0.1
    replies = queue.drain()
    queue.close()
    assert replies and replies[0].request_id == rid
    assert replies[0].text == "我听着。"
    assert submit_elapsed < 0.2, "submit 不得等待模型"


def test_dialogue_budget_returns_fallback():
    guard = BudgetGuard(BudgetLimits(dialogue_daily_calls=0))
    client = ProviderClient(_cfg(), transport=lambda *_a: (_ for _ in ()).throw(RuntimeError("x")))
    queue = AsyncDialogueQueue(client, budget=guard)
    queue.submit("shen_yanzhou", "在吗", day=0, tick=10)
    replies = queue.drain()
    queue.close()
    assert replies[0].fallback is True
    assert replies[0].text == FALLBACK_BUDGET


def test_three_slot_end_to_end(tmp_path, monkeypatch):
    with FakeLLMServer(seed=7, mode="decision") as decision, FakeIntentServer(seed=7) as intent:
        monkeypatch.setenv("LLM_BASE_URL", decision.base_url)
        monkeypatch.setenv("LLM_API_KEY", "fake")
        monkeypatch.setenv("LLM_MODEL", "fake-decision")
        monkeypatch.setenv("LLM_DECISION_MODEL", "fake-decision")
        monkeypatch.setenv("LLM_DIALOGUE_MODEL", "fake-dialogue")
        monkeypatch.setenv("LLM_INTENT_BASE_URL", intent.base_url)
        monkeypatch.setenv("XING_API_KEY", "fake")
        monkeypatch.setenv("XING_INTENT_MODEL", "fake-intent")
        result = run_headless(
            ROOT,
            RunConfig(
                days=2, seed=42, npc_limit=3, out_root=tmp_path,
                run_id="p3b-three-slot", enable_llm=True,
            ),
        )
    llm = result["llm"]
    assert llm["enabled"] is True
    # A3 最后一轮：决策 JSON 已合并 intent 参数，intent 槽调用归零。
    assert set(llm["by_slot"]) == {"decision"}
    reasoner = llm["reasoner"]
    assert reasoner["intent_calls"] == 0
    assert reasoner["intent_ok"] == 0
    assert reasoner["model_actions"] > 0
    assert reasoner["rule_actions"] > 0
    assert reasoner["action_ratio"]["rule"] > 0.5
