"""P3-A 接线：提取适配器、schema 拒绝、密钥不落盘、预算降级、假端点端到端。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from fake_llm_server import FakeLLMServer  # noqa: E402
from wsim.agent.budget import BudgetGuard, BudgetLimits  # noqa: E402
from wsim.agent.cost import CostTelemetry, load_cost_rates  # noqa: E402
from wsim.agent.prompt import SchemaError, extract_intent_payload, parse_llm_output  # noqa: E402
from wsim.agent.provider import (  # noqa: E402
    LLMReasoner,
    ProviderClient,
    ProviderConfig,
    ProviderError,
    ProviderUnavailable,
)
from wsim.sim.headless import RunConfig, run_headless  # noqa: E402
from wsim.sim.intent import MockReasoner  # noqa: E402
from wsim.world.store import EventStore  # noqa: E402


def _events(run_dir: Path):
    store = EventStore(run_dir / "events.sqlite")
    try:
        return store.read_all()
    finally:
        store.close()


def _reject_rate(events) -> float:
    total = len(events)
    rejected = sum(1 for e in events if e.outcome == "rejected")
    return rejected / total if total else 1.0


def _run_with_fake(
    tmp_path: Path,
    *,
    run_id: str,
    days: int,
    npc_limit: int,
    key: str = "fake-ci-key",
    budget: dict | None = None,
    **cfg_kwargs,
):
    srv = FakeLLMServer(seed=7)
    saved = {k: os.environ.get(k) for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")}
    try:
        srv.start()
        os.environ["LLM_BASE_URL"] = srv.base_url
        os.environ["LLM_API_KEY"] = key
        os.environ["LLM_MODEL"] = "fake/model"
        summary = run_headless(
            ROOT,
            RunConfig(
                days=days, seed=42, npc_limit=npc_limit, out_root=tmp_path, run_id=run_id,
                with_players=1, dump_state=True, enable_llm=True,
                llm_budget=budget or {
                    "daily_tokens": 2_000_000, "monthly_tokens": 20_000_000,
                    "daily_cost": 100.0, "monthly_cost": 1000.0,
                },
                **cfg_kwargs,
            ),
        )
    finally:
        port = srv.port
        srv.stop()
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return summary, tmp_path / run_id, port


def test_extract_adapter_keeps_raw_and_is_replayable():
    """网关差异只做提取：markdown 围栏 + data 包裹 + move.destination→to。"""
    raw = (
        "```json\n"
        '{"data":{"actions":[{"verb":"move","args":{"destination":"teahouse"},'
        '"reason":"去茶馆打听消息"}]}}\n'
        "```"
    )
    payload, meta = extract_intent_payload(raw)
    assert meta["stripped_fence"] is True
    assert meta["unwrapped"] is True
    assert meta["args_aliases"] == 1
    assert payload["actions"][0]["args"] == {"to": "teahouse"}

    work = extract_intent_payload(
        '{"actions":[{"verb":"work","args":{"location":"academy","hours":4},"reason":"讲学"}]}'
    )[0]
    assert work["actions"][0]["args"]["at"] == "academy"
    assert "location" not in work["actions"][0]["args"]

    first = parse_llm_output(raw, tick=7)
    second = parse_llm_output(raw, tick=7)
    assert first == second, "同一原始输出必须可重复回放"
    assert first[0].verb == "move" and first[0].args == {"to": "teahouse"}
    assert first[0].source == "llm" and first[0].tick == 7


def test_raw_model_output_is_preserved_in_telemetry(real_content, real_state):
    raw = (
        "```json\n"
        '{"actions":[{"verb":"move","args":{"destination":"market"},"reason":"去集市"}]}\n'
        "```"
    )
    cfg = ProviderConfig(base_url="https://example.invalid", key="k", model="m", offline=False)
    provider = ProviderClient(cfg, transport=lambda _c, _s, _u: raw)
    telemetry = CostTelemetry(rates=load_cost_rates(ROOT))
    reasoner = LLMReasoner(provider, MockReasoner(seed=42, content=real_content), real_content, telemetry)
    intents = reasoner.decide(
        {"id": "bai_lusheng"},
        {"tick": 10, "day": 0, "tier": "active", "state": real_state, "content": real_content},
    )
    assert intents[-1].args == {"to": "market"}
    assert telemetry.records[-1]["output_text"] == raw, "原始输出必须原样保留，提取层不改语义"


def test_usage_from_gateway_overrides_char_estimate(real_content, real_state):
    from wsim.agent.provider import Completion

    cfg = ProviderConfig(base_url="https://example.invalid", key="k", model="m", offline=False)
    provider = ProviderClient(
        cfg,
        transport=lambda _c, _s, _u: Completion(
            text='{"actions":[{"verb":"cultivate","args":{"skill":"craft","hours":2},"reason":"修炼"}]}',
            usage={"prompt_tokens": 759, "completion_tokens": 1608, "total_tokens": 2367},
        ),
    )
    telemetry = CostTelemetry(rates=load_cost_rates(ROOT))
    reasoner = LLMReasoner(provider, MockReasoner(seed=42, content=real_content), real_content, telemetry)
    reasoner.decide(
        {"id": "bai_lusheng"},
        {"tick": 10, "day": 0, "tier": "active", "state": real_state, "content": real_content},
    )
    rec = telemetry.records[-1]
    assert rec["input_tokens"] == 759 and rec["output_tokens"] == 1608
    assert provider.last_usage["total_tokens"] == 2367


def test_partial_or_invalid_output_never_enters_world(real_content, real_state):
    raw = '{"actions":[{"verb":"move","args":{"to":"market"},"reason":"残缺动作"},{"verb":"move"'
    cfg = ProviderConfig(base_url="https://example.invalid", key="k", model="m", offline=False)
    provider = ProviderClient(cfg, transport=lambda _c, _s, _u: raw)
    telemetry = CostTelemetry(rates=load_cost_rates(ROOT))
    mock = MockReasoner(seed=42, content=real_content)
    reasoner = LLMReasoner(provider, mock, real_content, telemetry)
    intents = reasoner.decide(
        {"id": "bai_lusheng"},
        {"tick": 10, "day": 0, "tier": "active", "state": real_state, "content": real_content},
    )
    assert reasoner.fallback_count == 1
    assert telemetry.schema_rejects["schema_reject"] == 1
    assert all(i.reason != "残缺动作" for i in intents), "半截输出不得进入世界"


def test_api_key_never_lands_in_run_files(tmp_path):
    secret = "sk-test-LEAK-9f2a"
    summary, run_dir, _ = _run_with_fake(tmp_path, run_id="key-leak", days=1, npc_limit=3, key=secret)
    assert secret not in json.dumps(summary, ensure_ascii=False)
    assert secret not in json.dumps(summary["llm"], ensure_ascii=False)
    for path in sorted(run_dir.rglob("*")):
        if path.is_file():
            assert secret.encode("utf-8") not in path.read_bytes(), f"key 泄漏到 {path.name}"
    assert (run_dir / "cost.jsonl").is_file()
    assert "api_key_env" in (ROOT / "config" / "llm.yaml").read_text(encoding="utf-8")


def test_budget_guard_demotes_then_mocks():
    guard = BudgetGuard(
        BudgetLimits(
            daily_tokens=1000, monthly_tokens=10_000, daily_cost=100.0,
            monthly_cost=1000.0, soft_ratio=0.8,
        ),
        input_rate=0.001,
        output_rate=0.002,
    )
    tier, reason = guard.check(0, "spotlight", "x" * 2400, 200)
    assert tier == "active" and reason == "", "接近上限先降一档"
    assert guard.degraded == 1

    guard.record(0, 600, 100, 0.5)
    tier, reason = guard.check(0, "active", "x" * 2400, 200)
    assert tier is None and reason == "budget_exceeded", "超过上限必须降级 Mock"
    assert guard.mocked == 1 and guard.take_audit(0) is True
    assert guard.take_audit(0) is False, "一天只写一条审计事件"


def test_tiny_budget_degrades_to_mock_without_stalling(tmp_path):
    budget = {
        "daily_tokens": 900, "monthly_tokens": 2000,
        "daily_cost": 100.0, "monthly_cost": 1000.0, "soft_ratio": 0.8,
    }
    summary, run_dir, _ = _run_with_fake(
        tmp_path, run_id="tiny-budget", days=2, npc_limit=5, budget=budget
    )
    assert summary["budget"]["mocked"] > 0, "超限必须真的降级 Mock"
    assert summary["llm"]["mock_by_timing"], "降级必须发生在决策时机上"
    assert summary["budget"]["mocked_by_day"], "降级按天记录"

    events = _events(run_dir)
    audits = [e for e in events if e.verb == "system_budget"]
    assert len(audits) == 2, "两天各一条 system_budget 审计事件"
    assert all(e.outcome == "ok" for e in audits)

    rate = _reject_rate(events)
    assert rate <= 0.02, f"降级后世界不得停摆，拒绝率 {rate:.2%}"
    assert summary["events"]["total"] > 0
    assert summary["k6"]["conservation"]["conservation_error"] == 0
    # 降级后仍由 Mock 底稿推进世界：每天都应有动作。
    daily = [
        json.loads(line)
        for line in (run_dir / "daily.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for row in daily:
        assert sum(row["verbs"].values()) > 0, f"day {row['day']} 世界停摆"


def test_fake_endpoint_end_to_end(tmp_path):
    summary, run_dir, port = _run_with_fake(
        tmp_path, run_id="fake-e2e", days=2, npc_limit=3
    )
    assert str(summary["llm"]["fingerprint"]["base_url"]).startswith("http://127.0.0.1:")
    assert summary["llm"]["reasoner_calls"] > 0
    assert summary["llm"]["schema_reject_total"] == 0
    assert summary["llm"]["fallbacks"] == 0
    dec = summary["decisions"]
    assert dec["night_batch"]["invocations"] > 0 and dec["night_batch"]["applied"] > 0
    assert dec["realtime"]["invocations"] > 0 and dec["realtime"]["applied"] > 0
    assert dec["night_batch"]["model_calls"] > 0 and dec["realtime"]["model_calls"] > 0

    events = _events(run_dir)
    landed = [e for e in events if e.source == "llm" and e.outcome == "ok" and e.effects]
    assert landed, "Intent 必须真的进了世界"
    assert _reject_rate(events) <= 0.02
    assert summary["k6"]["conservation"]["conservation_error"] == 0
    assert port > 0


def test_fake_server_failure_modes():
    with FakeLLMServer(fail_rate=1.0, seed=1) as srv:
        cfg = ProviderConfig(
            base_url=srv.base_url, key="k", model="m", offline=False,
            max_retries=0, backoff_base_s=0.0,
        )
        client = ProviderClient(cfg, sleep_fn=lambda _s: None)
        with pytest.raises((ProviderError, ProviderUnavailable)) as exc:
            client.complete("s", "u")
        assert client.last_error_kind == "http_5xx"
        assert client.last_attempts == 1

    with FakeLLMServer(bad_json_rate=1.0, seed=2) as srv:
        cfg = ProviderConfig(base_url=srv.base_url, key="k", model="m", offline=False, max_retries=0)
        text = ProviderClient(cfg).complete("s", "u")
        with pytest.raises(SchemaError) as exc:
            parse_llm_output(text, tick=1)
        assert exc.value.code == "invalid_json"

    with FakeLLMServer(long_output=True, seed=3) as srv:
        cfg = ProviderConfig(base_url=srv.base_url, key="k", model="m", offline=False, max_retries=0)
        text = ProviderClient(cfg).complete("s", "u")
        with pytest.raises(SchemaError) as exc:
            parse_llm_output(text, tick=1)
        assert exc.value.code == "too_many_actions"
