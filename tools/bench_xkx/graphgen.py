"""合成房间图 + 合成内容包，供 xkx 千房间性能实测使用。

只读产品接口，不依赖 content/ 下的真实内容包、不等待外部数据。
房间图是确定性的矩形网格：左/右/上/下相邻互指，天然连通、出口对称、
id 唯一；跳数距离 = 曼哈顿距离，便于按距离分层测 BFS。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from wsim.core.content import DEFAULT_ECONOMY, Content

ITEM_IDS = ("food", "drink", "medicine", "tool", "luxury")
NPC_ROLES = ("worker", "merchant", "guard", "cook", "clerk")


@dataclass(frozen=True)
class SyntheticSpec:
    rooms: int
    npcs: int
    shops: int
    seed: int = 42

    @property
    def key(self) -> str:
        return f"r{self.rooms}-n{self.npcs}-s{self.shops}"


def grid_shape(rooms: int) -> tuple[int, int]:
    """返回 (列数, 行数)；列数取 sqrt 向上取整，保证近似正方形。"""
    if rooms <= 0:
        return (0, 0)
    width = int(math.ceil(math.sqrt(rooms)))
    height = int(math.ceil(rooms / width))
    return width, height


def room_id(index: int) -> str:
    return f"r{index:05d}"


def room_coord(index: int, width: int) -> tuple[int, int]:
    return divmod(index, width)


def manhattan(a: int, b: int, width: int) -> int:
    ar, ac = room_coord(a, width)
    br, bc = room_coord(b, width)
    return abs(ar - br) + abs(ac - bc)


def pair_at_distance(
    rooms: int, width: int, distance: int, seed: int = 42, tries: int = 400
) -> tuple[str, str] | None:
    """找一对曼哈顿距离恰为 distance 的房间；找不到返回 None。"""
    import random

    rng = random.Random(seed * 1_000_003 + distance)
    for _ in range(tries):
        src = rng.randrange(rooms)
        sr, sc = room_coord(src, width)
        dr = rng.randint(0, distance)
        dc = distance - dr
        if rng.random() < 0.5:
            dr = -dr
        else:
            dc = -dc
        tr, tc = sr + dr, sc + dc
        if tr < 0 or tc < 0:
            continue
        target = tr * width + tc
        if target >= rooms:
            continue
        if tr >= grid_shape(rooms)[1]:
            continue
        if manhattan(src, target, width) != distance:
            continue
        return room_id(src), room_id(target)
    return None


def build_content(spec: SyntheticSpec) -> Content:
    """生成合成内容：locations / npcs / items / verbs / economy。

    每间房创世时会自动建一个地点行会（faction）与一个区域金库账户，这是
    产品 `build_genesis` 的真实行为；商业体只建在 `shops` 个房间，便于把
    “房间数”与“商业体数”分开观测。
    """
    width, height = grid_shape(spec.rooms)
    content = Content()
    content.economy = dict(DEFAULT_ECONOMY)

    locations: dict[str, dict[str, Any]] = {}
    for i in range(spec.rooms):
        r, c = room_coord(i, width)
        exits: list[str] = []
        # 左/右
        if c > 0:
            exits.append(room_id(i - 1))
        if c + 1 < width and i + 1 < spec.rooms:
            exits.append(room_id(i + 1))
        # 上/下
        if r > 0:
            exits.append(room_id(i - width))
        if r + 1 < height and i + width < spec.rooms:
            exits.append(room_id(i + width))
        locations[room_id(i)] = {
            "id": room_id(i),
            "name": f"合成房{i:05d}",
            "exits": sorted(exits),
            "services": [],
        }
    # 出口严格互指（网格生成已保证；这里再校验一次，避免边界行遗漏）。
    for lid, loc in locations.items():
        for nxt in list(loc["exits"]):
            if lid not in locations[nxt]["exits"]:
                locations[nxt]["exits"].append(lid)
        loc["exits"] = sorted(set(loc["exits"]))

    # 商业体：均匀撒在网格前 shops 个房间（每店卖一种物品，保持商品种类常数）。
    shop_rooms = sorted(locations)[: spec.shops]
    for idx, lid in enumerate(shop_rooms):
        locations[lid]["services"] = [ITEM_IDS[idx % len(ITEM_IDS)]]

    content.locations = locations
    content.items = {
        item: {"id": item, "name": f"合成物品{item}", "category": "food", "base_price": 10 + i}
        for i, item in enumerate(ITEM_IDS)
    }
    content.verbs = {
        "move": {"name": "move", "args": ["to"], "cooldown": 0, "allowed_roles": []},
        "work": {"name": "work", "args": ["at", "hours"], "cooldown": 0, "allowed_roles": []},
        "cultivate": {"name": "cultivate", "args": ["hours", "skill"], "cooldown": 0, "allowed_roles": []},
        "consume": {"name": "consume", "args": ["item"], "cooldown": 0, "allowed_roles": []},
    }
    content.economy.update({"stamina_move": 5, "stamina_work": 12, "stamina_cultivate": 4, "sell_ratio": 0.6})

    loc_ids = sorted(locations)
    npcs: list[dict[str, Any]] = []
    for i in range(spec.npcs):
        nid = f"npc{i:04d}"
        home = loc_ids[(i * 97 + spec.seed) % len(loc_ids)] if loc_ids else ""
        role = NPC_ROLES[i % len(NPC_ROLES)]
        npcs.append(
            {
                "id": nid,
                "name": f"合成NPC{i:04d}",
                "role": role,
                "personality": {"traits": [], "speech_style": ""},
                "goals": [{"id": f"g{i}", "type": "earn_money", "priority": 1.0}],
                "assets": {"money": 100, "items": []},
                "relations": [],
                "home": home,
            }
        )
    content.npcs = npcs
    return content


def write_content_pack(root: Path, content: Content) -> Path:
    """把合成内容写成 WorldSession 能加载的 content/*.yaml。

    只写临时目录；不碰仓库 content/。
    """
    content_dir = Path(root) / "content"
    content_dir.mkdir(parents=True, exist_ok=True)
    locations = [
        {
            "id": loc["id"],
            "name": loc["name"],
            "exits": list(loc.get("exits") or []),
            "services": list(loc.get("services") or []),
        }
        for loc in sorted(content.locations.values(), key=lambda x: x["id"])
    ]
    npcs = [
        {
            **npc,
            "home": npc.get("home") or (sorted(content.locations)[0] if content.locations else ""),
        }
        for npc in content.npcs
    ]
    (content_dir / "locations.yaml").write_text(
        yaml.safe_dump({"locations": locations}, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    (content_dir / "npcs.yaml").write_text(
        yaml.safe_dump({"npcs": npcs}, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    (content_dir / "items.yaml").write_text(
        yaml.safe_dump({"items": list(content.items.values())}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    (content_dir / "verbs.yaml").write_text(
        yaml.safe_dump({"verbs": [{"name": v} for v in sorted(content.verbs)]}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    (content_dir / "economy.yaml").write_text(
        yaml.safe_dump(content.economy, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return content_dir
