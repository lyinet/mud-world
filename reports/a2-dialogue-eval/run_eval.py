#!/usr/bin/env python3
"""A2 可复现实验：30 轮玩家对话（真实 dialogue 槽）+ 盲评包生成。

用法（在 world-sim 根目录）：
  LLM_API_KEY=... python3 reports/a2-dialogue-eval/run_eval.py

输出：
  runs/a2-dialogue-30/dialogue.jsonl     真实模型调用记录（含 prompt/usage/延迟）
  runs/a2-dialogue-30/eval_raw.jsonl     每轮：玩家输入 / 模型回复 / 模板回复 / 延迟
  reports/a2-dialogue-eval/blind_pairs.jsonl  匿名 A/B 对（供第二评审盲评）
  reports/a2-dialogue-eval/answer_key.jsonl   A/B 对应的来源（评分后再看）
"""
from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from wsim.agent.budget import BudgetGuard, BudgetLimits  # noqa: E402
from wsim.agent.cost import CostTelemetry, load_cost_rates  # noqa: E402
from wsim.agent.dialogue import compose_talk  # noqa: E402
from wsim.agent.dialogue_async import AsyncDialogueQueue  # noqa: E402
from wsim.agent.provider import (  # noqa: E402
    ProviderClient,
    ProviderConfig,
    load_model_slots,
    load_provider_settings,
)
from wsim.api.session import WorldSession  # noqa: E402

OUT_DIR = ROOT / "runs" / "a2-dialogue-30"
PKG_DIR = ROOT / "reports" / "a2-dialogue-eval"
TARGET = "duan_wudou"
ROUNDS = 30

MESSAGES = [
    "段大哥，今日肉价如何？",
    "听说北边不太平，你这边生意受影响吗？",
    "你这刀工是跟谁学的？",
    "最近镇上可有什么新鲜事？",
    "柳三娘做的绣品，你可买过？",
    "我初来乍到，哪里能找到落脚的地方？",
    "金掌柜为人如何？",
    "你这铺子一天能卖多少肉？",
    "若我想学门手艺，你有什么建议？",
    "最近可有外乡人经过？",
    "你觉得邢捕快办案公道吗？",
    "这镇上谁最有钱？",
    "书院里那位谢师爷，你认识吗？",
    "肉价为何比上月贵了？",
    "你家里还有些什么人？",
    "若有人赊账，你怎么办？",
    "夜里市场可安全？",
    "你平日里除了卖肉还做什么？",
    "听说有人在招人入伙，你知道吗？",
    "我想盘个铺子，要多少本钱？",
    "你觉得这镇子以后会变成什么样？",
    "你那把刀看着有些年头了。",
    "今日可有新鲜的猪下水？",
    "你和孟九娘熟吗？",
    "若是官府加税，你怕不怕？",
    "我请你喝碗酒，肯赏脸吗？",
    "你觉得读书人靠得住吗？",
    "最近夜里可有怪声？",
    "我想买半斤瘦肉，怎么切最好？",
    "最后一个问题：你信命吗？",
]


def _dialogue_client() -> tuple[ProviderClient, dict]:
    settings = load_provider_settings(ROOT)
    slots = load_model_slots(ROOT)
    s = dict(slots.get("dialogue") or settings)
    cfg = ProviderConfig.from_env(
        True,
        timeout_s=float(s.get("timeout_s", 60)),
        max_retries=int(s.get("max_retries", 2)),
        backoff_base_s=float(s.get("backoff_base_s", 1.0)),
        max_tokens=int(s.get("max_tokens", 4096)),
        temperature=float(s.get("temperature", 0.8)),
        base_url=str(s.get("base_url", "")),
        model=str(s.get("model", "")),
        key_env=str(s.get("api_key_env", "LLM_API_KEY")),
        json_mode=bool(s.get("json_mode", False)),
        slot="dialogue",
        model_env=str(s.get("model_env", "")),
        extra_body=list(s.get("extra_body") or []),
    )
    assert cfg is not None, "dialogue 槽配置缺失"
    return ProviderClient(cfg), s


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    PKG_DIR.mkdir(parents=True, exist_ok=True)
    client, _slot = _dialogue_client()
    rates = load_cost_rates(ROOT)
    telemetry = CostTelemetry(rates=rates)
    budget = BudgetGuard(
        BudgetLimits.from_dict(None),
        input_rate=float(rates.per_1k_input_tokens) / 1000.0,
        output_rate=float(rates.per_1k_output_tokens) / 1000.0,
    )
    queue = AsyncDialogueQueue(
        client, budget=budget, log_dir=OUT_DIR, telemetry=telemetry, tier="active"
    )

    session = WorldSession(
        ROOT,
        run_id="a2-dialogue-30",
        seed=42,
        npc_limit=12,
        tick_step=6,
    )
    session.registry.get("talk").dialogue = queue
    enter = session.perform("u_demo", "enter", {})
    if not enter.get("ok"):
        print(json.dumps(enter, ensure_ascii=False))
        return 1

    rows: list[dict] = []
    failures = 0
    for i, message in enumerate(MESSAGES, 1):
        tick = int(session.state.tick)
        resp = session.perform(
            "u_demo", "talk", {"target": TARGET, "message": message}
        )
        if not resp.get("ok"):
            failures += 1
        rows.append(
            {
                "round": i,
                "tick": tick,
                "message": message,
                "ok": bool(resp.get("ok")),
                "reject_reason": resp.get("reject_reason", ""),
            }
        )
    queue.close()

    # 模型回复按 tick 对齐（每次 talk 的 tick 唯一）。
    model_by_tick: dict[int, dict] = {}
    log_path = OUT_DIR / "dialogue.jsonl"
    if log_path.is_file():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                model_by_tick[int(rec["tick"])] = rec

    persona = session.content.personas.get(TARGET, "")
    target = session.state.entities[TARGET]
    raw_path = OUT_DIR / "eval_raw.jsonl"
    pairs_path = PKG_DIR / "blind_pairs.jsonl"
    key_path = PKG_DIR / "answer_key.jsonl"
    enriched: list[dict] = []
    rng = random.Random(20261104)
    with raw_path.open("w", encoding="utf-8") as raw_fh, pairs_path.open(
        "w", encoding="utf-8"
    ) as pair_fh, key_path.open("w", encoding="utf-8") as key_fh:
        for row in rows:
            rec = model_by_tick.get(int(row["tick"]))
            model_reply = str(rec.get("text") or "") if rec else ""
            template_reply = str(
                compose_talk(
                    persona,
                    TARGET,
                    target.name,
                    target.role,
                    row["message"],
                    int(row["tick"]),
                )["reply"]
            )
            out = dict(row)
            out.update(
                {
                    "model_reply": model_reply,
                    "template_reply": template_reply,
                    "model_ok": bool(rec and rec.get("ok")),
                    "fallback": bool(rec and rec.get("fallback")),
                    "fallback_reason": str(rec.get("fallback_reason") or "") if rec else "no_call",
                    "latency_s": float(rec.get("latency_s") or 0.0) if rec else 0.0,
                    "input_tokens": int(rec.get("input_tokens") or 0) if rec else 0,
                    "output_tokens": int(rec.get("output_tokens") or 0) if rec else 0,
                }
            )
            raw_fh.write(json.dumps(out, ensure_ascii=False, sort_keys=True) + "\n")
            enriched.append(out)
            model_is_a = rng.random() < 0.5
            a, b = (model_reply, template_reply) if model_is_a else (template_reply, model_reply)
            pair_fh.write(
                json.dumps(
                    {"id": row["round"], "prompt": row["message"], "A": a, "B": b},
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
            key_fh.write(
                json.dumps(
                    {"id": row["round"], "model": "A" if model_is_a else "B"},
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )

    stats = queue.stats()
    latencies = sorted(r["latency_s"] for r in enriched if r["latency_s"] > 0)
    if latencies:
        idx = min(len(latencies) - 1, int(round(0.95 * (len(latencies) - 1))))
        p95 = latencies[idx]
    else:
        p95 = 0.0
    calls = int(stats["calls"])
    ok = int(stats["ok"])
    result = {
        "rounds": ROUNDS,
        "talk_failures": failures,
        "dialogue_calls": calls,
        "dialogue_ok": ok,
        "success_rate": round(ok / calls, 4) if calls else 0.0,
        "p95_latency_s": round(float(p95), 3),
        "max_latency_s": round(max(latencies), 3) if latencies else 0.0,
        "dialogue_cost_usd": round(float(stats["estimated_cost"]), 6),
        "input_tokens": int(stats["input_tokens"]),
        "output_tokens": int(stats["output_tokens"]),
        "fallback": int(stats["fallback"]),
        "fallback_by_reason": stats["fallback_by_reason"],
        "budget": budget.summary(),
    }
    (OUT_DIR / "eval_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
