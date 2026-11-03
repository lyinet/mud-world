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
RUN = EVIDENCE / "runs" / "p3e-deepseek-real-2d-12npc-k6fix"
REAL_SUMMARY = EVIDENCE / "p3e-deepseek-real-2d-12npc-k6fix-summary.json"
REAL_BEFORE = EVIDENCE / "p3e-deepseek-real-2d-12npc-summary.json"
FAKE_SUMMARY = EVIDENCE / "p3e-fake-llm-2d-summary-k6fix.json"
FAKE_BEFORE = EVIDENCE / "p3e-fake-llm-2d-summary.json"


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
        "precondition_pruned": pruned,
        "precondition_pruned_by_verb": d["precondition_pruned"]["by_verb"],
        "precondition_pruned_examples": d["precondition_pruned"]["examples"],
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
    "item_not_held": "可由 sell 持有量前置校验消除",
    "quest_not_accepted_by_actor": "合理拦截：前置不可知（依赖当日规划是否成功接取）",
    "unknown_target": "可由存在性前置校验消除",
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
    return {
        "events_total": total,
        "rejected": rejected,
        "precondition_blocked": blocked,
        "precondition_blocked_by_verb": d["precondition_blocked"]["by_verb"],
        "precondition_blocked_examples": d["precondition_blocked"]["examples"],
        "precondition_pruned": pruned,
        "precondition_pruned_by_verb": d.get("precondition_pruned", {}).get("by_verb", {}),
        "precondition_pruned_examples": d.get("precondition_pruned", {}).get("examples", []),
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


def main() -> int:
    cov = coverage_stats()
    rec = recording_stats()
    real = real_run_stats()
    real_before = fake_run_stats(REAL_BEFORE)
    fake = fake_run_stats()
    fake_before = fake_run_stats(FAKE_BEFORE)
    partial = partial_replay_stats()
    replay = event_replay_stats()
    doc = {
        "coverage": cov,
        "intent_recordings": rec,
        "real_2d": real,
        "real_2d_before_k6fix": real_before,
        "fake_2d_k6": fake,
        "fake_2d_before_k6fix": fake_before,
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
        "## 3. K6 非法率（两套 2 天 run，三种口径）",
        "",
        "口径定义：`已执行 = rejected/events`；`严格 = (rejected+blocked)/events`（结论口径）；"
        "`含剪枝 = (rejected+blocked+pruned)/events`（防止“把违规移出指标”的质疑）。",
        "",
        "| run | events | rejected | blocked | pruned | 已执行 | 严格 | 含剪枝 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        f"| fake 旧（修复前） | {fake_before['events_total']} | {fake_before['rejected']} | "
        f"{fake_before['precondition_blocked']} | {fake_before['precondition_pruned']} | "
        f"{fake_before['illegal_rate_executed']:.2%} | {fake_before['illegal_attempt_rate']:.2%} FAIL | n/a |",
        f"| fake 新（修复后） | {fake['events_total']} | {fake['rejected']} | {fake['precondition_blocked']} | "
        f"{fake['precondition_pruned']} | {fake['illegal_rate_executed']:.2%} | "
        f"{fake['illegal_attempt_rate']:.2%} {'PASS' if fake['k6_illegal_pass_strict'] else 'FAIL'} | "
        f"{fake['illegal_proposal_rate']:.2%} {'PASS' if fake['k6_illegal_incl_pruned_pass'] else 'FAIL'} |",
        f"| deepseek 旧（修复前） | {real_before['events_total']} | {real_before['rejected']} | "
        f"{real_before['precondition_blocked']} | {real_before['precondition_pruned']} | "
        f"{real_before['illegal_rate_executed']:.2%} | {real_before['illegal_attempt_rate']:.2%} FAIL | n/a |",
        f"| deepseek 新（修复后） | {real['events_total']} | {real['rejected']} | {real['precondition_blocked']} | "
        f"{real['precondition_pruned']} | {real['illegal_rate_executed']:.2%} | "
        f"{real['illegal_attempt_rate']:.2%} {'PASS' if real['k6_illegal_pass'] else 'FAIL'} | "
        f"{real['illegal_proposal_rate']:.2%} {'PASS' if real['k6_illegal_incl_pruned_pass'] else 'FAIL'} |",
        "",
        "## 4. blocked 逐条分布（旧 27 条 → 新 residual）",
        "",
        "### 4.1 修复前后按动作 + 原因对比",
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
        "### 4.2 旧 27 条逐条明细（summary 的 examples 上限 20 条）",
        "",
        "| # | 动作 | 原因 | actor | tick | source |",
        "| ---: | --- | --- | --- | ---: | --- |",
    ]
    for idx, row in enumerate(fake_before["precondition_blocked_examples"], 1):
        lines.append(
            f"| {idx} | {row['verb']} | {row['reason']} | {row['actor']} | {row['tick']} | {row['source']} |"
        )
    lines += [
        "",
        f"剩余 {sum(old_counts.values()) - len(fake_before['precondition_blocked_examples'])} 条未落入 examples 上限，"
        "按上表计数分布统计。",
        "",
        "### 4.3 新 residual 逐条明细",
        "",
        "| 类别 | # | 动作 | 原因 | actor | tick | source |",
        "| --- | ---: | --- | --- | --- | ---: | --- |",
    ]
    for idx, row in enumerate(fake["precondition_blocked_examples"], 1):
        lines.append(
            f"| blocked | {idx} | {row['verb']} | {row['reason']} | {row['actor']} | {row['tick']} | {row['source']} |"
        )
    for idx, row in enumerate(fake["precondition_pruned_examples"], 1):
        lines.append(
            f"| pruned | {idx} | {row['verb']} | {row['reason']} | {row['actor']} | {row['tick']} | {row['source']} |"
        )
    lines += [
        "",
        "## 5. 复现链",
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
        "## 6. 意图录制复算",
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
