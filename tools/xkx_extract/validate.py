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


SOURCE_VALUES = {"lpc", "derived", "default", "missing"}


def _check_equipment_items(diags: list[dict], pack: dict) -> None:
    """NPC 装备引用的每个物品都必须在 items 表；缺一个就 error。"""
    item_ids = {i["id"] for i in pack.get("items", [])}
    for npc in pack.get("npcs", []):
        for slot in npc.get("equipment", []):
            ref = slot.get("ref")
            if ref not in item_ids:
                diags.append({
                    "rule": "E_EQUIP_ITEM_NOT_IN_ITEMS", "severity": "error",
                    "path": npc["source"]["path"], "line": npc["source"]["line"],
                    "message": f"NPC {npc['id']} 装备 {ref} 在 items 表中不存在",
                    "id": npc["id"], "ref": ref,
                })
            if slot.get("item_resolved") is False:
                diags.append({
                    "rule": "E_EQUIP_ITEM_UNRESOLVED", "severity": "error",
                    "path": npc["source"]["path"], "line": npc["source"]["line"],
                    "message": f"NPC {npc['id']} 装备 {ref} 源码不可解析",
                    "id": npc["id"], "ref": ref,
                })


def _check_npc_drops(diags: list[dict], pack: dict) -> None:
    """NPC 掉落：无掉落必须显式留痕；概率无法折算时保留原式；物品必须在 items 表。"""
    item_ids = {i["id"] for i in pack.get("items", [])}
    for npc in pack.get("npcs", []):
        path = npc["source"]["path"]
        line = npc["source"]["line"]
        src = npc.get("drops_source")
        if src not in ("lpc", "none"):
            diags.append({
                "rule": "E_DROP_SOURCE_INVALID", "severity": "error",
                "path": path, "line": line,
                "message": f"NPC {npc['id']} 的 drops_source 非法：{src!r}",
                "id": npc["id"],
            })
        drops = npc.get("drops")
        if not isinstance(drops, list):
            diags.append({
                "rule": "E_DROP_FIELD_MISSING", "severity": "error",
                "path": path, "line": line,
                "message": f"NPC {npc['id']} 缺少 drops 字段（无掉落也必须写空表）",
                "id": npc["id"],
            })
            continue
        if src == "none" and drops:
            diags.append({
                "rule": "E_DROP_SOURCE_INVALID", "severity": "error",
                "path": path, "line": line,
                "message": f"NPC {npc['id']} 标 drops_source=none 但 drops 非空",
                "id": npc["id"],
            })
        if src == "none" and not npc.get("drops_note"):
            diags.append({
                "rule": "E_DROP_NOTE_MISSING", "severity": "error",
                "path": path, "line": line,
                "message": f"NPC {npc['id']} 无掉落但没有写 drops_note 原因",
                "id": npc["id"],
            })
        for drop in drops:
            item = drop.get("item")
            if item not in item_ids:
                diags.append({
                    "rule": "E_DROP_ITEM_NOT_IN_ITEMS", "severity": "error",
                    "path": path, "line": drop.get("source", {}).get("line"),
                    "message": f"NPC {npc['id']} 的掉落物 {item} 不在 items 表",
                    "id": npc["id"], "ref": item,
                })
            qty = drop.get("qty")
            if not isinstance(qty, int) or qty < 1:
                diags.append({
                    "rule": "E_DROP_QTY_INVALID", "severity": "error",
                    "path": path, "line": drop.get("source", {}).get("line"),
                    "message": f"NPC {npc['id']} 掉落 {item} 的数量非法：{qty!r}",
                    "id": npc["id"], "ref": item,
                })
            chance = drop.get("chance")
            if chance is not None and not (isinstance(chance, (int, float)) and 0.0 <= chance <= 1.0):
                diags.append({
                    "rule": "E_DROP_CHANCE_INVALID", "severity": "error",
                    "path": path, "line": drop.get("source", {}).get("line"),
                    "message": f"NPC {npc['id']} 掉落 {item} 的概率非法：{chance!r}",
                    "id": npc["id"], "ref": item,
                })
            if chance is None and not drop.get("chance_expr"):
                diags.append({
                    "rule": "E_DROP_CHANCE_NO_EXPR", "severity": "error",
                    "path": path, "line": drop.get("source", {}).get("line"),
                    "message": f"NPC {npc['id']} 掉落 {item} 的概率无法折算却没有保留原式",
                    "id": npc["id"], "ref": item,
                })
            if drop.get("source_kind") != "lpc":
                diags.append({
                    "rule": "E_DROP_SOURCE_INVALID", "severity": "error",
                    "path": path, "line": drop.get("source", {}).get("line"),
                    "message": f"NPC {npc['id']} 掉落 {item} 的 source_kind 非法",
                    "id": npc["id"], "ref": item,
                })
            if drop.get("landing") != "ground":
                diags.append({
                    "rule": "E_DROP_LANDING_INVALID", "severity": "error",
                    "path": path, "line": drop.get("source", {}).get("line"),
                    "message": f"NPC {npc['id']} 掉落 {item} 的 landing 必须是 ground（不建模尸体）",
                    "id": npc["id"], "ref": item,
                })
            if drop.get("source_landing") not in ("corpse", "ground"):
                diags.append({
                    "rule": "E_DROP_LANDING_INVALID", "severity": "error",
                    "path": path, "line": drop.get("source", {}).get("line"),
                    "message": f"NPC {npc['id']} 掉落 {item} 的 source_landing 非法",
                    "id": npc["id"], "ref": item,
                })


def _src_error(diags, path, line, sid, message):
    diags.append({
        "rule": "E_SKILL_SOURCE_INVALID", "severity": "error",
        "path": path, "line": line, "message": message, "id": sid,
    })


def _check_skill_tables(diags: list[dict], pack: dict) -> None:
    """招式表与技能属性表的来源标记、默认值留痕校验。

    口径：`lpc` 是源码里直接读到的数值；`derived` 是我们按源码公式
    解释出来的数值；`default` 是源码缺键、按 LPC/引擎默认值补的 0。
    三类必须互斥且都有出处，engine 端不允许拿到无标记的裸数字。
    """
    actions = pack.get("skills_actions") or []
    stats = pack.get("skills_stats") or []
    action_ids = {a["id"] for a in actions}
    stat_ids = {s["id"] for s in stats}
    for sid in sorted(action_ids - stat_ids):
        diags.append({
            "rule": "E_SKILL_TABLE_MISMATCH", "severity": "error",
            "path": "kungfu/skill", "line": None,
            "message": f"招式表中的技能 {sid} 在属性表中不存在", "id": sid,
        })
    for sid in sorted(stat_ids - action_ids):
        diags.append({
            "rule": "E_SKILL_TABLE_MISMATCH", "severity": "error",
            "path": "kungfu/skill", "line": None,
            "message": f"属性表中的技能 {sid} 在招式表中不存在", "id": sid,
        })

    for skill in actions:
        path = skill["source"]["path"]
        line = skill["source"]["line"]
        for field in ("name_source", "text_source"):
            if skill.get(field, "missing") not in SOURCE_VALUES:
                _src_error(diags, path, line, skill["id"],
                           f"{field} 取值非法：{skill.get(field)!r}")
        gaps = set(skill.get("coefficient_gaps") or [])
        for act in skill["actions"]:
            coeffs = act["coefficients"]
            sources = act["coefficient_source"]
            if set(coeffs) != set(sources):
                _src_error(diags, path, act["source"]["line"], skill["id"],
                           f"招式 {act['name']} 的系数与来源标记键不一致")
            for key, src in sources.items():
                if src not in SOURCE_VALUES:
                    _src_error(diags, path, act["source"]["line"], skill["id"],
                               f"招式 {act['name']} 的 {key} 系数来源非法：{src!r}")
                if src == "default" and key not in gaps:
                    _src_error(diags, path, act["source"]["line"], skill["id"],
                               f"招式 {act['name']} 的 {key} 系数标 default，但未记入 coefficient_gaps")

    for skill in stats:
        path = skill["source"]["path"]
        line = skill["source"]["line"]
        if skill["category_source"] not in SOURCE_VALUES:
            _src_error(diags, path, line, skill["id"], "category_source 取值非法")
        if skill["apply_source"] not in SOURCE_VALUES:
            _src_error(diags, path, line, skill["id"], "apply_source 取值非法")
        if skill["apply_source"] == "default":
            if skill["apply"] or not skill.get("apply_reason"):
                _src_error(diags, path, line, skill["id"],
                           "apply 标 default 时必须为空表并写明默认原因")
        if skill["max_level"]["source_kind"] == "default" and not skill["max_level"].get("reason"):
            _src_error(diags, path, line, skill["id"], "max_level 默认值缺少 reason")
        for key in ("learn_bonus", "practice_bonus"):
            item = skill["curve"][key]
            if item["source_kind"] == "default" and not item.get("reason"):
                _src_error(diags, path, line, skill["id"], f"{key} 默认值缺少 reason")
        pp = skill["curve"]["power_point"]
        if pp["source_kind"] == "default" and not pp.get("reason"):
            _src_error(diags, path, line, skill["id"], "power_point 默认值缺少 reason")
        for name, hook in skill["hooks"].items():
            if hook["implemented"]:
                if hook["value_kind"] != "code" or not hook.get("expr"):
                    _src_error(diags, path, line, skill["id"],
                               f"钩子 {name} 标 implemented 但没有源码表达式")
            else:
                if hook["value"] != 0 or hook["value_kind"] != "default" or not hook.get("reason"):
                    _src_error(diags, path, line, skill["id"],
                               f"钩子 {name} 未实现时必须显式 value=0 且写明原因")


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

    # 9. 招式表 / 技能属性表：来源标记互斥、默认值必须留痕
    _check_skill_tables(diags, pack)

    # 10. NPC 掉落：无掉落必须显式留痕，概率不折算成必掉
    _check_npc_drops(diags, pack)

    # 11. NPC 装备：每个 ref 必须在 items 表，缺一个即 error
    _check_equipment_items(diags, pack)

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
