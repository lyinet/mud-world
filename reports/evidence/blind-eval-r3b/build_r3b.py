#!/usr/bin/env python3
"""R3b：用细粒度诉求路由重建 70 对盲评包（不调用任何模型）。

模型侧：
  - R2 30 条：runs/a2-dialogue-30/eval_raw.jsonl 的 model_reply 原样复用
  - R3 40 条：reports/evidence/blind-eval-r3/model-replies.jsonl 原样复用
模板侧：用当前 HEAD 的 compose_talk 对 70 条输入全部重新生成。

用法（在 world-sim 根目录）：
  PYTHONPATH=src .venv/bin/python reports/evidence/blind-eval-r3b/build_r3b.py
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from wsim.agent.dialogue import compose_talk  # noqa: E402
from wsim.core.content import load_content  # noqa: E402

PKG_DIR = Path(__file__).resolve().parent
R2_RAW = ROOT / "runs" / "a2-dialogue-30" / "eval_raw.jsonl"
R3_INPUTS = ROOT / "reports" / "evidence" / "blind-eval-r3" / "inputs-40.jsonl"
R3_REPLIES = ROOT / "reports" / "evidence" / "blind-eval-r3" / "model-replies.jsonl"
R3_PKG = ROOT / "reports" / "evidence" / "blind-eval-r3"
TARGET = "duan_wudou"
TICK_STEP = 6
SEED = 20261104
MARKDOWN_RE = re.compile(r"\*\*|__|`|^#|^\s*[-*]\s|\[[^\]]+\]\([^)]+\)", re.M)
THIRD_PERSON_RE = re.compile(r"(?<!其)他(?!人)|她")


def _load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _family(skeleton: str) -> str:
    return skeleton[:-2] if skeleton.endswith((".a", ".b")) else skeleton


def _longest_run(slots: list[str]) -> int:
    best = run = 0
    prev = ""
    for slot in slots:
        run = run + 1 if slot == prev else 1
        prev = slot
        best = max(best, run)
    return best


def _position_slots(n: int, model_as_a: int) -> list[str]:
    rng = random.Random(SEED)
    for _ in range(10_000):
        slots = ["A"] * model_as_a + ["B"] * (n - model_as_a)
        rng.shuffle(slots)
        if _longest_run(slots) <= 4 and 15 <= sum(1 for s in slots[: n // 2] if s == "A") <= 20:
            return slots
    raise RuntimeError("无法生成无位置规律的 A/B 分配")


def _buckets(lengths: list[int]) -> dict[str, int]:
    counts: Counter[str] = Counter()
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


def _old_duplicate_groups() -> list[dict]:
    """记录 R3 旧包的重复组，作为本轮修复的对照证据。"""
    pairs = _load_jsonl(R3_PKG / "pairs.jsonl")
    keys = {int(row["id"]): str(row["model"]) for row in _load_jsonl(R3_PKG / "answer_key.jsonl")}
    grouped: dict[str, list[int]] = defaultdict(list)
    for pair in pairs:
        pid = int(pair["id"])
        text = str(pair["A"]) if keys[pid] == "B" else str(pair["B"])
        grouped[text].append(pid)
    groups = [
        {"ids": sorted(ids), "rows": len(ids), "reply": text}
        for text, ids in grouped.items()
        if len(ids) > 1
    ]
    groups.sort(key=lambda g: (-g["rows"], g["ids"][0]))
    return groups


def main() -> int:
    content = load_content(ROOT, allow_fixture=False, use_content=True)
    persona = content.personas[TARGET]
    npc = next(n for n in content.npcs if str(n["id"]) == TARGET)
    name = str(npc.get("name") or "")
    role = str(npc.get("role") or "")

    r2_rows = _load_jsonl(R2_RAW)
    r2_rows.sort(key=lambda row: int(row["round"]))
    r3_rows = _load_jsonl(R3_INPUTS)
    r3_rows.sort(key=lambda row: int(row["id"]))
    r3_replies = {int(row["id"]): row for row in _load_jsonl(R3_REPLIES)}

    items: list[dict] = []
    routing: list[dict] = []
    for row in r2_rows:
        out = compose_talk(
            persona, TARGET, name, role, str(row["message"]), int(row["tick"])
        )
        pid = int(row["round"])
        items.append(
            {
                "id": pid,
                "prompt": str(row["message"]),
                "model": str(row["model_reply"]),
                "template": str(out["reply"]),
            }
        )
        routing.append(
            {
                "id": pid,
                "source": "r2",
                "intent": str(out["intent"]),
                "object": str(out["object"]),
                "demand": str(out.get("demand") or ""),
                "skeleton": str(out["skeleton"]),
            }
        )
    for row in r3_rows:
        r3id = int(row["id"])
        out = compose_talk(
            persona, TARGET, name, role, str(row["prompt"]), TICK_STEP * r3id
        )
        pid = 30 + r3id
        items.append(
            {
                "id": pid,
                "prompt": str(row["prompt"]),
                "model": str(r3_replies[r3id]["reply"]),
                "template": str(out["reply"]),
            }
        )
        routing.append(
            {
                "id": pid,
                "source": "r3",
                "intent": str(out["intent"]),
                "object": str(out["object"]),
                "demand": str(out.get("demand") or ""),
                "skeleton": str(out["skeleton"]),
            }
        )

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

    (PKG_DIR / "pairs.jsonl").write_text(
        "".join(json.dumps(p, ensure_ascii=False, sort_keys=True) + "\n" for p in pairs),
        encoding="utf-8",
    )
    (PKG_DIR / "answer_key.jsonl").write_text(
        "".join(json.dumps(k, ensure_ascii=False, sort_keys=True) + "\n" for k in keys),
        encoding="utf-8",
    )

    template_replies = [item["template"] for item in items]
    model_replies = [item["model"] for item in items]
    template_lengths = [len(text) for text in template_replies]
    model_lengths = [len(text) for text in model_replies]

    skeleton_counts = Counter(str(row["skeleton"]) for row in routing)
    family_counts = Counter(_family(str(row["skeleton"])) for row in routing)
    duplicate_groups = {
        text: count for text, count in Counter(template_replies).items() if count > 1
    }
    over_60: list[dict] = []
    worst = 0.0
    worst_ids: list[int] | None = None
    for left in range(len(template_replies)):
        for right in range(left + 1, len(template_replies)):
            ratio = SequenceMatcher(
                None, template_replies[left], template_replies[right]
            ).ratio()
            if ratio > worst:
                worst, worst_ids = ratio, [items[left]["id"], items[right]["id"]]
            if ratio > 0.6:
                over_60.append(
                    {
                        "ids": [items[left]["id"], items[right]["id"]],
                        "ratio": round(ratio, 4),
                    }
                )

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
    markdown_model = [
        {"id": item["id"], "reply": item["model"]}
        for item in items
        if MARKDOWN_RE.search(item["model"])
    ]
    third_person_template = [
        {"id": item["id"], "reply": item["template"]}
        for item in items
        if THIRD_PERSON_RE.search(item["template"])
    ]
    r3_fallbacks = [row["id"] for row in _load_jsonl(R3_REPLIES) if bool(row["fallback"])]
    r3_latencies = sorted(
        float(row["latency_s"]) for row in _load_jsonl(R3_REPLIES) if float(row["latency_s"]) > 0
    )
    idx = min(len(r3_latencies) - 1, int(round(0.95 * (len(r3_latencies) - 1))))
    checks = {
        "samples": len(pairs),
        "sources": {"r2_model_rows": len(r2_rows), "r3_model_rows": len(r3_replies)},
        "id_map": {"r2": [1, 30], "r3": [31, 70]},
        "seed": SEED,
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
        "skeleton_counts": dict(sorted(skeleton_counts.items())),
        "skeleton_family_counts": dict(sorted(family_counts.items())),
        "distinct_skeletons": len(skeleton_counts),
        "max_skeleton_reuse": max(skeleton_counts.values()),
        "max_family_reuse": max(family_counts.values()),
        "routing": routing,
        "reply_similarity": {
            "worst": round(worst, 4),
            "worst_ids": worst_ids,
            "threshold": 0.60,
            "pairs_over_60": over_60,
            "template_duplicate_groups": len(duplicate_groups),
            "template_duplicate_rows": sum(duplicate_groups.values()),
        },
        "bracket_hits": {
            "template": bracket_template,
            "model": bracket_model,
            "total": len(bracket_template) + len(bracket_model),
        },
        "markdown_model_hits": markdown_model,
        "third_person_template_hits": third_person_template,
        "r3_model": {
            "fallback": len(r3_fallbacks),
            "fallback_ids": r3_fallbacks,
            "p95_latency_s": round(r3_latencies[idx], 4),
            "total_cost_usd": round(
                sum(float(row["cost"]) for row in _load_jsonl(R3_REPLIES)), 6
            ),
        },
        "position_balance": {
            "model_as_A": sum(1 for k in keys if k["model"] == "A"),
            "model_as_B": sum(1 for k in keys if k["model"] == "B"),
            "longest_run": _longest_run(slots),
            "alternating": all(
                slots[i] == ("A" if i % 2 == 0 else "B") for i in range(len(slots))
            ),
        },
        "before_duplicates": {
            "groups": _old_duplicate_groups(),
            "group_count": len(_old_duplicate_groups()),
            "row_count": sum(g["rows"] for g in _old_duplicate_groups()),
        },
        "fields": {
            "pairs": ["id", "prompt", "A", "B"],
            "answer_key": ["id", "model"],
            "labels_only_in_answer_key": True,
        },
        "fingerprints": {
            "pairs_sha256": hashlib.sha256((PKG_DIR / "pairs.jsonl").read_bytes()).hexdigest(),
            "answer_key_sha256": hashlib.sha256(
                (PKG_DIR / "answer_key.jsonl").read_bytes()
            ).hexdigest(),
        },
    }
    (PKG_DIR / "checks.json").write_text(
        json.dumps(checks, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    summary = {
        "samples": checks["samples"],
        "distinct_skeletons": checks["distinct_skeletons"],
        "max_skeleton_reuse": checks["max_skeleton_reuse"],
        "max_family_reuse": checks["max_family_reuse"],
        "pairs_over_60": len(over_60),
        "template_duplicates": checks["reply_similarity"]["template_duplicate_rows"],
        "model_as_A": checks["position_balance"]["model_as_A"],
        "model_as_B": checks["position_balance"]["model_as_B"],
        "longest_run": checks["position_balance"]["longest_run"],
        "bracket_total": checks["bracket_hits"]["total"],
        "r3_fallback": checks["r3_model"]["fallback"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
