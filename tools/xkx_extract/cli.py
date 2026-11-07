"""命令行入口。

用法：
    python tools/xkx_extract/cli.py \
        --src tools/xkx_extract/_src/xkx100 \
        --areas hangzhou,jiaxing,shiliang \
        --out content/xkx --strict

--strict：errors 非零或任一覆盖率低于目标时退出码非零。
（默认只把 errors 当阻断项，覆盖率缺口写入 diagnostics 并打印。）
"""

from __future__ import annotations

import argparse
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xkx_extract import emit  # noqa: E402
from xkx_extract import extract as extract_mod  # noqa: E402
from xkx_extract import validate as validate_mod  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="xkx LPC -> JSON 内容抽取器")
    p.add_argument("--src", required=True, help="xkx100 源码根目录")
    p.add_argument("--out", default="content/xkx", help="输出目录")
    p.add_argument("--areas", default=",".join(extract_mod.AREA_DIRS), help="切片区域，逗号分隔")
    p.add_argument("--commit", default=None, help="覆盖 manifest 里的源 commit")
    p.add_argument("--verify", action="store_true", help="重抽一遍并逐文件比对，不写盘")
    p.add_argument("--strict", action="store_true", help="覆盖率不达标也视为失败")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    areas = [a.strip() for a in args.areas.split(",") if a.strip()]
    if not os.path.isdir(args.src):
        print(f"源码目录不存在：{args.src}", file=sys.stderr)
        return 2

    ex = extract_mod.Extractor(args.src, areas)
    pack = ex.run()
    diags = list(ex.diagnostics) + validate_mod.validate(pack)
    counts = ex.counts()
    summary = validate_mod.summarize(emit.dedupe(diags))
    source_info = {
        "repo": "fluffos/xkx100",
        "commit": args.commit or extract_mod.source_commit(args.src),
        "areas": areas,
    }
    files = emit.build_outputs(pack, diags, counts, ex.coverage, source_info)

    if args.verify:
        if not os.path.isdir(args.out):
            print(f"产物目录不存在，无法比对：{args.out}", file=sys.stderr)
            return 2
        diffs = emit.verify_outputs(args.out, files)
        if diffs:
            print("幂等校验失败，以下文件与现有产物不一致：")
            for name in diffs:
                print(f"  - {name}")
            return 1
        print(f"幂等校验通过：{len(files)} 个文件逐字节一致")
        return 0

    sizes = emit.write_outputs(args.out, files)
    total_bytes = sum(sizes.values())

    print("== 抽取统计 ==")
    print(f"  房间：切片 {counts['rooms_in_slice']} + 边界 stub {counts['rooms_boundary_stub']}")
    print(f"  出口：{counts['exits']} 条（含方向名）")
    print(f"  物品：切片 {counts['items_in_slice']} + 外部引用 {counts['items_external']}")
    print(f"  NPC：切片 {counts['npcs_in_slice']} + 外部引用 {counts['npcs_external']}")
    print(f"  对话：{counts['dialogue_topics_in_slice']} 个主题（外部 {counts['dialogue_topics'] - counts['dialogue_topics_in_slice']}）")
    print(f"  商店 {counts['shops']} / 门派 {counts['sects']} / 师父 {counts['masters']} / 主动攻击 {counts['aggressive_npcs']} / 技能 {counts['skills']}")
    print("== 覆盖率 ==")
    for metric, data in ex.coverage.items():
        flag = "OK" if data["ratio"] >= data["target"] else "LOW"
        print(f"  {metric}: {data['hit']}/{data['total']} = {data['ratio']:.1%} (目标 {data['target']:.1%}) {flag}")
    print("== 诊断 ==")
    print(f"  errors={summary['errors']} warnings={summary['warnings']} infos={summary['infos']}")
    for rule, n in sorted(summary["by_rule"].items()):
        print(f"    {rule}: {n}")
    print("== 输出 ==")
    for name in sorted(sizes):
        print(f"  {name}: {sizes[name]} bytes")
    print(f"  合计 {len(sizes)} 个文件 / {total_bytes} bytes")

    if summary["errors"] > 0:
        print("error 级诊断阻断发布，详见 diagnostics.jsonl", file=sys.stderr)
        return 1
    if args.strict:
        low = [m for m, d in ex.coverage.items() if d["ratio"] < d["target"]]
        if low:
            print(f"strict 模式：覆盖率未达标 {low}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
