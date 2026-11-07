#!/usr/bin/env python3
"""xkx 确定性审计单命令入口。

用法：
  python tools/xkx_audit/__main__.py                 # 完整审计（默认 2 日 seed=42）
  python tools/xkx_audit/__main__.py --quick         # CI 静态 + 会话隔离（跳过两次世界）
  python tools/xkx_audit/__main__.py --require-combat # 战斗未就绪也判失败

退出码：任一条 redline FAIL（或 --require-combat 下战斗 NOT_READY）→ 1；否则 0。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:  # 允许 `python -m xkx_audit` 与直接执行脚本两种方式
    from .audit import REPORT_DIR, run_audit, write_reports
except ImportError:  # pragma: no cover - 直接脚本执行
    from audit import REPORT_DIR, run_audit, write_reports  # type: ignore[no-redef]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="xkx 复刻版确定性审计（只读）")
    parser.add_argument("--days", type=int, default=2, help="每个无头世界运行的天数")
    parser.add_argument("--seed", type=int, default=42, help="世界 seed")
    parser.add_argument("--npc-limit", type=int, default=0, help="NPC 上限（0=全部）")
    parser.add_argument("--quick", action="store_true", help="跳过两次无头世界，仅跑静态与会话检查")
    parser.add_argument(
        "--require-combat",
        action="store_true",
        help="战斗项 NOT_READY 也判失败（引擎落地后 CI 使用）",
    )
    parser.add_argument(
        "--out",
        default=str(REPORT_DIR / "audit.json"),
        help="JSON 报告路径（同目录生成 AUDIT.md）",
    )
    parser.add_argument("--runs-dir", default=None, help="保留运行目录的位置（默认临时目录）")
    parser.add_argument("--keep-runs", action="store_true", help="不删除临时运行目录")
    args = parser.parse_args(argv)

    result = run_audit(
        days=args.days,
        seed=args.seed,
        npc_limit=args.npc_limit,
        quick=args.quick,
        require_combat=args.require_combat,
        runs_dir=Path(args.runs_dir) if args.runs_dir else None,
        keep_runs=args.keep_runs,
    )
    out = Path(args.out)
    if not out.is_absolute():
        out = Path.cwd() / out
    md = write_reports(result, out)

    summary = result["summary"]
    print(
        f"xkx-audit commit={result['commit'][:12]} "
        f"通过={summary['pass']} 不通过={summary['fail']} "
        f"未就绪={summary['not_ready']} 跳过={summary['skip']} "
        f"exit={summary['exit_code']} 耗时={summary['duration_s']}s"
    )
    for check in result["checks"]:
        marker = "!" if check["status"] == "FAIL" else ("-" if check["status"] in {"SKIP"} else " ")
        print(f"  [{marker}] {check['status']:<9} {check['id']}: {check['summary']}")
    print(f"report: {out}")
    print(f"summary: {md}")
    if summary["fail_ids"] and args.quick:
        print(json.dumps({"fail": summary["fail_ids"]}, ensure_ascii=False), file=sys.stderr)
    return int(summary["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
