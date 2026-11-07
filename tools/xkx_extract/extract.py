"""xkx 切片抽取：LPC 源码 -> 引擎可消费的表。

范围：杭州 + 嘉兴 + 石梁（`d/hangzhou`、`d/jiaxing`、`d/shiliang`）。
产物口径与 `specs/xkx-parity/plan.md` 对齐；不做任何文案补写，
源码里没有的内容一律进 diagnostics。
"""

from __future__ import annotations

import os
import posixpath
import re
import subprocess
from collections import Counter, OrderedDict

from . import lpc

AREA_DIRS = ("hangzhou", "jiaxing", "shiliang")

ROOM_BASE = "ROOM"
NPC_BASE = "NPC"
MASTER_BASE = "F_MASTER"

# 物品基类 -> (category, equip_slot)
ITEM_BASES = {
    "ITEM": ("misc", None),
    "COMBINED_ITEM": ("misc", None),
    "MONEY": ("money", None),
    "CLOTH": ("armor", "cloth"),
    "HEAD": ("armor", "head"),
    "NECK": ("armor", "neck"),
    "ARMOR": ("armor", "armor"),
    "SURCOAT": ("armor", "surcoat"),
    "WAIST": ("armor", "waist"),
    "WRISTS": ("armor", "wrists"),
    "SHIELD": ("armor", "shield"),
    "FINGER": ("armor", "finger"),
    "HANDS": ("armor", "hands"),
    "BOOTS": ("armor", "boots"),
    "EQUIP": ("equip", None),
    "SWORD": ("weapon", "sword"),
    "BLADE": ("weapon", "blade"),
    "MACE": ("weapon", "mace"),
    "STAFF": ("weapon", "staff"),
    "WHIP": ("weapon", "whip"),
    "AXE": ("weapon", "axe"),
    "HAMMER": ("weapon", "hammer"),
    "DAGGER": ("weapon", "dagger"),
    "FORK": ("weapon", "fork"),
    "THROWING": ("weapon", "throwing"),
    "CLUB": ("weapon", "club"),
    "F_FOOD": ("food", None),
    "F_LIQUID": ("liquid", None),
    "PILL": ("medicine", None),
    "POWDER": ("medicine", None),
}

# 房间 flags：只保留引擎侧会用到的布尔/枚举标记。
ROOM_FLAGS = {
    "outdoors", "no_fight", "no_beg", "no_steal", "no_get", "no_clean_up",
    "no_drop", "valid_startroom", "shop_type", "owner", "no_fight_area",
}

_INHERIT_RE = re.compile(r"^inherit\s+([A-Za-z_][A-Za-z0-9_]*)\s*;", re.M)
_SKILL_HEADER_RE = re.compile(r"^//\s*([A-Za-z0-9_\-]+)\.(?:c|lpc)\s+(.+)$")
_SKILL_WRITE_NAME_RE = re.compile(r'"\\n([\u4e00-\u9fff][^"\\n：:]{1,11})[：:]"')
_SKILL_TYPE_RE = re.compile(r'string\s+type\s*\(\s*\)\s*\{\s*return\s+"([^"]+)"')
_SKILL_MARTIALTYPE_RE = re.compile(r'string\s+martialtype\s*\(\s*\)\s*\{\s*return\s+"([^"]+)"')
_SKILL_ENABLE_RE = re.compile(r'usage\s*==\s*"([^"]+)"')


def _sha256(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Extractor:
    def __init__(self, src_root: str, areas=AREA_DIRS):
        self.src_root = os.path.abspath(src_root)
        self.areas = list(areas)
        self.diagnostics: list[dict] = []
        self.files: dict[str, str] = {}  # mudlib id -> 源码仓库相对路径
        self.rooms: "OrderedDict[str, dict]" = OrderedDict()
        self.exits: list[dict] = []
        self.items: "OrderedDict[str, dict]" = OrderedDict()
        self.npcs: "OrderedDict[str, dict]" = OrderedDict()
        self.dialogue: list[dict] = []
        self.shops: list[dict] = []
        self.sects: list[dict] = []
        self.skills: "OrderedDict[str, dict]" = OrderedDict()
        self._text_cache: dict[str, str] = {}
        self._item_refs: set[str] = set()
        self._npc_refs: set[str] = set()
        self._stub_reachable: set[str] = set()

    # -- 诊断 ---------------------------------------------------------------

    def diag(self, rule, severity, path, line, message, **extra):
        item = {
            "rule": rule,
            "severity": severity,
            "path": path,
            "line": line,
            "message": message,
        }
        item.update({k: v for k, v in extra.items() if v is not None})
        self.diagnostics.append(item)

    def _normalize_path(self, path: str, rel_path: str, line) -> str:
        """源码里个别出口/引用把 `.lpc` 写进了路径；若文件真实存在就归一化并记账。"""
        for suffix in (".lpc", ".c"):
            if path.endswith(suffix):
                candidate = path[: -len(suffix)]
                if os.path.isfile(os.path.join(self.src_root, candidate.lstrip("/") + ".lpc")):
                    self.diag(
                        "I_PATH_NORMALIZED", "info", rel_path, line,
                        f"路径带扩展名 {path}，按 {candidate} 归一化",
                        path_value=path,
                    )
                    return candidate
        return path

    # -- 源文件 -------------------------------------------------------------

    def read(self, rel_path: str) -> str | None:
        if rel_path in self._text_cache:
            return self._text_cache[rel_path]
        abs_path = os.path.join(self.src_root, rel_path)
        try:
            with open(abs_path, "rb") as fh:
                raw = fh.read()
        except OSError:
            return None
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            self.diag(
                "E_ENCODING", "error", rel_path, None,
                "源码不是合法 UTF-8，跳过抽取",
                detail=str(exc),
            )
            return None
        if text.startswith("\ufeff"):
            text = text[1:]
        self._text_cache[rel_path] = text
        return text

    def iter_area_files(self):
        for area in self.areas:
            base = os.path.join(self.src_root, "d", area)
            if not os.path.isdir(base):
                self.diag("E_AREA_MISSING", "error", f"d/{area}", None, "切片区域目录不存在")
                continue
            found = []
            for root, dirs, names in os.walk(base):
                dirs.sort()
                for name in sorted(names):
                    if name.endswith(".lpc"):
                        found.append(os.path.join(root, name))
            for abs_path in sorted(found):
                rel_path = os.path.relpath(abs_path, self.src_root).replace(os.sep, "/")
                yield area, rel_path

    @staticmethod
    def bases_of(text: str) -> set[str]:
        return set(_INHERIT_RE.findall(text))

    # -- 主流程 -------------------------------------------------------------

    def run(self):
        self._collect_rooms()
        self._collect_items()
        self._collect_npcs()
        self._resolve_room_objects()
        self._collect_shops()
        self._collect_skills()
        self._collect_sects()
        self._coverage()
        return {
            "rooms": list(self.rooms.values()),
            "exits": self.exits,
            "items": list(self.items.values()),
            "npcs": list(self.npcs.values()),
            "dialogue": self.dialogue,
            "shops": self.shops,
            "sects": self.sects,
            "skills": list(self.skills.values()),
        }

    # -- 房间 ---------------------------------------------------------------

    def _collect_rooms(self):
        pending = []
        for _area, rel_path in self.iter_area_files():
            text = self.read(rel_path)
            if text is None:
                continue
            bases = self.bases_of(text)
            if ROOM_BASE not in bases and "SHOP" not in bases:
                continue
            mid = lpc.mudlib_id(rel_path)
            self.files[mid] = rel_path
            rec = self._parse_room(rel_path, mid, text)
            if rec is not None:
                pending.append(rec)

        for rec in pending:
            if rec["id"] in self.rooms:
                self.diag(
                    "E_DUP_ROOM_ID", "error", rec["source"]["path"], rec["source"]["line"],
                    f"房间 id 重复：{rec['id']}",
                    id=rec["id"],
                )
                continue
            self.rooms[rec["id"]] = rec
            for ex in rec.pop("_exits"):
                ex["from"] = rec["id"]
                self.exits.append(ex)

        # 切片内房间的出口可能指向区外：为区外目标建 boundary stub。
        self._build_boundary_stubs()

    def _parse_room(self, rel_path, mid, text):
        ctx = lpc.ParseContext(mid, lpc.mudlib_dir(rel_path), lpc.extract_defines(text))
        calls, errors = lpc.scan_calls(text, ctx)
        for err in errors:
            self.diag(
                "E_PARSE", "error", rel_path, err["line"], "LPC 字面量解析失败",
                detail=err["message"],
            )
        short = None
        short_line = None
        desc = None
        desc_line = None
        exits: dict[str, dict] = {}
        objects: dict[str, object] = {}
        objects_line = None
        flags: dict = {}
        coord: dict = {}
        for name, args, line, _method in calls:
            if name != "set" or not args or not isinstance(args[0], str):
                continue
            key = args[0]
            value = args[1] if len(args) > 1 else None
            if key == "short" and isinstance(value, str) and short is None:
                short = lpc.clean_text(value)
                short_line = line
            elif key == "long" and isinstance(value, str) and desc is None:
                desc = lpc.clean_text(value)
                desc_line = line
            elif key == "exits" and isinstance(value, dict):
                # 后出现的同名方向覆盖先出现的（与 LPC set 语义一致）。
                for direction, target in value.items():
                    if not isinstance(target, str):
                        self.diag(
                            "E_EXIT_TARGET", "error", rel_path, line,
                            f"出口 {direction} 的目标不是可解析路径",
                            detail=repr(target),
                        )
                        continue
                    target = self._normalize_path(target, rel_path, line)
                    exits[direction] = {
                        "dir": direction, "to": target, "line": line,
                        "status": "open", "traversable": True,
                    }
            elif key == "objects" and isinstance(value, dict):
                objects.update(value)
                objects_line = line
            elif key in ROOM_FLAGS:
                flags[key] = value
            elif key.startswith("coor/"):
                coord[key.split("/", 1)[1]] = value

        if short is None and desc is None and not exits and not objects:
            self.diag(
                "E_ROOM_EMPTY", "error", rel_path, 1, "房间没有任何可抽取字段，可能解析失败"
            )
            return None

        obj_list = []
        for ref, count in objects.items():
            ref = self._normalize_path(ref, rel_path, objects_line)
            entry = {"ref": ref, "count": count if isinstance(count, int) else None}
            if not isinstance(count, int):
                entry["count_expr"] = getattr(count, "text", str(count))
            obj_list.append(entry)

        if short is None:
            self.diag("W_NO_NAME", "warning", rel_path, 1, "房间没有 short 名称")
        if desc is None:
            self.diag("W_NO_LONG", "warning", rel_path, short_line, "房间没有 long 描述")
        if not exits:
            self.diag("W_NO_EXIT", "warning", rel_path, short_line, "房间没有任何出口")

        return {
            "id": mid,
            "name": short,
            "desc": desc,
            "objects": obj_list,
            "flags": flags,
            "coord": coord,
            "kind": "room",
            "origin": "slice",
            "source": {"path": rel_path, "line": short_line or desc_line or 1},
            "_exits": list(exits.values()),
        }

    def _build_boundary_stubs(self):
        slice_ids = set(self.rooms)
        targets = sorted({ex["to"] for ex in self.exits if ex["to"] not in slice_ids})
        for target in targets:
            if target in self.rooms:
                continue
            rel = target.lstrip("/") + ".lpc"
            text = self.read(rel)
            if text is None:
                self.diag(
                    "E_DANGLING_EXIT", "error", rel, None,
                    f"出口目标不存在：{target}",
                    target=target,
                )
                continue
            rec = self._parse_room(rel, target, text)
            if rec is None:
                self.diag(
                    "E_DANGLING_EXIT", "error", rel, None,
                    f"出口目标无法解析为房间：{target}",
                    target=target,
                )
                continue
            rec["kind"] = "boundary_stub"
            rec["origin"] = "boundary"
            stub_exits = rec.pop("_exits")
            kept = []
            for ex in stub_exits:
                if ex["to"] in slice_ids:
                    ex["from"] = target
                    ex["from_kind"] = "boundary_stub"
                    ex["boundary"] = False
                    kept.append(ex)
                    self.exits.append(ex)
                else:
                    self.diag(
                        "I_STUB_EXIT_DROPPED", "info", rel, ex["line"],
                        f"边界房间 {target} 的出口 {ex['dir']} -> {ex['to']} 超出切片，已裁剪",
                    )
            if kept:
                self._stub_reachable.add(target)
            self.rooms[target] = rec

        for ex in self.exits:
            if ex["to"] not in self.rooms:
                self.diag(
                    "E_DANGLING_EXIT", "error", self.files.get(ex["from"], ex.get("source_path")),
                    ex["line"],
                    f"出口 {ex['dir']} 指向不存在的房间：{ex['to']}",
                    target=ex["to"],
                )
            elif ex.get("from_kind") != "boundary_stub" and self.rooms[ex["to"]]["kind"] == "boundary_stub":
                reachable = ex["to"] in self._stub_reachable
                ex["boundary"] = True
                ex["status"] = "open" if reachable else "closed"
                ex["traversable"] = reachable
                self.diag(
                    "W_BOUNDARY_EXIT", "warning", self.files.get(ex["from"]), ex["line"],
                    f"边界出口 {ex['dir']} -> {ex['to']}（区外，"
                    f"{'有回程，可通行' if reachable else '无回程，按 closed stub 阻断'}）",
                    target=ex["to"],
                )

    # -- 物品 ---------------------------------------------------------------

    def _collect_items(self):
        for _area, rel_path in self.iter_area_files():
            text = self.read(rel_path)
            if text is None:
                continue
            bases = self.bases_of(text)
            if not (bases & set(ITEM_BASES)):
                continue
            mid = lpc.mudlib_id(rel_path)
            self.files[mid] = rel_path
            rec = self._parse_item(rel_path, mid, text, bases, origin="slice")
            if rec["id"] in self.items:
                self.diag(
                    "E_DUP_ITEM_ID", "error", rel_path, rec["source"]["line"],
                    f"物品 id 重复：{rec['id']}", id=rec["id"],
                )
                continue
            self.items[rec["id"]] = rec

    def _parse_item(self, rel_path, mid, text, bases, origin="slice"):
        emit_diag = origin == "slice"
        ctx = lpc.ParseContext(mid, lpc.mudlib_dir(rel_path), lpc.extract_defines(text))
        calls, errors = lpc.scan_calls(text, ctx)
        name = None
        name_line = None
        values: dict[str, object] = {}
        weight = None
        for cname, args, line, _method in calls:
            if cname == "set_name" and args and isinstance(args[0], str) and name is None:
                name = lpc.clean_text(args[0])
                name_line = line
            elif cname == "set_weight" and args and isinstance(args[0], int):
                weight = args[0]
            elif cname == "set" and args and isinstance(args[0], str):
                values.setdefault(args[0], (args[1] if len(args) > 1 else None, line))

        category, equip_slot = "misc", None
        for base in ITEM_BASES:
            if base in bases:
                category, equip_slot = ITEM_BASES[base]
                break

        desc, desc_line = values.get("long", (None, None))
        desc = lpc.clean_text(desc) if isinstance(desc, str) else None
        price, price_line = values.get("value", (None, None))
        price = price if isinstance(price, int) else None
        price_expr = None
        if price is None and "value" in values:
            raw = values["value"][0]
            price_expr = getattr(raw, "text", str(raw))

        line = name_line or desc_line or 1
        if name is None and emit_diag:
            self.diag("W_ITEM_NO_NAME", "warning", rel_path, line, "物品没有 set_name")
        if desc is None and emit_diag:
            self.diag("W_DESC_MISSING", "warning", rel_path, line,
                      "源码没有 long 描述；按 plan 保留 id 与空描述，不补写文案")
        if price is None and emit_diag:
            self.diag("W_NO_PRICE", "warning", rel_path, line,
                      "源码没有 set(\"value\") 价格")
        elif price == 0 and emit_diag:
            self.diag("I_PRICE_ZERO", "info", rel_path, price_line, "物品价格为 0")

        return {
            "id": mid,
            "name": name,
            "desc": desc,
            "price": price,
            "price_expr": price_expr,
            "unit": values.get("unit", (None, None))[0] if isinstance(values.get("unit", (None, None))[0], str) else None,
            "category": category,
            "equip_slot": equip_slot,
            "material": values.get("material", (None, None))[0] if isinstance(values.get("material", (None, None))[0], str) else None,
            "weight": weight,
            "food_supply": values.get("food_supply", (None, None))[0] if isinstance(values.get("food_supply", (None, None))[0], int) else None,
            "origin": origin,
            "source": {"path": rel_path, "line": line},
        }

    # -- NPC ----------------------------------------------------------------

    def _collect_npcs(self):
        for _area, rel_path in self.iter_area_files():
            text = self.read(rel_path)
            if text is None:
                continue
            bases = self.bases_of(text)
            if NPC_BASE not in bases:
                continue
            mid = lpc.mudlib_id(rel_path)
            self.files[mid] = rel_path
            rec = self._parse_npc(rel_path, mid, text, bases)
            if rec["id"] in self.npcs:
                self.diag(
                    "E_DUP_NPC_ID", "error", rel_path, rec["source"]["line"],
                    f"NPC id 重复：{rec['id']}", id=rec["id"],
                )
                continue
            self.npcs[rec["id"]] = rec
            for topic in rec.pop("_topics"):
                topic["npc"] = rec["id"]
                topic["source"]["path"] = rel_path
                self.dialogue.append(topic)

    def _parse_npc(self, rel_path, mid, text, bases, origin="slice"):
        emit_diag = origin == "slice"
        ctx = lpc.ParseContext(mid, lpc.mudlib_dir(rel_path), lpc.extract_defines(text))
        calls, errors = lpc.scan_calls(text, ctx)
        name = None
        name_line = None
        aliases: list[str] = []
        values: dict[str, object] = {}
        skills: dict[str, int] = {}
        map_skills: dict[str, str] = {}
        prepare_skills: list[str] = []
        equipment: list[dict] = []
        topics: list[dict] = []
        for cname, args, line, method in calls:
            if cname == "set_name" and args and isinstance(args[0], str) and name is None:
                name = lpc.clean_text(args[0])
                name_line = line
                if len(args) > 1 and isinstance(args[1], list):
                    aliases = [a for a in args[1] if isinstance(a, str)]
            elif cname == "set_skill" and args and isinstance(args[0], str):
                if isinstance(args[1], int):
                    skills[args[0]] = args[1]
                elif len(args) > 1 and emit_diag:
                    self.diag(
                        "W_SKILL_LEVEL_EXPR", "warning", rel_path, line,
                        f"技能 {args[0]} 等级不是整数字面量",
                        detail=getattr(args[1], "text", str(args[1])),
                    )
            elif cname == "map_skill" and len(args) >= 2 and isinstance(args[0], str) and isinstance(args[1], str):
                map_skills[args[0]] = args[1]
            elif cname == "prepare_skill" and args and isinstance(args[0], str):
                prepare_skills.append(args[0])
            elif cname == "carry_object" and args and isinstance(args[0], str):
                equipment.append({"ref": args[0], "slot": method})
            elif cname == "set" and args and isinstance(args[0], str):
                key = args[0]
                value = args[1] if len(args) > 1 else None
                if key == "inquiry" and isinstance(value, dict):
                    for topic, answer in value.items():
                        topics.append(self._make_topic(topic, answer, line, rel_path))
                elif key == "long" and isinstance(value, str) and "long" not in values:
                    values["long"] = (lpc.clean_text(value), line)
                elif key == "vendor_goods" and isinstance(value, list):
                    values["vendor_goods"] = ([v for v in value if isinstance(v, str)], line)
                elif key in values:
                    continue
                else:
                    values[key] = (value, line)

        sect = None
        parts = rel_path.split("/")
        if len(parts) >= 4 and parts[1] in self.areas and parts[2] not in ("npc", "obj"):
            sect = parts[2]

        is_master = MASTER_BASE in bases
        attitude = values.get("attitude", (None, None))[0]
        desc = values.get("long", (None, None))[0]
        if desc is None and emit_diag:
            self.diag("W_DESC_MISSING", "warning", rel_path, name_line or 1,
                      "源码没有 long 描述；按 plan 保留 id 与空描述，不补写文案")
        if name is None and emit_diag:
            self.diag("W_NPC_NO_NAME", "warning", rel_path, 1, "NPC 没有 set_name")

        attrs = {}
        for key in ("str", "int", "con", "dex", "per", "kar"):
            if key in values and isinstance(values[key][0], int):
                attrs[key] = values[key][0]
        vitals = {}
        for key in ("max_qi", "max_jing", "max_neili", "neili", "jiali", "combat_exp"):
            if key in values and isinstance(values[key][0], int):
                vitals[key] = values[key][0]

        vendor = "vendor_goods" in values
        return {
            "id": mid,
            "name": name,
            "aliases": aliases,
            "title": lpc.clean_text(values["title"][0]) if isinstance(values.get("title", (None, None))[0], str) else None,
            "nickname": lpc.clean_text(values["nickname"][0]) if isinstance(values.get("nickname", (None, None))[0], str) else None,
            "desc": desc,
            "gender": values.get("gender", (None, None))[0],
            "age": values.get("age", (None, None))[0] if isinstance(values.get("age", (None, None))[0], int) else None,
            "class": values.get("class", (None, None))[0],
            "attitude": attitude,
            "aggressive": attitude == "aggressive",
            "attrs": attrs,
            "vitals": vitals,
            "skills": skills,
            "map_skills": map_skills,
            "prepare_skills": prepare_skills,
            "equipment": equipment,
            "is_master": is_master,
            "sect": sect if is_master else None,
            "vendor": vendor,
            "origin": origin,
            "source": {"path": rel_path, "line": name_line or 1},
            "_topics": topics,
        }

    def _make_topic(self, topic, answer, line, rel_path):
        entry = {
            "topic": topic,
            "kind": "string",
            "text": None,
            "func": None,
            "source": {"path": rel_path, "line": line},
        }
        if isinstance(answer, str):
            entry["text"] = lpc.clean_text(answer)
        else:
            entry["kind"] = "fallback"
            entry["func"] = getattr(answer, "text", str(answer))
            self.diag(
                "I_DIALOGUE_FUNC", "info", rel_path, line,
                f"对话主题「{topic}」的值是函数，静态不可搬；保留主题键并转 fallback",
                topic=topic,
            )
        return entry

    # -- 外部引用 -----------------------------------------------------------

    def _resolve_room_objects(self):
        for room in self.rooms.values():
            if room["kind"] == "boundary_stub":
                continue
            for obj in room["objects"]:
                ref = obj["ref"]
                kind = self._ref_kind(ref)
                obj["kind"] = kind
                if kind == "item":
                    self._item_refs.add(ref)
                elif kind == "npc":
                    self._npc_refs.add(ref)
                if ref in self.rooms or ref in self.items or ref in self.npcs:
                    obj["in_slice"] = ref in self.items or ref in self.npcs
                    continue
                rel = ref.lstrip("/") + ".lpc"
                if self.read(rel) is None:
                    self.diag(
                        "E_MISSING_ITEM_REF" if kind != "npc" else "E_MISSING_NPC_REF",
                        "error", room["source"]["path"], room["source"]["line"],
                        f"房间 objects 引用了不存在的对象：{ref}", ref=ref,
                    )
                    continue
                obj["in_slice"] = False
                obj["origin"] = "external"
                if kind == "item" and ref not in self.items:
                    text = self.read(rel)
                    bases = self.bases_of(text)
                    self.items[ref] = self._parse_item(
                        rel, ref, text, bases, origin="external"
                    )
                elif kind == "npc" and ref not in self.npcs:
                    self._import_external_npc(rel, ref)

    def _ref_kind(self, ref: str) -> str:
        if ref in self.npcs:
            return "npc"
        if ref in self.items:
            return "item"
        rel = ref.lstrip("/") + ".lpc"
        text = self.read(rel)
        if text is None:
            return "item"
        bases = self.bases_of(text)
        if NPC_BASE in bases:
            return "npc"
        if bases & set(ITEM_BASES):
            return "item"
        return "item"

    def _import_external_npc(self, rel, ref):
        text = self.read(rel)
        if text is None:
            return
        bases = self.bases_of(text)
        rec = self._parse_npc(rel, ref, text, bases, origin="external")
        # 外部 NPC 不计入切片统计，也不进对话覆盖率分母。
        for topic in rec.pop("_topics"):
            topic["npc"] = ref
            topic["source"]["path"] = rel
            topic["origin"] = "external"
            self.dialogue.append(topic)
        self.npcs[ref] = rec

    # -- 商店 ---------------------------------------------------------------

    def _collect_shops(self):
        for npc in list(self.npcs.values()):
            if not npc.get("vendor"):
                continue
            # 重新解析一次拿到 vendor_goods 原文
            rel = npc["source"]["path"]
            text = self.read(rel)
            ctx = lpc.ParseContext(npc["id"], lpc.mudlib_dir(rel), lpc.extract_defines(text))
            calls, _ = lpc.scan_calls(text, ctx)
            stock = []
            for cname, args, line, _m in calls:
                if cname == "set" and len(args) >= 2 and args[0] == "vendor_goods" and isinstance(args[1], list):
                    for ref in args[1]:
                        if not isinstance(ref, str):
                            continue
                        entry = self._stock_entry(ref, rel, line)
                        if entry is not None:
                            stock.append(entry)
            self.shops.append({
                "id": npc["id"],
                "npc": npc["id"],
                "stock": stock,
                "source": {"path": rel, "line": npc["source"]["line"]},
            })

    def _stock_entry(self, ref, rel_path, line):
        ref = self._normalize_path(ref, rel_path, line)
        rel = ref.lstrip("/") + ".lpc"
        text = self.read(rel)
        if text is None:
            self.diag(
                "E_SHOP_STOCK_MISSING", "error", rel_path, line,
                f"商店库存引用了不存在的物品：{ref}", ref=ref,
            )
            return None
        bases = self.bases_of(text)
        if ref not in self.items:
            rec = self._parse_item(rel, ref, text, bases, origin="external")
            self.items[ref] = rec
        item = self.items[ref]
        return {
            "item": ref,
            "name": item.get("name"),
            "price": item.get("price"),
            "origin": item.get("origin", "external"),
        }

    # -- 技能 ---------------------------------------------------------------

    def _collect_skills(self):
        declared: dict[str, list[str]] = {}
        mapped: dict[str, list[str]] = {}
        for npc in self.npcs.values():
            if npc.get("origin") != "slice":
                continue
            for sid in npc["skills"]:
                declared.setdefault(sid, []).append(npc["id"])
            for sid in set(npc["map_skills"].values()) | set(npc["prepare_skills"]):
                mapped.setdefault(sid, []).append(npc["id"])
        for sid in sorted(set(declared) | set(mapped)):
            rel = f"kungfu/skill/{sid}.lpc"
            text = self.read(rel)
            if text is None:
                # set_skill 声明的技能缺失 = 真错误；只被 map_skill 引用的 = 源码不一致，降级为 warning。
                if sid in declared:
                    self.diag(
                        "E_MISSING_SKILL", "error", rel, None,
                        f"NPC set_skill 引用的技能文件不存在：{sid}", skill=sid,
                    )
                else:
                    self.diag(
                        "W_MISSING_SKILL", "warning", rel, None,
                        f"NPC map_skill 引用的技能文件不存在：{sid}", skill=sid,
                    )
                continue
            used_by = sorted(set(declared.get(sid, [])) | set(mapped.get(sid, [])))
            rec = self._parse_skill(sid, rel, text)
            rec["used_by"] = used_by
            self.skills[sid] = rec

    def _parse_skill(self, sid, rel, text):
        name = None
        m = _SKILL_HEADER_RE.search(text)
        if m:
            candidate = m.group(2).strip()
            if candidate:
                name = candidate
        if name is None:
            m = _SKILL_WRITE_NAME_RE.search(text)
            if m:
                name = m.group(1).strip()
        if not name:
            self.diag("W_SKILL_NAME_MISSING", "warning", rel, 1,
                      f"技能 {sid} 没有可静态提取的中文名")
        type_m = _SKILL_TYPE_RE.search(text)
        martial_m = _SKILL_MARTIALTYPE_RE.search(text)
        enables = sorted(set(_SKILL_ENABLE_RE.findall(text)))
        return {
            "id": sid,
            "name": name,
            "type": type_m.group(1) if type_m else None,
            "martialtype": martial_m.group(1) if martial_m else None,
            "enable": enables,
            "source": {"path": rel, "line": 1},
        }

    # -- 门派 ---------------------------------------------------------------

    def _collect_sects(self):
        groups: dict[str, list[dict]] = {}
        for npc in self.npcs.values():
            if npc.get("origin") == "slice" and npc.get("sect"):
                groups.setdefault(npc["sect"], []).append(npc)
        for sid, members in sorted(groups.items()):
            titles = [m["title"][:3] for m in members if m.get("title")]
            name = None
            if titles:
                name = Counter(titles).most_common(1)[0][0]
            if not name:
                self.diag("W_SECT_NAME_MISSING", "warning", f"d/{self.areas[0]}/{sid}", 1,
                          f"门派 {sid} 无法从标题推断中文名")
            self.sects.append({
                "id": sid,
                "name": name,
                "area": self._sect_area(members[0]),
                "masters": sorted(m["id"] for m in members),
                "master_count": len(members),
            })

    @staticmethod
    def _sect_area(npc):
        parts = npc["source"]["path"].split("/")
        return parts[1] if len(parts) > 1 else None

    # -- 覆盖率 -------------------------------------------------------------

    def _coverage(self):
        slice_rooms = [r for r in self.rooms.values() if r["origin"] == "slice"]
        slice_items = [i for i in self.items.values() if i["origin"] == "slice"]
        slice_npcs = [n for n in self.npcs.values() if n["origin"] == "slice"]
        slice_topics = [t for t in self.dialogue if t.get("origin") != "external"]
        with_exits = {
            ex["from"] for ex in self.exits
            if self.rooms.get(ex["from"], {}).get("origin") == "slice"
        }

        def ratio(hit, total):
            return round(hit / total, 4) if total else 0.0

        cov = {
            "rooms_desc": {
                "hit": sum(1 for r in slice_rooms if r["desc"]),
                "total": len(slice_rooms),
                "ratio": ratio(sum(1 for r in slice_rooms if r["desc"]), len(slice_rooms)),
                "target": 0.961,
            },
            "room_exits": {
                "hit": sum(1 for r in slice_rooms if r["id"] in with_exits),
                "total": len(slice_rooms),
                "ratio": ratio(sum(1 for r in slice_rooms if r["id"] in with_exits), len(slice_rooms)),
                "target": 0.978,
            },
            "items_desc": {
                "hit": sum(1 for i in slice_items if i["desc"]),
                "total": len(slice_items),
                "ratio": ratio(sum(1 for i in slice_items if i["desc"]), len(slice_items)),
                "target": 0.85,
            },
            "npcs_desc": {
                "hit": sum(1 for n in slice_npcs if n["desc"]),
                "total": len(slice_npcs),
                "ratio": ratio(sum(1 for n in slice_npcs if n["desc"]), len(slice_npcs)),
                "target": 0.90,
            },
            "dialogue_text": {
                "hit": sum(1 for t in slice_topics if t["kind"] == "string"),
                "total": len(slice_topics),
                "ratio": ratio(sum(1 for t in slice_topics if t["kind"] == "string"), len(slice_topics)),
                "target": 0.638,
            },
        }
        self.coverage = cov
        for metric, data in cov.items():
            if data["ratio"] < data["target"]:
                self.diag(
                    "W_COVERAGE_BELOW_TARGET", "warning", "coverage", None,
                    f"{metric} 覆盖率 {data['ratio']:.1%} 低于目标 {data['target']:.1%}"
                    f"（{data['hit']}/{data['total']}）；缺口已逐条写入 diagnostics",
                    metric=metric,
                )

    # -- 统计 ---------------------------------------------------------------

    def counts(self):
        return {
            "rooms_in_slice": sum(1 for r in self.rooms.values() if r["origin"] == "slice"),
            "rooms_boundary_stub": sum(1 for r in self.rooms.values() if r["origin"] == "boundary"),
            "exits": len(self.exits),
            "items_in_slice": sum(1 for i in self.items.values() if i["origin"] == "slice"),
            "items_external": sum(1 for i in self.items.values() if i["origin"] == "external"),
            "npcs_in_slice": sum(1 for n in self.npcs.values() if n["origin"] == "slice"),
            "npcs_external": sum(1 for n in self.npcs.values() if n["origin"] == "external"),
            "dialogue_topics": len(self.dialogue),
            "dialogue_topics_in_slice": sum(1 for t in self.dialogue if t.get("origin") != "external"),
            "shops": len(self.shops),
            "sects": len(self.sects),
            "masters": sum(
                1 for n in self.npcs.values()
                if n.get("origin") == "slice" and n.get("is_master")
            ),
            "aggressive_npcs": sum(1 for n in self.npcs.values() if n.get("origin") == "slice" and n.get("aggressive")),
            "skills": len(self.skills),
        }


def source_commit(src_root: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", src_root, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, check=True,
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
