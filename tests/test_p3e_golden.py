"""P3-E / P3I golden：确定性 2 天假端点 run 的事件序、状态哈希与经济流量必须锁死。

背景：K6 收口时把 `talk` 调到同 tick 首位，导致 18 条原本被前置拦截的话变成成功事件，
世界因此从 816 events 漂移到 824 events（economy 同步变化）。这不是“机制不变”，
而是两个不同世界；此测试把新世界作为显式基线，任何调度/前置改动造成的漂移都会立刻 FAIL。

P3I 基线说明（2026-11-04，非静默改旧基线）：
- 内容包新增 10 件物品、4 步新手引导链、8 条日常委托、1 张新债讨债单，
  并在集市/药铺/客栈/书院/铁匠铺/河渡追加了商品服务。
- 这些改动让 genesis 的 business 数量与 NPC 买卖路径发生变化，旧世界（824 events）
  在当前内容下不可复现，因此**保留旧基线 GOLDEN / DIALOGUE_GOLDEN 不删不改值**，
  两个旧测试显式标记 skip 并指向 P3I 新基线；新基线见 GOLDEN_P3I / DIALOGUE_GOLDEN_P3I。
- 复核方式见 docs/CONTENT-GUIDE-p3i.md。
"""
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
from wsim.sim.headless import RunConfig, run_headless  # noqa: E402

# 基线 v2（2026-11-04）：fallback 模板先改为“20–60 字第一人称短回应”，随后又按意图
# 分化句式骨架（问价/赊账/问路/打听/闲聊/求助/拒绝各用独立骨架，不再只换话题名词）。
# 两次都只改 talk 写入记忆的“我答：…”文本；事件数/经济流量不变，事件与状态哈希显式漂移。
# 基线 v3（2026-11-04，A3 最后一轮）：提交前二次位置校验把 trade 的 not_together 从
# pruned 改记为 stale（过期而非非法）。事件流不变，故 event/state 哈希保持；仅
# precondition_pruned 5→2（另 3 条转入 stale.jsonl）。
GOLDEN = {
    "events_total": 824,
    "state_final": "9d73cb2e87c52cfe9e8880ba704f957825288e95aaccdefc02725f98f08ed894",
    "event_final": "43e16d6b765561ed18889d533eb6d46e70192441d1ecb74e5208eb80d5c75263",
    "sell_in": 849,
    "buy_out": 795,
    "burned": 113,
    "business_cash": 12058,
    "precondition_blocked": 2,
    "precondition_pruned": 2,
}

# A2：dialogue 显式开启后，talk 的“我答：…”文本换成模型台词，事件数不变，
# 但 event/state hash 必然漂移。此基线锁定该开启路径的确定性（FakeLLMServer seed=7）。
DIALOGUE_GOLDEN = {
    "events_total": 824,
    "state_final": "b980558ee70f752127185a93fc52eece0593f0daa23598ba6a2bcf625253987c",
    "event_final": "c04895352f525d7ecaf6aa4175f987aacea7e0d600fd3a7fd03d31513f7dbb8c",
    "dialogue_calls": 65,
}

# P3I 新基线：内容包扩容后的确定性世界（seed=42，FakeLLMServer seed=7）。
P3I_BASELINE_REASON = (
    "P3I 内容扩充（10 物品 / 4 步引导 / 8 日常委托 / 新债权 / 新地点服务）"
    "改变了 genesis 商业体与买卖路径，旧 824-event 世界不可复现；"
    "旧基线值原样保留，本基线为显式迁移。"
)
# P3I 基线 v2（2026-11-04）：内容收口后重新采样。
# 采集 v1（792 events / sell 849）时关系边与债务朝向尚未定稿：随后把秦望川—阮小螺
# 的 friend 改成 master/disciple、把“石敢当欠陆九斤”的债权朝向反转（避免与目标策略
# 的“急事已了”路径冲突），并对三个 NPC 回退了第 4 条目标（引擎侧 _goal_score 缺字段）。
# 这些都会改变 NPC 的对话/任务归属与买卖路径，因此 v1 哈希不可复现；
# v2 为同一内容 + 同一引擎（含并行会话未提交的 goal-policy 改造）复算两次结果一致：
# 791 events，state/event 哈希如下。旧 v1 数值仅保留在本注释中作为漂移记录。
GOLDEN_P3I = {
    "events_total": 791,
    "state_final": "e5a986f79ffcc5903620caca8b914279e3e43488fc3b68070b385e07abe6c247",
    "event_final": "3e7afc830af4c85532e22cf2a80546e6181e0216bffba1a3e1d27e9d9dccd3e0",
    "sell_in": 1133,
    "buy_out": 852,
    "burned": 110,
    "business_cash": 11307,
    "precondition_blocked": 9,
    "precondition_pruned": 2,
}
DIALOGUE_GOLDEN_P3I = {
    "events_total": 791,
    "state_final": "9554ac3f2522da3dd31ed665266bfe2807dab2ab1283967a3bd7421ce4ee6f3d",
    "event_final": "cdc7a07c937751ff2df04810630f7b7d7472f93fd44626df57cde39d91d84406",
    "dialogue_calls": 63,
}


def _run_two_day(tmp_path: Path, run_id: str, *, dialogue: bool):
    env_keys = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "WSIM_DIALOGUE")
    saved = {k: os.environ.get(k) for k in env_keys}
    srv = FakeLLMServer(port=0, seed=7)
    try:
        srv.start()
        os.environ["LLM_BASE_URL"] = srv.base_url
        os.environ["LLM_API_KEY"] = "fake-ci-key"
        os.environ["LLM_MODEL"] = "fake/model"
        if dialogue:
            os.environ["WSIM_DIALOGUE"] = "1"
        return run_headless(
            ROOT,
            RunConfig(
                days=2, seed=42, npc_limit=0, out_root=tmp_path, run_id=run_id,
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


@pytest.mark.skip(reason="P3I 内容扩充后旧 824-event 世界不可复现；迁移到 P3I_GOLDEN，见文件头说明")
def test_deterministic_2d_fake_run_is_golden(tmp_path):
    summary = _run_two_day(tmp_path, "golden", dialogue=False)
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


@pytest.mark.skip(reason="P3I 内容扩充后旧 824-event 世界不可复现；迁移到 P3I_DIALOGUE_GOLDEN，见文件头说明")
def test_dialogue_enabled_fake_run_is_golden(tmp_path):
    """A2 开启路径基线：事件数不漂移，文本进入记忆导致 event/state hash 显式漂移。"""
    summary = _run_two_day(tmp_path, "golden-dialogue", dialogue=True)
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


def test_p3i_deterministic_2d_fake_run_is_golden(tmp_path):
    """P3I 新基线：内容包扩容后的确定性 2 天世界。"""
    summary = _run_two_day(tmp_path, "golden", dialogue=False)
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
    drift = {k: (GOLDEN_P3I[k], got[k]) for k in GOLDEN_P3I if got[k] != GOLDEN_P3I[k]}
    assert not drift, (
        "P3I 基线漂移（golden_p3i, actual）："
        + json.dumps(drift, ensure_ascii=False)
        + "；原因: " + P3I_BASELINE_REASON
    )

    pruned_file = tmp_path / "golden" / "pruned.jsonl"
    blocked_file = tmp_path / "golden" / "blocked.jsonl"
    assert pruned_file.exists() and blocked_file.exists(), "缺少全量拦截记录导出"
    assert len(pruned_file.read_text(encoding="utf-8").splitlines()) == GOLDEN_P3I["precondition_pruned"]
    assert len(blocked_file.read_text(encoding="utf-8").splitlines()) == GOLDEN_P3I["precondition_blocked"]


def test_p3i_dialogue_enabled_fake_run_is_golden(tmp_path):
    """P3I 新基线（dialogue 开启路径）。"""
    summary = _run_two_day(tmp_path, "golden-dialogue", dialogue=True)
    got = {
        "events_total": summary["events"]["total"],
        "state_final": summary["hashes"]["state_final"],
        "event_final": summary["hashes"]["event_final"],
        "dialogue_calls": summary["llm"]["dialogue_async"]["calls"],
    }
    drift = {k: (DIALOGUE_GOLDEN_P3I[k], got[k])
             for k in DIALOGUE_GOLDEN_P3I if got[k] != DIALOGUE_GOLDEN_P3I[k]}
    assert not drift, (
        "P3I dialogue 基线漂移（golden_p3i, actual）："
        + json.dumps(drift, ensure_ascii=False)
        + "；原因: " + P3I_BASELINE_REASON
    )
