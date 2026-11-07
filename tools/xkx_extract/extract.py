"""xkx 切片抽取：LPC 源码 -> 引擎可消费的表。

范围：杭州 + 嘉兴 + 石梁（`d/hangzhou`、`d/jiaxing`、`d/shiliang`）。
产物口径与 `specs/xkx-parity/plan.md` 对齐；原版缺失的描述只在
`authored.py` 里自撰并标 `desc_source="authored"`，与 `desc_source="lpc"` 分账，
不冒充原版文案。
"""

from __future__ import annotations

import os
import posixpath
import re
import subprocess
from collections import Counter, OrderedDict

from . import lpc
from .authored import AUTHORED_DESCS
from .npc_drops import extract_npc_drops
from .quests import extract_quests
from .skills_actions import extract_skill_tables

AREA_DIRS = ("hangzhou", "jiaxing", "shiliang")

# 原版种族 setup 里的随机属性：race 名 -> 种族 daemon。
# 只收 setup 中 `if (undefinedp(my["str"])) my["str"] = random(N) + M;` 这类
# 明确掷骰的键；显式 set("str", ...) 的键不覆盖。
RACE_ATTR_FILES = {
    "走兽": "adm/daemons/race/beast.lpc",
    "野兽": "adm/daemons/race/beast1.lpc",
    "走畜": "adm/daemons/race/stock.lpc",
    "飞禽": "adm/daemons/race/bird.lpc",
    "游鱼": "adm/daemons/race/fish.lpc",
    "爬蛇": "adm/daemons/race/snake.lpc",
    "昆虫": "adm/daemons/race/insect.lpc",
}
ATTR_KEYS = ("str", "int", "con", "dex", "per", "kar")
_RACE_ATTR_ROLL_RE = re.compile(
    r'if\s*\(\s*undefinedp\(\s*my\[\s*"([a-z]+)"\s*\]\s*\)\s*\)'
    r'\s*my\[\s*"\1"\s*\]\s*=\s*random\(\s*(\d+)\s*\)\s*\+\s*(\d+)\s*;'
)

ROOM_BASE = "ROOM"
NPC_BASE = "NPC"
MASTER_BASE = "F_MASTER"
# 少数 NPC 只继承 F_ 系列（`F_VENDOR_SALE` 内部再继承 NPC），
# 只按 `NPC` 判会把这些对象误记成物品。
NPC_LIKE_BASES = {NPC_BASE, MASTER_BASE, "F_VENDOR", "F_VENDOR_SALE"}


def _cn_number(n: int) -> str:
    """LPC chinese_number() 的 1..99 子集，用于门派代数。"""
    digits = "零一二三四五六七八九"
    if n < 10:
        return digits[n]
    if n < 20:
        return "十" + (digits[n % 10] if n % 10 else "")
    if n < 100:
        return digits[n // 10] + "十" + (digits[n % 10] if n % 10 else "")
    return str(n)


def _npc_display_name(name: str, title, nickname, family, party) -> str:
    """复刻 feature/name.c 的 short() 拼接顺序：

    [guard][degree][party][title + 空格(无 nickname 时)][「nickname」]name
    本切片只需 title / party / nickname；degree/guard 无来源。
    """
    s = name or ""
    if nickname:
        s = f"「{nickname}」{s}"
    if title:
        s = title + ("" if nickname else " ") + s
    if party:
        p = str(party.get("name") or "") + str(party.get("rank") or "")
        s = p + ("" if nickname else " ") + s
    return s


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

# set_temp 的战斗白名单：这些键原版由战斗代码直接读取。其余键照原样保留，
# 但在诊断里记一条未知键，避免静默吞掉。
TEMP_APPLY_KEYS = {
    "apply/attack",
    "apply/defense",
    "apply/armor",
    "apply/dodge",
    "apply/parry",
    "apply/damage",
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


def _function_span(text: str, name: str) -> tuple[int, int] | None:
    """返回具名函数体的行号区间（含首尾大括号所在行）；找不到返回 None。

    set_temp 只取 create() 里的调用：原版同名函数在运行期还会改临时标记，
    那些不是创世属性，混进来会让引擎在出生时套错状态。
    """
    tokens = lpc.lex(text)
    n = len(tokens)
    for i in range(n):
        if not (
            tokens[i].kind == "IDENT"
            and tokens[i].value == name
            and i + 1 < n
            and tokens[i + 1].kind == "PUNCT"
            and tokens[i + 1].value == "("
        ):
            continue
        # 跳过参数表，允许 `foo(object me, string arg)`；函数声明后面的
        # 分号不会命中，因为这里要求右括号之后紧跟 `{`。
        depth = 0
        j = i + 1
        while j < n:
            tok = tokens[j]
            if tok.kind == "PUNCT":
                if tok.value == "(":
                    depth += 1
                elif tok.value == ")":
                    depth -= 1
                    if depth == 0:
                        break
            j += 1
        if j + 1 >= n or tokens[j + 1].kind != "PUNCT" or tokens[j + 1].value != "{":
            continue
        depth = 0
        start_line = tokens[j + 1].line
        for tok in tokens[j + 1:]:
            if tok.kind != "PUNCT":
                continue
            if tok.value == "{":
                depth += 1
            elif tok.value == "}":
                depth -= 1
                if depth == 0:
                    return start_line, tok.line
    return None


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
        self.skills_actions: list[dict] = []
        self.skills_stats: list[dict] = []
        self.quests: list[dict] = []
        self.skill_counts: dict[str, int] = {}
        self.quest_counts: dict[str, int] = {}
        self.drop_counts: dict[str, int] = {}
        self.equipment_counts: dict[str, int] = {}
        self._text_cache: dict[str, str] = {}
        self._race_attr_cache: dict[str, dict] = {}
        self._region_name_cache: dict[str, dict] | None = None
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
        self._collect_drops()
        self._collect_equipment_items()
        self._collect_skill_tables()
        self._collect_quests()
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
            "skills_actions": self.skills_actions,
            "skills_stats": self.skills_stats,
            "quests": self.quests,
        }

    def _collect_skill_tables(self):
        self.skills_actions, self.skills_stats, diags, counts = extract_skill_tables(
            self.src_root, list(self.skills.values()), list(self.npcs.values())
        )
        self.diagnostics.extend(diags)
        self.skill_counts = counts

    def _collect_drops(self):
        diags, counts, item_refs = extract_npc_drops(
            self.src_root, list(self.npcs.values())
        )
        self.diagnostics.extend(diags)
        self.drop_counts = counts
        for ref in sorted(item_refs):
            if ref in self.items:
                continue
            rel = ref.lstrip("/") + ".lpc"
            text = self.read(rel)
            if text is None:
                continue
            self.items[ref] = self._parse_item(
                rel, ref, text, self.bases_of(text), origin="external"
            )

    def _collect_quests(self):
        quests, diags, counts = extract_quests(
            self.src_root, list(self.npcs.values())
        )
        self.diagnostics.extend(diags)
        self.quests = quests
        self.quest_counts = counts

    def _canonical_item_ref(self, ref: str, rel_path: str, line) -> str:
        """装备引用归一化：补前导 `/`、去掉 `.lpc`；真实文件存在才改，否则留原样。"""
        original = ref
        fixed = "/" + ref.lstrip("/")
        if fixed.endswith(".lpc"):
            fixed = fixed[:-4]
        if fixed == original:
            return ref
        if os.path.isfile(os.path.join(self.src_root, fixed.lstrip("/") + ".lpc")):
            self.diag(
                "I_EQUIP_REF_NORMALIZED", "info", rel_path, line,
                f"装备引用 {original} 归一化为 {fixed}",
                id=fixed,
            )
            return fixed
        return original

    @staticmethod
    def _ensure_item_weight(rec):
        """装备物品缺 set_weight() 时补基类默认 100，并留来源。"""
        if rec.get("weight") is not None:
            return
        rec["weight"] = 100
        rec["weight_source"] = "default"
        rec["weight_reason"] = (
            "基类 inherit/item/item.lpc 的 setup() 默认 set(\"weight\",100)"
        )

    def _collect_equipment_items(self):
        added = 0
        unresolved = 0
        normalized = 0
        for npc in self.npcs.values():
            rel_path = npc["source"]["path"]
            line = npc["source"]["line"]
            for slot in npc.get("equipment", []):
                ref = self._canonical_item_ref(slot["ref"], rel_path, line)
                if ref != slot["ref"]:
                    normalized += 1
                    slot["ref"] = ref
                if ref in self.items:
                    self._ensure_item_weight(self.items[ref])
                    slot["item_resolved"] = True
                    continue
                rel_item = ref.lstrip("/") + ".lpc"
                text = self.read(rel_item)
                if text is None:
                    unresolved += 1
                    slot["item_resolved"] = False
                    self.diag(
                        "W_EQUIP_ITEM_MISSING", "warning", rel_path, line,
                        f"装备 {ref} 的源文件不存在，无法抽进 items.json",
                        id=npc["id"], ref=ref,
                    )
                    continue
                origin = "slice" if ref.startswith((
                    "/d/hangzhou/", "/d/jiaxing/", "/d/shiliang/",
                )) else "external"
                self.items[ref] = self._parse_item(
                    rel_item, ref, text, self.bases_of(text), origin=origin
                )
                self._ensure_item_weight(self.items[ref])
                added += 1
                slot["item_resolved"] = True
        self.equipment_counts = {
            "equipment_items_added": added,
            "equipment_items_unresolved": unresolved,
            "equipment_refs_normalized": normalized,
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

        source = {"path": rel_path, "line": short_line or desc_line or 1}
        parts = rel_path.split("/")
        region = parts[1] if parts and parts[0] == "d" and len(parts) > 2 else None
        region_name = None
        region_source = None
        region_name_source = None
        if region:
            region_source = {
                "kind": "derived_path",
                "path": rel_path,
                "line": source["line"],
                "basis": f"路径 d/{region}/",
            }
            info = self._region_name_sources().get(region)
            if info:
                region_name = info["name"]
                region_name_source = dict(info["source"])
            else:
                self.diag(
                    "I_REGION_NAME_MISSING", "info", rel_path, source["line"],
                    f"区域 {region} 在 mapd/logind 中没有中文名来源，region_name=null",
                    region=region,
                )
        else:
            self.diag(
                "I_REGION_UNRESOLVED", "info", rel_path, source["line"],
                "房间不在 d/<区域>/ 目录下，region=null",
            )

        return {
            "id": mid,
            "name": short,
            "desc": desc,
            "desc_source": "lpc" if desc else None,
            "region": region,
            "region_name": region_name,
            "region_source": region_source,
            "region_name_source": region_name_source,
            "objects": obj_list,
            "flags": flags,
            "coord": coord,
            "kind": "room",
            "origin": "slice",
            "source": source,
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
            self._apply_authored(rec)
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
        amount = None
        for cname, args, line, _method in calls:
            if cname == "set_name" and args and isinstance(args[0], str) and name is None:
                name = lpc.clean_text(args[0])
                name_line = line
            elif cname == "set_weight" and args and isinstance(args[0], int):
                weight = args[0]
            elif cname == "set_amount" and args and isinstance(args[0], int):
                # 原版一件物品自带的数量（如鹅卵石 set_amount(10)）。
                amount = args[0]
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
        if name is None and desc:
            # 少数物品的 set_name() 是 random() 拼接（如 chairs），静态取不到。
            # 从源码自己的 long（“这是一把椅子。”）取中文名词，不另编名字。
            m = re.match(r"^这是一(?:[把张只个条匹本块件支面])?(.+?)[。.]?$", desc)
            if m:
                name = m.group(1)
                name_line = desc_line
        if name is None and emit_diag:
            self.diag("W_ITEM_NO_NAME", "warning", rel_path, line, "物品没有 set_name")
        if price is None and emit_diag:
            self.diag("W_NO_PRICE", "warning", rel_path, line,
                      "源码没有 set(\"value\") 价格")
        elif price == 0 and emit_diag:
            self.diag("I_PRICE_ZERO", "info", rel_path, price_line, "物品价格为 0")

        return {
            "id": mid,
            "name": name,
            "desc": desc,
            "desc_source": "lpc" if desc else None,
            "price": price,
            "price_expr": price_expr,
            "unit": values.get("unit", (None, None))[0] if isinstance(values.get("unit", (None, None))[0], str) else None,
            "category": category,
            "equip_slot": equip_slot,
            "material": values.get("material", (None, None))[0] if isinstance(values.get("material", (None, None))[0], str) else None,
            "weight": weight,
            "amount": amount,
            "food_supply": values.get("food_supply", (None, None))[0] if isinstance(values.get("food_supply", (None, None))[0], int) else None,
            "origin": origin,
            "source": {"path": rel_path, "line": line},
        }

    def _apply_authored(self, rec):
        """原版缺 long 的条目用自撰文案补齐，并标记 `desc_source="authored"`。

        source 覆盖率只认 `desc_source="lpc"`，这里不会抬高对原版忠实度的数字；
        total 覆盖率则要求玩家可见描述非空，两本账分开记。
        """
        if rec.get("desc"):
            return
        text = AUTHORED_DESCS.get(rec["id"])
        if not text:
            return
        rec["desc"] = text
        rec["desc_source"] = "authored"

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
            self._apply_authored(rec)
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
        create_span = _function_span(text, "create")
        ctx = lpc.ParseContext(mid, lpc.mudlib_dir(rel_path), lpc.extract_defines(text))
        calls, errors = lpc.scan_calls(text, ctx)
        name = None
        name_line = None
        aliases: list[str] = []
        values: dict[str, object] = {}
        family = None
        skills: dict[str, int] = {}
        map_skills: dict[str, str] = {}
        prepare_skills: list[str] = []
        equipment: list[dict] = []
        topics: list[dict] = []
        temp_apply: "OrderedDict[str, dict]" = OrderedDict()
        for cname, args, line, method in calls:
            if cname == "set_name" and args and isinstance(args[0], str) and name is None:
                name = lpc.clean_text(args[0])
                name_line = line
                if len(args) > 1 and isinstance(args[1], list):
                    aliases = [a for a in args[1] if isinstance(a, str)]
            elif (
                cname == "create_family"
                and len(args) >= 3
                and isinstance(args[0], str)
                and isinstance(args[1], int)
                and isinstance(args[2], str)
                and family is None
                and not method
                and (create_span is None or create_span[0] <= line <= create_span[1])
            ):
                family = {
                    "name": lpc.clean_text(args[0]),
                    "generation": args[1],
                    "title": lpc.clean_text(args[2]),
                    "line": line,
                }
            elif cname == "set_skill" and args and isinstance(args[0], str):
                if isinstance(args[1], int):
                    skills[args[0]] = args[1]
                elif len(args) > 1 and emit_diag:
                    self.diag(
                        "W_SKILL_LEVEL_EXPR", "warning", rel_path, line,
                        f"技能 {args[0]} 等级不是整数字面量",
                        detail=getattr(args[1], "text", str(args[1])),
                    )
            elif cname == "set_temp" and args and isinstance(args[0], str):
                if create_span is not None and not (create_span[0] <= line <= create_span[1]):
                    continue
                temp_apply[args[0]] = self._make_temp_apply(
                    args[0], args[1] if len(args) > 1 else None, line, rel_path, emit_diag
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
                        topics.append(
                            self._make_topic(
                                topic, answer, line, rel_path, text, origin
                            )
                        )
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
        desc = lpc.clean_text(desc) if isinstance(desc, str) else None
        if name is None and emit_diag:
            self.diag("W_NPC_NO_NAME", "warning", rel_path, 1, "NPC 没有 set_name")

        explicit_title = (
            lpc.clean_text(values["title"][0])
            if isinstance(values.get("title", (None, None))[0], str)
            else None
        )
        nickname = (
            lpc.clean_text(values["nickname"][0])
            if isinstance(values.get("nickname", (None, None))[0], str)
            else None
        )
        # create_family() 只在没有显式 title 时补门派头衔（与 feature/apprentice.c 一致）。
        title = explicit_title
        if not title and family:
            title = (
                f"{family['name']}第{_cn_number(family['generation'])}代"
                f"{family['title']}"
            )
        party = None
        party_name = values.get("party/party_name", (None, None))[0]
        party_rank = values.get("party/rank", (None, None))[0]
        if isinstance(party_name, str) and isinstance(party_rank, str):
            party = {
                "name": lpc.clean_text(party_name),
                "rank": lpc.clean_text(party_rank),
            }
        display_name = _npc_display_name(name or "", title, nickname, family, party)

        attrs = {}
        for key in ATTR_KEYS:
            if key in values and isinstance(values[key][0], int):
                attrs[key] = values[key][0]
        race_value, race_line = values.get("race", (None, None))
        race = race_value if isinstance(race_value, str) else None
        attrs_roll = {}
        attrs_roll_sources = {}
        if race:
            for key, meta in self._race_attr_defaults(race).items():
                # 显式 set 过的键（含无法静态取值的表达式）不覆盖。
                if key in values:
                    continue
                attrs_roll[key] = [meta["min"], meta["max"]]
                attrs_roll_sources[key] = meta["source"]
        vitals = {}
        for key in ("max_qi", "max_jing", "max_neili", "neili", "jiali", "combat_exp"):
            if key in values and isinstance(values[key][0], int):
                vitals[key] = values[key][0]

        vendor = "vendor_goods" in values
        return {
            "id": mid,
            "name": name,
            "aliases": aliases,
            "title": title,
            "nickname": nickname,
            "family": (
                {
                    "name": family["name"],
                    "generation": family["generation"],
                    "title": family["title"],
                }
                if family
                else None
            ),
            "party": party,
            "display_name": display_name,
            "desc": desc,
            "desc_source": "lpc" if desc else None,
            "gender": values.get("gender", (None, None))[0],
            "age": values.get("age", (None, None))[0] if isinstance(values.get("age", (None, None))[0], int) else None,
            "class": values.get("class", (None, None))[0],
            "attitude": attitude,
            "aggressive": attitude == "aggressive",
            "race": race,
            "race_source": {
                "kind": "lpc", "path": rel_path, "line": race_line,
            } if race else None,
            "attrs": attrs,
            "attrs_roll": attrs_roll,
            "attrs_roll_source": attrs_roll_sources,
            "vitals": vitals,
            "skills": skills,
            "map_skills": map_skills,
            "prepare_skills": prepare_skills,
            "equipment": equipment,
            "temp_apply": list(temp_apply.values()),
            "is_master": is_master,
            "sect": sect if is_master else None,
            "vendor": vendor,
            "origin": origin,
            "source": {"path": rel_path, "line": name_line or 1},
            "_topics": topics,
        }

    def _region_name_sources(self) -> dict[str, dict]:
        """区域名只从原版区域表/出生地表取，取不到就不编。"""
        if self._region_name_cache is not None:
            return self._region_name_cache
        names: dict[str, dict] = {}
        mapd = self.read("adm/daemons/mapd.lpc")
        if mapd:
            lines = mapd.splitlines()
            span = None
            for idx, line in enumerate(lines):
                if re.search(r"\bmapping\s+map_short\s*=\s*\(\[", line):
                    end = None
                    for j in range(idx, len(lines)):
                        if re.search(r"\]\s*\)\s*;", lines[j]):
                            end = j
                            break
                    if end is not None:
                        span = (idx + 1, end + 1)
                    break
            if span is None:
                span = _function_span(mapd, "map_short")
            if span:
                start, end = span
                for lineno, line in enumerate(lines[start - 1:end], start):
                    m = re.search(r'"([a-z_]+)"\s*:\s*"([^"]+)"', line)
                    if m:
                        area, name = m.group(1), m.group(2)
                        names.setdefault(area, {
                            "name": name,
                            "source": {"kind": "lpc", "path": "adm/daemons/mapd.lpc", "line": lineno},
                        })
        logind = self.read("adm/daemons/logind.lpc")
        if logind:
            for lineno, line in enumerate(logind.splitlines(), 1):
                m = re.search(
                    r'hometown\s*=\s*"([^"]+)";\s*my\["START_ROOM"\]\s*=\s*"/d/([a-z_]+)/',
                    line,
                )
                if m:
                    name, area = m.group(1), m.group(2)
                    names.setdefault(area, {
                        "name": name,
                        "source": {"kind": "lpc", "path": "adm/daemons/logind.lpc", "line": lineno},
                    })
        self._region_name_cache = names
        return names

    def _race_attr_defaults(self, race: str) -> dict[str, dict]:
        """按种族 setup 抽 `random(N)+M` 掷骰区间；不写死具体值。"""
        if race in self._race_attr_cache:
            return self._race_attr_cache[race]
        rel_path = RACE_ATTR_FILES.get(race)
        defaults: dict[str, dict] = {}
        if rel_path:
            text = self.read(rel_path)
            if text is not None:
                for lineno, src_line in enumerate(text.splitlines(), 1):
                    m = _RACE_ATTR_ROLL_RE.search(src_line)
                    if not m:
                        continue
                    key, raw_n, raw_base = m.group(1), int(m.group(2)), int(m.group(3))
                    if key not in ATTR_KEYS:
                        continue
                    defaults[key] = {
                        "min": raw_base,
                        "max": raw_base + raw_n - 1,
                        "expr": f"random({raw_n}) + {raw_base}",
                        "source": {
                            "kind": "lpc",
                            "path": rel_path,
                            "line": lineno,
                        },
                    }
        self._race_attr_cache[race] = defaults
        return defaults

    def _make_temp_apply(self, key, value, line, rel_path, emit_diag):
        """把一条 create() 内的 set_temp 转成记录；非整数值保留原式，不折算。"""
        known = key in TEMP_APPLY_KEYS
        entry = {
            "key": key,
            "value": value if isinstance(value, int) else None,
            "value_expr": None,
            "known": known,
            "source_kind": "lpc",
            "source": {"path": rel_path, "line": line},
        }
        if not isinstance(value, int):
            if isinstance(value, str):
                entry["value_expr"] = '"' + value + '"'
            elif value is not None:
                entry["value_expr"] = getattr(value, "text", None)
            if emit_diag:
                self.diag(
                    "W_TEMP_VALUE_EXPR", "warning", rel_path, line,
                    f"临时属性 {key} 的值不是整数字面量，保留原式不折算",
                    key=key,
                )
        if not known and emit_diag:
            self.diag(
                "W_TEMP_KEY_UNKNOWN", "warning", rel_path, line,
                f"set_temp 键 {key} 不在战斗白名单，原样保留待确认",
                key=key,
            )
        return entry

    def _make_topic(self, topic, answer, line, rel_path, text="", origin="slice"):
        entry = {
            "topic": topic,
            "kind": "string",
            "text": None,
            "func": None,
            "source_kind": "lpc",
            "source": {"path": rel_path, "line": line},
        }
        if isinstance(answer, str):
            entry["text"] = lpc.clean_text(answer)
            return entry

        entry["kind"] = "fallback"
        func = getattr(answer, "text", str(answer))
        entry["func"] = func
        if origin != "slice":
            return entry

        analysis = self._analyze_dialogue_func(text, func, rel_path)
        entry.update(analysis)
        self.diag(
            "I_DIALOGUE_FUNC", "info", rel_path, analysis["fallback_source"]["line"],
            f"对话主题「{topic}」→ {func}：{analysis['fallback_reason']}",
            topic=topic,
            func=func,
            features=",".join(analysis["fallback_features"]),
        )
        return entry

    def _analyze_dialogue_func(self, text, func, rel_path):
        """只做静态判定，不编台词：能压成固定串就静态化，否则记原因。"""
        func_name = None
        m = re.fullmatch(r"\(\s*:\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*\)", func.strip())
        if m:
            func_name = m.group(1)
        span = _function_span(text, func_name) if func_name and text else None
        if span is None:
            return {
                "fallback_reason": "函数引用不是本文件内的具名函数，无法静态解析函数体",
                "fallback_features": ["unresolved_func"],
                "fallback_source": {"kind": "lpc", "path": rel_path, "line": None},
            }

        start, end = span
        body = "\n".join(text.splitlines()[start - 1:end])
        inner = re.sub(r"^[^{]*\{", "", body, count=1).rsplit("}", 1)[0]

        features = []
        if re.search(r"\bif\s*\(", inner) or re.search(r"\belse\b", inner):
            features.append("conditional")
        if re.search(r"\b(set_temp|delete_temp|add|move|set|unconcious)\s*\(", inner):
            features.append("state_write")
        if re.search(r"\bnew\s*\(", inner):
            features.append("item_create")
        if re.search(r"\bteach_perform\s*\(", inner):
            features.append("teaching")
        if "RANK_D->" in inner or re.search(r'"\s*\+', inner):
            features.append("dynamic_text")
        label = {
            "conditional": "if/else 条件分支",
            "state_write": "运行时状态写入（set_temp/delete_temp/add/move/set）",
            "item_create": "new() 创建物品",
            "teaching": "teach_perform 教授技能",
            "dynamic_text": "RANK_D 动态称谓/拼接文案",
        }

        # 保守静态化：无条件、无状态写入，只有固定 tell_object 和/或固定 return。
        static_text = None
        if not features:
            tell_strings = re.findall(
                r'tell_object\(\s*(?:me|this_player\(\))\s*,\s*'
                r'"((?:[^"\\]|\\.)*)"\s*\)\s*;', inner, re.S,
            )
            residue = re.sub(
                r'tell_object\(\s*(?:me|this_player\(\))\s*,\s*'
                r'"((?:[^"\\]|\\.)*)"\s*\)\s*;', "", inner, flags=re.S,
            )
            ret = re.fullmatch(r'\s*return\s+"((?:[^"\\]|\\.)*)"\s*;\s*', residue, re.S)
            if ret and (tell_strings or ret.group(1)):
                chunks = [lpc.clean_text(s) for s in tell_strings]
                if ret.group(1):
                    chunks.append(lpc.clean_text(ret.group(1)))
                static_text = "\n".join(c for c in chunks if c)

        if static_text is not None:
            return {
                "kind": "string",
                "text": static_text,
                "staticized_from_func": func,
                "fallback_reason": None,
                "fallback_features": [],
                "fallback_source": {"kind": "lpc", "path": rel_path, "line": start},
            }

        reason = "函数体含" + "、".join(label[f] for f in features)
        reason += "；回复依赖玩家状态或运行时对象，无法压成单一固定文本，保留 fallback"
        return {
            "fallback_reason": reason,
            "fallback_features": features,
            "fallback_source": {"kind": "lpc", "path": rel_path, "line": start},
        }

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
        if bases & NPC_LIKE_BASES:
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
        """两条口径分列：source（仅 lpc 原版）与 total（含自撰）。

        source 是对原版忠实度的回归线；total 是玩家可见描述完整性（必须 100%）。
        total 只作用于描述类指标；出口/对话没有自撰概念，不设 total 口径。
        """
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

        def metric(hit, total, threshold):
            return {
                "hit": hit,
                "total": total,
                "ratio": ratio(hit, total),
                "threshold": threshold,
            }

        source = {
            "rooms_desc": metric(
                sum(1 for r in slice_rooms if r.get("desc_source") == "lpc"),
                len(slice_rooms), 0.96),
            "room_exits": metric(
                sum(1 for r in slice_rooms if r["id"] in with_exits),
                len(slice_rooms), 0.97),
            "items_desc": metric(
                sum(1 for i in slice_items if i.get("desc_source") == "lpc"),
                len(slice_items), 0.82),
            "npcs_desc": metric(
                sum(1 for n in slice_npcs if n.get("desc_source") == "lpc"),
                len(slice_npcs), 0.90),
            "dialogue_text": metric(
                sum(1 for t in slice_topics if t["kind"] == "string"),
                len(slice_topics), 0.63),
        }
        total = {
            "rooms_desc": metric(
                sum(1 for r in slice_rooms if r["desc"]),
                len(slice_rooms), 1.0),
            "items_desc": metric(
                sum(1 for i in slice_items if i["desc"]),
                len(slice_items), 1.0),
            "npcs_desc": metric(
                sum(1 for n in slice_npcs if n["desc"]),
                len(slice_npcs), 1.0),
        }
        self.coverage = {"source": source, "total": total}

        for metric_name, data in source.items():
            if data["ratio"] < data["threshold"]:
                self.diag(
                    "W_COVERAGE_BELOW_TARGET", "warning", "coverage", None,
                    f"source.{metric_name} 覆盖率 {data['ratio']:.1%} 低于门槛 "
                    f"{data['threshold']:.1%}（{data['hit']}/{data['total']}）；"
                    f"缺口已逐条写入 diagnostics",
                    metric=f"source.{metric_name}",
                )
        for metric_name, data in total.items():
            if data["ratio"] < data["threshold"]:
                self.diag(
                    "E_COVERAGE_TOTAL_INCOMPLETE", "error", "coverage", None,
                    f"total.{metric_name} 覆盖率 {data['ratio']:.1%} 未达 100%"
                    f"（{data['hit']}/{data['total']}）；玩家会看到空白描述",
                    metric=f"total.{metric_name}",
                )

    # -- 统计 ---------------------------------------------------------------

    def counts(self):
        temp_entries = [
            entry
            for npc in self.npcs.values()
            for entry in npc.get("temp_apply", [])
        ]
        temp_keys = Counter(entry["key"] for entry in temp_entries)
        temp_unknown = sorted(k for k in temp_keys if k not in TEMP_APPLY_KEYS)
        return {
            "rooms_in_slice": sum(1 for r in self.rooms.values() if r["origin"] == "slice"),
            "rooms_boundary_stub": sum(1 for r in self.rooms.values() if r["origin"] == "boundary"),
            "rooms_with_region": sum(1 for r in self.rooms.values() if r.get("region")),
            "rooms_with_region_name": sum(1 for r in self.rooms.values() if r.get("region_name")),
            "rooms_region_null": sum(1 for r in self.rooms.values() if not r.get("region")),
            "rooms_region_name_null": sum(1 for r in self.rooms.values() if not r.get("region_name")),
            "exits": len(self.exits),
            "items_in_slice": sum(1 for i in self.items.values() if i["origin"] == "slice"),
            "items_external": sum(1 for i in self.items.values() if i["origin"] == "external"),
            "npcs_in_slice": sum(1 for n in self.npcs.values() if n["origin"] == "slice"),
            "npcs_external": sum(1 for n in self.npcs.values() if n["origin"] == "external"),
            "npcs_with_attrs_roll": sum(1 for n in self.npcs.values() if n.get("attrs_roll")),
            "npcs_with_attrs_roll_in_slice": sum(
                1 for n in self.npcs.values()
                if n["origin"] == "slice" and n.get("attrs_roll")
            ),
            "npcs_beast_attrs_roll": sum(
                1 for n in self.npcs.values()
                if n.get("race") in ("走兽", "野兽") and n.get("attrs_roll")
            ),
            "attrs_roll_fields": sum(
                len(n.get("attrs_roll") or {}) for n in self.npcs.values()
            ),
            "attrs_roll_fields_in_slice": sum(
                len(n.get("attrs_roll") or {}) for n in self.npcs.values()
                if n["origin"] == "slice"
            ),
            "dialogue_topics": len(self.dialogue),
            "dialogue_topics_in_slice": sum(1 for t in self.dialogue if t.get("origin") != "external"),
            "dialogue_fallback_with_reason": sum(
                1 for t in self.dialogue if t.get("fallback_reason")
            ),
            "dialogue_staticized_from_func": sum(
                1 for t in self.dialogue if t.get("staticized_from_func")
            ),
            "shops": len(self.shops),
            "sects": len(self.sects),
            "masters": sum(
                1 for n in self.npcs.values()
                if n.get("origin") == "slice" and n.get("is_master")
            ),
            "aggressive_npcs": sum(1 for n in self.npcs.values() if n.get("origin") == "slice" and n.get("aggressive")),
            "skills": len(self.skills),
            "desc_authored_items": sum(
                1 for i in self.items.values()
                if i.get("origin") == "slice" and i.get("desc_source") == "authored"
            ),
            "desc_authored_npcs": sum(
                1 for n in self.npcs.values()
                if n.get("origin") == "slice" and n.get("desc_source") == "authored"
            ),
            "skill_action_skills": self.skill_counts.get("action_skills", 0),
            "skill_action_entries": self.skill_counts.get("action_entries", 0),
            "skill_special_actions": self.skill_counts.get("special_actions", 0),
            "skill_action_gaps": self.skill_counts.get("action_gaps", 0),
            "skill_action_gap_skills": self.skill_counts.get("action_gap_skills", 0),
            "skill_action_gap_fields": self.skill_counts.get("action_gap_fields", 0),
            "skill_coefficient_inherited_defaults": self.skill_counts.get(
                "coefficient_inherited_defaults", 0
            ),
            "skill_action_unresolved": self.skill_counts.get("unresolved_action_skills", 0),
            "skill_stat_rows": self.skill_counts.get("stat_rows", 0),
            "skill_stat_default_fields": self.skill_counts.get("stat_default_fields", 0),
            "npcs_with_drops": self.drop_counts.get("npcs_with_drops", 0),
            "drop_entries": self.drop_counts.get("drop_entries", 0),
            "drops_guaranteed": self.drop_counts.get("drops_guaranteed", 0),
            "drops_random": self.drop_counts.get("drops_random", 0),
            "drops_unresolved_chance": self.drop_counts.get("drops_unresolved_chance", 0),
            "npcs_no_drops": self.drop_counts.get("npcs_no_drops", 0),
            "equipment_items_added": self.equipment_counts.get("equipment_items_added", 0),
            "equipment_items_unresolved": self.equipment_counts.get("equipment_items_unresolved", 0),
            "equipment_refs_normalized": self.equipment_counts.get("equipment_refs_normalized", 0),
            "temp_apply_npcs": sum(1 for n in self.npcs.values() if n.get("temp_apply")),
            "temp_apply_npcs_in_slice": sum(
                1 for n in self.npcs.values()
                if n.get("origin") == "slice" and n.get("temp_apply")
            ),
            "temp_apply_entries": len(temp_entries),
            "temp_apply_unknown_entries": sum(
                n for k, n in temp_keys.items() if k not in TEMP_APPLY_KEYS
            ),
            "temp_apply_keys": dict(sorted(temp_keys.items())),
            "temp_apply_unknown_keys": temp_unknown,
            "quests_total": self.quest_counts.get("quests_total", 0),
            "quests_dynamic": self.quest_counts.get("quests_dynamic", 0),
            "quests_talk_gated": self.quest_counts.get("quests_talk_gated", 0),
            "quests_static_entries": self.quest_counts.get("quests_static_entries", 0),
            "quests_static_unique": self.quest_counts.get("quests_static_unique", 0),
            "quests_static_kill_targets_in_slice": self.quest_counts.get(
                "quests_static_kill_targets_in_slice", 0
            ),
            "quests_dynamic_total": self.quest_counts.get("quests_dynamic_total", 0),
            "quests_dynamic_outside_slice": self.quest_counts.get(
                "quests_dynamic_outside_slice", 0
            ),
            "quests_non_task": self.quest_counts.get("quests_non_task", 0),
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
