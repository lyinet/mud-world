#!/usr/bin/env python3
"""打印种子 run 中最近若干条 NPC 自主行动，用于首发现场观察。

用法:
  python3 scripts/world_feed.py --run runs/quickstart-2d --lines 8
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

VERB_LABEL = {
    "move": "移动",
    "work": "干活",
    "consume": "吃饭",
    "trade": "交易",
    "sell": "卖出",
    "buy": "买入",
    "cultivate": "修炼",
    "join_sect": "入派",
    "open_shop": "开店",
    "apprentice": "收徒",
    "marry": "成亲",
    "found_sect": "立派",
    "escort": "押镖",
    "debt_settle": "还债",
    "advance_skill": "练功",
    "gain_status": "扬名",
    "system_quest_post": "发布任务",
}


def load_names(root: Path) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    npc_names: dict[str, str] = {}
    loc_names: dict[str, str] = {}
    item_names: dict[str, str] = {}
    for path in (root / "content").rglob("*.yaml"):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        if isinstance(data.get("npcs"), list):
            for row in data["npcs"]:
                if isinstance(row, dict) and row.get("id"):
                    npc_names[str(row["id"])] = str(row.get("name") or row["id"])
        if isinstance(data.get("locations"), list):
            for row in data["locations"]:
                if isinstance(row, dict) and row.get("id"):
                    loc_names[str(row["id"])] = str(row.get("name") or row["id"])
        if isinstance(data.get("items"), list):
            for row in data["items"]:
                if isinstance(row, dict) and row.get("id"):
                    item_names[str(row["id"])] = str(row.get("name") or row["id"])
    return npc_names, loc_names, item_names


def time_label(tick: int) -> str:
    tick = max(0, int(tick))
    day = tick // 144 + 1
    hour = (tick % 144) // 6
    minute = (tick % 6) * 10
    return f"第{day}日 {hour:02d}:{minute:02d}"


def action_text(verb: str, args: dict, loc_names: dict[str, str], item_names: dict[str, str]) -> str:
    label = VERB_LABEL.get(verb, verb)
    item = item_names.get(str(args.get("item")), str(args.get("item") or ""))
    qty = args.get("qty", 1)
    if verb == "move" and args.get("to"):
        return f"去了{loc_names.get(str(args['to']), args['to'])}"
    if verb == "work" and args.get("at"):
        return f"在{loc_names.get(str(args['at']), args['at'])}干活"
    if verb == "consume" and item:
        return f"消耗了{item} ×{qty}"
    if verb in ("buy", "sell", "trade") and item:
        return f"{label} {item} ×{qty}"
    if verb == "escort" and args.get("route"):
        return f"押镖 {args['route']}"
    return label


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", default="runs/quickstart-2d")
    parser.add_argument("--lines", type=int, default=8)
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    run_dir = Path(args.run)
    if not run_dir.is_absolute():
        run_dir = (root / run_dir).resolve()
    events_path = run_dir / "events.jsonl"
    if not events_path.exists():
        print(f"[feed] 找不到 {events_path}；请先运行 ./start.sh 生成种子世界。", file=sys.stderr)
        return 1

    npc_names, loc_names, item_names = load_names(root)
    rows = []
    with events_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            actor = str(row.get("actor") or "")
            if actor == "system" or actor.startswith("player:") or row.get("outcome") != "ok":
                continue
            if actor not in npc_names or str(row.get("verb")).startswith("system_"):
                continue
            rows.append(row)

    summary: dict = {}
    summary_path = run_dir / "summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    days = summary.get("days") or summary.get("config", {}).get("days")
    total = sum(1 for _ in events_path.open(encoding="utf-8"))
    print(f"种子世界: {run_dir.name} · {days or '?'} 游戏日 · {total} 条事件 · NPC 自主行动 {len(rows)} 条")
    print(f"最近 {min(args.lines, len(rows))} 条 NPC 自主行动（只读回放，不是你的实时会话）:")
    for row in rows[-args.lines:]:
        actor = str(row.get("actor"))
        print(
            f"  [{time_label(int(row.get('tick') or 0))}] "
            f"{npc_names.get(actor, actor)}："
            f"{action_text(str(row.get('verb')), row.get('args') or {}, loc_names, item_names)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
