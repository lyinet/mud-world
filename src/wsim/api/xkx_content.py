"""xkx 内容包 → 引擎 Content 的只读适配层。

`content/xkx/` 是另一路交付的 JSON 内容包：
rooms.json / exits.json / npcs.json / items.json / shops.json / sects.json。
引擎的 `core.load_content` 只认 `locations.yaml/npcs.yaml/items.yaml`，
且不解析出口方向与状态。本模块只在 API 进程内把 JSON 转成内存里的
`Content`，不写内容包、不复制内容、不改引擎加载规则。

约定：
- 只取 `origin == "slice"` 的房间（195 间）；`origin == "boundary"` 的
  6 间边界房不建地点，但指向它们的出口仍保留并标记“未开放”。
- 出口方向以 `exits.json` 的 `dir` 为准；`status != open` 或
  `traversable == false` 的出口保留在页面但不可走，并写明原因。
- NPC 的 `home` 取自房间 `objects` 里的 `kind == "npc"` 摆放；
  内容里没有摆放的 NPC 交给创世按确定性规则分配，不编造位置。
"""
from __future__ import annotations

import json
import warnings
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ..core.content import Content, DEFAULT_ECONOMY, _normalize_npc, _repair_exits

XKX_REQUIRED_FILES = ("rooms.json", "exits.json", "npcs.json")

# 方向排序与反向：反向边由 `_repair_exits` 自动补齐，方向按正向推导。
DIRECTION_ORDER: tuple[str, ...] = (
    "north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest",
    "northup", "eastup", "southup", "westup", "up",
    "northdown", "eastdown", "southdown", "westdown", "down",
    "out", "enter",
)
OPPOSITE_DIRECTION: dict[str, str] = {
    "north": "south",
    "south": "north",
    "east": "west",
    "west": "east",
    "northeast": "southwest",
    "southwest": "northeast",
    "northwest": "southeast",
    "southeast": "northwest",
    "up": "down",
    "down": "up",
    "northup": "southdown",
    "southdown": "northup",
    "southup": "northdown",
    "northdown": "southup",
    "eastup": "westdown",
    "westdown": "eastup",
    "eastdown": "westup",
    "westup": "eastdown",
    "out": "enter",
    "enter": "out",
}


_QUEST_NPC_KEYS = frozenset({"giver", "target", "npc", "target_npc"})


def _collect_quest_npc_refs(node: Any, out: set[str]) -> None:
    """收集任务里指向 NPC 的路径；target/giver 既可能是字符串也可能是 {id}。"""
    if isinstance(node, dict):
        for key, value in node.items():
            if str(key) in _QUEST_NPC_KEYS:
                if isinstance(value, str) and value.startswith("/"):
                    out.add(value)
                elif isinstance(value, dict) and str(value.get("id") or "").startswith("/"):
                    out.add(str(value["id"]))
            _collect_quest_npc_refs(value, out)
    elif isinstance(node, list):
        for value in node:
            _collect_quest_npc_refs(value, out)


def is_xkx_pack(root: Path | str) -> bool:
    """目录是否是 xkx JSON 内容包（不看文件名以外的任何配置）。"""
    path = Path(root)
    return all((path / name).is_file() for name in XKX_REQUIRED_FILES)


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _rows(data: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        data = data.get(key) or []
    return [row for row in (data or []) if isinstance(row, dict)]


def _int_or(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _direction_key(direction: str) -> tuple[int, str]:
    direction = str(direction or "")
    if direction in DIRECTION_ORDER:
        return DIRECTION_ORDER.index(direction), ""
    return len(DIRECTION_ORDER), direction


def _ref_name(ref: str) -> str:
    """内容包外的对象没有中文名时，用路径末段做兜底，不假装有名字。"""
    tail = str(ref or "").rstrip("/").rsplit("/", 1)[-1]
    return tail or str(ref or "")


def load_xkx_content(pack_root: Path | str) -> Content:
    """把 xkx JSON 内容包读成内存 Content；不修改任何文件。"""
    pack = Path(pack_root)
    rooms_raw = _rows(_read_json(pack / "rooms.json"), "rooms")
    exits_raw = _rows(_read_json(pack / "exits.json"), "exits")
    npcs_raw = _rows(_read_json(pack / "npcs.json"), "npcs")
    items_raw = _rows(_read_json(pack / "items.json"), "items") if (pack / "items.json").is_file() else []
    shops_raw = _rows(_read_json(pack / "shops.json"), "shops") if (pack / "shops.json").is_file() else []
    sects_raw = _rows(_read_json(pack / "sects.json"), "sects") if (pack / "sects.json").is_file() else []
    skills_raw = _rows(_read_json(pack / "skills.json"), "skills") if (pack / "skills.json").is_file() else []
    stats_path = pack / "skills_stats.json"
    skills_stats_raw = _rows(_read_json(stats_path), "skills_stats") if stats_path.is_file() else []
    quests_raw = _rows(_read_json(pack / "quests.json"), "quests") if (pack / "quests.json").is_file() else []

    rooms: dict[str, dict[str, Any]] = {}
    for raw in rooms_raw:
        rid = str(raw.get("id") or "")
        if rid:
            rooms[rid] = raw
    boundary_ids = {rid for rid, raw in rooms.items() if str(raw.get("origin")) == "boundary"}
    slice_ids = {rid for rid in rooms if rid not in boundary_ids}

    exits_by_from: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in exits_raw:
        src = str(raw.get("from") or "")
        if src in rooms:
            exits_by_from[src].append(raw)

    # 房间摆放：NPC 落点与地面物件（只读，用于页面展示）。
    placement: dict[str, list[str]] = {}
    objects_by_room: dict[str, list[dict[str, Any]]] = {}
    for rid in sorted(slice_ids):
        rows: list[dict[str, Any]] = []
        for obj in rooms[rid].get("objects") or []:
            if not isinstance(obj, dict):
                continue
            ref = str(obj.get("ref") or "")
            if not ref:
                continue
            kind = str(obj.get("kind") or "")
            count = _int_or(obj.get("count"), 0)
            rows.append({"kind": kind, "ref": ref, "count": count})
            if kind == "npc":
                placement.setdefault(ref, []).append(rid)
        objects_by_room[rid] = rows

    content = Content()
    content.sources = {
        "locations": "content/xkx/rooms.json+exits.json",
        "npcs": "content/xkx/npcs.json",
        "items": "content/xkx/items.json",
        "sects": "content/xkx/sects.json",
        "xkx_quests": "content/xkx/quests.json",
        "economy": "xkx_adapter/defaults",
    }

    # 任务：按 id 索引；只保留可完成的任务（抽取器已把静态清单与切片外
    # 动态任务留在 summary/diagnostics，不入库）。
    for raw in quests_raw:
        qid = str(raw.get("id") or "")
        if qid:
            content.xkx_quests[qid] = raw

    # 物品先建：房间地面物件与商店货架都要引用物品名。
    for raw in items_raw:
        iid = str(raw.get("id") or "")
        if not iid:
            continue
        price = raw.get("price")
        base = max(1, _int_or(price, 10)) if price is not None else 10
        content.items[iid] = {
            "id": iid,
            "name": str(raw.get("name") or _ref_name(iid)),
            "category": str(raw.get("category") or "misc"),
            "base_price": base,
            "unit": str(raw.get("unit") or "件"),
            "description": str(raw.get("desc") or ""),
            "weight": _int_or(raw.get("weight"), 0),
            # 内容包的装备槽与重量来源只读透传；引擎穿脱未接通时只做展示。
            "equip_slot": str(raw.get("equip_slot") or ""),
            "weight_source": str(raw.get("weight_source") or ""),
            "weight_reason": str(raw.get("weight_reason") or ""),
        }

    # 引擎自带的初始行囊（actions 层发放）不在 xkx 内容包里；补几条展示用
    # 元数据，避免玩家在行囊里看到 flatbread 这种内部 id。价格沿用旧内容默认。
    for iid, (name, price, category) in {
        "flatbread": ("炊饼", 3, "food"),
        "coarse_cloth": ("粗布", 10, "cloth"),
    }.items():
        if iid not in content.items:
            content.items[iid] = {
                "id": iid,
                "name": name,
                "category": category,
                "base_price": price,
                "unit": "件",
                "description": "",
                "weight": 0,
                "engine_default": True,
            }

    # 商店：掌柜 NPC 所在的房间就是货架所在房间；只把库存列进 services。
    shop_by_npc: dict[str, dict[str, Any]] = {}
    for raw in shops_raw:
        npc_id = str(raw.get("npc") or raw.get("id") or "")
        if npc_id:
            shop_by_npc[npc_id] = raw
    stock_by_room: dict[str, list[str]] = defaultdict(list)
    for npc_id, shop in sorted(shop_by_npc.items()):
        homes = sorted(placement.get(npc_id) or [])
        if not homes:
            continue
        for row in shop.get("stock") or []:
            if not isinstance(row, dict):
                continue
            item_id = str(row.get("item") or "")
            if item_id:
                stock_by_room[homes[0]].append(item_id)

    # 地点：只建 slice 房间；边界房只作为“不可走出口”的目标名出现。
    for rid in sorted(slice_ids):
        raw = rooms[rid]
        exits_detail: list[dict[str, Any]] = []
        usable: list[str] = []
        for exit_row in exits_by_from.get(rid, []):
            to = str(exit_row.get("to") or "")
            if to not in rooms:
                continue
            direction = str(exit_row.get("dir") or "")
            status = str(exit_row.get("status") or "open")
            traversable = bool(exit_row.get("traversable", True))
            available = status == "open" and traversable and to in slice_ids
            reason = ""
            if to in boundary_ids:
                reason = f"这里是内容边界（{rooms[to].get('name') or _ref_name(to)}），尚未开放。"
            elif status != "open":
                reason = f"这道门现在关着（内容标注 {status}）。"
            elif not traversable:
                reason = "这一侧不可通行。"
            if available:
                usable.append(to)
            exits_detail.append(
                {
                    "to": to,
                    "dir": direction,
                    "status": status,
                    "traversable": traversable,
                    "name": str(rooms[to].get("name") or _ref_name(to)),
                    "available": available,
                    "reason": reason,
                }
            )
        content.locations[rid] = {
            "id": rid,
            "name": str(raw.get("name") or _ref_name(rid)),
            "description": str(raw.get("desc") or "").strip(),
            "exits": sorted(set(usable)),
            "services": sorted(set(stock_by_room.get(rid) or [])),
            "exits_detail": exits_detail,
            "objects": objects_by_room.get(rid) or [],
            "coord": raw.get("coord") or {},
            "flags": raw.get("flags") or {},
            # 区域信息只读透传：内容包没有时留空，API 层给「暂不可用」原因，不猜。
            "region": str(raw.get("region") or ""),
            "region_name": str(raw.get("region_name") or ""),
            "origin": str(raw.get("origin") or "slice"),
        }

    # 反向边：沿用引擎规则补齐，保证不存在单向死路；补出来的边按正向推导方向。
    _repair_exits(content)
    boundary_names = {rid: str(rooms[rid].get("name") or _ref_name(rid)) for rid in boundary_ids}
    for lid, loc in content.locations.items():
        known = {str(row["to"]) for row in loc["exits_detail"]}
        for target in loc["exits"]:
            if target in known:
                continue
            back = ""
            for row in content.locations[target]["exits_detail"]:
                if str(row["to"]) == lid:
                    back = OPPOSITE_DIRECTION.get(str(row["dir"]), "")
                    break
            loc["exits_detail"].append(
                {
                    "to": target,
                    "dir": back,
                    "status": "open",
                    "traversable": True,
                    "name": str(content.locations[target].get("name") or _ref_name(target)),
                    "available": True,
                    "reason": "",
                    "derived": True,
                }
            )
        loc["exits_detail"].sort(
            key=lambda row: (_direction_key(str(row.get("dir") or "")), str(row.get("name") or ""))
        )

    # NPC：只收 slice；home 用内容里的真实摆放，没摆放的留空交给创世。
    placed_npcs: dict[str, str] = {}
    for ref, homes in placement.items():
        if homes:
            placed_npcs[ref] = sorted(homes)[0]
    for idx, raw in enumerate(sorted(npcs_raw, key=lambda n: str(n.get("id") or ""))):
        nid = str(raw.get("id") or "")
        # 切片外的 NPC 默认不收；但若它被明确摆在切片房间（如马厩的枣红马/
        # 黄骠马/紫骝马/马夫），就必须收，否则玩家进房看不到原版在场 NPC。
        if str(raw.get("origin") or "slice") != "slice" and nid not in placed_npcs:
            continue
        npc = _normalize_npc(raw, idx)
        npc["role"] = str(raw.get("class") or raw.get("role") or "wanderer")
        npc["home"] = placed_npcs.get(nid, "")
        npc["personality"] = {
            "traits": [x for x in (raw.get("title"), raw.get("nickname"), raw.get("sect")) if x],
            "speech_style": str(raw.get("title") or ""),
        }
        # 战斗/技能字段原样保留，等 actions 层接上内容属性后直接可用。
        npc["attrs"] = dict(raw.get("attrs") or {})
        npc["vitals"] = dict(raw.get("vitals") or {})
        npc["skills"] = dict(raw.get("skills") or {})
        npc["sect"] = str(raw.get("sect") or "")
        content.npcs.append(npc)

    # 同模板多实例：原版 `reset()` 按 (房间, 模板) -> count 逐个 `new()`。
    # 规范实例占 placed_npcs[ref] 的第 1 槽（id 仍是模板路径），其余槽位
    # 派生为额外实例 `ref@room#n`；n 是该房间内的槽位，规范槽位不重复。
    # 只做派生与校验，不创建实体（P2 才进创世）。
    npc_by_id = {str(n["id"]): n for n in content.npcs}
    quest_npc_refs: set[str] = set()
    for quest in content.xkx_quests.values():
        _collect_quest_npc_refs(quest, quest_npc_refs)
    instances: list[dict[str, Any]] = []
    for ref in sorted(placement):
        npc = npc_by_id.get(ref)
        if npc is None:
            continue
        canonical_room = placed_npcs.get(ref, "")
        room_counts: dict[str, int] = {}
        for rid in sorted(set(placement[ref])):
            for obj in rooms[rid].get("objects") or []:
                if not isinstance(obj, dict):
                    continue
                if str(obj.get("kind") or "") != "npc" or str(obj.get("ref") or "") != ref:
                    continue
                count = _int_or(obj.get("count"), 0)
                if count > 0:
                    room_counts[rid] = max(room_counts.get(rid, 0), count)
        slots: list[tuple[str, int]] = []
        for rid in sorted(room_counts):
            start = 2 if rid == canonical_room else 1
            slots.extend((rid, n) for n in range(start, room_counts[rid] + 1))
        if not slots:
            continue
        # 只有真的会多出实例时才校验；单只摆放的师父/店主/任务 NPC 不告警。
        reason = ""
        if npc.get("relations"):
            reason = "has_relations"
        elif npc.get("is_master"):
            reason = "is_master"
        elif ref in shop_by_npc:
            reason = "is_shopkeeper"
        elif ref in quest_npc_refs:
            reason = "is_quest_npc"
        if reason:
            warnings.warn(f"clone_not_supported:{ref}:{reason}", RuntimeWarning, stacklevel=2)
            continue
        for rid, n in slots:
            instances.append(
                {
                    "id": f"{ref}@{rid}#{n}",
                    "template": ref,
                    "room": rid,
                    "instance": n,
                }
            )
    instances.sort(key=lambda row: (row["template"], row["room"], row["instance"]))
    content.npc_instances = instances

    # 门派：位置取门人出现最多的房间；没有摆放则留空，不编造。
    for raw in sects_raw:
        sid = str(raw.get("id") or "")
        if not sid:
            continue
        homes = [
            placed_npcs[m]
            for m in (str(x) for x in (raw.get("masters") or []))
            if placed_npcs.get(m)
        ]
        location = Counter(homes).most_common(1)[0][0] if homes else ""
        content.sects[sid] = {
            "id": sid,
            "name": str(raw.get("name") or sid),
            "location": location,
        }

    # 技能元数据只读加载：learn/practice 按原版曲线取 learn_bonus/practice_bonus。
    content.skills = list(skills_raw)
    content.skills_stats = list(skills_stats_raw)

    # 经济参数：沿用引擎默认；只把商人房间准备金调小到“够找零”的量。
    content.economy = dict(DEFAULT_ECONOMY)
    content.economy.update(
        {
            "business_stock": 30,
            "price_markup": 1.0,
            "money_supply": {
                "location_float": {rid: 900 for rid in sorted(stock_by_room)},
            },
        }
    )

    content.manifest_version = ""
    manifest_path = pack / "manifest.json"
    if manifest_path.is_file():
        try:
            manifest = _read_json(manifest_path)
        except (OSError, json.JSONDecodeError):
            manifest = {}
        if isinstance(manifest, dict):
            content.manifest_version = str(manifest.get("content_hash") or "")

    # 里程碑核对用的真实计数：不做四舍五入，页面与接口都直接展示。
    walkable = sum(
        1 for loc in content.locations.values() for row in loc["exits_detail"] if row.get("available")
    )
    derived = sum(
        1 for loc in content.locations.values() for row in loc["exits_detail"] if row.get("derived")
    )
    blocked = sum(
        1
        for loc in content.locations.values()
        for row in loc["exits_detail"]
        if not row.get("available")
    )
    content.world_stats = {
        "rooms_total": len(rooms),
        "rooms_in_slice": len(content.locations),
        "rooms_boundary": len(boundary_ids),
        "boundary_names": boundary_names,
        "exits_total": len(exits_raw),
        "exits_from_slice": sum(1 for raw in exits_raw if str(raw.get("from") or "") in slice_ids),
        "exits_walkable": walkable,
        "exits_walkable_targets": sum(len(loc["exits"]) for loc in content.locations.values()),
        "exits_blocked": blocked,
        "exits_derived": derived,
        "npcs_total": len(npcs_raw),
        "npcs_in_slice": len(content.npcs),
        "npcs_placed": sum(1 for npc in content.npcs if npc.get("home")),
        "items_total": len(items_raw),
        "items_in_slice": sum(1 for raw in items_raw if str(raw.get("origin") or "slice") == "slice"),
        "shops": len(shops_raw),
        "shop_goods": sum(len(shop.get("stock") or []) for shop in shops_raw),
        "sects": len(sects_raw),
        "masters": sum(1 for npc in content.npcs if npc.get("is_master")),
        "npc_instances": len(content.npc_instances),
        "xkx_quests": len(content.xkx_quests),
    }
    return content
