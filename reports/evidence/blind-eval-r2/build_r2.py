#!/usr/bin/env python3
"""R2 盲评包：复用已落盘的 30 条模型回复，只把模板侧换成新短模板。

用法（在 world-sim 根目录）：
  python3 reports/evidence/blind-eval-r2/build_r2.py

输入：runs/a2-dialogue-30/eval_raw.jsonl（message / model_reply / tick 原样复用）
输出：reports/evidence/blind-eval-r2/{pairs.jsonl,answer_key.jsonl,checks.json}
不调用任何模型接口，不产生 API 成本。
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from wsim.agent.dialogue import compose_talk  # noqa: E402
from wsim.core.content import load_content  # noqa: E402

PKG_DIR = Path(__file__).resolve().parent
SOURCE = ROOT / "runs" / "a2-dialogue-30" / "eval_raw.jsonl"
TARGET = "duan_wudou"
SEED = 20261104

MARKDOWN_RE = re.compile(r"\*\*|__|`|^#|^\s*[-*]\s|\[[^\]]+\]\([^)]+\)", re.M)
THIRD_PERSON_RE = re.compile(r"(?<!其)他(?!人)|她")
FIRST_PERSON = "我"


def main() -> int:
    content = load_content(ROOT, allow_fixture=False, use_content=True)
    persona = content.personas[TARGET]
    npc = next(n for n in content.npcs if str(n["id"]) == TARGET)
    role = str(npc.get("role") or "")

    rows = [
        json.loads(line)
        for line in SOURCE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rows.sort(key=lambda r: int(r["round"]))

    enriched: list[dict] = []
    lengths: list[int] = []
    markdown_hits: list[dict] = []
    third_person_hits: list[dict] = []
    missing_first_person: list[dict] = []
    for row in rows:
        message = str(row["message"])
        tick = int(row["tick"])
        model_reply = str(row["model_reply"])
        out = compose_talk(persona, TARGET, str(npc.get("name") or ""), role, message, tick)
        template_reply = str(out["reply"])
        lengths.append(len(template_reply))
        record = {"id": int(row["round"]), "reply": template_reply}
        if MARKDOWN_RE.search(template_reply):
            markdown_hits.append(record)
        if THIRD_PERSON_RE.search(template_reply):
            third_person_hits.append(record)
        if FIRST_PERSON not in template_reply:
            missing_first_person.append(record)
        enriched.append(
            {
                "id": int(row["round"]),
                "prompt": message,
                "model": model_reply,
                "template": template_reply,
            }
        )

    # 位置平衡：15 条模型在 A、15 条模型在 B，再按固定 seed 洗牌。
    model_slots = ["A"] * 15 + ["B"] * 15
    rng = random.Random(SEED)
    rng.shuffle(model_slots)

    pairs: list[dict] = []
    keys: list[dict] = []
    for item, model_slot in zip(enriched, model_slots):
        a, b = (
            (item["model"], item["template"])
            if model_slot == "A"
            else (item["template"], item["model"])
        )
        pairs.append({"id": item["id"], "prompt": item["prompt"], "A": a, "B": b})
        keys.append({"id": item["id"], "model": model_slot})

    pairs_path = PKG_DIR / "pairs.jsonl"
    key_path = PKG_DIR / "answer_key.jsonl"
    pairs_path.write_text(
        "".join(json.dumps(p, ensure_ascii=False, sort_keys=True) + "\n" for p in pairs),
        encoding="utf-8",
    )
    key_path.write_text(
        "".join(json.dumps(k, ensure_ascii=False, sort_keys=True) + "\n" for k in keys),
        encoding="utf-8",
    )

    in_range = sum(1 for n in lengths if 20 <= n <= 60)
    checks = {
        "samples": len(pairs),
        "source": str(SOURCE.relative_to(ROOT)),
        "seed": SEED,
        "template_length": {
            "min": min(lengths),
            "max": max(lengths),
            "avg": round(sum(lengths) / len(lengths), 2),
            "in_20_60": in_range,
        },
        "markdown_hits": markdown_hits,
        "third_person_hits": third_person_hits,
        "missing_first_person": missing_first_person,
        "position_balance": {
            "model_as_A": sum(1 for k in keys if k["model"] == "A"),
            "model_as_B": sum(1 for k in keys if k["model"] == "B"),
        },
        "fields": {
            "pairs": ["id", "prompt", "A", "B"],
            "answer_key": ["id", "model"],
            "labels_only_in_answer_key": True,
        },
        "fingerprints": {
            "pairs_sha256": hashlib.sha256(pairs_path.read_bytes()).hexdigest(),
            "answer_key_sha256": hashlib.sha256(key_path.read_bytes()).hexdigest(),
        },
    }
    (PKG_DIR / "checks.json").write_text(
        json.dumps(checks, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(checks, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
