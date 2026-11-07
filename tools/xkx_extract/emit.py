"""规范化 JSON 输出、manifest 与幂等校验。"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile

DATA_FILES = (
    "rooms.json",
    "exits.json",
    "items.json",
    "npcs.json",
    "dialogue.json",
    "shops.json",
    "sects.json",
    "skills.json",
    "skills_actions.json",
    "skills_stats.json",
    "quests.json",
)

TABLES = {
    "rooms.json": "rooms",
    "exits.json": "exits",
    "items.json": "items",
    "npcs.json": "npcs",
    "dialogue.json": "dialogue",
    "shops.json": "shops",
    "sects.json": "sects",
    "skills.json": "skills",
    "skills_actions.json": "skills_actions",
    "skills_stats.json": "skills_stats",
    "quests.json": "quests",
}


def dumps(obj) -> str:
    """固定分隔符与排序，保证同一输入逐字节一致。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2, separators=(",", ": ")) + "\n"


def dumps_jsonl(rows) -> str:
    lines = [
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for row in rows
    ]
    return "\n".join(lines) + ("\n" if lines else "")


def dedupe(diags: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for d in diags:
        key = (d.get("rule"), d.get("path"), d.get("line"), d.get("message"))
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    out.sort(key=lambda d: (
        d.get("path") or "",
        d.get("line") or 0,
        d.get("rule") or "",
        d.get("message") or "",
    ))
    return out


def build_outputs(pack, diagnostics, counts, coverage, source_info) -> dict[str, str]:
    """返回 {文件名: 文本内容}，不写盘。"""
    diags = dedupe(diagnostics)
    sev = {"error": 0, "warning": 0, "info": 0}
    by_rule: dict[str, int] = {}
    for d in diags:
        sev[d["severity"]] = sev.get(d["severity"], 0) + 1
        by_rule[d["rule"]] = by_rule.get(d["rule"], 0) + 1

    files: dict[str, str] = {}
    for filename, key in TABLES.items():
        files[filename] = dumps({key: pack[key]})

    files["diagnostics.jsonl"] = dumps_jsonl(diags)

    summary = {
        "counts": counts,
        "coverage": coverage,
        "errors": sev.get("error", 0),
        "warnings": sev.get("warning", 0),
        "infos": sev.get("info", 0),
        "by_rule": dict(sorted(by_rule.items())),
    }
    files["summary.json"] = dumps(summary)

    hashes = {
        name: hashlib.sha256(text.encode("utf-8")).hexdigest()
        for name, text in files.items()
    }
    content_hash = hashlib.sha256(
        "".join(f"{name}:{hashes[name]}\n" for name in sorted(hashes)).encode("utf-8")
    ).hexdigest()

    authored = []
    for table, key in (("items", "items"), ("npcs", "npcs")):
        for rec in pack[key]:
            if rec.get("desc_source") != "authored":
                continue
            authored.append({
                "table": table,
                "id": rec["id"],
                "desc": rec["desc"],
            })
    authored.sort(key=lambda r: (r["table"], r["id"]))

    manifest = {
        "schema_version": 1,
        "rules_version": 1,
        "generator": "tools/xkx_extract",
        "source": source_info,
        "areas": source_info.get("areas", []),
        "counts": counts,
        "coverage": coverage,
        "field_semantics": {
            "desc": "玩家可见描述文本（房间 short/long 中的 long、物品/NPC 的 long）。",
            "desc_source": {
                "lpc": "从 xkx100 LPC 源码静态抽取，计入 source 覆盖率。",
                "authored": "原版缺 long，由本项目按原版器物/身份常识自撰，不计入 source 覆盖率，只计入 total 覆盖率。",
                "null": "描述缺失，玩家会看到空白；total 覆盖率不达标即 error。",
            },
            "room_region": "region 由房间源码路径 d/<region>/ 推导（kind=derived_path）；region_name 只从 mapd.lpc 全局映射 map_short/logind.lpc hometown 取（kind=lpc），取不到为 null 并记 diagnostics。region 不是 flags.outdoors。",
            "item_apply": "apply 是原版 armor_prop/*、weapon_prop/* 以及 init_<武器>(damage) 映射后的 apply/<key> 加成；整数字面量直接搬运，表达式放 apply_expr 且值为 null，未识别键保留原样并进 apply_unknown_keys/diagnostics。apply_source 逐键给源码行，兵器伤害另给 base_path/base_line。",
            "skill_action_source": {
                "lpc": "招式/系数在原版 LPC 中直接写死，原样搬运。",
                "derived": "原版只给公式或结构，数值由抽取器按源码公式解释得出。",
                "default": "原版缺键，按 LPC mapping 语义取 0；缺键记入 coefficient_gaps 与 diagnostics.jsonl。",
                "missing": "文案缺失，没有可搬运原文（仅 name_source 使用）。",
            },
            "skill_hook": "hooks 五项固定齐全；value_kind=code 表示钩子函数存在、value 为 null 而 expr 为源码函数体；value_kind=default 表示明确占位 value=0 并写 reason。",
            "npc_drops": {
                "chance": "1.0 表示原版必掉；小于 1 由源码 random 条件折算；null 表示无法静态折算，必须读 chance_expr。",
                "chance_expr": "源码概率条件原文；null 表示原版没有概率判定。",
                "landing": "引擎实际落地方式；本项目不建模尸体，统一为 ground。",
                "source_landing": "原版实际放置位置（corpse/ground），仅作溯源，不影响引擎落地。",
                "drops_source": "lpc 表示 die() 中有真实掉落；none 表示源码无掉落，drops 为空表且 drops_note 写明原因。",
            },
            "npc_attrs_roll": "attrs_roll[attr]=[min,max] 是原版种族 setup 的 random(N)+M 掷骰区间（闭区间）；仅当该属性没有被显式 set 时出现。引擎必须按创世种子生成随机值，不得取 min/max 或中位数写死。attrs_roll_source 为每个区间的源码文件与行号。",
            "dialogue_fallback": "kind=fallback 且带 fallback_reason 表示原版函数体含条件/状态/物品/教学，不能压成固定文本；fallback_source 指向函数定义行。loader 只保留主题键，不得伪造台词。",
            "action_coefficient_default": "动作条目缺 damage/parry/force/dodge 键时按 LPC mapping 取 0；coefficient_source_detail 给出条目行号与 combatd.lpc 语义行号；coefficient_default_basis 给出四键证据及 inherit 链检查（inherited_defaults 为空即无父技能隐式默认）。",
            "npc_equipment": "equipment[].ref 必须在 items.json 中存在；ref 已补前导 / 并去掉 .lpc；缺 set_weight 时按基类默认 100 并带 weight_source=default。",
            "npc_temp_apply": "create() 内 set_temp 的静态快照，按创世即带处理；key 为原版键名，known=false 表示不在战斗白名单（原样保留并记诊断）；value 为整数时直接可用，为 null 时读 value_expr，不折算；source_kind=lpc 表示照源码搬运。",
            "xkx_quests": "只收切片内可闭环的 4 条支线；preconditions 是原版 set_temp/accept_object 的触发语义，不是运行时状态；动态任务奖励 exp/pot/score 以 base+random 表达，由引擎用 PRNG(seed, seq, salt) 按 exp→pot除数→score 顺序取数；静态清单与切片外动态任务只进 sorted counts 与 diagnostics，不入库。",
        },
        "authored_descriptions": authored,
        "coverage_semantics": {
            "source": "对原版忠实度：只统计 desc_source=lpc 的条目；低于 threshold 视为回归。",
            "total": "玩家可见描述完整性：含 authored，必须 100%。",
        },
        "files": {
            name: {"sha256": hashes[name], "bytes": len(files[name].encode("utf-8"))}
            for name in sorted(files)
        },
        "content_hash": content_hash,
    }
    files["manifest.json"] = dumps(manifest)
    return files


def write_outputs(outdir: str, files: dict[str, str]) -> dict[str, int]:
    os.makedirs(outdir, exist_ok=True)
    sizes = {}
    for name in sorted(files):
        path = os.path.join(outdir, name)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(files[name])
        sizes[name] = len(files[name].encode("utf-8"))
    return sizes


def verify_outputs(outdir: str, files: dict[str, str]) -> list[str]:
    """逐文件比对现有产物与本次重抽结果，返回差异文件名。"""
    diffs = []
    for name in sorted(files):
        path = os.path.join(outdir, name)
        if not os.path.isfile(path):
            diffs.append(name)
            continue
        with open(path, encoding="utf-8") as fh:
            existing = fh.read()
        if existing != files[name]:
            diffs.append(name)
    return diffs


def extract_to_temp(src_root, areas):
    """幂等自检用：重新抽一遍到临时目录。"""
    from . import extract as extract_mod

    ex = extract_mod.Extractor(src_root, areas)
    pack = ex.run()
    from . import validate as validate_mod

    diags = list(ex.diagnostics) + validate_mod.validate(pack)
    counts = ex.counts()
    source_info = {
        "repo": "fluffos/xkx100",
        "commit": extract_mod.source_commit(src_root),
        "areas": list(areas),
    }
    files = build_outputs(pack, diags, counts, ex.coverage, source_info)
    tmp = tempfile.mkdtemp(prefix="xkx-verify-")
    write_outputs(tmp, files)
    return tmp, files
