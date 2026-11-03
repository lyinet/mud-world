#!/usr/bin/env python3
"""生成扩展内容 YAML 片段，并立即跑 manifest/引用校验。

用法（默认写入 content/additions/<type>.yaml，并自动在 content/manifest.yaml 登记）：
  python3 tools/new_content.py npc --id tea_boy --name 阿二 --role 茶博士 --home teahouse
  python3 tools/new_content.py item --id tea_cake --name 茶点 --category food --price 6
  python3 tools/new_content.py location --id tea_room --name 茶室 --exit teahouse --service tea
  python3 tools/new_content.py role --id 茶役 --name 茶役 --wage 18 --workplace teahouse
  python3 tools/new_content.py sect --id tea_house_guild --name 茶馆行 --location teahouse
  python3 tools/new_content.py verb --name brew_tea --args "target:npc_id" --roles "*"

设计：
- 工具只写数据文件，不碰 src/；
- 生成带注释片段，避免手抄漏字段；
- 追加后立刻调用 content_manifest.validate_content_dir，失败则回滚本次写入并打印 `file:line`。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.core.content_manifest import ContentValidationError, validate_content_dir  # noqa: E402

TYPE_DIR = {
    "npc": ("npcs", "npcs", "content/additions/npcs.yaml"),
    "item": ("items", "items", "content/additions/items.yaml"),
    "location": ("locations", "locations", "content/additions/locations.yaml"),
    "role": ("roles", "roles", "content/additions/roles.yaml"),
    "sect": ("sects", "sects", "content/additions/sects.yaml"),
    "verb": ("verbs", "verbs", "content/additions/verbs.yaml"),
}


def _q(value: str) -> str:
    if any(ch in value for ch in ":#{}[],&*?|>%@`\"'") or value.strip() != value:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def _list_arg(values: list[str] | None) -> str:
    return "[" + ", ".join(_q(v) for v in (values or [])) + "]"


def _kv_arg(spec: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not spec:
        return out
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError(f"参数片段缺少冒号: {part!r}")
        k, v = part.split(":", 1)
        out[k.strip()] = v.strip()
    return out


def _npc_block(a) -> tuple[str, list[str]]:
    if not (a.id and a.name and a.role and a.home):
        return "", ["npc 需要 --id --name --role --home"]
    goal = a.goal or "earn_money"
    lines = [
        "  # 由 tools/new_content.py 生成；字段与 base npcs.yaml 一致。",
        f"  - id: {_q(a.id)}",
        f"    name: {_q(a.name)}",
        f"    role: {_q(a.role)}",
        f"    home: {_q(a.home)}",
        "    personality:",
        "      traits: [勤勉]",
        '      speech_style: "待补充"',
        "    goals:",
        f"      - id: goal_{_q(a.id)}_1",
        f"        type: {_q(goal)}",
        "        priority: 1",
        "    assets:",
        f"      money: {a.money}",
        "      items: []",
        "    relations: []",
    ]
    return "\n".join(lines) + "\n", []


def _item_block(a) -> tuple[str, list[str]]:
    if not (a.id and a.name):
        return "", ["item 需要 --id --name"]
    return (
        "  # 由 tools/new_content.py 生成。\n"
        f"  - id: {_q(a.id)}\n"
        f"    name: {_q(a.name)}\n"
        f"    category: {_q(a.category)}\n"
        f"    base_price: {a.price}\n",
        [],
    )


def _location_block(a) -> tuple[str, list[str]]:
    if not (a.id and a.name):
        return "", ["location 需要 --id --name"]
    return (
        "  # 由 tools/new_content.py 生成；单向 exit 会由加载器补齐反向边。\n"
        f"  - id: {_q(a.id)}\n"
        f"    name: {_q(a.name)}\n"
        f"    exits: {_list_arg(a.exit)}\n"
        f"    services: {_list_arg(a.service)}\n",
        [],
    )


def _role_block(a) -> tuple[str, list[str]]:
    if not (a.id and a.name and a.workplace):
        return "", ["role 需要 --id --name --workplace"]
    try:
        costs = _kv_arg(a.living)
    except ValueError as exc:
        return "", [str(exc)]
    cost_txt = "{" + ", ".join(f"{k}: {v}" for k, v in sorted(costs.items())) + "}"
    return (
        "  # 由 tools/new_content.py 生成；加载器会合并进 economy 的工资/生活成本/技能表。\n"
        f"  - id: {_q(a.id)}\n"
        f"    name: {_q(a.name)}\n"
        f"    wage: {a.wage}\n"
        f"    skill: {_q(a.skill or a.id)}\n"
        f"    workplace: {_q(a.workplace)}\n"
        f"    living_cost: {cost_txt}\n",
        [],
    )


def _sect_block(a) -> tuple[str, list[str]]:
    if not (a.id and a.name and a.location):
        return "", ["sect 需要 --id --name --location"]
    return (
        "  # 由 tools/new_content.py 生成；genesis 会实例化为公共组织。\n"
        f"  - id: {_q(a.id)}\n"
        f"    name: {_q(a.name)}\n"
        f"    location: {_q(a.location)}\n",
        [],
    )


def _verb_block(a) -> tuple[str, list[str]]:
    if not a.name:
        return "", ["verb 需要 --name"]
    try:
        args = _kv_arg(a.args)
    except ValueError as exc:
        return "", [str(exc)]
    args_txt = "{" + ", ".join(f"{k}: {_q(v)}" for k, v in sorted(args.items())) + "}"
    return (
        "  # 由 tools/new_content.py 生成；行为实现需要 plugins/actions/ 下的插件。\n"
        f"  - name: {_q(a.name)}\n"
        f"    args: {args_txt}\n"
        f"    allowed_roles: {_list_arg(a.roles)}\n",
        [],
    )


BUILDERS = {
    "npc": _npc_block,
    "item": _item_block,
    "location": _location_block,
    "role": _role_block,
    "sect": _sect_block,
    "verb": _verb_block,
}


def _ensure_manifest(content_dir: Path, type_name: str, rel_path: str, pkg_id: str) -> tuple[str, str]:
    mpath = content_dir / "manifest.yaml"
    text = mpath.read_text(encoding="utf-8") if mpath.exists() else ""
    if f"path: {rel_path}" in text:
        return text, ""
    block = (
        f"\n  - id: {pkg_id}\n"
        f"    type: {type_name}\n"
        f"    path: {rel_path}\n"
        '    version: "1.0.0"\n'
    )
    return text + block, block


def main() -> int:
    p = argparse.ArgumentParser(description="生成扩展内容并自动校验")
    p.add_argument("--root", default=str(ROOT), help="项目根目录（默认 world-sim/，测试可用临时副本）")
    sub = p.add_subparsers(dest="kind", required=True)

    def common(sp):
        sp.add_argument("--id", default="")
        sp.add_argument("--name", default="")
        sp.add_argument("--file", default="", help="目标 YAML（默认 content/additions/<type>.yaml）")
        sp.add_argument("--dry-run", action="store_true", help="只打印片段，不写入")

    sp = sub.add_parser("npc", help="新增 NPC")
    common(sp)
    sp.add_argument("--role", default="")
    sp.add_argument("--home", default="")
    sp.add_argument("--goal", default="earn_money")
    sp.add_argument("--money", type=int, default=0)

    sp = sub.add_parser("item", help="新增物品")
    common(sp)
    sp.add_argument("--category", default="food")
    sp.add_argument("--price", type=int, default=5)

    sp = sub.add_parser("location", help="新增地点")
    common(sp)
    sp.add_argument("--exit", action="append", default=[], help="可重复，如 --exit market")
    sp.add_argument("--service", action="append", default=[], help="可重复，服务/物品 id")

    sp = sub.add_parser("role", help="新增职业")
    common(sp)
    sp.add_argument("--wage", type=int, default=20)
    sp.add_argument("--skill", default="")
    sp.add_argument("--workplace", default="")
    sp.add_argument("--living", default="food:9,housing:2,upkeep:0,dues:1")

    sp = sub.add_parser("sect", help="新增门派")
    common(sp)
    sp.add_argument("--location", default="")

    sp = sub.add_parser("verb", help="新增动词元数据（行为需插件）")
    common(sp)
    sp.add_argument("--args", default="")
    sp.add_argument("--roles", action="append", default=["*"])

    a = p.parse_args()
    root = Path(a.root).resolve()
    type_name, root_key, default_file = TYPE_DIR[a.kind]
    block, problems = BUILDERS[a.kind](a)
    if problems:
        print("参数错误:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 2
    if not block:
        return 2

    if a.dry_run:
        print(f"# {root_key}:")
        print(block)
        return 0

    content_dir = root / "content"
    target = Path(a.file) if a.file else root / default_file
    if not target.is_absolute():
        target = root / target
    rel_path = target.relative_to(content_dir).as_posix()
    target.parent.mkdir(parents=True, exist_ok=True)

    original = target.read_text(encoding="utf-8") if target.exists() else None
    if original is None:
        new_text = f"# 由 tools/new_content.py 创建。\n{root_key}:\n{block}"
    else:
        new_text = original
        if not new_text.endswith("\n"):
            new_text += "\n"
        if f"\n{root_key}:" not in "\n" + new_text:
            new_text += f"{root_key}:\n"
        new_text += block
    target.write_text(new_text, encoding="utf-8")

    manifest_original, manifest_block = _ensure_manifest(
        content_dir, type_name, rel_path, f"gen-{type_name}-{target.stem}"
    )
    if manifest_block:
        (content_dir / "manifest.yaml").write_text(manifest_original, encoding="utf-8")

    try:
        validate_content_dir(content_dir)
    except ContentValidationError as exc:
        target.write_text(original, encoding="utf-8") if original is not None else target.unlink()
        if manifest_block:
            (content_dir / "manifest.yaml").write_text(manifest_original, encoding="utf-8")
        print(f"校验失败，已回滚 {target.name}:", file=sys.stderr)
        for msg in exc.messages:
            print("  " + msg, file=sys.stderr)
        return 1

    print(f"已写入 {rel_path}，校验通过。")
    print(block.rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
