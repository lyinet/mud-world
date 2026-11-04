#!/usr/bin/env python3
"""R4：接线后的 70 条真实模型回复 + 最终盲评包。

输入：
  - blind-eval-r2/pairs.jsonl 的 30 条 prompt（id 1-30）
  - blind-eval-r3/inputs-40.jsonl 的 40 条 prompt（id 31-70）
模板：
  - 直接复用 blind-eval-r3b 的模板侧，不重新生成。

  LLM_API_KEY=... .venv/bin/python reports/evidence/blind-eval-r4/run_r4.py
  .venv/bin/python reports/evidence/blind-eval-r4/run_r4.py --package-only
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from wsim.agent.budget import BudgetGuard, BudgetLimits  # noqa: E402
from wsim.agent.cost import CostTelemetry, load_cost_rates  # noqa: E402
from wsim.agent.dialogue_async import AsyncDialogueQueue  # noqa: E402
from wsim.agent.provider import (  # noqa: E402
    ProviderClient,
    ProviderConfig,
    load_model_slots,
    load_provider_settings,
)
from wsim.api.session import WorldSession  # noqa: E402

PKG_DIR = Path(__file__).resolve().parent
EVIDENCE = PKG_DIR.parent
R2_DIR = EVIDENCE / "blind-eval-r2"
R3_DIR = EVIDENCE / "blind-eval-r3"
R3B_DIR = EVIDENCE / "blind-eval-r3b"
MODEL_REPLIES = PKG_DIR / "model-replies.jsonl"
PAIRS = PKG_DIR / "pairs.jsonl"
ANSWER_KEY = PKG_DIR / "answer_key.jsonl"
CHECKS = PKG_DIR / "checks.json"

TARGET = "duan_wudou"
TICK_STEP = 6
MAX_FALLBACK = 5
COST_CAP = 0.25
SEED = 20261104
POSITION_SEED = 20261104

_MARK_BOLD = __import__("re").compile(r"\*\*")
_MARK_CJK = __import__("re").compile(r"【|】")
_MARK_PAREN = __import__("re").compile(r"[（(][^）)]{1,40}[）)]")
_THIRD = __import__("re").compile(r"(^|[。！？\n])\s*段五斗(?=\s*(?:[把将]|咧嘴|拍|插|搁|按))|他自己")


def _jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _load_inputs_70() -> list[dict]:
    r2_pairs = sorted(_jsonl(R2_DIR / "pairs.jsonl"), key=lambda r: int(r["id"]))
    assert [int(r["id"]) for r in r2_pairs] == list(range(1, 31))
    items = [{"id": int(r["id"]), "prompt": str(r["prompt"]), "source": "r2"} for r in r2_pairs]
    r3_inputs = sorted(_jsonl(R3_DIR / "inputs-40.jsonl"), key=lambda r: int(r["id"]))
    assert [int(r["id"]) for r in r3_inputs] == list(range(1, 41))
    items += [
        {"id": 31 + int(r["id"]) - 1, "prompt": str(r["prompt"]), "source": "r3"}
        for r in r3_inputs
    ]
    assert [i["id"] for i in items] == list(range(1, 71))
    return items


def _template_map() -> dict[int, str]:
    pairs = {int(p["id"]): p for p in _jsonl(R3B_DIR / "pairs.jsonl")}
    keys = {int(k["id"]): str(k["model"]) for k in _jsonl(R3B_DIR / "answer_key.jsonl")}
    out: dict[int, str] = {}
    for pid, pair in pairs.items():
        side = keys[pid]
        out[pid] = str(pair["B"] if side == "A" else pair["A"])
    assert sorted(out) == list(range(1, 71))
    return out


def _dialogue_client() -> ProviderClient:
    settings = load_provider_settings(ROOT)
    slots = load_model_slots(ROOT)
    s = dict(slots.get("dialogue") or settings)
    cfg = ProviderConfig.from_env(
        True,
        timeout_s=float(s.get("timeout_s", 60)),
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


def _p95(values: list[float]) -> float:
    vals = sorted(float(v) for v in values if float(v) > 0)
    if not vals:
        return 0.0
    return round(vals[min(len(vals) - 1, int(round(0.95 * (len(vals) - 1))))], 4)


def _p50(values: list[float]) -> float:
    vals = sorted(float(v) for v in values if float(v) > 0)
    if not vals:
        return 0.0
    return round(vals[(len(vals) - 1) // 2], 4)


def _log_rows(path: Path) -> list[dict]:
    return _jsonl(path) if path.is_file() else []


def generate() -> dict:
    items = _load_inputs_70()
    rates = load_cost_rates(ROOT)
    telemetry = CostTelemetry(rates=rates)
    budget = BudgetGuard(
        BudgetLimits.from_dict(
            {
                "dialogue_daily_calls": 80,
                "soft_cost": COST_CAP,
                "hard_cost": COST_CAP,
            }
        ),
        input_rate=float(rates.per_1k_input_tokens) / 1000.0,
        output_rate=float(rates.per_1k_output_tokens) / 1000.0,
    )
    run_dir = Path(tempfile.mkdtemp(prefix="wsim-r4-"))
    queue = AsyncDialogueQueue(
        client=_dialogue_client(),
        budget=budget,
        log_dir=run_dir,
        telemetry=telemetry,
        tier="active",
    )
    session = WorldSession(
        ROOT,
        run_id="r4-eval-70",
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
    fallback_count = 0
    total_cost = 0.0
    aborted = ""
    for item in items:
        if fallback_count > MAX_FALLBACK:
            aborted = "fallback_limit"
            break
        if total_cost > COST_CAP:
            aborted = "cost_limit"
            break
        before_tele = len(telemetry.records)
        before_log = len(_log_rows(log_path))
        session.perform("u_demo", "talk", {"target": TARGET, "message": str(item["prompt"])})
        new_logs = _log_rows(log_path)[before_log:]
        new_tele = telemetry.records[before_tele:]
        if not new_logs or not new_tele:
            aborted = "missing_call_record"
            break
        rec = new_logs[-1]
        ctx = new_tele[-1]
        fb = bool(rec.get("fallback"))
        if fb:
            fallback_count += 1
        cost = float(ctx.get("estimated_cost") or 0.0)
        total_cost = round(total_cost + cost, 10)
        rows.append(
            {
                "id": int(item["id"]),
                "source": item["source"],
                "prompt": str(item["prompt"]),
                "reply": str(rec.get("text") or ""),
                "latency_s": round(float(rec.get("latency_s") or ctx.get("latency_s") or 0.0), 4),
                "fallback": fb,
                "fallback_reason": str(rec.get("fallback_reason") or ""),
                "cost": round(cost, 10),
                "after_guard": bool(rec.get("after_guard")),
                "guard_flags": list(rec.get("guard_flags") or []),
                "repeated_phrase": str(rec.get("repeated_phrase") or ""),
            }
        )
        MODEL_REPLIES.write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
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
    report = {
        "ok": len(rows) == 70 and not aborted,
        "stage": "generate",
        "rows": len(rows),
        "fallback": fallback_count,
        "p95_latency_s": _p95([float(r["latency_s"]) for r in rows]),
        "total_cost_usd": round(total_cost, 6),
        "after_guard": sum(1 for r in rows if r["after_guard"]),
        "aborted": aborted,
        "budget": budget.summary(),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return report


def _position_slots(n: int, model_as_a: int) -> list[str]:
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
    replies = sorted(_jsonl(MODEL_REPLIES), key=lambda r: int(r["id"]))
    assert len(replies) == 70, len(replies)
    assert [int(r["id"]) for r in replies] == list(range(1, 71))
    assert not any(bool(r.get("fallback")) for r in replies), "存在 fallback，拒绝构建包"

    templates = _template_map()
    inputs = {int(i["id"]): i for i in _load_inputs_70()}
    assert {int(r["id"]) for r in replies} == set(inputs)
    for row in replies:
        assert row["prompt"] == inputs[int(row["id"])]["prompt"]

    slots = _position_slots(70, 35)
    pairs: list[dict] = []
    keys: list[dict] = []
    for row in replies:
        pid = int(row["id"])
        model = str(row["reply"])
        template = templates[pid]
        a, b = (model, template) if slots[pid - 1] == "A" else (template, model)
        pairs.append({"id": pid, "prompt": str(row["prompt"]), "A": a, "B": b})
        keys.append({"id": pid, "model": slots[pid - 1]})

    PAIRS.write_text(
        "".join(json.dumps(p, ensure_ascii=False, sort_keys=True) + "\n" for p in pairs),
        encoding="utf-8",
    )
    ANSWER_KEY.write_text(
        "".join(json.dumps(k, ensure_ascii=False, sort_keys=True) + "\n" for k in keys),
        encoding="utf-8",
    )

    models = [str(r["reply"]) for r in replies]
    tmpls = [templates[int(r["id"])] for r in replies]
    model_lengths = [len(t) for t in models]
    tmpl_lengths = [len(t) for t in tmpls]

    def _residual(texts: list[str]) -> dict[str, int]:
        return {
            "markdown_bold": sum(len(_MARK_BOLD.findall(t)) for t in texts),
            "cjk_bracket": sum(len(_MARK_CJK.findall(t)) for t in texts),
            "paren_action": sum(len(_MARK_PAREN.findall(t)) for t in texts),
        }

    model_res = _residual(models)
    tmpl_res = _residual(tmpls)
    third_hits = [
        {"id": int(r["id"]), "reply": m}
        for r, m in zip(replies, models)
        if _THIRD.search(m)
    ]
    detected_third = [int(r["id"]) for r in replies if "third_person" in (r.get("guard_flags") or [])]
    repeated = [int(r["id"]) for r in replies if r.get("repeated_phrase")]
    slogan_after = sum(
        m.count("包在我身上") + m.count("包在我段五斗身上") + m.count("包在五斗身上")
        for m in models
    )
    duplicate_templates = {
        text: count for text, count in Counter(tmpls).items() if count > 1
    }

    checks = {
        "samples": len(pairs),
        "id_map": {"r2": [1, 30], "r3": [31, 70]},
        "seed": SEED,
        "position_seed": POSITION_SEED,
        "format_residual": {
            "model": model_res,
            "template": tmpl_res,
            "total": sum(model_res.values()) + sum(tmpl_res.values()),
        },
        "third_person": {
            "final_model_hits": len(third_hits),
            "final_ids": [x["id"] for x in third_hits],
            "detected_by_guard": len(detected_third),
            "detected_ids": detected_third,
        },
        "slogan_repetition": {
            "phrases": ["包在我身上", "包在我段五斗身上", "包在五斗身上"],
            "occurrences_after_guard": slogan_after,
            "rewritten_ids": repeated,
            "rewritten_count": len(repeated),
        },
        "model_length": {
            "min": min(model_lengths),
            "max": max(model_lengths),
            "avg": round(sum(model_lengths) / len(model_lengths), 2),
            "buckets": _buckets(model_lengths),
        },
        "template_length": {
            "min": min(tmpl_lengths),
            "max": max(tmpl_lengths),
            "avg": round(sum(tmpl_lengths) / len(tmpl_lengths), 2),
            "buckets": _buckets(tmpl_lengths),
            "duplicate_groups": len(duplicate_templates),
        },
        "p50_latency_s": _p50([float(r["latency_s"]) for r in replies]),
        "p95_latency_s": _p95([float(r["latency_s"]) for r in replies]),
        "total_cost_usd": round(sum(float(r["cost"]) for r in replies), 6),
        "cost_cap_usd": COST_CAP,
        "fallback": {"count": 0, "ids": []},
        "after_guard": {
            "count": sum(1 for r in replies if r["after_guard"]),
            "ids": [int(r["id"]) for r in replies if r["after_guard"]],
        },
        "position_balance": {
            "model_as_A": sum(1 for k in keys if k["model"] == "A"),
            "model_as_B": sum(1 for k in keys if k["model"] == "B"),
            "longest_run": _longest_run(slots),
            "alternating": all(slots[i] == ("A" if i % 2 == 0 else "B") for i in range(70)),
            "first_half_model_as_A": sum(1 for s in slots[:35] if s == "A"),
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
    if not report.get("ok"):
        return 2
    build_package()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
