"""内容包 manifest 与扩展内容加载校验。

设计目标（P2）：
- content/ 下的基础包与 additions/ 扩展包都由 manifest 声明，加载器只认 manifest。
- 新增 NPC / 物品 / 地点 / 角色 / 门派 / 动词元数据只写 YAML，不改 src/。
- 校验错误带 `content/<file>:<line>`，缺字段、引用不存在 id、师徒关系成环、
  地点图不连通都必须在启动时失败，而不是静默忽略。

本模块只用标准库 + PyYAML（项目已有依赖）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None


CONTENT_MANIFEST_VERSION = "1.0.0"
ENGINE_VERSION = "0.1.0"
SUPPORTED_MANIFEST_MAJOR = 1

TYPE_ROOT_KEYS: dict[str, str | None] = {
    "npcs": "npcs",
    "locations": "locations",
    "items": "items",
    "verbs": "verbs",
    "economy": None,
    "roles": "roles",
    "sects": "sects",
}
REQUIRED_TYPES = ("npcs", "locations", "items", "verbs", "economy")

ITEM_CATEGORIES = {"food", "drink", "material", "tool", "medicine", "luxury", "service"}
GOAL_TYPES = {
    "earn_money",
    "advance_skill",
    "gain_status",
    "build_relation",
    "build_shop",
    "join_sect",
    "found_sect",
}
RELATION_TYPES = {
    "family",
    "friend",
    "rival",
    "creditor",
    "debtor",
    "master",
    "disciple",
    "ally",
    "acquaintance",
}
ID_RE = re.compile(r"^[a-z][a-z0-9_.-]*$")
ROLE_ID_RE = re.compile(r"^[^\s:#-][^:#]*$")

_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


class ContentValidationError(ValueError):
    """内容包校验失败；messages 已是可直接打印的 `file:line` 列表。"""

    def __init__(self, messages: list[str]):
        self.messages = list(messages)
        super().__init__("\n".join(self.messages))


@dataclass
class LoadedContent:
    data: dict[str, Any] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    manifest_version: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# YAML / 行号
# --------------------------------------------------------------------------


def _read_yaml(path: Path) -> Any:
    if yaml is None:
        raise ContentValidationError([f"{path.name}: PyYAML 未安装，无法解析 YAML"])
    try:
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except yaml.YAMLError as exc:
        line = getattr(getattr(exc, "problem_mark", None), "line", None)
        loc = f"{path.name}:{line + 1}" if line is not None else path.name
        raise ContentValidationError([f"{loc}: YAML 解析失败: {getattr(exc, 'problem', exc)}"]) from exc


def _strip_comment(line: str) -> str:
    out, quote, i = [], None, 0
    while i < len(line):
        ch = line[i]
        if quote:
            out.append(ch)
            if quote == '"' and ch == "\\" and i + 1 < len(line):
                out.append(line[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
        else:
            if ch in "\"'":
                quote = ch
                out.append(ch)
            elif ch == "#":
                break
            else:
                out.append(ch)
        i += 1
    return "".join(out).rstrip()


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def scan_entry_lines(path: Path, root_key: str) -> dict[str, int]:
    """扫描 `root_key` 下每个 `- id:` / `- name:` 的行号。

    PyYAML 不保留行号，这里只做一个够用的轻量扫描：错误定位到条目起始行即可。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    found_root = False
    result: dict[str, int] = {}
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = _strip_comment(raw)
        if not found_root:
            if re.match(rf"^{re.escape(root_key)}\s*:", line):
                found_root = True
            continue
        if not line.strip():
            continue
        # 顶层下一个键，root_key 段结束
        if not line.startswith((" ", "\t", "-")):
            break
        m = re.match(r"^\s*-\s+(id|name)\s*:\s*(.+)$", line)
        if not m:
            continue
        key = _unquote(m.group(2))
        if key and key not in result:
            result[key] = lineno
    return result


def _line_of(line_map: dict[str, int], entry: Any) -> int:
    if not isinstance(entry, dict):
        return 0
    for key in ("id", "name"):
        val = entry.get(key)
        if isinstance(val, str) and val in line_map:
            return line_map[val]
    return 0


def _loc(path: Path, content_dir: Path, line: int, msg: str) -> str:
    rel = path.relative_to(content_dir).as_posix()
    prefix = f"content/{rel}"
    return f"{prefix}:{line}: {msg}" if line else f"{prefix}: {msg}"


# --------------------------------------------------------------------------
# 版本
# --------------------------------------------------------------------------


def _parse_semver(value: Any) -> tuple[int, int, int] | None:
    if not isinstance(value, str):
        return None
    m = _SEMVER_RE.match(value.strip())
    if not m:
        return None
    return tuple(int(x) for x in m.groups())


def _version_less(a: tuple[int, int, int], b: tuple[int, int, int]) -> bool:
    return a < b


# --------------------------------------------------------------------------
# manifest
# --------------------------------------------------------------------------


@dataclass
class Package:
    id: str
    type: str
    path: Path
    version: str
    root_key: str | None
    line: int = 0


def _load_manifest(content_dir: Path) -> tuple[dict[str, Any], list[Package], list[str]]:
    path = content_dir / "manifest.yaml"
    errors: list[str] = []
    data = _read_yaml(path)
    if not isinstance(data, dict):
        raise ContentValidationError([f"content/manifest.yaml: 顶层必须是映射"])

    version = data.get("manifest_version")
    parsed = _parse_semver(version)
    if parsed is None:
        errors.append("content/manifest.yaml: manifest_version 必须是语义化版本，如 1.0.0")
    elif parsed[0] != SUPPORTED_MANIFEST_MAJOR:
        errors.append(
            f"content/manifest.yaml: manifest_version {version} 不受支持（当前只支持 {SUPPORTED_MANIFEST_MAJOR}.x）"
        )

    engine_min = data.get("engine_min_version")
    if engine_min is not None:
        em = _parse_semver(engine_min)
        if em is None:
            errors.append("content/manifest.yaml: engine_min_version 必须是语义化版本")
        elif _version_less(_parse_semver(ENGINE_VERSION) or (0, 0, 0), em):
            errors.append(
                f"content/manifest.yaml: 需要引擎 >= {engine_min}，当前 {ENGINE_VERSION}"
            )

    raw_packages = data.get("packages")
    if not isinstance(raw_packages, list) or not raw_packages:
        errors.append("content/manifest.yaml: packages 必须是非空列表")
        raw_packages = []

    packages: list[Package] = []
    seen_ids: set[str] = set()
    seen_types: set[str] = set()
    for i, pkg in enumerate(raw_packages):
        label = f"content/manifest.yaml: packages[{i}]"
        if not isinstance(pkg, dict):
            errors.append(f"{label}: 必须是映射")
            continue
        missing = [k for k in ("id", "type", "path", "version") if not pkg.get(k)]
        if missing:
            errors.append(f"{label}: 缺少字段 {', '.join(missing)}")
            continue
        pid = str(pkg["id"])
        ptype = str(pkg["type"])
        pver = str(pkg["version"])
        if pid in seen_ids:
            errors.append(f"{label}: 包 id 重复: {pid}")
            continue
        seen_ids.add(pid)
        if ptype not in TYPE_ROOT_KEYS:
            errors.append(f"{label}: 未知内容类型 {ptype}（支持: {', '.join(sorted(TYPE_ROOT_KEYS))}）")
            continue
        if _parse_semver(pver) is None:
            errors.append(f"{label}: version 必须是语义化版本，实际 {pver!r}")
        seen_types.add(ptype)

        rel = Path(str(pkg["path"]))
        if rel.is_absolute() or ".." in rel.parts:
            errors.append(f"{label}: path 必须是 content/ 内的相对路径")
            continue
        ppath = content_dir / rel
        if not ppath.exists():
            errors.append(f"{label}: 文件不存在 content/{rel.as_posix()}")
            continue
        packages.append(
            Package(
                id=pid,
                type=ptype,
                path=ppath,
                version=pver,
                root_key=TYPE_ROOT_KEYS[ptype],
            )
        )

    missing_types = [t for t in REQUIRED_TYPES if t not in seen_types]
    if missing_types:
        errors.append(
            "content/manifest.yaml: 缺少必须内容类型声明: " + ", ".join(missing_types)
        )

    # additions/ 下的文件必须显式声明，避免“自动加载意外文件”
    declared = {p.path.resolve() for p in packages}
    additions = sorted((content_dir / "additions").glob("*.yaml")) if (content_dir / "additions").is_dir() else []
    for ap in additions:
        if ap.resolve() not in declared:
            errors.append(
                f"content/manifest.yaml: additions/{ap.name} 未被 packages 声明"
            )

    if errors:
        raise ContentValidationError(errors)
    return data, packages, errors


# --------------------------------------------------------------------------
# 合并与校验
# --------------------------------------------------------------------------


def _merge_dict(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge_dict(out[k], v)
        else:
            out[k] = v
    return out


def _entry_key(entry: Any) -> str | None:
    if not isinstance(entry, dict):
        return None
    for key in ("id", "name"):
        if isinstance(entry.get(key), str) and entry[key]:
            return entry[key]
    return None


def _check_required(entry: Any, required: list[str], label: str, path: Path, content_dir: Path,
                    line: int, errors: list[str], optional: tuple[str, ...] = ()) -> None:
    if not isinstance(entry, dict):
        errors.append(_loc(path, content_dir, line, f"{label} 必须是映射"))
        return
    missing = [k for k in required if k not in entry]
    if missing:
        errors.append(_loc(path, content_dir, line, f"{label} 缺少字段: {', '.join(missing)}"))
    allowed = set(required) | set(optional)
    extra = [k for k in entry if k not in allowed]
    if extra:
        errors.append(_loc(path, content_dir, line, f"{label} 存在未知字段: {', '.join(extra)}"))


def _check_ids(entries: list[Any], pkgs: list[tuple[Path, dict[str, int]]], label: str,
               content_dir: Path, errors: list[str], pattern: re.Pattern | None = None) -> None:
    seen: dict[str, tuple[Path, int]] = {}
    for entry in entries:
        key = _entry_key(entry)
        path, line = _entry_location(pkgs, entry, content_dir)
        if not key:
            errors.append(_loc(path, content_dir, line, f"{label} 条目缺少 id/name"))
            continue
        if pattern is not None and not pattern.match(key):
            errors.append(_loc(path, content_dir, line, f"{label} id 不合法: {key!r}"))
        if key in seen:
            p0, l0 = seen[key]
            errors.append(
                _loc(path, content_dir, line,
                     f"{label} id 重复: {key}（首次出现于 {p0.relative_to(content_dir).as_posix()}:{l0}）")
            )
        else:
            seen[key] = (path, line)


def _entry_location(pkgs: list[tuple[Path, dict[str, int]]], entry: Any,
                    content_dir: Path) -> tuple[Path, int]:
    key = _entry_key(entry)
    for path, lm in pkgs:
        if key and key in lm:
            return path, lm[key]
    if pkgs:
        return pkgs[0][0], 0
    return content_dir / "npcs.yaml", 0


def _find_master_cycle(master_of: dict[str, str]) -> list[str]:
    """返回成环链条（含重复首元素），无环返回 []。"""
    state: dict[str, int] = {}
    for node in sorted(master_of):
        if state.get(node, 0) != 0:
            continue
        path: list[str] = []
        cur = node
        while True:
            if state.get(cur, 0) == 2:
                break
            if state.get(cur, 0) == 1:
                idx = path.index(cur) if cur in path else 0
                return path[idx:] + [cur]
            state[cur] = 1
            path.append(cur)
            if cur not in master_of:
                break
            cur = master_of[cur]
        for p in path:
            state[p] = 2
    return []


def validate_merged(merged: dict[str, Any], meta: dict[str, Any], content_dir: Path) -> None:
    """校验合并后的内容。meta 记录每个文件的 entry 行号映射。"""
    errors: list[str] = []
    # meta[type] = list[(path, line_map)]
    npc_pkgs: list[tuple[Path, dict[str, int]]] = meta.get("npcs") or []
    loc_pkgs: list[tuple[Path, dict[str, int]]] = meta.get("locations") or []
    item_pkgs: list[tuple[Path, dict[str, int]]] = meta.get("items") or []
    verb_pkgs: list[tuple[Path, dict[str, int]]] = meta.get("verbs") or []
    role_pkgs: list[tuple[Path, dict[str, int]]] = meta.get("roles") or []
    sect_pkgs: list[tuple[Path, dict[str, int]]] = meta.get("sects") or []

    def line_for(pkgs: list[tuple[Path, dict[str, int]]], entry: Any) -> tuple[Path, int]:
        return _entry_location(pkgs, entry, content_dir)

    npcs = merged.get("npcs") or []
    locations = merged.get("locations") or []
    items = merged.get("items") or []
    verbs = merged.get("verbs") or []
    roles = merged.get("roles") or []
    sects = merged.get("sects") or []
    economy = merged.get("economy") or {}

    # ---------------- 唯一性与 id 格式 ----------------
    _check_ids(npcs, npc_pkgs, "NPC", content_dir, errors, ID_RE)
    _check_ids(locations, loc_pkgs, "地点", content_dir, errors, ID_RE)
    _check_ids(items, item_pkgs, "物品", content_dir, errors, ID_RE)
    _check_ids(verbs, verb_pkgs, "动词", content_dir, errors)
    _check_ids(roles, role_pkgs, "职业", content_dir, errors, ROLE_ID_RE)
    _check_ids(sects, sect_pkgs, "门派", content_dir, errors, ID_RE)

    # ---------------- NPC ----------------
    for entry in npcs:
        key = _entry_key(entry)
        path, line = line_for(npc_pkgs, entry)
        _check_required(
            entry,
            ["id", "name", "role", "home"],
            f"NPC {key}",
            path,
            content_dir,
            line,
            errors,
            optional=(
                "personality",
                "goals",
                "assets",
                "relations",
                "location",
                "speech_style",
            ),
        )

    location_ids = {_entry_key(e) for e in locations if _entry_key(e)}
    item_ids = {_entry_key(e) for e in items if _entry_key(e)}
    npc_ids = {_entry_key(e) for e in npcs if _entry_key(e)}
    base_roles = set((economy.get("wages_daily") or {}).keys()) | set(
        ((economy.get("living_costs") or {}).get("by_role") or {}).keys()
    )
    role_ids = base_roles | {_entry_key(e) for e in roles if _entry_key(e)}
    verb_names = {_entry_key(e) for e in verbs if _entry_key(e)}

    for entry in npcs:
        key = _entry_key(entry)
        path, line = line_for(npc_pkgs, entry)
        if not isinstance(entry, dict):
            continue
        home = entry.get("home")
        if home is not None and home not in location_ids:
            errors.append(_loc(path, content_dir, line, f"NPC {key} home 引用不存在的地点: {home}"))
        role = entry.get("role")
        if role is not None and role not in role_ids:
            errors.append(_loc(path, content_dir, line, f"NPC {key} role 引用不存在的职业: {role}"))
        for g in entry.get("goals") or []:
            if not isinstance(g, dict):
                errors.append(_loc(path, content_dir, line, f"NPC {key} goals 条目必须是映射"))
                continue
            if g.get("type") not in GOAL_TYPES:
                errors.append(
                    _loc(path, content_dir, line, f"NPC {key} goal 类型未知: {g.get('type')!r}")
                )
        assets = entry.get("assets") or {}
        for it in assets.get("items") or []:
            if not isinstance(it, dict):
                errors.append(_loc(path, content_dir, line, f"NPC {key} assets.items 条目必须是映射"))
                continue
            if it.get("id") not in item_ids:
                errors.append(
                    _loc(path, content_dir, line, f"NPC {key} 物品引用不存在: {it.get('id')}")
                )
        for rel in entry.get("relations") or []:
            if not isinstance(rel, dict):
                errors.append(_loc(path, content_dir, line, f"NPC {key} relations 条目必须是映射"))
                continue
            if rel.get("target") not in npc_ids:
                errors.append(
                    _loc(path, content_dir, line, f"NPC {key} 关系目标不存在: {rel.get('target')}")
                )
            if rel.get("type") not in RELATION_TYPES:
                errors.append(
                    _loc(path, content_dir, line, f"NPC {key} 关系类型未知: {rel.get('type')!r}")
                )

    # ---------------- 地点 ----------------
    for entry in locations:
        key = _entry_key(entry)
        path, line = line_for(loc_pkgs, entry)
        _check_required(entry, ["id", "name", "exits", "services"], f"地点 {key}",
                        path, content_dir, line, errors)
        if not isinstance(entry, dict):
            continue
        for ex in entry.get("exits") or []:
            if ex not in location_ids:
                errors.append(_loc(path, content_dir, line, f"地点 {key} exit 引用不存在: {ex}"))
            if ex == key:
                errors.append(_loc(path, content_dir, line, f"地点 {key} exit 指向自身"))
        for svc in entry.get("services") or []:
            if svc not in item_ids:
                errors.append(_loc(path, content_dir, line, f"地点 {key} service 引用不存在的物品: {svc}"))

    # ---------------- 物品 ----------------
    for entry in items:
        key = _entry_key(entry)
        path, line = line_for(item_pkgs, entry)
        _check_required(entry, ["id", "name", "category", "base_price"], f"物品 {key}",
                        path, content_dir, line, errors)
        if isinstance(entry, dict) and entry.get("category") not in ITEM_CATEGORIES:
            errors.append(_loc(path, content_dir, line, f"物品 {key} category 未知: {entry.get('category')!r}"))

    # ---------------- 职业 ----------------
    for entry in roles:
        key = _entry_key(entry)
        path, line = line_for(role_pkgs, entry)
        _check_required(entry, ["id", "name", "wage", "living_cost", "workplace"],
                        f"职业 {key}", path, content_dir, line, errors, optional=("skill",))
        if not isinstance(entry, dict):
            continue
        if entry.get("workplace") not in location_ids:
            errors.append(_loc(path, content_dir, line, f"职业 {key} workplace 引用不存在的地点: {entry.get('workplace')}"))
        wage = entry.get("wage")
        if isinstance(wage, bool) or not isinstance(wage, int) or wage <= 0:
            errors.append(_loc(path, content_dir, line, f"职业 {key} wage 必须是正整数"))
        lc = entry.get("living_cost")
        if not isinstance(lc, dict):
            errors.append(_loc(path, content_dir, line, f"职业 {key} living_cost 必须是映射"))

    # ---------------- 门派 ----------------
    for entry in sects:
        key = _entry_key(entry)
        path, line = line_for(sect_pkgs, entry)
        _check_required(entry, ["id", "name", "location"], f"门派 {key}",
                        path, content_dir, line, errors)
        if isinstance(entry, dict) and entry.get("location") not in location_ids:
            errors.append(
                _loc(path, content_dir, line, f"门派 {key} location 引用不存在的地点: {entry.get('location')}")
            )

    # ---------------- 动词 ----------------
    for entry in verbs:
        key = _entry_key(entry)
        path, line = line_for(verb_pkgs, entry)
        _check_required(entry, ["name", "args", "allowed_roles"], f"动词 {key}",
                        path, content_dir, line, errors, optional=("cooldown", "description"))
        if not isinstance(entry, dict):
            continue
        for role in entry.get("allowed_roles") or []:
            if role == "*":
                continue
            if role not in role_ids:
                errors.append(_loc(path, content_dir, line, f"动词 {key} allowed_role 不存在: {role}"))

    # ---------------- 引用成环 ----------------
    master_of: dict[str, str] = {}
    where: dict[str, tuple[Path, int]] = {}
    for entry in npcs:
        key = _entry_key(entry)
        if not isinstance(entry, dict):
            continue
        path, line = line_for(npc_pkgs, entry)
        where[key] = (path, line)
        for rel in entry.get("relations") or []:
            if not isinstance(rel, dict):
                continue
            if rel.get("type") == "master":
                master_of[str(rel.get("target"))] = key
            elif rel.get("type") == "disciple":
                master_of[key] = str(rel.get("target"))
    cycle = _find_master_cycle({k: v for k, v in master_of.items() if k in npc_ids and v in npc_ids})
    if cycle:
        chain = " -> ".join(cycle)
        first = cycle[0]
        path, line = where.get(first, (npcs and npc_pkgs[0][0] or content_dir / "npcs.yaml", 0))
        errors.append(_loc(path, content_dir, line, f"师徒关系成环: {chain}"))

    # ---------------- 地点连通 ----------------
    if location_ids:
        undirected: dict[str, set[str]] = {lid: set() for lid in location_ids}
        for entry in locations:
            key = _entry_key(entry)
            if not isinstance(entry, dict):
                continue
            for ex in entry.get("exits") or []:
                if ex in undirected and key in undirected:
                    undirected[key].add(ex)
                    undirected[ex].add(key)
        start = sorted(location_ids)[0]
        seen = {start}
        stack = [start]
        while stack:
            cur = stack.pop()
            for nxt in sorted(undirected[cur]):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        missing = sorted(location_ids - seen)
        if missing:
            errors.append(f"content/locations.yaml: 地点图不连通，未到达: {', '.join(missing)}")

    if errors:
        raise ContentValidationError(errors)


def load_content_packages(content_dir: Path) -> LoadedContent:
    """读取 manifest 并返回合并后的原始数据（未规范化）。"""
    _, packages, _ = _load_manifest(content_dir)
    merged: dict[str, Any] = {
        "npcs": [],
        "locations": [],
        "items": [],
        "verbs": [],
        "economy": {},
        "roles": [],
        "sects": [],
    }
    meta: dict[str, list[tuple[Path, dict[str, int]]]] = {k: [] for k in merged}
    sources: dict[str, list[str]] = {k: [] for k in merged}

    for pkg in packages:
        data = _read_yaml(pkg.path)
        if pkg.root_key is None:
            if not isinstance(data, dict):
                raise ContentValidationError([f"content/{pkg.path.relative_to(content_dir)}: economy 顶层必须是映射"])
            merged["economy"] = _merge_dict(merged["economy"], data)
        else:
            if not isinstance(data, dict):
                raise ContentValidationError(
                    [f"content/{pkg.path.relative_to(content_dir)}: 顶层必须是映射"]
                )
            entries = data.get(pkg.root_key)
            if not isinstance(entries, list):
                raise ContentValidationError(
                    [f"content/{pkg.path.relative_to(content_dir)}: {pkg.root_key} 必须是列表"]
                )
            merged[pkg.type].extend(entries)
            lm = scan_entry_lines(pkg.path, pkg.root_key)
            meta[pkg.type].append((pkg.path, lm))
        sources[pkg.type].append(f"content/{pkg.path.relative_to(content_dir).as_posix()}")

    # 每个类型的所有文件行号映射合并（用于 _check_ids）
    merged_meta = dict(meta)
    validate_merged(merged, merged_meta, content_dir)

    manifest_data = _read_yaml(content_dir / "manifest.yaml")
    return LoadedContent(
        data=merged,
        sources={k: "+".join(v) for k, v in sources.items() if v},
        manifest_version=str(manifest_data.get("manifest_version", "")),
    )


def validate_content_dir(content_dir: Path) -> LoadedContent:
    """供 tools/new_content.py / framework_check 调用的校验入口。"""
    return load_content_packages(Path(content_dir))
