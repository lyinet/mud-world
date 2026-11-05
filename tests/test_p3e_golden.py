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
- P3I 价格基线（2026-11-04）：把 content 里的供需模型接进 `Content.item_price()` 后，
  成交金额随日供需与库存变化，事件数仍为 791；旧 P3I 基线保留并 skip，新基线见
  GOLDEN_P3I_PRICE / DIALOGUE_GOLDEN_P3I_PRICE。

P4 里程碑基线 v2（2026-11-04，self-goal 改造后）：
- `goal_create` 的 effect payload 新增 `secondary: bool`（urgent/npc 目标都带该字段），
  只写进事件日志，不改变投影状态：事件数仍为 800、state hash 不变、经济流量与
  前置拦截计数逐项相同；仅 events.jsonl 的 event hash 漂移。
- 旧 GOLDEN_MILESTONE / DIALOGUE_GOLDEN_MILESTONE 原样保留并显式 skip；
  新基线见 GOLDEN_MILESTONE_V2 / DIALOGUE_GOLDEN_MILESTONE_V2。

P4 里程碑基线 v3（2026-11-05，任务公平轮转 + 接单提前）：
- `Planner` 按模板 `last_served_tick` 公平轮转开放委托，并把“接单->执行”从
  上工/午饭之后移到当天计划开头，避免发起人临时走开导致 `not_at_giver` 静默拒绝
  （该拒绝不写事件日志，但会让 2 天期限模板整轮空转）。10 天开启态：零接单模板
  16→0、接单 106→181；关闭态 K1 62/91 不变、content_hash 不变。
- 2 天世界因此改变 NPC 一日行动顺序：800→693 events，经济流量同步漂移
  （sell 971→967、buy 820→793、burned 127→81、business_cash 12184→12599），
  拦截计数 blocked 11→8、pruned 2→4，state/event 哈希显式变化。
- 旧 GOLDEN_MILESTONE_V2 / DIALOGUE_GOLDEN_MILESTONE_V2 原样保留并显式 skip；
  新基线见 GOLDEN_MILESTONE_V3 / DIALOGUE_GOLDEN_MILESTONE_V3。

P3F 里程碑基线 v4（2026-11-05，玩家消费类动作开闸）：
- 本次只改 HTTP/API 入口与只读 state（放行 buy/sell/consume/escort、补原因码与价格字段），
  不改 NPC 动作前置、不改调度、不改 content；2 天 headless 世界不执行玩家 API 动作，
  实跑与 V3 逐项完全相同（693 events / sell 967 / buy 793 / burned 81 /
  business_cash 12599 / blocked 8 / pruned 4，state/event 哈希不变），即**零漂移**。
- 旧 V3 基线值原样保留并显式 skip，新基线见 GOLDEN_MILESTONE_V4 /
  DIALOGUE_GOLDEN_MILESTONE_V4；V4 是本 HEAD 上同值的显式复核基线，不是改旧数字。
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

# P3I 价格基线（2026-11-04）：把供需模型接进 `Content.item_price()` 后，成交金额随
# 日供需曲线与库存压力变化，但事件数仍为 791（调度与前置未被打破）。
# 旧 P3I 基线（791 events / sell 1133 / buy 852）保留不删不改值，两个旧测试显式 skip；
# 本组为“价格接入”后的显式迁移基线。
P3I_PRICE_BASELINE_REASON = (
    "P3I 物价接线（item_price 叠加日供需 + 库存压力，±20% 护栏）后成交金额变化；"
    "事件数仍为 791，state/event 哈希与经济流量显式漂移；旧 P3I 基线原样保留。"
)
GOLDEN_P3I_PRICE = {
    "events_total": 791,
    "state_final": "86871fafdf16f72d8b9ad045d00b07ceb17358367f500f51080f94c0deb86974",
    "event_final": "ee7d0a90b5bdb6598f66d974f763e12764e506e5336f17b5f5cb43ad46df3e69",
    "sell_in": 1145,
    "buy_out": 842,
    "burned": 110,
    "business_cash": 11285,
    "precondition_blocked": 9,
    "precondition_pruned": 2,
}
DIALOGUE_GOLDEN_P3I_PRICE = {
    "events_total": 791,
    "state_final": "177904390adb8571ab6bb851872346cc17ab624bea6e9d80abd6d3e75bc006bf",
    "event_final": "a82c516a44932e918fab98045014833bd578f955aa8e45d313e68f35f3e9640c",
    "dialogue_calls": 63,
}

# 目标体量校准基线（2026-11-04，非静默改旧基线）：
# 10 天规则臂诊断显示 target=600/0.6/40 时 earn_money/advance_skill/build_relation
# 完成率≈0；按 10 天有效产能 P60×0.6-0.8 校准为 142/0.033/12 后，K1 49.45%→65.93%，
# 三类完成率 30.8%/31.3%/50.0%。目标值会写入 goal_create 事件与状态，2 天世界必然漂移
# （791→796 events、经济流量与哈希同步变化），故旧 P3I_PRICE 基线值原样保留并 skip。
CALIB_BASELINE_REASON = (
    "目标体量校准（wealth 600→142、mastery 0.6→0.033、bond 40→12）后，"
    "goal_create 的 target 与 NPC 目标轮转时点改变，2 天世界从 791 漂移到 796 events；"
    "旧 P3I_PRICE 基线原样保留，本组为显式迁移基线。"
)
GOLDEN_CALIB = {
    "events_total": 796,
    "state_final": "55ac3ed8bde6182714e0b9576880c04eaed4296e2a04a079fb60a92c2e537db8",
    "event_final": "344b501a5969cef1e518e36dd2b448c0f08145f8468fc621269c4c260f6fedb2",
    "sell_in": 971,
    "buy_out": 820,
    "burned": 127,
    "business_cash": 12184,
    "precondition_blocked": 11,
    "precondition_pruned": 2,
}
DIALOGUE_GOLDEN_CALIB = {
    "events_total": 796,
    "state_final": "139a879c30680314ddb93a026fc107ef9cb7609dbe264a47dadd205ed0bbf8c4",
    "event_final": "bcd166d00d546c5f8c3aee69e661ca926680f3494cb3ee275f9194024717ae98",
    "dialogue_calls": 63,
}

# P4 里程碑 / 维护基线（2026-11-04，非静默改旧基线）：
# 新增两个系统日界动作 system_milestone / system_maintenance，每个日界各发 1 条意图，
# 2 天世界因此多出 4 条事件（796→800）；系统动作不改 NPC 的既有买卖与对话路径，
# 经济流量（sell 971 / buy 820 / burned 127 / business_cash 12184）与拦截计数完全不变，
# 仅 state/event 哈希与事件数显式漂移。旧 GOLDEN_CALIB / DIALOGUE_GOLDEN_CALIB
# 原样保留，对应旧测试显式 skip；新基线见 GOLDEN_MILESTONE / DIALOGUE_GOLDEN_MILESTONE。
MILESTONE_BASELINE_REASON = (
    "新增 system_milestone / system_maintenance 日界动作（2 天共 4 条系统意图），"
    "事件数 796→800，state/event 哈希漂移；经济流量与前置拦截计数不变，"
    "旧 GOLDEN_CALIB 基线原样保留，本组为显式迁移基线。"
)
GOLDEN_MILESTONE = {
    "events_total": 800,
    "state_final": "a155665368313c1703ebcb5b1e4cdf5110a5890928918b23a4a6e725030428a0",
    "event_final": "61b1531a03bb79b7c6548b87fb8f5e161d9ffc511dd13e8276419d936e7ac664",
    "sell_in": 971,
    "buy_out": 820,
    "burned": 127,
    "business_cash": 12184,
    "precondition_blocked": 11,
    "precondition_pruned": 2,
}
DIALOGUE_GOLDEN_MILESTONE = {
    "events_total": 800,
    "state_final": "ec1b2d5a1564b2d30a751d7e91bbd55a9b1df36be47c9183709bd5aa291de88f",
    "event_final": "741f98f3ce37f1457ee6d95b519081167eb01c253fa09148a4cdd77b92ccec95",
    "dialogue_calls": 63,
}

# P4 里程碑基线 v2（2026-11-04，self-goal 改造后，非静默改旧基线）：
# `goal_create` effect 新增 `secondary` 字段（self-goal 改造引入），仅事件 payload 变化；
# 实跑两次一致：800 events、state hash 与旧基线相同、经济流量（sell 971 / buy 820 /
# burned 127 / business_cash 12184）与拦截计数（blocked 11 / pruned 2）逐项不变，
# 只有 event hash 漂移。旧 GOLDEN_MILESTONE 原样保留并显式 skip，本组为显式迁移基线。
SECONDARY_BASELINE_REASON = (
    "goal_create 的 effect payload 新增 secondary 字段（self-goal 改造），"
    "只影响事件日志序列化；事件数仍为 800、state hash 不变、"
    "经济流量与前置拦截计数逐项相同，仅 event hash 显式漂移。"
    "旧 GOLDEN_MILESTONE 基线原样保留。"
)
GOLDEN_MILESTONE_V2 = {
    "events_total": 800,
    "state_final": "a155665368313c1703ebcb5b1e4cdf5110a5890928918b23a4a6e725030428a0",
    "event_final": "72dd62c2923ebec867398bfba2f4cb304af8c59d66570bd3539b0991e93a24ce",
    "sell_in": 971,
    "buy_out": 820,
    "burned": 127,
    "business_cash": 12184,
    "precondition_blocked": 11,
    "precondition_pruned": 2,
}
DIALOGUE_GOLDEN_MILESTONE_V2 = {
    "events_total": 800,
    "state_final": "ec1b2d5a1564b2d30a751d7e91bbd55a9b1df36be47c9183709bd5aa291de88f",
    "event_final": "fa22d38584f600eec958cf2e65527e56fa51f8b47056112ec1597f710634a8d3",
    "dialogue_calls": 63,
}

# P4 里程碑基线 v3（2026-11-05，任务公平轮转 + 接单提前，非静默改旧基线）：
# 接单顺序改为“模板最久未服务优先”，并把任务步骤移到上工之前；2 天世界里 NPC
# 一日行动顺序随之改变，800→693 events，经济流量与拦截计数同步漂移。
# 旧 V2 基线原样保留并显式 skip，本组为显式迁移基线。
ROTATION_BASELINE_REASON = (
    "任务公平轮转 + 接单提前改变了 2 天世界的 NPC 行动顺序："
    "事件数 800→693，经济流量（sell 971→967 / buy 820→793 / burned 127→81 / "
    "business_cash 12184→12599）与拦截计数（blocked 11→8 / pruned 2→4）显式漂移，"
    "state/event 哈希变化；旧 V2 基线原样保留。"
)
GOLDEN_MILESTONE_V3 = {
    "events_total": 693,
    "state_final": "a8aecf44c5dc845782eefda10789ac3a937424438720d56ef72cfeb29035efe4",
    "event_final": "ba66bb4db27f974b2f81833399bc40ed312e1a4e752da38c83efa3c087a8833d",
    "sell_in": 967,
    "buy_out": 793,
    "burned": 81,
    "business_cash": 12599,
    "precondition_blocked": 8,
    "precondition_pruned": 4,
}
DIALOGUE_GOLDEN_MILESTONE_V3 = {
    "events_total": 693,
    "state_final": "22d415077f26873dcb1fd26c1b94ac4efd3ba8b8035ce43ce8c0fefddcee01b9",
    "event_final": "b7ded604509b24579eab66771c88f4bdfad9d049fc827994dbb6b07f6472049e",
    "dialogue_calls": 65,
}

# P3F 里程碑基线 v4（2026-11-05，玩家消费类动作开闸，非静默改旧基线）：
# 只改 API 入口/只读 state，2 天 headless NPC 行为不变；实跑与 V3 逐项相同（零漂移）。
PLAYER_COMMERCE_BASELINE_REASON = (
    "玩家消费类动作开闸只改 API 入口与只读 state，不改 NPC 动作前置、调度与 content；"
    "2 天 headless 世界实跑与 V3 逐项相同（693 events、经济流量/拦截计数/状态哈希零漂移），"
    "V4 为同 HEAD 的显式复核基线，旧 V3 数值原样保留。"
)
GOLDEN_MILESTONE_V4 = {
    "events_total": 693,
    "state_final": "a8aecf44c5dc845782eefda10789ac3a937424438720d56ef72cfeb29035efe4",
    "event_final": "ba66bb4db27f974b2f81833399bc40ed312e1a4e752da38c83efa3c087a8833d",
    "sell_in": 967,
    "buy_out": 793,
    "burned": 81,
    "business_cash": 12599,
    "precondition_blocked": 8,
    "precondition_pruned": 4,
}
DIALOGUE_GOLDEN_MILESTONE_V4 = {
    "events_total": 693,
    "state_final": "22d415077f26873dcb1fd26c1b94ac4efd3ba8b8035ce43ce8c0fefddcee01b9",
    "event_final": "b7ded604509b24579eab66771c88f4bdfad9d049fc827994dbb6b07f6472049e",
    "dialogue_calls": 65,
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


@pytest.mark.skip(reason="P3I 物价接线后成交金额漂移；迁移到 GOLDEN_P3I_PRICE，见文件头说明")
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


@pytest.mark.skip(reason="P3I 物价接线后哈希漂移；迁移到 DIALOGUE_GOLDEN_P3I_PRICE，见文件头说明")
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


@pytest.mark.skip(reason="目标体量校准后 goal_create/状态哈希漂移；迁移到 GOLDEN_CALIB，见文件头说明")
def test_p3i_price_deterministic_2d_fake_run_is_golden(tmp_path):
    """P3I 物价接线后的新基线：事件流不变（791），金额与哈希显式迁移。"""
    summary = _run_two_day(tmp_path, "golden-price", dialogue=False)
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
    drift = {k: (GOLDEN_P3I_PRICE[k], got[k])
             for k in GOLDEN_P3I_PRICE if got[k] != GOLDEN_P3I_PRICE[k]}
    assert not drift, (
        "P3I 价格基线漂移（golden_p3i_price, actual）："
        + json.dumps(drift, ensure_ascii=False)
        + "；原因: " + P3I_PRICE_BASELINE_REASON
    )

    pruned_file = tmp_path / "golden-price" / "pruned.jsonl"
    blocked_file = tmp_path / "golden-price" / "blocked.jsonl"
    assert pruned_file.exists() and blocked_file.exists(), "缺少全量拦截记录导出"
    assert len(pruned_file.read_text(encoding="utf-8").splitlines()) == GOLDEN_P3I_PRICE["precondition_pruned"]
    assert len(blocked_file.read_text(encoding="utf-8").splitlines()) == GOLDEN_P3I_PRICE["precondition_blocked"]


@pytest.mark.skip(reason="目标体量校准后 goal_create/状态哈希漂移；迁移到 DIALOGUE_GOLDEN_CALIB，见文件头说明")
def test_p3i_price_dialogue_enabled_fake_run_is_golden(tmp_path):
    """P3I 物价接线后的新基线（dialogue 开启路径）。"""
    summary = _run_two_day(tmp_path, "golden-price-dialogue", dialogue=True)
    got = {
        "events_total": summary["events"]["total"],
        "state_final": summary["hashes"]["state_final"],
        "event_final": summary["hashes"]["event_final"],
        "dialogue_calls": summary["llm"]["dialogue_async"]["calls"],
    }
    drift = {k: (DIALOGUE_GOLDEN_P3I_PRICE[k], got[k])
             for k in DIALOGUE_GOLDEN_P3I_PRICE if got[k] != DIALOGUE_GOLDEN_P3I_PRICE[k]}
    assert not drift, (
        "P3I 价格 dialogue 基线漂移（golden_p3i_price, actual）："
        + json.dumps(drift, ensure_ascii=False)
        + "；原因: " + P3I_PRICE_BASELINE_REASON
    )


@pytest.mark.skip(reason="P4 新增日界系统事件（800 events）后 796-event 世界不可复现；迁移到 GOLDEN_MILESTONE，见文件头说明")
def test_goal_calib_deterministic_2d_fake_run_is_golden(tmp_path):
    """目标体量校准后的新基线：2 天世界显式迁移到 796 events。"""
    summary = _run_two_day(tmp_path, "golden-calib", dialogue=False)
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
    drift = {k: (GOLDEN_CALIB[k], got[k]) for k in GOLDEN_CALIB if got[k] != GOLDEN_CALIB[k]}
    assert not drift, (
        "目标体量校准基线漂移（golden_calib, actual）："
        + json.dumps(drift, ensure_ascii=False)
        + "；原因: " + CALIB_BASELINE_REASON
    )

    pruned_file = tmp_path / "golden-calib" / "pruned.jsonl"
    blocked_file = tmp_path / "golden-calib" / "blocked.jsonl"
    assert pruned_file.exists() and blocked_file.exists(), "缺少全量拦截记录导出"
    assert len(pruned_file.read_text(encoding="utf-8").splitlines()) == GOLDEN_CALIB["precondition_pruned"]
    assert len(blocked_file.read_text(encoding="utf-8").splitlines()) == GOLDEN_CALIB["precondition_blocked"]


@pytest.mark.skip(reason="P4 新增日界系统事件（800 events）后 796-event 世界不可复现；迁移到 DIALOGUE_GOLDEN_MILESTONE，见文件头说明")
def test_goal_calib_dialogue_enabled_fake_run_is_golden(tmp_path):
    """目标体量校准后的新基线（dialogue 开启路径）。"""
    summary = _run_two_day(tmp_path, "golden-calib-dialogue", dialogue=True)
    got = {
        "events_total": summary["events"]["total"],
        "state_final": summary["hashes"]["state_final"],
        "event_final": summary["hashes"]["event_final"],
        "dialogue_calls": summary["llm"]["dialogue_async"]["calls"],
    }
    drift = {k: (DIALOGUE_GOLDEN_CALIB[k], got[k])
             for k in DIALOGUE_GOLDEN_CALIB if got[k] != DIALOGUE_GOLDEN_CALIB[k]}
    assert not drift, (
        "目标体量校准 dialogue 基线漂移（golden_calib_dialogue, actual）："
        + json.dumps(drift, ensure_ascii=False)
        + "；原因: " + CALIB_BASELINE_REASON
    )


@pytest.mark.skip(reason="self-goal 改造给 goal_create 加 secondary 字段后 event hash 漂移；迁移到 GOLDEN_MILESTONE_V2，见文件头说明")
def test_p4_milestone_deterministic_2d_fake_run_is_golden(tmp_path):
    """P4 旧基线（800 events 里程碑）：仅事件 payload 因 secondary 字段变化。"""
    summary = _run_two_day(tmp_path, "golden-milestone", dialogue=False)
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
    drift = {k: (GOLDEN_MILESTONE[k], got[k])
             for k in GOLDEN_MILESTONE if got[k] != GOLDEN_MILESTONE[k]}
    assert not drift, (
        "P4 里程碑基线漂移（golden_milestone, actual）："
        + json.dumps(drift, ensure_ascii=False)
        + "；原因: " + MILESTONE_BASELINE_REASON
    )

    pruned_file = tmp_path / "golden-milestone" / "pruned.jsonl"
    blocked_file = tmp_path / "golden-milestone" / "blocked.jsonl"
    assert pruned_file.exists() and blocked_file.exists(), "缺少全量拦截记录导出"
    assert len(pruned_file.read_text(encoding="utf-8").splitlines()) == GOLDEN_MILESTONE["precondition_pruned"]
    assert len(blocked_file.read_text(encoding="utf-8").splitlines()) == GOLDEN_MILESTONE["precondition_blocked"]


@pytest.mark.skip(reason="self-goal 改造给 goal_create 加 secondary 字段后 event hash 漂移；迁移到 DIALOGUE_GOLDEN_MILESTONE_V2，见文件头说明")
def test_p4_milestone_dialogue_enabled_fake_run_is_golden(tmp_path):
    """P4 旧基线（dialogue 开启路径）：仅事件 payload 因 secondary 字段变化。"""
    summary = _run_two_day(tmp_path, "golden-milestone-dialogue", dialogue=True)
    got = {
        "events_total": summary["events"]["total"],
        "state_final": summary["hashes"]["state_final"],
        "event_final": summary["hashes"]["event_final"],
        "dialogue_calls": summary["llm"]["dialogue_async"]["calls"],
    }
    drift = {k: (DIALOGUE_GOLDEN_MILESTONE[k], got[k])
             for k in DIALOGUE_GOLDEN_MILESTONE if got[k] != DIALOGUE_GOLDEN_MILESTONE[k]}
    assert not drift, (
        "P4 里程碑 dialogue 基线漂移（golden_milestone_dialogue, actual）："
        + json.dumps(drift, ensure_ascii=False)
        + "；原因: " + MILESTONE_BASELINE_REASON
    )


@pytest.mark.skip(reason="任务公平轮转 + 接单提前后行动顺序漂移（800→693 events）；迁移到 GOLDEN_MILESTONE_V3，见文件头说明")
def test_p4_milestone_v2_deterministic_2d_fake_run_is_golden(tmp_path):
    """P4 v2 基线：goal_create 带 secondary 字段（800 events，其余逐项不变）。"""
    summary = _run_two_day(tmp_path, "golden-milestone-v2", dialogue=False)
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
    drift = {k: (GOLDEN_MILESTONE_V2[k], got[k])
             for k in GOLDEN_MILESTONE_V2 if got[k] != GOLDEN_MILESTONE_V2[k]}
    assert not drift, (
        "P4 里程碑 v2 基线漂移（golden_milestone_v2, actual）："
        + json.dumps(drift, ensure_ascii=False)
        + "；原因: " + SECONDARY_BASELINE_REASON
    )

    pruned_file = tmp_path / "golden-milestone-v2" / "pruned.jsonl"
    blocked_file = tmp_path / "golden-milestone-v2" / "blocked.jsonl"
    assert pruned_file.exists() and blocked_file.exists(), "缺少全量拦截记录导出"
    assert len(pruned_file.read_text(encoding="utf-8").splitlines()) == GOLDEN_MILESTONE_V2["precondition_pruned"]
    assert len(blocked_file.read_text(encoding="utf-8").splitlines()) == GOLDEN_MILESTONE_V2["precondition_blocked"]


@pytest.mark.skip(reason="任务公平轮转 + 接单提前后行动顺序漂移（800→693 events）；迁移到 DIALOGUE_GOLDEN_MILESTONE_V3，见文件头说明")
def test_p4_milestone_v2_dialogue_enabled_fake_run_is_golden(tmp_path):
    """P4 v2 基线（dialogue 开启路径）：state 不变，仅 event hash 漂移。"""
    summary = _run_two_day(tmp_path, "golden-milestone-dialogue-v2", dialogue=True)
    got = {
        "events_total": summary["events"]["total"],
        "state_final": summary["hashes"]["state_final"],
        "event_final": summary["hashes"]["event_final"],
        "dialogue_calls": summary["llm"]["dialogue_async"]["calls"],
    }
    drift = {k: (DIALOGUE_GOLDEN_MILESTONE_V2[k], got[k])
             for k in DIALOGUE_GOLDEN_MILESTONE_V2 if got[k] != DIALOGUE_GOLDEN_MILESTONE_V2[k]}
    assert not drift, (
        "P4 里程碑 v2 dialogue 基线漂移（golden_milestone_dialogue_v2, actual）："
        + json.dumps(drift, ensure_ascii=False)
        + "；原因: " + SECONDARY_BASELINE_REASON
    )


@pytest.mark.skip(reason="玩家消费类动作开闸后基线迁移到 GOLDEN_MILESTONE_V4（同值零漂移复核），见文件头说明")
def test_p4_milestone_v3_deterministic_2d_fake_run_is_golden(tmp_path):
    """P4 v3 基线：任务公平轮转 + 接单提前后的 2 天世界（693 events）。"""
    summary = _run_two_day(tmp_path, "golden-milestone-v3", dialogue=False)
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
    drift = {k: (GOLDEN_MILESTONE_V3[k], got[k])
             for k in GOLDEN_MILESTONE_V3 if got[k] != GOLDEN_MILESTONE_V3[k]}
    assert not drift, (
        "P4 里程碑 v3 基线漂移（golden_milestone_v3, actual）："
        + json.dumps(drift, ensure_ascii=False)
        + "；原因: " + ROTATION_BASELINE_REASON
    )

    pruned_file = tmp_path / "golden-milestone-v3" / "pruned.jsonl"
    blocked_file = tmp_path / "golden-milestone-v3" / "blocked.jsonl"
    assert pruned_file.exists() and blocked_file.exists(), "缺少全量拦截记录导出"
    assert len(pruned_file.read_text(encoding="utf-8").splitlines()) == GOLDEN_MILESTONE_V3["precondition_pruned"]
    assert len(blocked_file.read_text(encoding="utf-8").splitlines()) == GOLDEN_MILESTONE_V3["precondition_blocked"]


@pytest.mark.skip(reason="玩家消费类动作开闸后 dialogue 基线迁移到 DIALOGUE_GOLDEN_MILESTONE_V4（同值零漂移复核），见文件头说明")
def test_p4_milestone_v3_dialogue_enabled_fake_run_is_golden(tmp_path):
    """P4 v3 基线（dialogue 开启路径）：事件数不变，state/event hash 显式漂移。"""
    summary = _run_two_day(tmp_path, "golden-milestone-dialogue-v3", dialogue=True)
    got = {
        "events_total": summary["events"]["total"],
        "state_final": summary["hashes"]["state_final"],
        "event_final": summary["hashes"]["event_final"],
        "dialogue_calls": summary["llm"]["dialogue_async"]["calls"],
    }
    drift = {k: (DIALOGUE_GOLDEN_MILESTONE_V3[k], got[k])
             for k in DIALOGUE_GOLDEN_MILESTONE_V3 if got[k] != DIALOGUE_GOLDEN_MILESTONE_V3[k]}
    assert not drift, (
        "P4 里程碑 v3 dialogue 基线漂移（golden_milestone_dialogue_v3, actual）："
        + json.dumps(drift, ensure_ascii=False)
        + "；原因: " + ROTATION_BASELINE_REASON
    )


def test_p4_milestone_v4_deterministic_2d_fake_run_is_golden(tmp_path):
    """P4 v4 基线：玩家消费类动作开闸后的 2 天世界（与 V3 同值，零漂移）。"""
    summary = _run_two_day(tmp_path, "golden-milestone-v4", dialogue=False)
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
    drift = {k: (GOLDEN_MILESTONE_V4[k], got[k])
             for k in GOLDEN_MILESTONE_V4 if got[k] != GOLDEN_MILESTONE_V4[k]}
    assert not drift, (
        "P4 里程碑 v4 基线漂移（golden_milestone_v4, actual）："
        + json.dumps(drift, ensure_ascii=False)
        + "；原因: " + PLAYER_COMMERCE_BASELINE_REASON
    )

    pruned_file = tmp_path / "golden-milestone-v4" / "pruned.jsonl"
    blocked_file = tmp_path / "golden-milestone-v4" / "blocked.jsonl"
    assert pruned_file.exists() and blocked_file.exists(), "缺少全量拦截记录导出"
    assert len(pruned_file.read_text(encoding="utf-8").splitlines()) == GOLDEN_MILESTONE_V4["precondition_pruned"]
    assert len(blocked_file.read_text(encoding="utf-8").splitlines()) == GOLDEN_MILESTONE_V4["precondition_blocked"]


def test_p4_milestone_v4_dialogue_enabled_fake_run_is_golden(tmp_path):
    """P4 v4 基线（dialogue 开启路径）：与 V3 同值（零漂移复核）。"""
    summary = _run_two_day(tmp_path, "golden-milestone-dialogue-v4", dialogue=True)
    got = {
        "events_total": summary["events"]["total"],
        "state_final": summary["hashes"]["state_final"],
        "event_final": summary["hashes"]["event_final"],
        "dialogue_calls": summary["llm"]["dialogue_async"]["calls"],
    }
    drift = {k: (DIALOGUE_GOLDEN_MILESTONE_V4[k], got[k])
             for k in DIALOGUE_GOLDEN_MILESTONE_V4 if got[k] != DIALOGUE_GOLDEN_MILESTONE_V4[k]}
    assert not drift, (
        "P4 里程碑 v4 dialogue 基线漂移（golden_milestone_dialogue_v4, actual）："
        + json.dumps(drift, ensure_ascii=False)
        + "；原因: " + PLAYER_COMMERCE_BASELINE_REASON
    )
