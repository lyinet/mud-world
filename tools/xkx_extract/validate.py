"""内容包校验：按 specs/xkx-parity/plan.md §4.4 分级。

error 级阻断发布；warning/info 逐条列出，不静默丢弃。
"""

from __future__ import annotations

from collections import Counter

DIRECTIONS = {
    "north", "south", "east", "west", "up", "down",
    "northeast", "northwest", "southeast", "southwest",
    "northup", "southup", "eastup", "westup",
    "northdown", "southdown", "eastdown", "westdown",
    "enter", "out",
}


def _check_desc_source(diags: list[dict], rec: dict, kind: str) -> None:
    """描述来源只允许 lpc / authored / null；authored 必须逐条留痕。"""
    desc = rec.get("desc")
    source = rec.get("desc_source")
    if source not in ("lpc", "authored", None):
        diags.append({
            "rule": "E_DESC_SOURCE_INVALID", "severity": "error",
            "path": rec["source"]["path"], "line": rec["source"]["line"],
            "message": f"{kind} {rec['id']} 的 desc_source 取值非法：{source!r}",
            "id": rec["id"],
        })
    elif source == "authored":
        if not desc:
            diags.append({
                "rule": "E_DESC_AUTHORED_EMPTY", "severity": "error",
                "path": rec["source"]["path"], "line": rec["source"]["line"],
                "message": f"{kind} {rec['id']} 标记为 authored 但没有描述文本",
                "id": rec["id"],
            })
            return
        diags.append({
            "rule": "I_DESC_AUTHORED", "severity": "info",
            "path": rec["source"]["path"], "line": rec["source"]["line"],
            "message": f"{kind} {rec['id']} 使用自撰描述（desc_source=authored），不计入 source 覆盖率",
            "id": rec["id"],
        })
    elif desc and source is None:
        diags.append({
            "rule": "E_DESC_SOURCE_INVALID", "severity": "error",
            "path": rec["source"]["path"], "line": rec["source"]["line"],
            "message": f"{kind} {rec['id']} 有描述但未标 desc_source",
            "id": rec["id"],
        })


def validate(pack: dict) -> list[dict]:
    """返回诊断列表；调用方负责合并进 diagnostics.jsonl。"""
    diags: list[dict] = []
    rooms = pack["rooms"]
    room_by_id = {r["id"]: r for r in rooms}
    exits = pack["exits"]
    slice_ids = {r["id"] for r in rooms if r["origin"] == "slice"}

    # 1. 重复 id
    counts = Counter(r["id"] for r in rooms)
    for rid, n in sorted(counts.items()):
        if n > 1:
            diags.append({
                "rule": "E_DUP_ROOM_ID", "severity": "error",
                "path": room_by_id[rid]["source"]["path"], "line": room_by_id[rid]["source"]["line"],
                "message": f"房间 id 出现 {n} 次：{rid}", "id": rid,
            })

    # 2. 出口方向名与悬空
    for ex in exits:
        if ex.get("from_kind") == "boundary_stub":
            continue
        if not ex.get("dir"):
            diags.append({
                "rule": "E_EXIT_NO_DIR", "severity": "error",
                "path": room_by_id.get(ex["from"], {}).get("source", {}).get("path"),
                "line": ex.get("line"),
                "message": f"出口缺少方向名：{ex['from']} -> {ex['to']}",
            })
        elif ex["dir"] not in DIRECTIONS:
            diags.append({
                "rule": "W_UNKNOWN_DIRECTION", "severity": "warning",
                "path": room_by_id.get(ex["from"], {}).get("source", {}).get("path"),
                "line": ex.get("line"),
                "message": f"非标准方向名：{ex['dir']}",
                "dir": ex["dir"],
            })
        if ex["to"] not in room_by_id and ex["to"] not in slice_ids:
            diags.append({
                "rule": "E_DANGLING_EXIT", "severity": "error",
                "path": room_by_id.get(ex["from"], {}).get("source", {}).get("path"),
                "line": ex.get("line"),
                "message": f"出口 {ex['dir']} 指向不存在的房间：{ex['to']}",
                "target": ex["to"],
            })

    # 3. 出口互逆缺失（只查切片内房间之间）
    edges = {(ex["from"], ex["to"]) for ex in exits}
    for ex in exits:
        if ex.get("from_kind") == "boundary_stub" or ex.get("boundary"):
            continue
        if ex["to"] not in slice_ids or ex["from"] not in slice_ids:
            continue
        if (ex["to"], ex["from"]) not in edges:
            diags.append({
                "rule": "W_ASYMMETRIC_EXIT", "severity": "warning",
                "path": room_by_id[ex["from"]]["source"]["path"], "line": ex.get("line"),
                "message": f"{ex['from']} 的 {ex['dir']} 出口到 {ex['to']}，但目标没有回程出口",
                "dir": ex["dir"], "target": ex["to"],
            })

    # 4. 关键房间无描述：入度 >= 3 的枢纽房间
    indegree = Counter(ex["to"] for ex in exits if ex.get("from_kind") != "boundary_stub")
    for rid in sorted(indegree):
        room = room_by_id.get(rid)
        if room is None or room["origin"] != "slice":
            continue
        if indegree[rid] >= 3 and not room["desc"]:
            diags.append({
                "rule": "W_KEY_ROOM_NO_DESC", "severity": "warning",
                "path": room["source"]["path"], "line": room["source"]["line"],
                "message": f"枢纽房间（入度 {indegree[rid]}）没有描述：{rid}",
                "id": rid,
            })

    # 5. 物品无价 / 描述来源标记
    for item in pack["items"]:
        if item["origin"] != "slice":
            continue
        if item["price"] is None:
            diags.append({
                "rule": "W_NO_PRICE", "severity": "warning",
                "path": item["source"]["path"], "line": item["source"]["line"],
                "message": "源码没有 set(\"value\") 价格", "id": item["id"],
            })
        _check_desc_source(diags, item, "物品")

    # 6. NPC 描述来源标记
    for npc in pack["npcs"]:
        if npc["origin"] != "slice":
            continue
        _check_desc_source(diags, npc, "NPC")

    # 7. 商店库存必须能落到物品
    for shop in pack["shops"]:
        for entry in shop["stock"]:
            if entry.get("item") not in room_by_id and entry.get("name") is None:
                diags.append({
                    "rule": "E_SHOP_STOCK_MISSING", "severity": "error",
                    "path": shop["source"]["path"], "line": shop["source"]["line"],
                    "message": f"商店库存无法解析：{entry.get('item')}", "ref": entry.get("item"),
                })

    # 8. 技能必须都有中文名
    for skill in pack["skills"]:
        if skill.get("name") is None:
            diags.append({
                "rule": "W_SKILL_NAME_MISSING", "severity": "warning",
                "path": skill["source"]["path"], "line": 1,
                "message": f"技能 {skill['id']} 没有可静态提取的中文名", "id": skill["id"],
            })

    return diags


def summarize(diags: list[dict]) -> dict:
    by_rule = Counter(d["rule"] for d in diags)
    by_sev = Counter(d["severity"] for d in diags)
    return {
        "errors": by_sev.get("error", 0),
        "warnings": by_sev.get("warning", 0),
        "infos": by_sev.get("info", 0),
        "by_rule": dict(sorted(by_rule.items())),
    }
