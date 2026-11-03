#!/usr/bin/env python3
"""盲评计分：读评审提交的 {"id": 1..30, "winner": "A"|"B"|"tie"}，对照 answer_key 算模型胜率。

用法：
  python3 reports/a2-dialogue-eval/score_blind.py reviewer.jsonl
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main(path: str) -> int:
    key = {
        int(row["id"]): row["model"]
        for row in (json.loads(line) for line in (HERE / "answer_key.jsonl").read_text(encoding="utf-8").splitlines() if line.strip())
    }
    raw = Path(path).read_text(encoding="utf-8")
    if raw.lstrip().startswith("{"):
        data = json.loads(raw)
        rows = data.get("verdicts", []) if isinstance(data, dict) else []
        if isinstance(data, dict) and "winner" in data:
            rows = [data]
    else:
        rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    verdicts = {
        int(row["id"]): str(row["winner"]).strip()
        for row in rows
    }
    wins = ties = losses = 0
    for rid, model_slot in key.items():
        winner = verdicts.get(rid)
        if winner == "tie":
            ties += 1
        elif winner in ("model", "template"):
            wins += 1 if winner == "model" else 0
            losses += 1 if winner == "template" else 0
        elif winner == model_slot:
            wins += 1
        elif winner in ("A", "B"):
            losses += 1
    total = wins + ties + losses
    print(
        json.dumps(
            {
                "scored": total,
                "model_wins": wins,
                "template_wins": losses,
                "ties": ties,
                "model_win_rate": round(wins / total, 4) if total else 0.0,
                "excludes_ties": round(wins / (wins + losses), 4) if wins + losses else 0.0,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
