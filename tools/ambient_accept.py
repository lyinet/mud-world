#!/usr/bin/env python3
"""W2 验收器：对自走产物做只读校验，输出 PASS/FAIL 表。

示例：
  python tools/ambient_accept.py \
      --run-1x runs/w2/ambient-1x-30m \
      --run-4x runs/w2/ambient-4x-30m \
      --det-a runs/w2/det-a --det-b runs/w2/det-b \
      --player runs/w2/ambient-player \
      --pause runs/w2/ambient-pause
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.core.canonical import hash_obj  # noqa: E402


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _summary(run_dir: Path) -> dict:
    return read_json(run_dir / "ambient_summary.json")


def check_integrity(run_dir: Path) -> list[tuple[str, bool, str]]:
    sm = _summary(run_dir)
    progress = read_jsonl(run_dir / "ambient_progress.jsonl")
    audit = read_jsonl(run_dir / "ambient.jsonl")
    checks = []
    ticks = [r["tick"] for r in progress]
    ok = ticks == list(range(len(progress))) if ticks else False
    checks.append(("progress 连续", ok, f"{len(progress)} 条 {ticks[:1]}..{ticks[-1:]}" if ticks else "无记录"))
    ok = len(audit) == sm["events_total"]
    checks.append(("audit=事件总数", ok, f"audit={len(audit)} events={sm['events_total']}"))
    seqs = [r["seq"] for r in audit]
    ok = seqs == list(range(1, len(audit) + 1))
    checks.append(("audit seq 连续", ok, f"seq 1..{len(audit)}"))
    prev = ""
    chain_ok = True
    for row in audit:
        if row["event_hash"] != hash_obj(row["canonical"]):
            chain_ok = False
            break
        expected = hash_obj({"prev": prev, "event": row["canonical"]})
        if row["event_chain"] != expected:
            chain_ok = False
            break
        prev = row["event_chain"]
    checks.append(("audit 事件链可重算", chain_ok, "逐事件 sha256 链"))
    prev = ""
    progress_ok = True
    for row in progress:
        expected = hash_obj(
            {
                "prev": prev,
                "tick": row["tick"],
                "seq": row["seq"],
                "state": row["state_hash"],
                "event_chain": row["event_chain"],
            }
        )
        if row["chain"] != expected:
            progress_ok = False
            break
        prev = row["chain"]
    checks.append(("progress 推进链可重算", progress_ok, f"tail={prev[:12]}"))
    summaries = read_jsonl(run_dir / "ambient_summaries.jsonl")
    need = {"tick", "events_total", "npc_count", "verbs_total", "llm_calls", "cost_usd", "elapsed_s"}
    ok = bool(summaries) and all(need <= set(s) for s in summaries)
    checks.append(("每小时摘要完整", ok, f"{len(summaries)} 条"))
    return checks


def main() -> int:
    ap = argparse.ArgumentParser(description="W2 自走验收器")
    ap.add_argument("--run-1x", required=True)
    ap.add_argument("--run-4x", required=True)
    ap.add_argument("--det-a")
    ap.add_argument("--det-b")
    ap.add_argument("--player")
    ap.add_argument("--pause")
    ap.add_argument("--json-out")
    args = ap.parse_args()

    d1, d4 = Path(args.run_1x), Path(args.run_4x)
    s1, s4 = _summary(d1), _summary(d4)
    results: list[tuple[str, bool, str]] = []

    ticks_ok = s1["ticks"] >= 300
    results.append(("1x 30min ≥300 ticks", ticks_ok, f"{s1['ticks']} ticks / {s1['elapsed_s']:.0f}s"))
    ev_ok = s1["npc_events"] >= 12
    results.append(("1x NPC 自主事件 ≥12", ev_ok, f"{s1['npc_events']}"))
    npc_ok = s1["active_npcs"] >= 4
    results.append(("1x 有动作 NPC ≥4", npc_ok, f"{s1['active_npcs']}"))
    verb_ok = s1["distinct_verbs"] >= 3
    results.append(("1x 动词种类 ≥3", verb_ok, f"{s1['distinct_verbs']} {list(s1['verbs'])[:6]}"))
    zero_llm = s1["llm_calls"] == 0 and s4["llm_calls"] == 0
    results.append(("规则模式 0 LLM", zero_llm, f"1x={s1['llm_calls']} 4x={s4['llm_calls']}"))

    ratio = s4["ticks"] / s1["ticks"] if s1["ticks"] else 0.0
    results.append(("4x tick 比 3–5", 3 <= ratio <= 5, f"{ratio:.2f}x ({s4['ticks']}/{s1['ticks']})"))
    d_1 = s1["events_total"] / s1["ticks"] * 100
    d_4 = s4["events_total"] / s4["ticks"] * 100
    deviation = abs(d_4 - d_1) / d_1 if d_1 else 1.0
    results.append(
        ("事件密度偏差 ≤30%", deviation <= 0.30,
         f"1x={d_1:.1f}/100tick 4x={d_4:.1f}/100tick Δ={deviation*100:.1f}%")
    )

    if args.det_a and args.det_b:
        a, b = _summary(Path(args.det_a)), _summary(Path(args.det_b))
        ok = (
            a["state_hash"] == b["state_hash"]
            and a["event_hash"] == b["event_hash"]
            and a["event_chain"] == b["event_chain"]
            and a["progress_chain"] == b["progress_chain"]
        )
        results.append(
            ("同 seed hash 一致", ok,
             f"state={a['state_hash'][:10]} event={a['event_hash'][:10]}")
        )

    if args.player:
        pdir = Path(args.player)
        sm = _summary(pdir)
        progress = read_jsonl(pdir / "ambient_progress.jsonl")
        actions = sm.get("player_actions") or []
        enter = next((a for a in actions if a["action"] == "enter"), None)
        leave = next((a for a in actions if a["action"] == "leave"), None)
        rows = {r["tick"]: r for r in progress}
        contiguous = [r["tick"] for r in progress] == list(range(len(progress)))
        no_rollback = contiguous and len(rows) == len(progress)
        before_ok = bool(enter) and enter["tick"] >= 100
        append_ok = bool(enter) and enter["tick"] in rows and enter["seq"] > rows[enter["tick"]]["seq"]
        continuity = before_ok and no_rollback and append_ok
        if leave:
            continuity = continuity and leave["tick"] in rows
        results.append(
            ("玩家中途进入不回滚", continuity,
             f"enter@{enter['tick'] if enter else '-'} seq={enter['seq'] if enter else '-'} "
             f"progress={len(progress)}")
        )
        results.append(("玩家进入前 ≥100 ticks", before_ok, f"enter tick={enter['tick'] if enter else '-'}"))

    if args.pause:
        pdir = Path(args.pause)
        sm = _summary(pdir)
        ctl = sm.get("control") or []
        pauses = [c for c in ctl if c["command"] == "pause"]
        resumes = [c for c in ctl if c["command"] == "resume"]
        frozen = False
        detail = "缺少 pause/resume"
        if pauses and resumes:
            gap = resumes[0]["elapsed_s"] - pauses[0]["elapsed_s"]
            same_tick = pauses[0]["tick"] == resumes[0]["tick"]
            progress = read_jsonl(pdir / "ambient_progress.jsonl")
            ticks = [r["tick"] for r in progress]
            resume_tick = resumes[0]["tick"]
            next_ok = resume_tick + 1 in ticks or sm["ticks"] <= resume_tick + 1
            frozen = same_tick and gap >= 299 and next_ok
            detail = f"pause/resume@{pauses[0]['tick']} 停 {gap:.1f}s"
        results.append(("暂停 5 分钟不推进", frozen, detail))

    integrity_runs = [("1x", d1), ("4x", d4)]
    if args.det_a:
        integrity_runs.append(("det-a", Path(args.det_a)))
    if args.player:
        integrity_runs.append(("player", Path(args.player)))
    if args.pause:
        integrity_runs.append(("pause", Path(args.pause)))
    for label, d in integrity_runs:
        for name, ok, detail in check_integrity(d):
            results.append((f"{label} {name}", ok, detail))

    print("| 检查 | 结果 | 说明 |")
    print("|---|---|---|")
    for name, ok, detail in results:
        print(f"| {name} | {'PASS' if ok else 'FAIL'} | {detail} |")
    bad = [r for r in results if not r[1]]
    print(f"\n合计 {len(results)} 项，PASS {len(results) - len(bad)}，FAIL {len(bad)}")
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(
                [{"check": n, "pass": ok, "detail": d} for n, ok, d in results],
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
