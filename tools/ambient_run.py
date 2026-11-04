#!/usr/bin/env python3
"""W2 自走运行器：按虚拟时钟推进世界，不依赖 HTTP 层。

示例：
  # 1x 真实跑 30 分钟（≈300 ticks）
  python tools/ambient_run.py --run-id ambient-1x-30m --minutes 30 --speed 1

  # 4x 真实跑 30 分钟（≈1200 ticks）
  python tools/ambient_run.py --run-id ambient-4x-30m --minutes 30 --speed 4

  # 10 分钟跑，其中第 40 tick 暂停 5 分钟后自动恢复
  python tools/ambient_run.py --run-id ambient-pause --minutes 10 --speed 4 \
      --pause-at-tick 40 --resume-after 300

  # 不配速快跑 300 ticks，并在第 150 tick 模拟玩家进入、第 210 tick 退出
  python tools/ambient_run.py --run-id ambient-player --ticks 300 \
      --enter-at-tick 150 --leave-at-tick 210

产物目录：<out>/<run-id>/{events.sqlite, ambient.jsonl, ambient_progress.jsonl,
ambient_summaries.jsonl, ambient_control.jsonl, ambient.log, ambient_summary.json}
"""
from __future__ import annotations

import argparse
import json
import shutil
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.sim.scheduler import AmbientConfig, AmbientScheduler  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="W2 世界自走调度器运行器")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--out", default=str(ROOT / "runs" / "w2"))
    ap.add_argument("--minutes", type=float, help="真实运行时长（配速模式）")
    ap.add_argument("--ticks", type=int, help="固定 tick 数（不配速模式）")
    ap.add_argument("--speed", type=float, default=1.0, choices=(1.0, 2.0, 4.0))
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--npc-limit", type=int, default=0)
    ap.add_argument("--max-cpu", type=float, default=0.85, help="CPU/墙钟比例上限")
    ap.add_argument("--summary-every", type=int, default=6, help="摘要间隔（默认 6 ticks = 1 游戏小时）")
    ap.add_argument("--llm-max-calls", type=int, default=0, help="LLM 硬上限；默认 0（纯规则）")
    ap.add_argument("--control-file", help="轮询该 JSON 控制文件（pause/resume/step/speed/stop）")
    ap.add_argument("--pause-at-tick", type=int)
    ap.add_argument("--resume-after", type=float, help="暂停多少秒后自动恢复")
    ap.add_argument("--enter-at-tick", type=int, help="仅 --ticks 模式：模拟玩家进入的 tick")
    ap.add_argument("--leave-at-tick", type=int, help="仅 --ticks 模式：模拟玩家退出的 tick")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    if args.minutes is None and args.ticks is None:
        ap.error("必须给出 --minutes 或 --ticks")
    if (args.enter_at_tick or args.leave_at_tick) and args.ticks is None:
        ap.error("玩家进出场景仅支持 --ticks 模式")

    out_dir = Path(args.out) / args.run_id
    if out_dir.exists():
        if not args.overwrite:
            print(f"输出目录已存在：{out_dir}（加 --overwrite 覆盖）", file=sys.stderr)
            return 2
        shutil.rmtree(out_dir)

    cfg = AmbientConfig(
        out_dir=out_dir,
        run_id=args.run_id,
        seed=args.seed,
        npc_limit=args.npc_limit,
        speed=args.speed,
        max_cpu_ratio=args.max_cpu,
        llm_enabled=False,
        llm_max_calls=max(0, args.llm_max_calls),
        summary_every_ticks=args.summary_every,
        control_file=Path(args.control_file) if args.control_file else None,
        max_ticks=args.ticks,
    )
    scheduler = AmbientScheduler(ROOT, cfg)

    def _stop(_sig, _frame):
        scheduler.stop(source="signal")

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    if args.ticks is not None:
        cursor = 0
        points = sorted(
            (t, action)
            for t, action in (
                (args.enter_at_tick, "enter"),
                (args.leave_at_tick, "leave"),
            )
            if t is not None
        )
        for tick, action in points:
            scheduler.run_ticks(max(0, tick - cursor))
            cursor = tick
            if action == "enter":
                scheduler.enter_player()
            else:
                scheduler.leave_player()
        scheduler.run_ticks(max(0, args.ticks - cursor))
        summary = scheduler.final_summary()
    else:
        summary = scheduler.start(
            minutes=args.minutes,
            pause_at_tick=args.pause_at_tick,
            resume_after_s=args.resume_after,
        )
    result = {
        "run_id": summary["run_id"],
        "out_dir": str(out_dir),
        "ticks": summary["ticks"],
        "npc_events": summary["npc_events"],
        "active_npcs": summary["active_npcs"],
        "distinct_verbs": summary["distinct_verbs"],
        "llm_calls": summary["llm_calls"],
        "events_total": summary["events_total"],
        "state_hash": summary["state_hash"],
        "event_hash": summary["event_hash"],
        "elapsed_s": summary["elapsed_s"],
        "cpu_guard_sleeps": summary["cpu_guard_sleeps"],
        "error": summary.get("error"),
    }
    print("AMBIENT_RESULT " + json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 1 if summary.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
