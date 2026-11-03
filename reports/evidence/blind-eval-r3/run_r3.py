#!/usr/bin/env python3
"""R3：40 条真实模型回复 + 70 对合并盲评包。

用法（在 world-sim 根目录，必须显式传 key；本脚本不会写 key）：
  LLM_API_KEY=... .venv/bin/python reports/evidence/blind-eval-r3/run_r3.py

  # 仅从已落盘的 model-replies.jsonl 重建包（不调用模型）
  .venv/bin/python reports/evidence/blind-eval-r3/run_r3.py --package-only

约束：
  - 单次调用，max_retries=0，不重试不重采样；
  - 任一次调用后 fallback > 3 或累计成本 > $0.30 立即停止，不构建盲评包；
  - 不改任何引擎代码，只写 reports/evidence/blind-eval-r3/ 下的证据文件。
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import sys
import tempfile
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
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
from wsim.core.content import load_content  # noqa: E402

PKG_DIR = Path(__file__).resolve().parent
INPUTS = PKG_DIR / "inputs-40.jsonl"
MODEL_REPLIES = PKG_DIR / "model-replies.jsonl"
PAIRS = PKG_DIR / "pairs.jsonl"
ANSWER_KEY = PKG_DIR / "answer_key.jsonl"
CHECKS = PKG_DIR / "checks.json"
R2_DIR = PKG_DIR.parent / "blind-eval-r2"

TARGET = "duan_wudou"
TICK_STEP = 6
MAX_FALLBACK = 3
COST_CAP = 0.30
SEED = 20261104
POSITION_SEED = 20261104


def _load_inputs() -> list[dict]:
    rows = [
        json.loads(line)
        for line in INPUTS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rows.sort(key=lambda r: int(r["id"]))
    assert len(rows) == 40, len(rows)
    assert [int(r["id"]) for r in rows] == list(range(1, 41))
    return rows


def _dialogue_client() -> ProviderClient:
    settings = load_provider_settings(ROOT)
    slots = load_model_slots(ROOT)
    s = dict(slots.get("dialogue") or settings)
    cfg = ProviderConfig.from_env(
        True,
        timeout_s=float(s.get("timeout_s", 60)),
        # 本轮明确不重试。
        max_retries=0,
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
    return ProviderClient(cfg)


def _log_rows(log_path: Path) -> list[dict]:
    if not log_path.is_file():
        return []
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def generate() -> dict:
    inputs = _load_inputs()
    client = _dialogue_client()
    rates = load_cost_rates(ROOT)
    telemetry = CostTelemetry(rates=rates)
    budget = BudgetGuard(
        BudgetLimits.from_dict(
            {
                "dialogue_daily_calls": 50,
                "soft_cost": COST_CAP,
                "hard_cost": COST_CAP,
            }
        ),
        input_rate=float(rates.per_1k_input_tokens) / 1000.0,
        output_rate=float(rates.per_1k_output_tokens) / 1000.0,
    )
    run_dir = Path(tempfile.mkdtemp(prefix="wsim-r3-"))
    queue = AsyncDialogueQueue(
        client, budget=budget, log_dir=run_dir, telemetry=telemetry, tier="active"
    )
    session = WorldSession(
        ROOT,
        run_id="r3-eval-40",
        seed=42,
        npc_limit=12,
        tick_step=TICK_STEP,
        run_dir=run_dir,
    )
    session.registry.get("talk").dialogue = queue
    enter = session.perform("u_demo", "enter", {})
    if not enter.get("ok"):
        queue.close()
        session.close()
        return {"ok": False, "stage": "enter", "enter": enter}

    log_path = run_dir / "dialogue.jsonl"
    rows: list[dict] = []
    talk_failures = 0
    fallback_count = 0
    aborted = ""
    total_cost = 0.0
    for item in inputs:
        if fallback_count > MAX_FALLBACK:
            aborted = "fallback_limit"
            break
        if total_cost > COST_CAP:
            aborted = "cost_limit"
            break
        tick = int(session.state.tick)
        resp = session.perform(
            "u_demo", "talk", {"target": TARGET, "message": str(item["prompt"])}
        )
        if not resp.get("ok"):
            talk_failures += 1
        rec = None
        for entry in _log_rows(log_path):
            if int(entry.get("tick", -1)) == tick:
                rec = entry
        if rec is None:
            aborted = "missing_log"
            break
        fb = bool(rec.get("fallback"))
        if fb:
            fallback_count += 1
        ctx = None
        for record in telemetry.records:
            if int(record.get("tick", -1)) == tick:
                ctx = record
        if ctx is None:
            aborted = "missing_cost"
            break
        cost = float(ctx.get("estimated_cost") or 0.0)
        total_cost = round(total_cost + cost, 10)
        rows.append(
            {
                "id": int(item["id"]),
                "prompt": str(item["prompt"]),
                "reply": str(rec.get("text") or ""),
                "latency_s": round(float(rec.get("latency_s") or 0.0), 4),
                "fallback": fb,
                "cost": round(cost, 10),
            }
        )
        MODEL_REPLIES.write_text(
            "".join(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                for row in rows
            ),
            encoding="utf-8",
        )
        if fallback_count > MAX_FALLBACK:
            aborted = "fallback_limit"
            break
        if total_cost > COST_CAP:
            aborted = "cost_limit"
            break
        if budget.aborted:
            aborted = "budget_aborted"
            break

    queue.close()
    session.close()

    complete = len(rows) == 40 and not aborted
    report = {
        "ok": complete,
        "stage": "generate",
        "rows": len(rows),
        "talk_failures": talk_failures,
        "fallback": fallback_count,
        "p95_latency_s": _p95([row["latency_s"] for row in rows if row["latency_s"] > 0]),
        "total_cost_usd": round(total_cost, 6),
        "aborted": aborted,
        "budget": budget.summary(),
    }
    if aborted:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return report


def _p95(values: list[float]) -> float:
    values = sorted(float(v) for v in values if float(v) > 0)
    if not values:
        return 0.0
    idx = min(len(values) - 1, int(round(0.95 * (len(values) - 1))))
    return round(values[idx], 4)


def _template_replies(content) -> dict[int, str]:
    persona = content.personas[TARGET]
    npc = next(n for n in content.npcs if str(n["id"]) == TARGET)
    name = str(npc.get("name") or "")
    role = str(npc.get("role") or "")
    out: dict[int, str] = {}
    for item in _load_inputs():
        tick = TICK_STEP * int(item["id"])
        out[int(item["id"])] = str(
            compose_talk(persona, TARGET, name, role, str(item["prompt"]), tick)["reply"]
        )
    return out


def _load_r2() -> list[dict]:
    pairs = [
        json.loads(line)
        for line in (R2_DIR / "pairs.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    keys = {
        int(row["id"]): str(row["model"])
        for row in (
            json.loads(line)
            for line in (R2_DIR / "answer_key.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        )
    }
    items: list[dict] = []
    for pair in sorted(pairs, key=lambda p: int(p["id"])):
        pid = int(pair["id"])
        if keys[pid] == "A":
            model, template = str(pair["A"]), str(pair["B"])
        else:
            model, template = str(pair["B"]), str(pair["A"])
        items.append({"id": pid, "prompt": str(pair["prompt"]), "model": model, "template": template})
    assert len(items) == 30, len(items)
    return items


def _position_slots(n: int, model_as_a: int) -> list[str]:
    """固定 35/35，洗牌后拒绝明显的位置规律（长串 / 严格交替 / 半区失衡）。"""
    rng = random.Random(POSITION_SEED)
    for _ in range(10_000):
        slots = ["A"] * model_as_a + ["B"] * (n - model_as_a)
        rng.shuffle(slots)
        longest = _longest_run(slots)
        half = sum(1 for s in slots[: n // 2] if s == "A")
        alternating = all(slots[i] == ("A" if i % 2 == 0 else "B") for i in range(n))
        if longest <= 4 and 15 <= half <= 20 and not alternating:
            return slots
    raise RuntimeError("无法生成无位置规律的 A/B 分配")


def _longest_run(slots: list[str]) -> int:
    best = run = 0
    prev = ""
    for slot in slots:
        run = run + 1 if slot == prev else 1
        prev = slot
        best = max(best, run)
    return best


def _buckets(lengths: list[int]) -> dict[str, int]:
    counts = Counter()
    for n in lengths:
        if n < 20:
            counts["<20"] += 1
        elif n <= 29:
            counts["20-29"] += 1
        elif n <= 39:
            counts["30-39"] += 1
        elif n <= 49:
            counts["40-49"] += 1
        elif n <= 60:
            counts["50-60"] += 1
        else:
            counts[">60"] += 1
    return dict(sorted(counts.items()))


def build_package() -> dict:
    if not MODEL_REPLIES.is_file():
        raise SystemExit("缺少 model-replies.jsonl")
    replies = [
        json.loads(line)
        for line in MODEL_REPLIES.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    replies.sort(key=lambda r: int(r["id"]))
    assert len(replies) == 40, len(replies)
    assert [int(r["id"]) for r in replies] == list(range(1, 41))
    assert not any(bool(r.get("fallback")) for r in replies), "存在 fallback，拒绝构建包"

    content = load_content(ROOT, allow_fixture=False, use_content=True)
    templates = _template_replies(content)

    r2_items = _load_r2()
    r3_items = [
        {
            "id": 31 + int(row["id"]) - 1,
            "prompt": str(row["prompt"]),
            "model": str(row["reply"]),
            "template": templates[int(row["id"])],
        }
        for row in replies
    ]
    items = r2_items + r3_items
    assert len(items) == 70, len(items)

    slots = _position_slots(len(items), 35)
    pairs: list[dict] = []
    keys: list[dict] = []
    for item, slot in zip(items, slots):
        a, b = (
            (item["model"], item["template"])
            if slot == "A"
            else (item["template"], item["model"])
        )
        pairs.append({"id": item["id"], "prompt": item["prompt"], "A": a, "B": b})
        keys.append({"id": item["id"], "model": slot})

    PAIRS.write_text(
        "".join(json.dumps(p, ensure_ascii=False, sort_keys=True) + "\n" for p in pairs),
        encoding="utf-8",
    )
    ANSWER_KEY.write_text(
        "".join(json.dumps(k, ensure_ascii=False, sort_keys=True) + "\n" for k in keys),
        encoding="utf-8",
    )

    template_replies = [item["template"] for item in items]
    model_replies = [item["model"] for item in items]
    template_lengths = [len(text) for text in template_replies]
    model_lengths = [len(text) for text in model_replies]
    bracket_template = [
        {"id": item["id"], "reply": item["template"]}
        for item in items
        if "「" in item["template"] or "」" in item["template"]
    ]
    bracket_model = [
        {"id": item["id"], "reply": item["model"]}
        for item in items
        if "「" in item["model"] or "」" in item["model"]
    ]

    total_cost = round(sum(float(row["cost"]) for row in replies), 6)
    fallback_count = sum(1 for row in replies if bool(row.get("fallback")))
    duplicate_counts = Counter(template_replies)
    duplicate_groups = {
        text: count for text, count in duplicate_counts.items() if count > 1
    }
    duplicate_rows = sum(duplicate_groups.values())

    checks = {
        "samples": len(pairs),
        "sources": {"r2_pairs": len(r2_items), "r3_model_replies": len(r3_items)},
        "id_map": {"r2": [1, 30], "r3": [31, 70]},
        "seed": SEED,
        "position_seed": POSITION_SEED,
        "template_length": {
            "min": min(template_lengths),
            "max": max(template_lengths),
            "avg": round(sum(template_lengths) / len(template_lengths), 2),
            "in_20_60": sum(1 for n in template_lengths if 20 <= n <= 60),
            "buckets": _buckets(template_lengths),
        },
        "model_length": {
            "min": min(model_lengths),
            "max": max(model_lengths),
            "avg": round(sum(model_lengths) / len(model_lengths), 2),
            "buckets": _buckets(model_lengths),
        },
        "bracket_hits": {
            "template": bracket_template,
            "model": bracket_model,
            "total": len(bracket_template) + len(bracket_model),
        },
        "model_fallback": {
            "count": fallback_count,
            "limit": MAX_FALLBACK,
            "ids": [int(row["id"]) for row in replies if bool(row.get("fallback"))],
        },
        "p95_latency_s": _p95([float(row["latency_s"]) for row in replies]),
        "total_cost_usd": total_cost,
        "cost_cap_usd": COST_CAP,
        "position_balance": {
            "model_as_A": sum(1 for k in keys if k["model"] == "A"),
            "model_as_B": sum(1 for k in keys if k["model"] == "B"),
            "longest_run": _longest_run(slots),
            "alternating": all(
                slots[i] == ("A" if i % 2 == 0 else "B") for i in range(len(slots))
            ),
            "first_half_model_as_A": sum(1 for s in slots[: len(slots) // 2] if s == "A"),
        },
        "reply_similarity": {
            "worst": round(
                max(
                    (
                        SequenceMatcher(None, a, b).ratio()
                        for i, a in enumerate(template_replies)
                        for b in template_replies[i + 1 :]
                    ),
                    default=0.0,
                ),
                4,
            ),
            "threshold": 0.60,
            "template_duplicate_groups": len(duplicate_groups),
            "template_duplicate_rows": duplicate_rows,
        },
        "fields": {
            "pairs": ["id", "prompt", "A", "B"],
            "answer_key": ["id", "model"],
            "labels_only_in_answer_key": True,
        },
        "fingerprints": {
            "pairs_sha256": hashlib.sha256(PAIRS.read_bytes()).hexdigest(),
            "answer_key_sha256": hashlib.sha256(ANSWER_KEY.read_bytes()).hexdigest(),
        },
    }
    CHECKS.write_text(
        json.dumps(checks, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(checks, ensure_ascii=False, indent=2, sort_keys=True))
    return checks


def main() -> int:
    if "--package-only" in sys.argv:
        build_package()
        return 0
    if not os.environ.get("LLM_API_KEY", "").strip():
        raise SystemExit("LLM_API_KEY 未设置；本脚本不会写入 key")
    report = generate()
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if not report.get("ok"):
        return 2
    build_package()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
