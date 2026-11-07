"""NPC 死亡掉落抽取。

范围：NPC 的 `die()` 覆盖里 `new(<obj>)` 落地面的逻辑。原版还有一条
通用路径：`make_corpse()` 创建尸体、把随身装备塞进尸体。本项目不建模
尸体，`drops` 只记 `die()` 里显式创建并落地的物品；装备由引擎按
`npcs.json.equipment` 处理，避免双计。

概率照原式保留：`if (random(3) == 0)` 记 `chance=1/3` 且
`chance_expr="random(3)==0"`；无法静态折算的记 `chance=null` 并进
`diagnostics.jsonl`，不猜、不四舍五入成必掉。
"""

from __future__ import annotations

import os
import re

from .skills_actions import _line_at, _match, _nearest_if, _read, _strip_comments

_DIE_RE = re.compile(r"(?:void|int|mixed)\s+die\s*\(")
_NEW_RE = re.compile(r"new\s*\(\s*([^)]*)\)")
_MOVE_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*->\s*move\s*\(\s*([^)]*)\)")
_LOOP_RE = re.compile(r"\b(for|while)\s*\(")


def _die_body(text: str):
    """返回 (函数体, 函数体在全文中的起始下标)；找不到 die() 返回 (None, -1)。"""
    m = _DIE_RE.search(text)
    if not m:
        return None, -1
    start = text.find("{", m.end())
    if start < 0:
        return None, -1
    end = _match(text, start)
    if end < 0:
        return None, -1
    return text[start + 1:end], start + 1


def _resolve_new_arg(raw: str, rel_path: str) -> str | None:
    """解析 `new("path")` / `new(__DIR__ "obj/x")`。"""
    raw = raw.strip()
    if not raw:
        return None
    if raw.startswith('"'):
        parts = re.findall(r'"([^"]*)"', raw)
        if len(parts) == 1:
            return "/" + parts[0].lstrip("/")
        return None
    if raw.startswith("__DIR__"):
        rest = raw[len("__DIR__"):]
        parts = re.findall(r'"([^"]*)"', rest)
        if not parts:
            return None
        base = os.path.dirname(rel_path)
        return "/" + os.path.normpath(os.path.join(base, "".join(parts))).replace(os.sep, "/").lstrip("/")
    return None


def _chance_from_condition(cond: str | None):
    """把 `if (random(N) == K)` 一类条件折算成概率；不能折算返回 (None, 原因)。"""
    if not cond:
        return 1.0, None
    c = " ".join(cond.split())

    def _num(pattern):
        m = re.fullmatch(pattern, c)
        return (int(m.group(1)), int(m.group(2))) if m else None

    pair = _num(r"random\s*\(\s*(\d+)\s*\)\s*==\s*(\d+)")
    if pair:
        n, k = pair
        if n <= 0 or k >= n:
            return 0.0, None
        return (1.0 / n if k == 0 else 0.0), None

    pair = _num(r"random\s*\(\s*(\d+)\s*\)\s*<\s*(\d+)")
    if pair:
        n, k = pair
        if n <= 0:
            return None, c
        return min(1.0, max(0.0, k / n)), None

    pair = _num(r"random\s*\(\s*(\d+)\s*\)\s*>=\s*(\d+)")
    if pair:
        n, k = pair
        if n <= 0:
            return None, c
        return min(1.0, max(0.0, (n - k) / n)), None

    pair = _num(r"random\s*\(\s*(\d+)\s*\)\s*>\s*(\d+)")
    if pair:
        n, k = pair
        if n <= 0:
            return None, c
        return min(1.0, max(0.0, (n - 1 - k) / n)), None

    # 条件里出现随机数但我们不会折算：保留原文，交给引擎。
    if "random" in c:
        return None, c
    return 1.0, None


def _loop_before(body: str, idx: int) -> str | None:
    """判断 new 是否被 for/while 包着；返回最近的循环头原文。"""
    window = body[max(0, idx - 200):idx]
    found = None
    for m in _LOOP_RE.finditer(window):
        found = m.group(0)
    return found


def extract_npc_drops(src_root: str, npcs: list[dict]) -> tuple[list[dict], dict, set[str]]:
    """就地给每个 NPC 写 `drops` / `drops_source` / `drops_note`。

    返回 (diagnostics, counts, 需要补进 items 的外部物品 id 集合)。
    """
    diags: list[dict] = []
    item_refs: set[str] = set()
    counts = {
        "npcs_with_drops": 0,
        "drop_entries": 0,
        "drops_guaranteed": 0,
        "drops_random": 0,
        "drops_unresolved_chance": 0,
        "npcs_no_drops": 0,
    }

    for npc in npcs:
        rel_path = npc["source"]["path"]
        text = _read(src_root, rel_path)
        drops: list[dict] = []
        if text is None:
            npc["drops"] = []
            npc["drops_source"] = "none"
            npc["drops_note"] = "源码不可读，按无掉落处理"
            counts["npcs_no_drops"] += 1
            diags.append({
                "rule": "W_DROP_SOURCE_UNREADABLE", "severity": "warning",
                "path": rel_path, "line": None,
                "message": f"NPC {npc['id']} 源码不可读，无法确认掉落", "id": npc["id"],
            })
            continue

        body, body_start = _die_body(text)
        if body is None:
            npc["drops"] = []
            npc["drops_source"] = "none"
            npc["drops_note"] = "源码无 die() 覆盖，按无掉落处理，不虚构"
            counts["npcs_no_drops"] += 1
            continue

        cleaned = _strip_comments(body)
        for m in _NEW_RE.finditer(cleaned):
            ref = _resolve_new_arg(m.group(1), rel_path)
            abs_idx = body_start + m.start()
            line = _line_at(text, abs_idx)
            if not ref:
                diags.append({
                    "rule": "W_DROP_ITEM_UNRESOLVED", "severity": "warning",
                    "path": rel_path, "line": line,
                    "message": f"die() 中的 new({m.group(1).strip()}) 无法解析为物品路径",
                    "id": npc["id"],
                })
                continue
            rel_item = ref.lstrip("/") + ".lpc"
            item_exists = os.path.isfile(os.path.join(src_root, rel_item))
            if not item_exists:
                diags.append({
                    "rule": "W_DROP_ITEM_MISSING", "severity": "warning",
                    "path": rel_path, "line": line,
                    "message": f"掉落物 {ref} 的源文件不存在，item_resolved=false",
                    "id": npc["id"], "ref": ref,
                })
            else:
                item_refs.add(ref)

            cond = _nearest_if(cleaned, m.start())
            chance, chance_expr = _chance_from_condition(cond)
            if chance is None:
                counts["drops_unresolved_chance"] += 1
                diags.append({
                    "rule": "W_DROP_CHANCE_EXPR", "severity": "warning",
                    "path": rel_path, "line": line,
                    "message": f"掉落概率条件无法静态折算，保留原式：{chance_expr}",
                    "id": npc["id"], "ref": ref,
                })
            elif chance >= 1.0:
                counts["drops_guaranteed"] += 1
            else:
                counts["drops_random"] += 1

            loop = _loop_before(cleaned, m.start())
            if loop:
                diags.append({
                    "rule": "W_DROP_QTY_LOOP", "severity": "warning",
                    "path": rel_path, "line": line,
                    "message": f"掉落物 {ref} 在循环里创建，数量不是 1，需人工复核",
                    "id": npc["id"], "ref": ref,
                })

            move_target = None
            var = None
            prefix = cleaned[cleaned.rfind("\n", 0, m.start()) + 1:m.start()]
            var_m = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*$", prefix)
            if var_m:
                var = var_m.group(1)
            if var:
                for mm in _MOVE_RE.finditer(cleaned, m.end()):
                    if mm.group(1) == var:
                        move_target = mm.group(2).strip()
                        break
                    if mm.start() - m.end() > 400:
                        break
            corpse = "corpse" in (move_target or "").lower()

            drops.append({
                "item": ref,
                "qty": 1,
                "chance": round(chance, 6) if chance is not None else None,
                "chance_expr": chance_expr,
                "item_resolved": item_exists,
                "landing": "ground",
                "source_landing": "corpse" if corpse else "ground",
                "source": {"path": rel_path, "line": line},
                "source_kind": "lpc",
            })

        if drops:
            npc["drops"] = drops
            npc["drops_source"] = "lpc"
            npc["drops_note"] = None
            counts["npcs_with_drops"] += 1
            counts["drop_entries"] += len(drops)
        else:
            npc["drops"] = []
            npc["drops_source"] = "none"
            npc["drops_note"] = "die() 存在但没有创建物品落地，按无掉落处理"
            counts["npcs_no_drops"] += 1

    return diags, counts, item_refs
