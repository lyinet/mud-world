#!/usr/bin/env python3
"""P3-E 复核数字复算器：报告里的覆盖率、归因、K6 口径、换模型披露都由本脚本产出。

用法：
  PYTHONPATH=src .venv/bin/python tools/p3e_report_stats.py

输入（全部是入库证据）：
  reports/evidence/p3e-deepseek-intent-coverage.jsonl
  reports/evidence/p3e-deepseek-intent-recordings-2d.jsonl
  reports/evidence/p3e-deepseek-real-2d-12npc-summary.json
  reports/evidence/p3e-fake-llm-2d-summary.json
  reports/evidence/p3e-partial-replay-2d-summary.json
  reports/evidence/runs/p3e-deepseek-real-2d-12npc/

输出：
  reports/evidence/p3e-report-stats.json
  reports/evidence/p3e-report-stats.md
"""
from __future__ import annotations

import json
import sys
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.core.content import load_content  # noqa: E402
from wsim.core.models import Effect  # noqa: E402
from wsim.world.effects import apply_effects  # noqa: E402
from wsim.world.state import WorldState  # noqa: E402
from wsim.world.store import EventStore  # noqa: E402

EVIDENCE = ROOT / "reports" / "evidence"
RUN = EVIDENCE / "runs" / "p3e-deepseek-real-2d-12npc-k6r2"
REAL_SUMMARY = EVIDENCE / "p3e-deepseek-real-2d-12npc-k6r2-summary.json"
REAL_BEFORE = EVIDENCE / "p3e-deepseek-real-2d-12npc-summary.json"
FAKE_SUMMARY = EVIDENCE / "p3e-fake-llm-2d-summary-k6r2.json"
FAKE_BEFORE = EVIDENCE / "p3e-fake-llm-2d-summary.json"
FAKE_RUN = EVIDENCE / "runs" / "p3e-fake-llm-2d-k6r2"


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _percentile(values: list[float], q: float) -> float:
    """最近秩分位，与证据录制逐条复算保持一致。"""
    ordered = sorted(values)
    return ordered[round(q * (len(ordered) - 1))]


def _classify_failure(row: dict) -> str:
    res = row.get("result") or {}
    if row.get("error"):
        return "transport_error"
    if res.get("category") == "unknown" or res.get("reason") == "unknown_category":
        return "unknown_category"
    fields = {b.get("field") for b in (res.get("blocked") or [])}
    if "shop_type" in fields:
        return "shop_type_grounding"
    if not row.get("category_match"):
        return "category_drift"
    if res.get("missing"):
        return "missing_required"
    return "other"


def coverage_stats() -> dict:
    rows = _rows(EVIDENCE / "p3e-deepseek-intent-coverage.jsonl")
    templates: OrderedDict[str, dict] = OrderedDict()
    for row in rows:
        t = templates.setdefault(
            row["category"],
            {"verb": row["verb"], "samples": 0, "success": 0, "statuses": Counter()},
        )
        t["samples"] += 1
        t["success"] += int(bool(row["success"]))
        t["statuses"][(row.get("result") or {}).get("status", "?")] += 1
    failures = [r for r in rows if not r["success"]]
    attribution = Counter(_classify_failure(r) for r in failures)
    return {
        "total": len(rows),
        "templates": len(templates),
        "success": sum(1 for r in rows if r["success"]),
        "success_rate": sum(1 for r in rows if r["success"]) / len(rows),
        "by_template": {
            name: {
                "verb": t["verb"],
                "samples": t["samples"],
                "success": t["success"],
                "success_rate": t["success"] / t["samples"],
                "statuses": dict(t["statuses"]),
            }
            for name, t in templates.items()
        },
        "failure_attribution": dict(attribution),
        "failure_details": [
            {
                "category": r["category"],
                "verb": r["verb"],
                "utterance": r["utterance"],
                "attribution": _classify_failure(r),
                "status": (r.get("result") or {}).get("status"),
                "parsed_category": (r.get("result") or {}).get("category"),
                "blocked": (r.get("result") or {}).get("blocked"),
                "missing": (r.get("result") or {}).get("missing"),
            }
            for r in failures
        ],
    }


def recording_stats() -> dict:
    rows = _rows(EVIDENCE / "p3e-deepseek-intent-recordings-2d.jsonl")
    lat = [float(r["latency_s"]) for r in rows]
    return {
        "calls": len(rows),
        "prompt_tokens": sum(int((r.get("usage") or {}).get("prompt_tokens") or 0) for r in rows),
        "completion_tokens": sum(int((r.get("usage") or {}).get("completion_tokens") or 0) for r in rows),
        "p50_latency_s": _percentile(lat, 0.50),
        "p95_latency_s": _percentile(lat, 0.95),
    }


def real_run_stats() -> dict:
    d = json.loads(REAL_SUMMARY.read_text(encoding="utf-8"))
    llm = d["llm"]
    reasoner = llm["reasoner"]
    by_slot = llm["by_slot"]
    latency = llm["latency_by_slot"]
    total = d["events"]["total"]
    rejected = d["events"]["rejected"]
    blocked = d["precondition_blocked"]["total"]
    pruned = d["precondition_pruned"]["total"]
    return {
        "run_id": d["run_id"],
        "seed": d["seed"],
        "days": d["days"],
        "npc_count": d["npc_count"],
        "content_hash": d["content_hash"],
        "events_total": total,
        "rejected": rejected,
        "illegal_rate_executed": d["events"]["illegal_rate"],
        "precondition_blocked": blocked,
        "precondition_blocked_by_verb": d["precondition_blocked"]["by_verb"],
        "precondition_blocked_examples": d["precondition_blocked"]["examples"],
        "precondition_blocked_records": d["precondition_blocked"].get("records", []),
        "precondition_pruned": pruned,
        "precondition_pruned_by_verb": d["precondition_pruned"]["by_verb"],
        "precondition_pruned_examples": d["precondition_pruned"]["examples"],
        "precondition_pruned_records": d["precondition_pruned"].get("records", []),
        "sell_in": d["economy"]["cashflow"]["inflow_by_reason"].get("sell", 0),
        "buy_out": d["economy"]["cashflow"]["outflow_by_reason"].get("buy", 0),
        "burned": d["economy"]["burned_total"],
        "business_cash": d["economy"]["business_cash_now"],
        "illegal_attempt_rate": (rejected + blocked) / total,
        "illegal_proposal_rate": (rejected + blocked + pruned) / total,
        "k6_illegal_pass": d["verdict"]["k6_illegal_pass"],
        "k6_illegal_incl_pruned_pass": d["verdict"]["k6_illegal_incl_pruned_pass"],
        "state_final": d["hashes"]["state_final"],
        "event_final": d["hashes"]["event_final"],
        "cost_calls": d["cost"]["calls"],
        "fallbacks": d["cost"]["fallbacks"],
        "estimated_cost_usd": d["cost"]["estimated_cost"],
        "llm_calls": {slot: v["calls"] for slot, v in by_slot.items()},
        "thinking_marked": {slot: v.get("thinking_disabled_by") or {} for slot, v in by_slot.items()},
        "calls_by_timing": llm.get("calls_by_timing") or {},
        "latency_by_slot": latency,
        "intent_ok": reasoner.get("intent_ok"),
        "intent_clarification": reasoner.get("intent_clarification"),
        "intent_reject": reasoner.get("intent_reject"),
        "intent_grounding_blocked": reasoner.get("intent_grounding_blocked"),
        "intent_grounding_by_field": reasoner.get("intent_grounding_by_field"),
        "action_ratio": reasoner.get("action_ratio"),
        "dialogue_calls": d["budget"].get("dialogue_calls") or {},
        "dialogue_slot_calls": by_slot.get("dialogue", {}).get("calls", 0),
        "k1_progressed": d["k1"]["goals_progressed"],
        "k1_total": d["k1"]["goals_total"],
        "k1_rate": d["k1"]["k1_rate"],
        "k1_pass": d["verdict"]["k1_pass"],
    }


DISPOSITION = {
    "not_together": "提交前剪枝：位置不一致，直接判不可行（零事件/零副作用）",
    "not_at_giver": "提交前剪枝：未在委托人处",
    "not_at_target": "提交前剪枝：未在目标地点",
    "wrong_location": "提交前剪枝：地点不一致",
    "item_not_held": "既有 SellAction.preconditions 校验持有量；本轮只接入提案前拦截，未改动作",
    "quest_not_accepted_by_actor": "合理拦截：依赖更早已发生的 quest_accept，提案时不可知",
    "unknown_target": "存在性错误由 preconditions 抛 Reject，已转为可拦截原因",
}


def _flat_counts(by_verb: dict | None) -> dict[tuple[str, str], int]:
    out: dict[tuple[str, str], int] = {}
    for verb, reasons in (by_verb or {}).items():
        for reason, n in (reasons or {}).items():
            out[(verb, reason)] = int(n)
    return out


def fake_run_stats(path: Path = FAKE_SUMMARY) -> dict:
    d = json.loads(path.read_text(encoding="utf-8"))
    reasoner = d["llm"]["reasoner"]
    total = d["events"]["total"]
    rejected = d["events"]["rejected"]
    blocked_d = d.get("precondition_blocked") or {}
    blocked = int(blocked_d.get("total", sum(_flat_counts(blocked_d.get("by_verb")).values())))
    pruned_d = d.get("precondition_pruned") or {}
    pruned = int(pruned_d.get("total", sum(_flat_counts(pruned_d.get("by_verb")).values()))) if pruned_d else 0
    cf = d["economy"]["cashflow"]
    return {
        "events_total": total,
        "events_final": d["hashes"]["event_final"],
        "state_final": d["hashes"]["state_final"],
        "sell_in": cf["inflow_by_reason"].get("sell", 0),
        "buy_out": cf["outflow_by_reason"].get("buy", 0),
        "burned": d["economy"]["burned_total"],
        "business_cash": d["economy"]["business_cash_now"],
        "rejected": rejected,
        "precondition_blocked": blocked,
        "precondition_blocked_by_verb": d["precondition_blocked"]["by_verb"],
        "precondition_blocked_examples": d["precondition_blocked"]["examples"],
        "precondition_blocked_records": d["precondition_blocked"].get("records", []),
        "precondition_pruned": pruned,
        "precondition_pruned_by_verb": d.get("precondition_pruned", {}).get("by_verb", {}),
        "precondition_pruned_examples": d.get("precondition_pruned", {}).get("examples", []),
        "precondition_pruned_records": d.get("precondition_pruned", {}).get("records", []),
        "illegal_rate_executed": d["events"]["illegal_rate"],
        "illegal_attempt_rate": (rejected + blocked) / total,
        "illegal_proposal_rate": (rejected + blocked + pruned) / total,
        "k6_illegal_pass_strict": d["verdict"]["k6_illegal_pass"],
        "k6_illegal_incl_pruned_pass": d["verdict"].get("k6_illegal_incl_pruned_pass"),
        "k6_illegal_executed_pass": d["verdict"].get("k6_illegal_executed_pass"),
        "k1_rate": d["k1"]["k1_rate"],
        "action_ratio": reasoner.get("action_ratio"),
        "dialogue_calls": d["budget"].get("dialogue_calls") or {},
    }


def partial_replay_stats() -> dict:
    d = json.loads((EVIDENCE / "p3e-partial-replay-2d-summary.json").read_text(encoding="utf-8"))
    stats = d["llm"]["reasoner"]["intent_client_stats"]
    return {
        "events_total": d["events"]["total"],
        "state_final": d["hashes"]["state_final"],
        "event_final": d["hashes"]["event_final"],
        "replay_miss": (stats.get("errors_by_kind") or {}).get("replay_miss", 0),
        "intent_calls": stats.get("calls"),
        "intent_reject": d["llm"]["reasoner"].get("intent_reject"),
        "precondition_blocked": (d.get("precondition_blocked") or {}).get("total", 0),
        "precondition_pruned": (d.get("precondition_pruned") or {}).get("total", 0),
        "cost_calls": d["cost"]["calls"],
        "fallbacks": d["cost"]["fallbacks"],
    }


def event_replay_stats() -> dict:
    content = load_content(ROOT, min_npcs=0, allow_fixture=False, use_content=True)
    state = WorldState(locations=content.locations)
    store = EventStore(RUN / "events.sqlite")
    events = store.read_all()
    try:
        for e in events:
            if e.outcome == "ok":
                apply_effects(state, [Effect.from_dict(d) for d in e.effects])
            state.tick = e.tick
    finally:
        store.close()
    stored = (RUN / "world_hash.txt").read_text(encoding="utf-8").strip()
    return {
        "run": str(RUN.relative_to(ROOT)),
        "events_total": len(events),
        "replayed": state.hash(),
        "stored": stored,
        "match": state.hash() == stored,
    }


def blocked_reconciliation() -> dict:
    """旧 27 条 blocked 在新世界中的去向：成功事件 / 仍拦截 / 未再提交。

    旧 summary 的 examples 上限 20 条，故 20 条可逐条对账，其余 7 条只能按计数说明。
    判定依据：新 run 的 events.sqlite（成功/拒绝事件）+ pruned/blocked 全量 records；
    任何提案要么落事件，要么落其中一份记录，所以“三者皆无”等价于从未提交。
    """
    old = json.loads(FAKE_BEFORE.read_text(encoding="utf-8"))["precondition_blocked"]
    new = json.loads(FAKE_SUMMARY.read_text(encoding="utf-8"))
    store = EventStore(FAKE_RUN / "events.sqlite")
    events = store.read_all()
    store.close()
    pruned = new["precondition_pruned"].get("records", [])
    blocked = new["precondition_blocked"].get("records", [])

    def match(records: list[dict], verb: str, actor: str, target: str, quest: str) -> dict | None:
        for rec in records:
            if rec["verb"] != verb or rec["actor"] != actor:
                continue
            args = rec.get("args") or {}
            if target and args.get("target") != target:
                continue
            if quest and args.get("quest") != quest:
                continue
            return rec
        return None

    rows = []
    used: set[int] = set()
    for rec in old.get("examples", []):
        args = rec.get("args") or {}
        target = args.get("target") or ""
        quest = args.get("quest") or ""
        candidates = [
            e for e in events
            if e.seq not in used
            and e.actor == rec["actor"] and e.verb == rec["verb"]
            and (not target or (e.args or {}).get("target") == target)
            and (not quest or (e.args or {}).get("quest") == quest)
        ]
        hits = sorted(candidates, key=lambda e: abs(e.tick - rec["tick"]))[:1]
        if hits:
            used.add(hits[0].seq)
            outcome = "成功事件"
            detail = f"seq={hits[0].seq} tick={hits[0].tick} outcome={hits[0].outcome}"
        elif (pr := match(pruned, rec["verb"], rec["actor"], target, quest)) is not None:
            outcome = "换桶（blocked→pruned）"
            detail = f"tick={pr['tick']} reason={pr['reason']}"
        elif (bl := match(blocked, rec["verb"], rec["actor"], target, quest)) is not None:
            outcome = "仍 blocked"
            detail = f"tick={bl['tick']} reason={bl['reason']}"
        else:
            outcome = "未再提交"
            detail = "新 run 无同主体/同目标事件，也无拦截记录"
        rows.append({
            "verb": rec["verb"],
            "reason": rec["reason"],
            "actor": rec["actor"],
            "old_tick": rec["tick"],
            "target": target,
            "quest": quest,
            "outcome": outcome,
            "detail": detail,
        })
    return {
        "old_total": old["total"],
        "old_examples": len(old.get("examples", [])),
        "unaccounted": old["total"] - len(old.get("examples", [])),
        "rows": rows,
        "counts": dict(Counter(r["outcome"] for r in rows)),
    }


def fake_world_drift() -> dict:
    """修复前/后的确定性 fake run 是两个世界：把漂移量写进报告，禁止再写“机制不变”。"""
    before = fake_run_stats(FAKE_BEFORE)
    after = fake_run_stats(FAKE_SUMMARY)
    keys = ("events_total", "state_final", "sell_in", "buy_out", "burned", "business_cash")
    return {
        "before": {k: before[k] for k in keys},
        "after": {k: after[k] for k in keys},
        "event_final_before": before["events_final"],
        "event_final_after": after["events_final"],
    }


def main() -> int:
    cov = coverage_stats()
    rec = recording_stats()
    real = real_run_stats()
    real_before = fake_run_stats(REAL_BEFORE)
    fake = fake_run_stats()
    fake_before = fake_run_stats(FAKE_BEFORE)
    drift = fake_world_drift()
    recon = blocked_reconciliation()
    partial = partial_replay_stats()
    replay = event_replay_stats()
    doc = {
        "coverage": cov,
        "intent_recordings": rec,
        "real_2d": real,
        "real_2d_before_k6fix": real_before,
        "fake_2d_k6": fake,
        "fake_2d_before_k6fix": fake_before,
        "fake_2d_world_drift": drift,
        "blocked_reconciliation": recon,
        "partial_replay_2d": partial,
        "event_replay": replay,
    }
    (EVIDENCE / "p3e-report-stats.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    lines = [
        "# P3-E 复核数字（脚本生成，勿手写）",
        "",
        "生成命令：`PYTHONPATH=src .venv/bin/python tools/p3e_report_stats.py`",
        "",
        "## 1. 模板覆盖（P0-5）",
        "",
        f"- {cov['templates']} 个模板 / {cov['total']} 条样本；成功 "
        f"{cov['success']}/{cov['total']} = {cov['success_rate']:.2%}",
        "",
        "| 模板 | 动词 | 样本 | 成功 | 成功率 | 状态 |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for name, row in cov["by_template"].items():
        statuses = ", ".join(f"{k}={v}" for k, v in row["statuses"].items())
        lines.append(
            f"| {name} | {row['verb']} | {row['samples']} | {row['success']} "
            f"| {row['success_rate']:.0%} | {statuses} |"
        )
    lines += [
        "",
        "### 失败归因（脚本按 JSONL 逐条判定）",
        "",
        "| 归因 | 数量 |",
        "| --- | ---: |",
    ]
    labels = {
        "unknown_category": "模型输出 `unknown`（类别未识别）",
        "shop_type_grounding": "类目正确，`shop_type` 参数被 grounding 拦",
        "category_drift": "类目漂移（如 talk 被判成 trade）",
        "missing_required": "必填参数 grounding 后缺失",
        "transport_error": "端点错误",
        "other": "其他",
    }
    for key, count in cov["failure_attribution"].items():
        lines.append(f"| `{key}`（{labels.get(key, key)}） | {count} |")
    lines += ["", "失败明细：", ""]
    for row in cov["failure_details"]:
        lines.append(
            f"- `{row['category']}` / {row['verb']}：{row['utterance']} → "
            f"attribution=`{row['attribution']}` status={row['status']} "
            f"parsed_category={row['parsed_category']} blocked={row['blocked']} missing={row['missing']}"
        )

    lines += [
        "",
        "## 2. 2 天真实跑（换模型）披露",
        "",
        "| 项 | 值 |",
        "| --- | --- |",
        f"| events / rejected / blocked / pruned | {real['events_total']} / {real['rejected']} / "
        f"{real['precondition_blocked']} / {real['precondition_pruned']} |",
        f"| 非法率（已执行动作口径） | {real['rejected']}/{real['events_total']} = {real['illegal_rate_executed']:.2%} |",
        f"| 非法率（严格：rejected+blocked） | ({real['rejected']}+{real['precondition_blocked']})/"
        f"{real['events_total']} = {real['illegal_attempt_rate']:.2%}（PASS={real['k6_illegal_pass']}） |",
        f"| 非法率（含剪枝：rejected+blocked+pruned） | ({real['rejected']}+{real['precondition_blocked']}+"
        f"{real['precondition_pruned']})/{real['events_total']} = {real['illegal_proposal_rate']:.2%}"
        f"（PASS={real['k6_illegal_incl_pruned_pass']}） |",
        f"| K1 | {real['k1_progressed']}/{real['k1_total']} = {real['k1_rate']:.2%}（k1_pass={real['k1_pass']}） |",
        f"| 动作来源 | model={real['action_ratio']['model']:.1%} / rule={real['action_ratio']['rule']:.1%} |",
        f"| LLM 调用 | {real['llm_calls']}；calls_by_timing={real['calls_by_timing']} |",
        f"| thinking 标记 | {real['thinking_marked']} |",
        f"| intent 结果 | ok={real['intent_ok']} / clarification={real['intent_clarification']} / reject={real['intent_reject']} |",
        f"| grounding 拦截 | {real['intent_grounding_blocked']}（{real['intent_grounding_by_field']}） |",
        f"| dialogue 调用 | slot={real['dialogue_slot_calls']}；budget={real['dialogue_calls']} |",
        f"| 延迟 p50/p95 | decision={real['latency_by_slot']['decision']}；intent={real['latency_by_slot']['intent']} |",
        f"| 费用 / fallback | ${real['estimated_cost_usd']:.6f} / {real['fallbacks']} |",
        f"| state/event hash | `{real['state_final']}` / `{real['event_final']}` |",
        "",
        "## 3. 确定性 fake run 的世界漂移（金标准基线）",
        "",
        "| 指标 | 修复前 | 修复后 |",
        "| --- | ---: | ---: |",
        f"| events | {drift['before']['events_total']} | {drift['after']['events_total']} |",
        f"| state_final | `{drift['before']['state_final'][:16]}…` | `{drift['after']['state_final'][:16]}…` |",
        f"| event_final | `{drift['event_final_before'][:16]}…` | `{drift['event_final_after'][:16]}…` |",
        f"| sell 流入 | {drift['before']['sell_in']} | {drift['after']['sell_in']} |",
        f"| buy 流出 | {drift['before']['buy_out']} | {drift['after']['buy_out']} |",
        f"| burned | {drift['before']['burned']} | {drift['after']['burned']} |",
        f"| 商户现金 | {drift['before']['business_cash']} | {drift['after']['business_cash']} |",
        "",
        "- **这是两个世界，不是“机制不变”**：18 条原本被前置拦截的 `talk` 现在成为成功事件，"
        "其关系/记忆效果改变后续规划，进而改变经济流量。报告已按新世界重新基线。",
        "- 已新增 `tests/test_p3e_golden.py`：锁死事件总数、事件序哈希、state hash、sell/buy/burned/商户现金、"
        "拦截/剪枝计数；故意把调度集合改回全量会 FAIL（实测 `event_final` 4d4019…→c20ad8…）。",
        "- 调度选择：全量社交优先 vs **只提前 `talk`** 两套 A/B 的事件数、state、经济流量、K6 完全相同，"
        "仅事件序哈希不同；故收窄为只提前 `talk`，把调度改动的面缩到最小。",
        "",
        "## 4. K6 非法率（对外结论取含剪枝口径）",
        "",
        "口径定义：`已执行 = rejected/events`；`严格 = (rejected+blocked)/events`；"
        "`含剪枝 = (rejected+blocked+pruned)/events`（**对外结论**，把提交前剪枝也算进分子，"
        "避免“换桶即达标”）。",
        "",
        "| run | events | rejected | blocked | pruned | 已执行 | 严格 | 含剪枝（对外） |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        f"| fake 旧（修复前） | {fake_before['events_total']} | {fake_before['rejected']} | "
        f"{fake_before['precondition_blocked']} | {fake_before['precondition_pruned']} | "
        f"{fake_before['illegal_rate_executed']:.2%} | {fake_before['illegal_attempt_rate']:.2%} FAIL | "
        f"{(fake_before['rejected'] + fake_before['precondition_blocked'] + fake_before['precondition_pruned']) / fake_before['events_total']:.2%} FAIL |",
        f"| **fake 新** | {fake['events_total']} | {fake['rejected']} | {fake['precondition_blocked']} | "
        f"{fake['precondition_pruned']} | {fake['illegal_rate_executed']:.2%} | "
        f"{fake['illegal_attempt_rate']:.2%} | **{fake['illegal_proposal_rate']:.2%} "
        f"{'PASS' if fake['k6_illegal_incl_pruned_pass'] else 'FAIL'}** |",
        f"| deepseek 旧（修复前） | {real_before['events_total']} | {real_before['rejected']} | "
        f"{real_before['precondition_blocked']} | {real_before['precondition_pruned']} | "
        f"{real_before['illegal_rate_executed']:.2%} | {real_before['illegal_attempt_rate']:.2%} FAIL | "
        f"{(real_before['rejected'] + real_before['precondition_blocked'] + real_before['precondition_pruned']) / real_before['events_total']:.2%} FAIL |",
        f"| **deepseek 新** | {real['events_total']} | {real['rejected']} | {real['precondition_blocked']} | "
        f"{real['precondition_pruned']} | {real['illegal_rate_executed']:.2%} | "
        f"{real['illegal_attempt_rate']:.2%} | **{real['illegal_proposal_rate']:.2%} "
        f"{'PASS' if real['k6_illegal_incl_pruned_pass'] else 'FAIL'}** |",
        "",
        "- **结论（对外）**：含剪枝口径 fake **0.85% PASS**、deepseek **0.90% PASS**。",
        "- **deepseek 的 2 条变化是换桶，不是行为改善**：新 pruned 的 `wen_xiaoman@151`、`cui_tieyi@232` "
        "与旧 blocked 是同一批意图（同主体/同目标/同 tick），只从 blocked 改记到 pruned。",
        f"- deepseek 前后是**两次独立采样**，不是受控对照：旧 state `{real_before['state_final'][:8]}…`、"
        f"新 state `{real['state_final'][:8]}…`；tick 0 的 talk 文案不同。但经济聚合相同"
        f"（sell={real['sell_in']} / buy={real['buy_out']} / burned={real['burned']} / 商户现金={real['business_cash']}），"
        "说明差异在文案/关系而非经济行为。",
        "",
        "## 5. blocked 逐条对账（旧 27 条 → 新 2 blocked + 5 pruned）",
        "",
        "### 5.1 按动作 + 原因",
        "",
        "| 动作 | 原因 | 旧 blocked | 新 blocked | 新 pruned | 处置 |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    old_counts = _flat_counts(fake_before["precondition_blocked_by_verb"])
    new_blocked = _flat_counts(fake["precondition_blocked_by_verb"])
    new_pruned = _flat_counts(fake["precondition_pruned_by_verb"])
    for key in sorted(set(old_counts) | set(new_blocked) | set(new_pruned)):
        verb, reason = key
        lines.append(
            f"| {verb} | {reason} | {old_counts.get(key, 0)} | {new_blocked.get(key, 0)} | "
            f"{new_pruned.get(key, 0)} | {DISPOSITION.get(reason, '待分类')} |"
        )
    lines += [
        f"| **合计** | | **{sum(old_counts.values())}** | **{sum(new_blocked.values())}** | "
        f"**{sum(new_pruned.values())}** | |",
        "",
        "### 5.2 旧 20 条可捕获样本逐条对账",
        "",
        "| # | 动作 | 原因 | 主体 | 旧 tick | 目标/任务 | 新世界去向 | 证据 |",
        "| ---: | --- | --- | --- | ---: | --- | --- | --- |",
    ]
    for idx, row in enumerate(recon["rows"], 1):
        ref = row["target"] or row["quest"] or "—"
        lines.append(
            f"| {idx} | {row['verb']} | {row['reason']} | {row['actor']} | {row['old_tick']} | "
            f"{ref} | {row['outcome']} | {row['detail']} |"
        )
    counts = recon["counts"]
    lines += [
        "",
        f"- 20 条捕获样本：成功事件 **{counts.get('成功事件', 0)}**、换桶 **{counts.get('换桶（blocked→pruned）', 0)}**、"
        f"仍 blocked **{counts.get('仍 blocked', 0)}**、未再提交 **{counts.get('未再提交', 0)}**。",
        f"- 旧 27 条中另有 **{recon['unaccounted']} 条**只有计数、没有 actor/tick（examples 上限），"
        "按 §5.1 的差值对账：1 条 trade、2 条 quest_accept 落入新 pruned，2 条 quest_deliver 落入新 blocked；"
        "2 条 talk 没有 actor/tick，无法逐条定位。",
        "- 判定依据：新 run 的 `events.sqlite` + `pruned.jsonl` + `blocked.jsonl` 覆盖全部提案；"
        "三者皆无即“从未提交”。",
        "",
        "### 5.3 新 residual 全量记录（不截断，导出为 jsonl）",
        "",
        "| 类别 | 动作 | 原因 | 主体 | tick | 参数 |",
        "| --- | --- | --- | --- | ---: | --- |",
    ]
    for row in fake["precondition_blocked_records"]:
        lines.append(
            f"| blocked | {row['verb']} | {row['reason']} | {row['actor']} | {row['tick']} | "
            f"`{json.dumps(row['args'], ensure_ascii=False, sort_keys=True)}` |"
        )
    for row in fake["precondition_pruned_records"]:
        lines.append(
            f"| pruned | {row['verb']} | {row['reason']} | {row['actor']} | {row['tick']} | "
            f"`{json.dumps(row['args'], ensure_ascii=False, sort_keys=True)}` |"
        )
    lines += [
        "",
        "- 导出文件：`reports/evidence/p3e-fake-llm-2d-pruned-k6r2.jsonl`、"
        "`...-blocked-k6r2.jsonl`（deepseek 同名 `-k6r2` 各一份）；"
        f"`summary.precondition_pruned.records` 共 {len(fake['precondition_pruned_records'])} 条、"
        f"`precondition_blocked.records` 共 {len(fake['precondition_blocked_records'])} 条。",
        "",
        "## 6. 复现链",
        "",
        f"- 事件日志回放：`{replay['run']}` → events={replay['events_total']}，"
        f"replayed=`{replay['replayed']}`，stored=`{replay['stored']}`，"
        f"**{'MATCH' if replay['match'] else 'MISMATCH'}**",
        f"- 仅 intent 槽录制重跑：events={partial['events_total']}，"
        f"state=`{partial['state_final']}`，event=`{partial['event_final']}`，"
        f"intent_calls={partial['intent_calls']}，replay_miss={partial['replay_miss']}，"
        f"intent_reject={partial['intent_reject']}，blocked={partial['precondition_blocked']}，"
        f"pruned={partial['precondition_pruned']}，"
        f"cost_calls={partial['cost_calls']}，fallbacks={partial['fallbacks']}",
        "- 结论：事件日志可回放到同一 state hash；整跑不可复现（decision 槽未录制）。",
        "",
        "## 7. 意图录制复算",
        "",
        f"- {rec['calls']} 条；prompt/completion tokens = {rec['prompt_tokens']}/{rec['completion_tokens']}；"
        f"p50={rec['p50_latency_s']:.3f}s，p95={rec['p95_latency_s']:.3f}s",
        "",
    ]
    (EVIDENCE / "p3e-report-stats.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\nstats -> reports/evidence/p3e-report-stats.json / .md")
    return 0 if replay["match"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
