"""P3-E golden：确定性 2 天假端点 run 的事件序、状态哈希与经济流量必须锁死。

背景：K6 收口时把 `talk` 调到同 tick 首位，导致 18 条原本被前置拦截的话变成成功事件，
世界因此从 816 events 漂移到 824 events（economy 同步变化）。这不是“机制不变”，
而是两个不同世界；此测试把新世界作为显式基线，任何调度/前置改动造成的漂移都会立刻 FAIL。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from fake_llm_server import FakeLLMServer  # noqa: E402
from wsim.sim.headless import RunConfig, run_headless  # noqa: E402

# 2026-11-04：fallback 模板从“第三人称档案 + 固定台词”改为“20–60 字第一人称短回应”，
# talk 写入记忆的“我答：…”文本随之为变，事件数/经济流量不变，事件与状态哈希显式漂移。
GOLDEN = {
    "events_total": 824,
    "state_final": "38bdacdaa2764374ac0b59a27eab5e84788b58ba84d7e210b6915384d5f220e5",
    "event_final": "9f2547cc85a4f89476ac947085d1a5ce9f6bd3e35b80561baa9569a99f3b7766",
    "sell_in": 849,
    "buy_out": 795,
    "burned": 113,
    "business_cash": 12058,
    "precondition_blocked": 2,
    "precondition_pruned": 5,
}

# A2：dialogue 显式开启后，talk 的“我答：…”文本换成模型台词，事件数不变，
# 但 event/state hash 必然漂移。此基线锁定该开启路径的确定性（FakeLLMServer seed=7）。
DIALOGUE_GOLDEN = {
    "events_total": 824,
    "state_final": "a52d5a482f5708298fb3be5c863df8a777428716e67cd811644b674d16565b96",
    "event_final": "86946e7c66012dcd2c51ed65f1004752eae165742d050431e2d5fb3b80e1a6a0",
    "dialogue_calls": 65,
}


def test_deterministic_2d_fake_run_is_golden(tmp_path):
    env_keys = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")
    saved = {k: os.environ.get(k) for k in env_keys}
    srv = FakeLLMServer(port=0, seed=7)
    try:
        srv.start()
        os.environ["LLM_BASE_URL"] = srv.base_url
        os.environ["LLM_API_KEY"] = "fake-ci-key"
        os.environ["LLM_MODEL"] = "fake/model"
        summary = run_headless(
            ROOT,
            RunConfig(
                days=2, seed=42, npc_limit=0, out_root=tmp_path, run_id="golden",
                with_players=1, dump_state=True, enable_llm=True,
                llm_budget={
                    "daily_tokens": 2_000_000, "monthly_tokens": 20_000_000,
                    "daily_cost": 100.0, "monthly_cost": 1000.0,
                },
            ),
        )
    finally:
        srv.stop()
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    cf = summary["economy"]["cashflow"]
    got = {
        "events_total": summary["events"]["total"],
        "state_final": summary["hashes"]["state_final"],
        "event_final": summary["hashes"]["event_final"],
        "sell_in": cf["inflow_by_reason"].get("sell", 0),
        "buy_out": cf["outflow_by_reason"].get("buy", 0),
        "burned": summary["economy"]["burned_total"],
        "business_cash": summary["economy"]["business_cash_now"],
        "precondition_blocked": summary["precondition_blocked"]["total"],
        "precondition_pruned": summary["precondition_pruned"]["total"],
    }
    drift = {k: (GOLDEN[k], got[k]) for k in GOLDEN if got[k] != GOLDEN[k]}
    assert not drift, (
        "确定性 2 天 run 基线漂移（golden, actual）："
        + json.dumps(drift, ensure_ascii=False)
        + "；若确属有意变更，请同步更新 GOLDEN 与报告基线说明"
    )

    # 全量拦截记录必须可导出，且总数与计数一致。
    pruned_file = tmp_path / "golden" / "pruned.jsonl"
    blocked_file = tmp_path / "golden" / "blocked.jsonl"
    assert pruned_file.exists() and blocked_file.exists(), "缺少全量拦截记录导出"
    assert len(pruned_file.read_text(encoding="utf-8").splitlines()) == GOLDEN["precondition_pruned"]
    assert len(blocked_file.read_text(encoding="utf-8").splitlines()) == GOLDEN["precondition_blocked"]


def test_dialogue_enabled_fake_run_is_golden(tmp_path):
    """A2 开启路径基线：事件数不漂移，文本进入记忆导致 event/state hash 显式漂移。"""
    env_keys = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "WSIM_DIALOGUE")
    saved = {k: os.environ.get(k) for k in env_keys}
    srv = FakeLLMServer(port=0, seed=7)
    try:
        srv.start()
        os.environ["LLM_BASE_URL"] = srv.base_url
        os.environ["LLM_API_KEY"] = "fake-ci-key"
        os.environ["LLM_MODEL"] = "fake/model"
        os.environ["WSIM_DIALOGUE"] = "1"
        summary = run_headless(
            ROOT,
            RunConfig(
                days=2, seed=42, npc_limit=0, out_root=tmp_path, run_id="golden-dialogue",
                with_players=1, dump_state=True, enable_llm=True,
                llm_budget={
                    "daily_tokens": 2_000_000, "monthly_tokens": 20_000_000,
                    "daily_cost": 100.0, "monthly_cost": 1000.0,
                },
            ),
        )
    finally:
        srv.stop()
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    got = {
        "events_total": summary["events"]["total"],
        "state_final": summary["hashes"]["state_final"],
        "event_final": summary["hashes"]["event_final"],
        "dialogue_calls": summary["llm"]["dialogue_async"]["calls"],
    }
    drift = {k: (DIALOGUE_GOLDEN[k], got[k]) for k in DIALOGUE_GOLDEN if got[k] != DIALOGUE_GOLDEN[k]}
    assert not drift, (
        "dialogue 开启路径基线漂移（golden, actual）："
        + json.dumps(drift, ensure_ascii=False)
        + "；若确属有意变更，请同步更新 DIALOGUE_GOLDEN 与报告基线说明"
    )
