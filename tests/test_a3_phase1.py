"""A3 第二轮阶段一：not_together 丢弃/改选、成本硬闸、决策槽整 run 回放。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from fake_llm_server import FakeLLMServer  # noqa: E402
from wsim.agent.budget import BudgetGuard, BudgetLimits  # noqa: E402
from wsim.agent.provider import LLMReasoner  # noqa: E402
from wsim.core.models import Intent  # noqa: E402
from wsim.sim.headless import RunConfig, run_headless  # noqa: E402


class _NotTogetherAction:
    def preconditions(self, state, intent):
        return ["not_together"]


class _Registry:
    def get(self, verb):
        return _NotTogetherAction()


def _reasoner() -> LLMReasoner:
    return LLMReasoner(
        provider=SimpleNamespace(config=SimpleNamespace(max_tokens=64)),
        fallback=SimpleNamespace(),
        content=SimpleNamespace(verbs={}),
        telemetry=None,
        registry=_Registry(),
    )


def _state(locations: dict[str, str]) -> SimpleNamespace:
    entities = {
        eid: SimpleNamespace(id=eid, name=eid, location=loc, status="alive")
        for eid, loc in locations.items()
    }
    return SimpleNamespace(entities=entities)


def test_not_together_retargets_same_tick_same_location():
    state = _state({"a": "here", "b": "elsewhere", "c": "here"})
    reasoner = _reasoner()
    intent = Intent(actor="a", verb="trade", args={"target": "b"}, tick=5, source="llm")
    draft = [Intent(actor="a", verb="trade", args={"target": "c"}, tick=5, source="planner")]
    out, kept = reasoner._filter_model_actions([intent], draft, state, "a")
    assert reasoner.not_together_total == 1
    assert reasoner.not_together_retargeted == 1
    assert reasoner.not_together_dropped == 0
    assert len(kept) == 1 and kept[0].args["target"] == "c"
    # 不再 retime：tick 保持模型原值。
    assert kept[0].tick == 5


def test_not_together_drops_when_no_same_location_alternative():
    state = _state({"a": "here", "b": "elsewhere", "c": "elsewhere"})
    reasoner = _reasoner()
    intent = Intent(actor="a", verb="trade", args={"target": "b"}, tick=5, source="llm")
    draft = [Intent(actor="a", verb="trade", args={"target": "c"}, tick=5, source="planner")]
    out, kept = reasoner._filter_model_actions([intent], draft, state, "a")
    assert reasoner.not_together_total == 1
    assert reasoner.not_together_dropped == 1
    assert reasoner.not_together_retargeted == 0
    assert kept == [] and draft == out


def test_budget_soft_warns_then_hard_aborts():
    guard = BudgetGuard(BudgetLimits(soft_cost=0.005, hard_cost=0.01))
    guard.record(0, 100, 10, 0.006)
    assert guard.soft_triggered is True and guard.aborted is False
    assert guard.warnings and guard.warnings[0].startswith("budget_soft_limit")
    guard.record(0, 100, 10, 0.006)
    assert guard.hard_triggered is True and guard.aborted is True
    assert any(w.startswith("budget_hard_limit") for w in guard.warnings)
    summary = guard.summary()
    assert summary["aborted"] is True and summary["soft_cost"] == 0.005


def _run(tmp_path: Path, run_id: str, server, *, budget=None, record=None, replay=None) -> dict:
    saved = {k: os.environ.get(k) for k in (
        "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL",
        "WSIM_DECISION_RECORD", "WSIM_DECISION_REPLAY",
        "WSIM_INTENT_RECORD", "WSIM_INTENT_REPLAY",
    )}
    try:
        os.environ["LLM_BASE_URL"] = server.base_url
        os.environ["LLM_API_KEY"] = "fake-ci-key"
        os.environ["LLM_MODEL"] = "fake/model"
        os.environ["WSIM_DECISION_RECORD"] = record or ""
        os.environ["WSIM_DECISION_REPLAY"] = replay or ""
        os.environ["WSIM_INTENT_RECORD"] = record or ""
        os.environ["WSIM_INTENT_REPLAY"] = replay or ""
        return run_headless(
            ROOT,
            RunConfig(
                days=1, seed=42, npc_limit=5, out_root=tmp_path, run_id=run_id,
                with_players=1, enable_llm=True,
                llm_budget=budget or {
                    "daily_tokens": 2_000_000, "monthly_tokens": 20_000_000,
                    "daily_cost": 100.0, "monthly_cost": 1000.0,
                },
            ),
        )
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_decision_replay_reproduces_same_event_hash(tmp_path):
    record = tmp_path / "decision-recordings.jsonl"
    with FakeLLMServer(seed=7, mode="decision") as srv:
        rec = _run(tmp_path, "a3-replay-record", srv, record=str(record))
    assert rec["llm"]["replay"]["mode"] == "record"
    assert record.is_file() and record.read_text(encoding="utf-8").strip()

    with FakeLLMServer(seed=7, mode="decision") as srv:
        rep = _run(tmp_path, "a3-replay-replay", srv, replay=str(record))
    assert rep["llm"]["replay"]["mode"] == "replay"
    assert rep["hashes"]["event_final"] == rec["hashes"]["event_final"]


def test_budget_hard_gate_aborts_run_and_saves_state(tmp_path):
    with FakeLLMServer(seed=7, mode="decision") as srv:
        summary = _run(
            tmp_path, "a3-budget-abort", srv,
            budget={"soft_cost": 0.00001, "hard_cost": 0.00002, "daily_cost": 100.0,
                    "monthly_cost": 1000.0},
        )
    assert summary["aborted"]["aborted"] is True
    assert summary["budget"]["hard_triggered"] is True
    assert summary["budget"]["warnings"]
    run_dir = tmp_path / "a3-budget-abort"
    assert (run_dir / "state_abort.json").is_file()
    cost_rows = [json.loads(x) for x in (run_dir / "cost.jsonl").read_text().splitlines() if x]
    assert cost_rows, "逐请求记账必须保留已发生的调用"
