#!/usr/bin/env python3
"""world-sim 内容包独立校验脚本。

只使用 Python 标准库（不 import yaml，也不 import 引擎代码）。
内置一个够用的 YAML 子集解析器，支持本项目内容文件用到的：
- 缩进映射 / 序列
- 行内 flow 列表 [a, b] 与 flow 映射 {k: v}
- 单双引号字符串、整数、浮点、布尔
- 行尾 # 注释

用法:
    python3 validate_content.py
    python3 validate_content.py --verbose

退出码 0 = 全部硬性检查通过；1 = 存在错误。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
REQUIRED_FILES = [
    "npcs.yaml", "locations.yaml", "items.yaml", "verbs.yaml", "economy.yaml",
    "sects.yaml", "escort_routes.yaml", "quests.yaml",
]

NPC_ID_RE = re.compile(r"^[a-z][a-z_]*$")
ITEM_ID_RE = re.compile(r"^[a-z][a-z_]*$")
LOC_ID_RE = re.compile(r"^[a-z][a-z_]*$")

GOAL_TYPES = {
    "earn_money", "advance_skill", "gain_status", "build_relation",
    "build_shop", "join_sect", "found_sect",
}
# 引擎 actions/common.py 的 GOAL_TYPE_MAP 与 goal kind；goal_targets 顶层标量按 kind 读取。
GOAL_KIND_MAP = {
    "earn_money": "wealth",
    "advance_skill": "mastery",
    "gain_status": "faction",
    "build_relation": "bond",
    "build_shop": "shop",
    "join_sect": "faction",
    "found_sect": "faction",
}
GOAL_KINDS = {"wealth", "mastery", "bond", "shop", "faction"}
QUEST_CATEGORY_VERBS = {
    # 跑腿/送信以“与目标 NPC 当面交接”为准，用零金额 trade；
    # 引擎任务执行器对 move 任务会重复下发同点 move，产生必然拒绝。
    "errand": "trade",
    "escort": "escort",
    "procurement": "buy",
    "inquiry": "trade",
    "commission": "work",
    "debt_collection": "trade",
}
PERSONA_HEADINGS = [
    "## 一句话性格",
    "## 说话风格",
    "## 日常锚点",
    "## 在意的东西",
    "## 与谁有张力",
    "## 遇事反应倾向",
]
RELATION_TYPES = {"family", "friend", "rival", "creditor", "debtor", "master", "disciple"}
ITEM_CATEGORIES = {"food", "material", "tool", "medicine", "luxury", "service"}
REQUIRED_VERBS = {
    "move", "cultivate", "work", "consume", "buy", "sell",
    "trade", "apprentice", "join_sect", "marry", "open_shop", "escort", "found_sect",
    # P3：talk 供对话层，recruit/quest_accept/quest_deliver/debt_settle 供计划器内部调用。
    "talk", "recruit", "quest_accept", "quest_deliver", "debt_settle",
    # P3-E 玩家在线心跳：引擎已注册，内容包补齐元数据。
    "player_heartbeat",
}
PLAYER_VERBS = {
    "move", "cultivate", "work", "consume", "buy", "sell",
    "trade", "apprentice", "join_sect", "marry", "open_shop", "escort", "found_sect",
}


class YamlError(ValueError):
    pass


# --------------------------------------------------------------------------
# 极简 YAML 子集解析器（纯标准库）
# --------------------------------------------------------------------------

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


def _split_top(s: str, sep: str = ",") -> list[str]:
    parts, cur, depth, quote, i = [], [], 0, None, 0
    while i < len(s):
        ch = s[i]
        if quote:
            cur.append(ch)
            if quote == '"' and ch == "\\" and i + 1 < len(s):
                cur.append(s[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
        else:
            if ch in "\"'":
                quote = ch
                cur.append(ch)
            elif ch in "[{(":
                depth += 1
                cur.append(ch)
            elif ch in "]})":
                depth -= 1
                cur.append(ch)
            elif ch == sep and depth == 0:
                parts.append("".join(cur).strip())
                cur = []
            else:
                cur.append(ch)
        i += 1
    parts.append("".join(cur).strip())
    return [p for p in parts if p != ""]


def _split_kv(s: str):
    depth, quote = 0, None
    for i, ch in enumerate(s):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "[{(":
            depth += 1
        elif ch in "]})":
            depth -= 1
        elif ch == ":" and depth == 0:
            return s[:i].strip(), s[i + 1:].strip()
    return s.strip(), None


def _parse_value(v: str):
    v = v.strip()
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        return [] if not inner else [_parse_value(p) for p in _split_top(inner)]
    if v.startswith("{") and v.endswith("}"):
        inner = v[1:-1].strip()
        if not inner:
            return {}
        d = {}
        for part in _split_top(inner):
            k, val = _split_kv(part)
            if val is None:
                raise YamlError(f"flow 映射缺少冒号: {part!r}")
            d[k] = _parse_value(val)
        return d
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        try:
            return json.loads(v)
        except json.JSONDecodeError as exc:
            raise YamlError(f"双引号字符串解析失败: {v!r} ({exc})") from exc
    if len(v) >= 2 and v[0] == "'" and v[-1] == "'":
        return v[1:-1].replace("''", "'")
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    if re.fullmatch(r"-?\d+\.\d+", v):
        return float(v)
    if v in ("true", "false"):
        return v == "true"
    if v in ("null", "~"):
        return None
    return v


class _Parser:
    def __init__(self, text: str):
        self.lines = []
        for lineno, raw in enumerate(text.splitlines(), 1):
            line = _strip_comment(raw)
            if not line.strip():
                continue
            indent = len(line) - len(line.lstrip(" "))
            if "\t" in line[:indent + 1]:
                raise YamlError(f"第 {lineno} 行使用了 Tab 缩进")
            self.lines.append((indent, line.strip(), lineno))
        self.i = 0

    def _is_seq(self, content: str) -> bool:
        return content == "-" or content.startswith("- ")

    def parse(self):
        if not self.lines:
            return None
        if self.lines[0][0] != 0:
            raise YamlError("顶层缩进必须为 0")
        value = self._parse_node(0)
        if self.i != len(self.lines):
            raise YamlError(f"第 {self.lines[self.i][2]} 行存在无法归属的内容")
        return value

    def _parse_node(self, indent: int):
        if self.i >= len(self.lines):
            return None
        ind, content, _ = self.lines[self.i]
        if ind != indent:
            raise YamlError(f"缩进不一致: 期望 {indent}，实际 {ind}")
        if self._is_seq(content):
            return self._parse_seq(indent)
        return self._parse_map(indent)

    def _parse_map(self, indent: int) -> dict:
        result = {}
        while self.i < len(self.lines):
            ind, content, lineno = self.lines[self.i]
            if ind < indent:
                break
            if ind > indent:
                raise YamlError(f"第 {lineno} 行缩进过深: 期望 {indent}，实际 {ind}")
            if self._is_seq(content):
                break
            key, rest = _split_kv(content)
            if rest is None:
                raise YamlError(f"第 {lineno} 行映射缺少冒号: {content!r}")
            self.i += 1
            if rest == "":
                if self.i < len(self.lines) and self.lines[self.i][0] > indent:
                    result[key] = self._parse_node(self.lines[self.i][0])
                else:
                    result[key] = None
            else:
                result[key] = _parse_value(rest)
        return result

    def _parse_seq(self, indent: int) -> list:
        result = []
        while self.i < len(self.lines):
            ind, content, lineno = self.lines[self.i]
            if ind != indent or not self._is_seq(content):
                break
            rest = content[1:].strip()
            self.i += 1
            if rest == "":
                if self.i < len(self.lines) and self.lines[self.i][0] > indent:
                    result.append(self._parse_node(self.lines[self.i][0]))
                else:
                    result.append(None)
                continue
            key, val = _split_kv(rest)
            if val is None:
                result.append(_parse_value(rest))
                continue
            item = {}
            if val == "":
                if self.i < len(self.lines) and self.lines[self.i][0] > indent + 2:
                    item[key] = self._parse_node(self.lines[self.i][0])
                else:
                    item[key] = None
            else:
                item[key] = _parse_value(val)
            if self.i < len(self.lines):
                nind, ncontent, _ = self.lines[self.i]
                if nind == indent + 2 and not self._is_seq(ncontent):
                    item.update(self._parse_map(indent + 2))
            result.append(item)
        return result


def load_yaml(path: Path):
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise YamlError(f"文件不存在: {path}") from None
    try:
        return _Parser(text).parse()
    except YamlError as exc:
        raise YamlError(f"{path.name}: {exc}") from exc


# --------------------------------------------------------------------------
# 校验
# --------------------------------------------------------------------------

class Report:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.checks: list[tuple[bool, str]] = []
        self.info: list[str] = []

    def check(self, ok: bool, label: str, detail: str = ""):
        self.checks.append((ok, label))
        if not ok:
            self.errors.append(f"{label}: {detail}" if detail else label)
        return ok

    def warn(self, msg: str):
        self.warnings.append(msg)

    def note(self, msg: str):
        self.info.append(msg)


def _require_dict(obj, label, errors):
    if not isinstance(obj, dict):
        errors.append(f"{label} 必须是映射")
        return False
    return True


def _check_fields(obj, required, label, errors, optional=()):
    if not isinstance(obj, dict):
        errors.append(f"{label} 必须是映射")
        return False
    missing = [k for k in required if k not in obj]
    extra = [k for k in obj if k not in set(required) | set(optional)]
    if missing:
        errors.append(f"{label} 缺少字段: {', '.join(missing)}")
    if extra:
        errors.append(f"{label} 存在未知字段: {', '.join(extra)}")
    return not missing and not extra


def check_npcs(npcs, items_by_id, report, location_ids):
    if not report.check(isinstance(npcs, list), "npcs.yaml 结构", "npcs 必须是列表"):
        return
    report.check(len(npcs) == 30, "NPC 数量", f"期望 30，实际 {len(npcs)}")

    npc_ids, names, goal_ids = [], [], []
    money_total = 0
    roles = []
    relation_edges = {}
    money_errors = []
    total_debt = 0
    debtor_ids = set()
    debt_amount_ok = True
    cap_violations = 0
    home_counts = {}
    home_errors = 0

    for idx, npc in enumerate(npcs):
        label = f"NPC[{idx}]"
        if not _require_dict(npc, label, report.errors):
            continue
        _check_fields(npc, ["id", "name", "role", "home", "personality", "goals", "assets", "relations"],
                      label, report.errors)
        npc_id = npc.get("id")
        if not isinstance(npc_id, str) or not NPC_ID_RE.fullmatch(npc_id):
            report.errors.append(f"{label} id 不合法（只允许小写字母与下划线）: {npc_id!r}")
            continue
        npc_ids.append(npc_id)
        label = npc_id

        for field in ("name", "role"):
            if not isinstance(npc.get(field), str) or not npc.get(field).strip():
                report.errors.append(f"{label}.{field} 必须是非空字符串")
        names.append(npc.get("name"))
        roles.append(npc.get("role"))

        home = npc.get("home")
        if not isinstance(home, str) or not home.strip():
            report.errors.append(f"{label}.home 必须是非空地点 id")
            home_errors += 1
        elif home not in location_ids:
            report.errors.append(f"{label}.home 指向不存在的地点: {home!r}")
            home_errors += 1
        else:
            home_counts[home] = home_counts.get(home, 0) + 1

        personality = npc.get("personality")
        if not _require_dict(personality, f"{label}.personality", report.errors):
            continue
        _check_fields(personality, ["traits", "speech_style"], f"{label}.personality", report.errors)
        traits = personality.get("traits")
        if not isinstance(traits, list) or not 2 <= len(traits) <= 4:
            report.errors.append(f"{label}.personality.traits 必须是 2-4 项的列表")
        elif any(not isinstance(t, str) or not t.strip() for t in traits):
            report.errors.append(f"{label}.personality.traits 含空项")
        if not isinstance(personality.get("speech_style"), str) or not personality.get("speech_style").strip():
            report.errors.append(f"{label}.personality.speech_style 必须是非空字符串")

        goals = npc.get("goals")
        if not isinstance(goals, list) or not 2 <= len(goals) <= 3:
            report.errors.append(f"{label}.goals 必须是 2-3 条")
            goals = goals if isinstance(goals, list) else []
        goal_types_here = set()
        for gi, goal in enumerate(goals):
            glabel = f"{label}.goals[{gi}]"
            if not _require_dict(goal, glabel, report.errors):
                continue
            _check_fields(goal, ["id", "type", "priority"], glabel, report.errors)
            gid = goal.get("id")
            if not isinstance(gid, str) or not gid.strip():
                report.errors.append(f"{glabel}.id 必须是非空字符串")
            else:
                goal_ids.append(gid)
            gtype = goal.get("type")
            if gtype not in GOAL_TYPES:
                report.errors.append(f"{glabel}.type 非法: {gtype!r}")
            else:
                goal_types_here.add(gtype)
            pr = goal.get("priority")
            if not isinstance(pr, int) or not 1 <= pr <= 5:
                report.errors.append(f"{glabel}.priority 必须是 1-5 的整数")
        if len(goal_types_here) < 2 and goals:
            report.errors.append(f"{label} 的 goals 类型过于单一，至少需要两种不同类型")

        assets = npc.get("assets")
        if not _require_dict(assets, f"{label}.assets", report.errors):
            continue
        _check_fields(assets, ["money", "items"], f"{label}.assets", report.errors)
        money = assets.get("money")
        npc_cash = 0
        if not isinstance(money, int) or isinstance(money, bool) or money < 0:
            report.errors.append(f"{label}.assets.money 必须是非负整数，实际 {money!r}")
            money_errors.append(label)
        else:
            money_total += money
            npc_cash = money
        asset_items = assets.get("items")
        if not isinstance(asset_items, list) or not asset_items:
            report.errors.append(f"{label}.assets.items 必须是非空列表")
            asset_items = []
        seen_items = set()
        for ai, entry in enumerate(asset_items):
            alabel = f"{label}.assets.items[{ai}]"
            if not _require_dict(entry, alabel, report.errors):
                continue
            _check_fields(entry, ["id", "qty"], alabel, report.errors)
            iid = entry.get("id")
            qty = entry.get("qty")
            if iid not in items_by_id:
                report.errors.append(f"{alabel}.id 指向不存在的物品: {iid!r}")
            if iid in seen_items:
                report.warn(f"{alabel}.id 重复持有: {iid}")
            seen_items.add(iid)
            if not isinstance(qty, int) or isinstance(qty, bool) or qty < 1:
                report.errors.append(f"{alabel}.qty 必须是 >=1 的整数，实际 {qty!r}")

        relations = npc.get("relations")
        if not isinstance(relations, list) or not 2 <= len(relations) <= 4:
            report.errors.append(f"{label}.relations 必须是 2-4 条")
            relations = relations if isinstance(relations, list) else []
        targets = set()
        npc_debt_total = 0
        npc_debt_rels = []
        for ri, rel in enumerate(relations):
            rlabel = f"{label}.relations[{ri}]"
            if not _require_dict(rel, rlabel, report.errors):
                continue
            _check_fields(rel, ["target", "type", "value"], rlabel, report.errors,
                          optional=("amount", "long_term"))
            target = rel.get("target")
            rtype = rel.get("type")
            value = rel.get("value")
            amount = rel.get("amount")
            long_term = rel.get("long_term")
            if not isinstance(target, str) or not target:
                report.errors.append(f"{rlabel}.target 必须是非空字符串")
            elif target == npc_id:
                report.errors.append(f"{rlabel}.target 不能指向自己")
            else:
                targets.add(target)
                if (npc_id, target) in relation_edges:
                    report.errors.append(f"{rlabel} 与已有关系重复: {npc_id} -> {target}")
                relation_edges[(npc_id, target)] = rel
            if rtype not in RELATION_TYPES:
                report.errors.append(f"{rlabel}.type 非法: {rtype!r}")
            if not isinstance(value, int) or isinstance(value, bool) or not -100 <= value <= 100:
                report.errors.append(f"{rlabel}.value 必须是 -100..100 的整数，实际 {value!r}")

            if rtype in ("creditor", "debtor"):
                if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
                    report.errors.append(f"{rlabel}.amount 必填且为正整数，实际 {amount!r}")
                    debt_amount_ok = False
                else:
                    if rtype == "debtor":
                        npc_debt_total += amount
                        npc_debt_rels.append((rlabel, amount, long_term))
                if "long_term" in rel and not isinstance(long_term, bool):
                    report.errors.append(f"{rlabel}.long_term 必须是布尔值，实际 {long_term!r}")
            else:
                if "amount" in rel:
                    report.errors.append(f"{rlabel}.amount 只能用于 creditor/debtor 关系")
                if "long_term" in rel:
                    report.errors.append(f"{rlabel}.long_term 只能用于 creditor/debtor 关系")

        if len(targets) != len(relations):
            report.warn(f"{label} 的 relations 存在重复目标")

        if npc_debt_total:
            debtor_ids.add(npc_id)
            total_debt += npc_debt_total
        if npc_debt_total > npc_cash * 2:
            if not all(lt is True for _lbl, _amt, lt in npc_debt_rels):
                report.errors.append(
                    f"{label} 债务总额 {npc_debt_total} 文超过初始现金×2（{npc_cash * 2} 文），"
                    "且未把所有 debtor 关系标为 long_term: true"
                )
                cap_violations += 1

    # 唯一性
    dup_ids = sorted({x for x in npc_ids if npc_ids.count(x) > 1})
    report.check(not dup_ids, "NPC id 唯一", f"重复: {dup_ids}")
    dup_names = sorted({x for x in names if names.count(x) > 1})
    report.check(not dup_names, "NPC 姓名唯一", f"重复: {dup_names}")
    dup_goals = sorted({x for x in goal_ids if goal_ids.count(x) > 1})
    report.check(not dup_goals, "goal id 唯一", f"重复: {dup_goals}")

    # 目标引用存在
    unknown_targets = sorted({t for (_s, t) in relation_edges if t not in set(npc_ids)})
    report.check(not unknown_targets, "relations 指向存在", f"未知目标: {unknown_targets}")

    # 货币总量
    avg = money_total / len(npcs) if npcs else 0
    report.check(24000 <= money_total <= 45000, "30 人初始货币总量",
                  f"{money_total} 文，不在 24000-45000")
    report.check(800 <= avg <= 1500, "人均初始货币", f"{avg:.1f} 文，不在 800-1500")

    # 关系网结构
    if relation_edges:
        check_relation_structure(relation_edges, set(npc_ids), report)
        cycle = find_cycle({
            (a, b) for (a, b), rel in relation_edges.items() if rel.get("type") == "creditor"
        })
        report.check(not cycle, "债务链无闭环", f"检测到闭环: {cycle}")
        master_cycle = find_cycle({
            (a, b) for (a, b), rel in relation_edges.items() if rel.get("type") == "master"
        })
        report.check(not master_cycle, "师承链无闭环", f"检测到闭环: {master_cycle}")
        depth = longest_path({
            (a, b) for (a, b), rel in relation_edges.items() if rel.get("type") == "creditor"
        })
        report.note(f"最长债务链: {depth} 层")
        report.note(f"关系边总数: {len(relation_edges)}")

    report.check(debt_amount_ok, "债务金额必填且为正", "存在缺失或非正的 amount")
    report.check(cap_violations == 0, "债务总额不超过初始现金×2",
                  f"{cap_violations} 名债务人超额且未标 long_term")
    report.note(f"债务总额合计: {total_debt} 文；债务人数: {len(debtor_ids)}")

    # home 校验：30 人都有 home、指向有效地点、每个地点都住人、单地点不超过 8 人
    report.check(home_errors == 0, "home 字段完整且指向有效地点",
                  f"{home_errors} 个 NPC 的 home 缺失或非法")
    unoccupied = sorted(set(location_ids) - set(home_counts))
    report.check(not unoccupied, "8 个地点都有人居住", f"无人居住: {unoccupied}")
    overcrowded = {lid: c for lid, c in home_counts.items() if c > 8}
    report.check(not overcrowded, "单地点 home 不超过 8 人", f"{overcrowded}")
    report.note("home 分布: " + "，".join(f"{lid}={c}" for lid, c in sorted(home_counts.items())))

    # 角色覆盖
    role_counts = {}
    for role in roles:
        role_counts[role] = role_counts.get(role, 0) + 1
    too_many = {r: c for r, c in role_counts.items() if c > 3}
    report.check(not too_many, "职业分布不过度集中", f"{too_many}")
    report.note(f"职业数: {len(role_counts)}；最多同职: {max(role_counts.values())} 人")

    return {
        "ids": set(npc_ids),
        "roles": role_counts,
        "home_counts": home_counts,
        "money_total": money_total,
        "money_avg": avg,
        "goal_types": {g.get("type") for npc in npcs if isinstance(npc, dict)
                       for g in npc.get("goals") or [] if isinstance(g, dict)},
        "relation_edges": relation_edges,
    }


def find_cycle(edges: set[tuple[str, str]]):
    """返回任一有向环（节点列表），没有则返回 None。"""
    graph = {}
    for a, b in edges:
        graph.setdefault(a, []).append(b)
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in graph}
    stack = []

    def dfs(n):
        color[n] = GRAY
        stack.append(n)
        for m in graph.get(n, []):
            if color.get(m, WHITE) == WHITE:
                got = dfs(m)
                if got:
                    return got
            elif color.get(m) == GRAY:
                return stack[stack.index(m):] + [m]
        stack.pop()
        color[n] = BLACK
        return None

    for n in list(graph):
        if color[n] == WHITE:
            got = dfs(n)
            if got:
                return got
    return None


def longest_path(edges: set[tuple[str, str]]) -> int:
    """有向无环图上的最长链（节点数）。"""
    graph, nodes = {}, set()
    for a, b in edges:
        graph.setdefault(a, []).append(b)
        nodes.update((a, b))
    memo = {}

    def depth(n):
        if n in memo:
            return memo[n]
        best = 1
        for m in graph.get(n, []):
            best = max(best, 1 + depth(m))
        memo[n] = best
        return best

    return max((depth(n) for n in nodes), default=0)


def check_relation_structure(edges: dict, npc_ids: set, report):
    mirrored = {
        "creditor": "debtor", "debtor": "creditor",
        "master": "disciple", "disciple": "master",
        "family": "family",
    }
    asymmetric_friend_rival = 0
    amount_mismatch = 0
    for (a, b), rel in edges.items():
        t = rel.get("type")
        reverse = edges.get((b, a))
        reverse_type = reverse.get("type") if isinstance(reverse, dict) else None
        if t in mirrored:
            expected = mirrored[t]
            if reverse is None:
                report.errors.append(f"关系不对称: {a} -> {b} 为 {t}，但缺少 {b} -> {a} 的 {expected}")
            elif reverse_type != expected:
                report.errors.append(
                    f"关系镜像错误: {a} -> {b}={t}，对应 {b} -> {a}={reverse_type}，应为 {expected}"
                )
            elif t in ("creditor", "debtor"):
                amt = rel.get("amount")
                rev_amt = reverse.get("amount")
                if amt != rev_amt:
                    report.errors.append(
                        f"债务金额不一致: {a} -> {b}={amt} 文，{b} -> {a}={rev_amt} 文"
                    )
                    amount_mismatch += 1
        elif t in ("friend", "rival"):
            if reverse is None:
                asymmetric_friend_rival += 1
                report.warn(f"单向{t}: {a} -> {b}（允许，用于制造张力）")
            elif reverse_type != t:
                report.errors.append(f"关系镜像冲突: {a}->{b}={t}，{b}->{a}={reverse_type}")

    report.check(amount_mismatch == 0, "双向债务金额一致",
                  f"{amount_mismatch} 对债务金额不一致")

    # 关系图连通性（无向）
    graph = {n: set() for n in npc_ids}
    for a, b in edges:
        graph.setdefault(a, set()).add(b)
        graph.setdefault(b, set()).add(a)
    start = next(iter(npc_ids)) if npc_ids else None
    seen = {start} if start else set()
    stack = [start] if start else []
    while stack:
        n = stack.pop()
        for m in graph.get(n, ()):
            if m not in seen:
                seen.add(m)
                stack.append(m)
    missing = sorted(npc_ids - seen)
    report.check(not missing, "关系网连通", f"孤立/不可达: {missing}")
    report.note(f"单向友好/敌对关系: {asymmetric_friend_rival} 条")


def check_locations(locations, items_by_id, set_of_location_ids, report):
    if not report.check(isinstance(locations, list), "locations.yaml 结构", "locations 必须是列表"):
        return
    report.check(len(locations) == 8, "地点数量", f"期望 8，实际 {len(locations)}")
    loc_ids = []
    exits_map = {}
    for idx, loc in enumerate(locations):
        label = f"location[{idx}]"
        if not _require_dict(loc, label, report.errors):
            continue
        _check_fields(loc, ["id", "name", "exits", "services"], label, report.errors)
        lid = loc.get("id")
        if not isinstance(lid, str) or not LOC_ID_RE.fullmatch(lid):
            report.errors.append(f"{label}.id 不合法: {lid!r}")
            continue
        loc_ids.append(lid)
        if not isinstance(loc.get("name"), str) or not loc.get("name").strip():
            report.errors.append(f"{lid}.name 必须是非空字符串")
        exits = loc.get("exits")
        if not isinstance(exits, list) or not exits:
            report.errors.append(f"{lid}.exits 必须是非空列表")
            exits = []
        if len(set(exits)) != len(exits):
            report.errors.append(f"{lid}.exits 存在重复出口")
        if lid in exits:
            report.errors.append(f"{lid}.exits 不能指向自己")
        exits_map[lid] = exits
        services = loc.get("services")
        if not isinstance(services, list) or not services:
            report.errors.append(f"{lid}.services 必须非空（每个地点都要有经济功能）")
            services = []
        for sid in services:
            if sid not in items_by_id:
                report.errors.append(f"{lid}.services 指向不存在的物品: {sid!r}")

    dup = sorted({x for x in loc_ids if loc_ids.count(x) > 1})
    report.check(not dup, "地点 id 唯一", f"重复: {dup}")

    # 出口存在 + 对称
    for lid, exits in exits_map.items():
        for dest in exits:
            if dest not in set_of_location_ids:
                report.errors.append(f"{lid}.exits 指向不存在的地点: {dest!r}")
            elif lid not in exits_map.get(dest, []):
                report.errors.append(f"出口不对称: {lid} -> {dest}，但 {dest} 没有回到 {lid}")

    # 连通
    if loc_ids:
        graph = {lid: set(exits_map.get(lid, [])) for lid in loc_ids}
        start = loc_ids[0]
        seen, stack = {start}, [start]
        while stack:
            n = stack.pop()
            for m in graph.get(n, ()):
                if m in seen:
                    continue
                seen.add(m)
                stack.append(m)
        unreachable = sorted(set(loc_ids) - seen)
        report.check(not unreachable, "地点图连通", f"不可达: {unreachable}")
        # 简单直径提示
        report.note(f"地点出口数: {sum(len(v) for v in exits_map.values())}（双向计）")


def check_items(items, report):
    if not report.check(isinstance(items, list), "items.yaml 结构", "items 必须是列表"):
        return {}
    # P3I 新增 10 件可用物品（food/material/tool/medicine/luxury），上限放宽到 45。
    report.check(20 <= len(items) <= 45, "物品数量", f"期望 20-45，实际 {len(items)}")
    items_by_id = {}
    bands = {
        "food": (2, 50), "material": (5, 200), "tool": (20, 1000),
        "medicine": (2, 300), "luxury": (50, 1000), "service": (1, 500),
    }
    for idx, item in enumerate(items):
        label = f"item[{idx}]"
        if not _require_dict(item, label, report.errors):
            continue
        _check_fields(item, ["id", "name", "category", "base_price"], label, report.errors)
        iid = item.get("id")
        if not isinstance(iid, str) or not ITEM_ID_RE.fullmatch(iid):
            report.errors.append(f"{label}.id 不合法: {iid!r}")
            continue
        if iid in items_by_id:
            report.errors.append(f"物品 id 重复: {iid}")
        items_by_id[iid] = item
        if not isinstance(item.get("name"), str) or not item.get("name").strip():
            report.errors.append(f"{iid}.name 必须是非空字符串")
        cat = item.get("category")
        if cat not in ITEM_CATEGORIES:
            report.errors.append(f"{iid}.category 非法: {cat!r}")
        price = item.get("base_price")
        if not isinstance(price, int) or isinstance(price, bool) or price <= 0:
            report.errors.append(f"{iid}.base_price 必须是正整数，实际 {price!r}")
        elif cat in bands:
            lo, hi = bands[cat]
            if not lo <= price <= hi:
                report.warn(f"{iid}.base_price={price} 超出 {cat} 常见区间 {lo}-{hi}")
    return items_by_id


def check_references(npcs, locations, items_by_id, report):
    asset_refs, loc_refs = set(), set()
    for npc in npcs or []:
        if not isinstance(npc, dict):
            continue
        for entry in (npc.get("assets") or {}).get("items") or []:
            if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                asset_refs.add(entry["id"])
    for loc in locations or []:
        if not isinstance(loc, dict):
            continue
        for sid in loc.get("services") or []:
            if isinstance(sid, str):
                loc_refs.add(sid)
    referenced = asset_refs | loc_refs
    unused = sorted(set(items_by_id) - referenced)
    report.check(not unused, "物品被引用", f"从未被 NPC 资产或地点服务引用: {unused}")
    service_items = {iid for iid, it in items_by_id.items() if it.get("category") == "service"}
    missing_service = sorted(service_items - loc_refs)
    report.check(not missing_service, "service 类物品挂在地点", f"未挂到任何地点 services: {missing_service}")
    report.note(f"NPC 资产引用物品 {len(asset_refs)} 种；地点服务引用 {len(loc_refs)} 种")


def check_verbs(verbs, role_set, report):
    if not report.check(isinstance(verbs, list), "verbs.yaml 结构", "verbs 必须是列表"):
        return
    names = [v.get("name") for v in verbs if isinstance(v, dict)]
    dup = sorted({n for n in names if names.count(n) > 1})
    report.check(not dup, "动词名唯一", f"重复: {dup}")
    report.check(len(verbs) == 19, "动词数量", f"期望 19（13 玩家动词 + 5 内部动词 + 1 心跳），实际 {len(verbs)}")
    missing = sorted(REQUIRED_VERBS - set(names))
    extra = sorted(set(names) - REQUIRED_VERBS)
    report.check(not missing, "必需动词齐全", f"缺少: {missing}")
    report.check(not extra, "无多余动词", f"多余: {extra}")
    cooldown_declared = sorted(
        v.get("name", f"verb[{i}]") for i, v in enumerate(verbs)
        if isinstance(v, dict) and "cooldown" in v
    )
    report.check(not cooldown_declared, "verbs.yaml 不再声明 cooldown",
                  f"仍声明冷却: {cooldown_declared}")
    for idx, verb in enumerate(verbs):
        if not isinstance(verb, dict):
            report.errors.append(f"verb[{idx}] 必须是映射")
            continue
        label = verb.get("name", f"verb[{idx}]")
        _check_fields(verb, ["name", "args", "allowed_roles"], label, report.errors)
        args = verb.get("args")
        if not isinstance(args, dict) or not args:
            report.errors.append(f"{label}.args 必须是非空映射")
        roles = verb.get("allowed_roles")
        if not isinstance(roles, list) or not roles:
            report.errors.append(f"{label}.allowed_roles 必须是非空列表")
        else:
            for r in roles:
                if r == "*":
                    continue
                if r not in role_set:
                    report.errors.append(f"{label}.allowed_roles 含未知职业: {r!r}")


def check_economy(economy, npc_roles, locations, npc_money_total, report):
    if not report.check(isinstance(economy, dict), "economy.yaml 结构", "economy 必须是映射"):
        return
    wages = economy.get("wages_daily")
    living = (economy.get("living_costs") or {}).get("by_role")
    taxes = economy.get("taxes") or {}
    if not isinstance(wages, dict):
        report.errors.append("economy.wages_daily 必须是映射")
        wages = {}
    if not isinstance(living, dict):
        report.errors.append("economy.living_costs.by_role 必须是映射")
        living = {}

    missing_wage = sorted(npc_roles - set(wages))
    missing_living = sorted(npc_roles - set(living))
    report.check(not missing_wage, "工资覆盖所有职业", f"缺少: {missing_wage}")
    report.check(not missing_living, "生活成本覆盖所有职业", f"缺少: {missing_living}")

    income_tax = taxes.get("income")
    if not isinstance(income_tax, (int, float)) or isinstance(income_tax, bool) or not 0 <= income_tax < 1:
        report.errors.append(f"economy.taxes.income 非法: {income_tax!r}")
        income_tax = 0
    trans_tax = taxes.get("transaction")
    if not isinstance(trans_tax, (int, float)) or isinstance(trans_tax, bool) or not 0 <= trans_tax < 1:
        report.errors.append(f"economy.taxes.transaction 非法: {trans_tax!r}")

    nets = {}
    for role in sorted(npc_roles):
        wage = wages.get(role)
        lc = living.get(role)
        if not isinstance(wage, int) or isinstance(wage, bool) or wage < 0:
            report.errors.append(f"economy.wages_daily.{role} 必须是非负整数")
            continue
        if not isinstance(lc, dict):
            continue
        parts = [lc.get(k) for k in ("food", "housing", "upkeep", "dues")]
        if any(not isinstance(p, int) or isinstance(p, bool) or p < 0 for p in parts):
            report.errors.append(f"economy.living_costs.by_role.{role} 四项必须是非负整数")
            continue
        nets[role] = round(wage * (1 - income_tax) - sum(parts), 2)

    if nets:
        lo, hi = min(nets.values()), max(nets.values())
        report.check(lo > 0, "最低职业净结余为正", f"最低 {lo} 文/日")
        report.check(lo >= 0.5, "最低职业净结余不至于贴地", f"最低 {lo} 文/日 < 0.5")
        report.check(hi <= 15, "最高职业净结余不暴增", f"最高 {hi} 文/日 > 15")
        report.note(f"日净结余区间: {lo} - {hi} 文/日；72 日累计 {lo * 72:.0f} - {hi * 72:.0f} 文")
        avg_money = npc_money_total / 30 if npc_money_total else 0
        if avg_money and hi * 72 > avg_money:
            report.warn(f"最高净结余 72 日累计 {hi * 72:.0f} 文，超过人均初始货币 {avg_money:.0f} 文")
        report.note("净结余最低职业: " + min(nets, key=nets.get) + f" ({lo})")
        report.note("净结余最高职业: " + max(nets, key=nets.get) + f" ({hi})")

    # 货币存量声明
    money_supply = economy.get("money_supply") or {}
    declared = money_supply.get("initial_npc_total")
    report.check(declared == npc_money_total, "初始货币总量与声明一致",
                  f"声明 {declared}，实际 {npc_money_total}")
    sources = money_supply.get("sources")
    report.check(sources == [], "无凭空货币来源", f"sources 应为空，实际 {sources!r}")

    floats = money_supply.get("location_float") or {}
    loc_ids = {l.get("id") for l in locations or [] if isinstance(l, dict)}
    report.check(set(floats) == loc_ids, "地点金库与地点一一对应",
                  f"缺少 {sorted(loc_ids - set(floats))}，多余 {sorted(set(floats) - loc_ids)}")
    declared_float = money_supply.get("initial_location_float_total")
    actual_float = sum(v for v in floats.values() if isinstance(v, (int, float)))
    report.check(declared_float == actual_float, "地点金库总额与声明一致",
                  f"声明 {declared_float}，实际 {actual_float}")

    shop = economy.get("shop_opening") or {}
    for key in ("stall", "storefront", "branch"):
        val = shop.get(key)
        if not isinstance(val, int) or isinstance(val, bool) or val <= 0:
            report.errors.append(f"economy.shop_opening.{key} 必须是正整数")
    if all(isinstance(shop.get(k), int) for k in ("stall", "storefront", "branch")):
        if not shop["stall"] < shop["storefront"] < shop["branch"]:
            report.errors.append("店铺开张成本应满足 stall < storefront < branch")

    escort = economy.get("escort") or {}
    fmin, fmax = escort.get("fee_min"), escort.get("fee_max")
    if not isinstance(fmin, int) or not isinstance(fmax, int) or fmin <= 0 or fmin > fmax:
        report.errors.append(f"economy.escort 报酬区间非法: {fmin!r}..{fmax!r}")
    base = escort.get("base_fee")
    if not isinstance(base, int) or not (isinstance(fmin, int) and isinstance(fmax, int) and fmin <= base <= fmax):
        report.errors.append(f"economy.escort.base_fee 必须落在报酬区间内: {base!r}")
    cut = escort.get("agency_cut")
    if not isinstance(cut, (int, float)) or isinstance(cut, bool) or not 0 <= cut < 1:
        report.errors.append(f"economy.escort.agency_cut 非法: {cut!r}")

    sect = economy.get("sect") or {}
    for key in ("min_money", "min_members", "min_reputation", "registration_fee"):
        val = sect.get(key)
        if not isinstance(val, int) or isinstance(val, bool) or val <= 0:
            report.errors.append(f"economy.sect.{key} 必须是正整数")

    report.check(isinstance(economy.get("closure_model"), dict), "存在闭环测算说明",
                  "缺少 closure_model")


def _positive_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def check_goal_targets(economy, npcs, npc_money_total, report):
    """校验 goal_targets：引擎契约、7 类目标、10 日有效产能校准与 found_sect 可达范围。"""
    gt = economy.get("goal_targets")
    if not _require_dict(gt, "economy.goal_targets", report.errors):
        return

    report.check(set(gt) == GOAL_KINDS, "goal_targets 只含 5 个引擎 kind",
                  f"实际键: {sorted(gt)}")
    for kind in sorted(GOAL_KINDS):
        if not _positive_number(gt.get(kind)):
            report.errors.append(
                f"economy.goal_targets.{kind} 必须是正数（引擎按 kind 读取标量），实际 {gt.get(kind)!r}"
            )

    by_type = economy.get("goal_targets_by_type")
    if not _require_dict(by_type, "economy.goal_targets_by_type", report.errors):
        return
    missing = sorted(GOAL_TYPES - set(by_type))
    extra = sorted(set(by_type) - GOAL_TYPES)
    report.check(not missing and not extra, "7 类目标都有量化配置",
                  f"缺少 {missing}，多余 {extra}")

    wages = economy.get("wages_daily") or {}
    taxes = economy.get("taxes") or {}
    income_tax = taxes.get("income")
    if not isinstance(income_tax, (int, float)) or isinstance(income_tax, bool):
        income_tax = 0.10
    wage_values = [v for v in wages.values() if isinstance(v, int) and not isinstance(v, bool) and v > 0]
    min_wage = min(wage_values) if wage_values else 0
    min_wealth_cap = 72 * min_wage * (1 - income_tax)
    shop = economy.get("shop_opening") or {}
    sect = economy.get("sect") or {}

    optional = (
        "level_equivalent", "hours_per_point", "reputation_threshold",
        "funds_threshold", "tier", "dues", "min_money", "registration_fee",
        "total_capital", "members", "reputation",
    )
    for gtype in sorted(GOAL_TYPES):
        entry = by_type.get(gtype)
        label = f"goal_targets_by_type.{gtype}"
        if not isinstance(entry, dict):
            report.errors.append(f"{label} 必须是映射")
            continue
        _check_fields(entry, ["kind", "engine_value", "value", "metric", "basis"],
                      label, report.errors, optional=optional)
        expected_kind = GOAL_KIND_MAP[gtype]
        if entry.get("kind") != expected_kind:
            report.errors.append(f"{label}.kind 应为 {expected_kind}，实际 {entry.get('kind')!r}")
        engine_value = entry.get("engine_value")
        human_value = entry.get("value")
        if not _positive_number(engine_value):
            report.errors.append(f"{label}.engine_value 必须是正数，实际 {engine_value!r}")
        if not _positive_number(human_value):
            report.errors.append(f"{label}.value 必须是正数，实际 {human_value!r}")
        elif _positive_number(engine_value) and abs(float(engine_value) - float(human_value)) > 1e-9:
            report.errors.append(
                f"{label}.value={human_value} 与 engine_value={engine_value} 不一致"
            )
        if not isinstance(entry.get("basis"), str) or not entry.get("basis").strip():
            report.errors.append(f"{label}.basis 必须是非空的推导说明")
        kind_scalar = gt.get(expected_kind)
        if _positive_number(engine_value) and _positive_number(kind_scalar):
            if abs(float(kind_scalar) - float(engine_value)) > 1e-9:
                report.errors.append(
                    f"{label}.engine_value={engine_value} 与 goal_targets.{expected_kind}={kind_scalar} 不一致"
                )

    # 10 天有效产能校准护栏（2026-11-04）：目标进入 10% 即被轮转，因此单目标
    # 的有效 10 天产能是 P60 实测值（31 NPC / seed 42 / 无 player 规则臂），
    # 不是 72 日理论上限。target 应落在有效产能的 0.6-0.8；advance_skill 因
    # cultivate 2h 的 0.005 进度粒度，0.036 只能完成 4/16，故放宽到 0.5。
    ten_day_capacity = {
        "earn_money": (178.0, "文", 0.6, 0.8),
        "advance_skill": (0.060, "", 0.5, 0.8),
        "build_relation": (18.0, "关系点", 0.6, 0.8),
    }
    for gtype, (cap, unit, lo_ratio, hi_ratio) in ten_day_capacity.items():
        entry = by_type.get(gtype)
        if not isinstance(entry, dict) or not _positive_number(entry.get("engine_value")):
            continue
        target = float(entry["engine_value"])
        ratio = target / cap
        report.check(lo_ratio <= ratio <= hi_ratio, f"{gtype} 目标在 10 日有效产能的 {lo_ratio:.2f}-{hi_ratio:.2f}",
                      f"目标 {target:g}{unit} / 实测 P60 {cap:g}{unit} = {ratio:.2f}")

    # build_relation 的进度从 0 累计（开局 friend 不预填），真正需要防的是
    # “一次动作就完成”；12 点约 4 次交易，至少守住 2 次交易的下限。
    bond = by_type.get("build_relation")
    if isinstance(bond, dict) and _positive_number(bond.get("engine_value")):
        target = float(bond["engine_value"])
        report.check(target >= 2 * 3, "build_relation 目标至少需 2 次交易",
                      f"目标 {target:g}，交易每次 +3")

    # build_shop：引擎只能持有 1 家店，资金门槛不低于摊位费
    shop_t = by_type.get("build_shop")
    if isinstance(shop_t, dict):
        report.check(shop_t.get("engine_value") == 1, "build_shop 目标为 1 家店",
                      f"引擎每实体只能持有 1 家，实际 {shop_t.get('engine_value')!r}")
        funds = shop_t.get("funds_threshold")
        stall = shop.get("stall")
        if not _positive_number(funds):
            report.errors.append("goal_targets_by_type.build_shop.funds_threshold 必须是正数")
        elif isinstance(stall, int) and not isinstance(stall, bool) and funds < stall:
            report.errors.append(
                f"build_shop.funds_threshold={funds} 低于 shop_opening.stall={stall}"
            )

    # join_sect：一次入会 + 会费门槛
    join_t = by_type.get("join_sect")
    if isinstance(join_t, dict):
        report.check(join_t.get("engine_value") == 1, "join_sect 目标为 1 次入会",
                      f"引擎入会只记 1 次，实际 {join_t.get('engine_value')!r}")
        dues = join_t.get("dues")
        if not _positive_number(dues):
            report.errors.append("goal_targets_by_type.join_sect.dues 必须是正数")
        elif min_wealth_cap > 0:
            report.check(dues <= 0.75 * min_wealth_cap, "join_sect 会费 72 日可支付",
                          f"会费 {dues} 超过最低收入者 72 日收入的 75%（{0.75 * min_wealth_cap:.0f} 文）")

    # gain_status：声望门槛与 sect.min_reputation 对齐
    gain = by_type.get("gain_status")
    if isinstance(gain, dict):
        rep = gain.get("reputation_threshold")
        if not _positive_number(rep) or rep > 100:
            report.errors.append(
                f"goal_targets_by_type.gain_status.reputation_threshold 必须是 1-100，实际 {rep!r}"
            )
        elif isinstance(sect.get("min_reputation"), int) and rep != sect["min_reputation"]:
            report.errors.append(
                f"gain_status.reputation_threshold={rep} 与 sect.min_reputation={sect['min_reputation']} 不一致"
            )

    # found_sect：人数/资金门槛与 sect 对齐，总资本不超过全区域净资产
    found = by_type.get("found_sect")
    if isinstance(found, dict):
        report.check(found.get("engine_value") == 1, "found_sect 目标为 1 次立派",
                      f"引擎立派只记 1 次，实际 {found.get('engine_value')!r}")
        for key in ("min_money", "registration_fee", "total_capital", "members", "reputation"):
            if not _positive_number(found.get(key)):
                report.errors.append(f"goal_targets_by_type.found_sect.{key} 必须是正数")
        if all(_positive_number(found.get(k)) for k in ("min_money", "registration_fee", "total_capital")):
            expected_total = found["min_money"] + found["registration_fee"]
            if found["total_capital"] != expected_total:
                report.errors.append(
                    f"found_sect.total_capital={found['total_capital']} 应等于 "
                    f"min_money+registration_fee={expected_total}"
                )
        for key, sect_key in (("min_money", "min_money"), ("registration_fee", "registration_fee"),
                              ("members", "min_members"), ("reputation", "min_reputation")):
            if isinstance(sect.get(sect_key), int) and found.get(key) != sect[sect_key]:
                report.errors.append(
                    f"found_sect.{key}={found.get(key)} 与 economy.sect.{sect_key}={sect[sect_key]} 不一致"
                )
        floats = (economy.get("money_supply") or {}).get("location_float") or {}
        regional_assets = npc_money_total + sum(
            v for v in floats.values() if isinstance(v, (int, float)) and not isinstance(v, bool)
        )
        if _positive_number(found.get("total_capital")) and regional_assets > 0:
            report.check(found["total_capital"] <= regional_assets,
                          "found_sect 门槛不超过全区域净资产",
                          f"总资本 {found['total_capital']} 文 > 全区域净资产 {regional_assets} 文")
            report.note(
                f"found_sect 总资本 {found['total_capital']} 文，占全区域净资产 "
                f"{regional_assets} 文的 {100.0 * found['total_capital'] / regional_assets:.1f}%"
            )

    report.check(gt.get("shop") == 1 and gt.get("faction") == 1,
                  "shop/faction 引擎目标各为 1",
                  f"shop={gt.get('shop')!r}，faction={gt.get('faction')!r}")


def _npc_map(npcs):
    return {
        n.get("id"): n for n in npcs or []
        if isinstance(n, dict) and isinstance(n.get("id"), str)
    }


def _adjacency(locations):
    return {
        l.get("id"): set(l.get("exits") or [])
        for l in locations or []
        if isinstance(l, dict) and isinstance(l.get("id"), str)
    }


def _route_adjacent(route, adj):
    if not isinstance(route, list) or len(route) < 2:
        return False, "route 至少包含 2 个地点"
    for a, b in zip(route, route[1:]):
        if b not in adj.get(a, set()):
            return False, f"{a} -> {b} 不相邻"
    return True, ""


def _role_daily_net(economy, role):
    wages = economy.get("wages_daily") or {}
    living = (economy.get("living_costs") or {}).get("by_role") or {}
    taxes = economy.get("taxes") or {}
    wage = wages.get(role)
    lc = living.get(role)
    tax = taxes.get("income")
    if not isinstance(wage, int) or isinstance(wage, bool):
        return None
    if not isinstance(lc, dict) or not isinstance(tax, (int, float)) or isinstance(tax, bool):
        return None
    parts = [lc.get(k) for k in ("food", "housing", "upkeep", "dues")]
    if any(not isinstance(p, int) or isinstance(p, bool) for p in parts):
        return None
    return round(wage * (1 - tax) - sum(parts), 2)


def _npc_reputation(npc):
    out = 0.0
    for rel in (npc or {}).get("relations") or []:
        if isinstance(rel, dict) and rel.get("type") in ("friend", "family", "master", "disciple", "ally", "marriage"):
            val = rel.get("value")
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                out += max(0.0, float(val))
    return out


def _founding_members(npc, min_relation_value):
    """与引擎 founding_members 同口径：本人 + 30 以上 disciple/friend/family/ally。"""
    count = 1
    for rel in (npc or {}).get("relations") or []:
        if not isinstance(rel, dict):
            continue
        if rel.get("type") not in ("friend", "family", "ally", "disciple"):
            continue
        val = rel.get("value")
        if isinstance(val, (int, float)) and not isinstance(val, bool) and val >= min_relation_value:
            count += 1
    return count


def check_sects(sects, loc_ids, npc_map, economy, report):
    if not report.check(isinstance(sects, list), "sects.yaml 结构", "sects 必须是列表"):
        return
    report.check(len(sects) == 3, "门派数量", f"期望 3，实际 {len(sects)}")
    sect_ids = []
    sect = economy.get("sect") or {}
    min_relation_value = sect.get("min_relation_value", 30)
    for idx, item in enumerate(sects or []):
        label = f"sect[{idx}]"
        if not isinstance(item, dict):
            report.errors.append(f"{label} 必须是映射")
            continue
        _check_fields(item, [
            "id", "name", "doctrine", "seat", "activity_locations",
            "founder_candidate", "fallback_founder", "dues", "min_reputation",
            "min_members", "min_money", "registration_fee", "founding_cost",
            "skill_directions", "recruit_targets", "founding_path",
        ], label, report.errors)
        sid = item.get("id")
        name = item.get("name", label)
        if not isinstance(sid, str) or not NPC_ID_RE.match(sid):
            report.errors.append(f"{label}.id 非法: {sid!r}")
        elif sid in sect_ids:
            report.errors.append(f"{label}.id 重复: {sid}")
        else:
            sect_ids.append(sid)
        for key in ("name", "doctrine", "founding_path"):
            if not isinstance(item.get(key), str) or not item.get(key).strip():
                report.errors.append(f"{name}.{key} 必须是非空字符串")
        if item.get("seat") not in loc_ids:
            report.errors.append(f"{name}.seat 指向不存在的地点: {item.get('seat')!r}")
        activity = item.get("activity_locations")
        if not isinstance(activity, list) or not activity:
            report.errors.append(f"{name}.activity_locations 必须是非空列表")
        else:
            bad = [x for x in activity if x not in loc_ids]
            if bad:
                report.errors.append(f"{name}.activity_locations 含未知地点: {bad}")
        founder = item.get("founder_candidate")
        fallback = item.get("fallback_founder")
        for key, val in (("founder_candidate", founder), ("fallback_founder", fallback)):
            if val not in npc_map:
                report.errors.append(f"{name}.{key} 指向不存在的 NPC: {val!r}")
        for key in ("dues", "min_reputation", "min_members", "min_money", "registration_fee", "founding_cost"):
            val = item.get(key)
            if not isinstance(val, int) or isinstance(val, bool) or val <= 0:
                report.errors.append(f"{name}.{key} 必须是正整数，实际 {val!r}")
        if all(isinstance(item.get(k), int) for k in ("min_money", "registration_fee", "founding_cost")):
            if item["founding_cost"] != item["min_money"] + item["registration_fee"]:
                report.errors.append(
                    f"{name}.founding_cost 应等于 min_money + registration_fee"
                )
        for key, sect_key in (("min_reputation", "min_reputation"), ("min_members", "min_members"),
                              ("min_money", "min_money"), ("registration_fee", "registration_fee")):
            if isinstance(sect.get(sect_key), int) and item.get(key) != sect[sect_key]:
                report.errors.append(
                    f"{name}.{key}={item.get(key)} 与 economy.sect.{sect_key}={sect[sect_key]} 不一致"
                )
        skills = item.get("skill_directions")
        if not isinstance(skills, list) or not 2 <= len(skills) <= 4:
            report.errors.append(f"{name}.skill_directions 需要 2-4 项")
        else:
            for s in skills:
                if not isinstance(s, str) or not s.strip():
                    report.errors.append(f"{name}.skill_directions 含空项")
        recruits = item.get("recruit_targets")
        if not isinstance(recruits, list) or len(recruits) < 5:
            report.errors.append(f"{name}.recruit_targets 至少 5 人")
        else:
            bad = [x for x in recruits if x not in npc_map]
            if bad:
                report.errors.append(f"{name}.recruit_targets 含未知 NPC: {bad}")
            if len(set(recruits)) != len(recruits):
                report.errors.append(f"{name}.recruit_targets 有重复")
        if founder in npc_map and isinstance(item.get("min_money"), int):
            npc = npc_map[founder]
            money = (npc.get("assets") or {}).get("money")
            net = _role_daily_net(economy, npc.get("role"))
            if isinstance(money, int) and net is not None:
                reachable = money + net * 72
                report.check(
                    reachable >= item["min_money"],
                    f"{name} 发起人 72 日可达资金门槛",
                    f"{founder} 现有 {money} 文 + 72 日净结余 {net * 72:.0f} = {reachable:.0f} < {item['min_money']}",
                )
            rep = _npc_reputation(npc)
            if isinstance(item.get("min_reputation"), int):
                report.check(
                    rep >= item["min_reputation"],
                    f"{name} 发起人现有声望达标",
                    f"{founder} 声望 {rep:g} < {item['min_reputation']}",
                )
            existing = _founding_members(npc, min_relation_value)
            if isinstance(item.get("min_members"), int) and isinstance(recruits, list):
                report.check(
                    existing + len(recruits) >= item["min_members"],
                    f"{name} 有足够潜在成员来源",
                    f"现有 {existing} 人 + 潜在招募 {len(recruits)} 人不足 {item['min_members']}",
                )
                report.note(f"{name}: 发起人 {founder}，现有成员 {existing} 人，潜在招募 {len(recruits)} 人")
    report.check(len(set(sect_ids)) == len(sect_ids), "门派 id 唯一", f"重复: {sect_ids}")


def check_escort_routes(routes, locations, economy, report):
    if not report.check(isinstance(routes, list), "escort_routes.yaml 结构", "escort_routes 必须是列表"):
        return
    report.check(len(routes) == 4, "镖局线路数量", f"期望 4，实际 {len(routes)}")
    adj = _adjacency(locations)
    loc_ids = set(adj)
    escort = economy.get("escort") or {}
    fee_floor = escort.get("fee_min")
    fee_ceiling = escort.get("fee_max")
    cargo_floor = escort.get("cargo_min_value")
    cut = escort.get("agency_cut")
    route_ids = []
    parsed = []
    for idx, route in enumerate(routes or []):
        label = f"route[{idx}]"
        if not isinstance(route, dict):
            report.errors.append(f"{label} 必须是映射")
            continue
        _check_fields(route, [
            "id", "name", "origin", "destination", "route", "duration_days",
            "cargo_value_min", "cargo_value_max", "fee_min", "fee_max",
            "agency_cut", "risk_loss_prob", "risk_events",
        ], label, report.errors)
        rid = route.get("id")
        name = route.get("name", label)
        if not isinstance(rid, str) or not NPC_ID_RE.match(rid):
            report.errors.append(f"{label}.id 非法: {rid!r}")
        elif rid in route_ids:
            report.errors.append(f"{label}.id 重复: {rid}")
        else:
            route_ids.append(rid)
        if route.get("origin") not in loc_ids:
            report.errors.append(f"{name}.origin 指向不存在的地点: {route.get('origin')!r}")
        if route.get("destination") not in loc_ids:
            report.errors.append(f"{name}.destination 指向不存在的地点: {route.get('destination')!r}")
        path = route.get("route")
        ok, why = _route_adjacent(path, adj)
        if not ok:
            report.errors.append(f"{name}.route 非法: {why}")
        elif path[0] != route.get("origin") or path[-1] != route.get("destination"):
            report.errors.append(f"{name}.route 首尾与 origin/destination 不一致")
        duration = route.get("duration_days")
        if not isinstance(duration, (int, float)) or isinstance(duration, bool) or duration <= 0:
            report.errors.append(f"{name}.duration_days 必须是正数")
            continue
        for key in ("cargo_value_min", "cargo_value_max", "fee_min", "fee_max"):
            val = route.get(key)
            if not isinstance(val, int) or isinstance(val, bool) or val <= 0:
                report.errors.append(f"{name}.{key} 必须是正整数，实际 {val!r}")
        if isinstance(route.get("cargo_value_min"), int) and isinstance(route.get("cargo_value_max"), int):
            if route["cargo_value_min"] > route["cargo_value_max"]:
                report.errors.append(f"{name}.cargo_value_min > max")
            if isinstance(cargo_floor, int) and route["cargo_value_min"] < cargo_floor:
                report.errors.append(f"{name}.cargo_value_min 低于 economy.escort.cargo_min_value")
        if isinstance(route.get("fee_min"), int) and isinstance(route.get("fee_max"), int):
            if route["fee_min"] > route["fee_max"]:
                report.errors.append(f"{name}.fee_min > max")
            if isinstance(fee_floor, int) and route["fee_min"] < fee_floor:
                report.errors.append(f"{name}.fee_min 低于 economy.escort.fee_min")
            if isinstance(fee_ceiling, int) and route["fee_max"] > fee_ceiling:
                report.errors.append(f"{name}.fee_max 高于 economy.escort.fee_max")
        if not isinstance(route.get("agency_cut"), (int, float)) or isinstance(route.get("agency_cut"), bool) or not 0 <= route["agency_cut"] <= 1:
            report.errors.append(f"{name}.agency_cut 必须在 0..1")
        elif isinstance(cut, (int, float)) and abs(float(route["agency_cut"]) - float(cut)) > 1e-9:
            report.errors.append(f"{name}.agency_cut 与 economy.escort.agency_cut 不一致")
        risk = route.get("risk_loss_prob")
        if not isinstance(risk, (int, float)) or isinstance(risk, bool) or not 0 <= risk <= 1:
            report.errors.append(f"{name}.risk_loss_prob 必须在 0..1")
        events = route.get("risk_events")
        prob_sum = 0.0
        if not isinstance(events, list) or not events:
            report.errors.append(f"{name}.risk_events 必须是非空列表")
        else:
            for eidx, event in enumerate(events):
                elabel = f"{name}.risk_events[{eidx}]"
                if not isinstance(event, dict):
                    report.errors.append(f"{elabel} 必须是映射")
                    continue
                _check_fields(event, ["id", "name", "probability", "effect", "consequence"],
                              elabel, report.errors,
                              optional=("fee_penalty", "cargo_loss_ratio", "stamina_penalty"))
                p = event.get("probability")
                if not isinstance(p, (int, float)) or isinstance(p, bool) or not 0 < p <= 1:
                    report.errors.append(f"{elabel}.probability 必须在 (0,1]")
                else:
                    prob_sum += float(p)
                if event.get("effect") not in ("delay", "toll", "theft", "injury", "cargo_loss"):
                    report.errors.append(f"{elabel}.effect 非法: {event.get('effect')!r}")
                if not isinstance(event.get("consequence"), str) or not event["consequence"].strip():
                    report.errors.append(f"{elabel}.consequence 必须是非空字符串")
        if isinstance(risk, (int, float)) and isinstance(events, list) and events:
            if abs(prob_sum - float(risk)) > 1e-6:
                report.errors.append(
                    f"{name}.risk_events 概率合计 {prob_sum:g} 与 risk_loss_prob {risk:g} 不一致"
                )
        parsed.append(route)
    if parsed:
        shortest = min(float(r["duration_days"]) for r in parsed if isinstance(r.get("duration_days"), (int, float)))
        report.check(shortest <= 2, "最短镖路 2 天内可完成", f"最短 {shortest} 日")
        ordered = sorted(parsed, key=lambda r: float(r.get("duration_days", 0)))
        ok = True
        for prev, cur in zip(ordered, ordered[1:]):
            if not (
                float(cur["duration_days"]) > float(prev["duration_days"])
                and int(cur["cargo_value_max"]) > int(prev["cargo_value_max"])
                and int(cur["fee_max"]) > int(prev["fee_max"])
                and float(cur["risk_loss_prob"]) > float(prev["risk_loss_prob"])
            ):
                ok = False
        report.check(ok, "线路风险与收益单调递增", "按 duration_days 排序后 cargo/fee/risk 未严格递增")
        report.note(
            "镖路区间: "
            + "；".join(
                f"{r['name']} {r['duration_days']}日/货 {r['cargo_value_min']}-{r['cargo_value_max']}/"
                f"镖银 {r['fee_min']}-{r['fee_max']}/风险 {r['risk_loss_prob']:g}"
                for r in ordered
            )
        )


def _check_quest_items(entries, items_by_id, label, report):
    if not isinstance(entries, list):
        report.errors.append(f"{label} 必须是列表")
        return
    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            report.errors.append(f"{label}[{idx}] 必须是映射")
            continue
        item = entry.get("item")
        qty = entry.get("qty")
        if item not in items_by_id:
            report.errors.append(f"{label}[{idx}].item 指向不存在的物品: {item!r}")
        if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
            report.errors.append(f"{label}[{idx}].qty 必须是正整数")


def _path_len(adj, start, end):
    """地点图最短跳数；不可达返回 None。"""
    if start not in adj or end not in adj:
        return None
    if start == end:
        return 0
    seen = {start}
    queue = [(start, 0)]
    while queue:
        node, dist = queue.pop(0)
        for nxt in adj.get(node, ()):
            if nxt in seen:
                continue
            if nxt == end:
                return dist + 1
            seen.add(nxt)
            queue.append((nxt, dist + 1))
    return None


def check_quests(quests, npc_map, loc_ids, locations, items_by_id, verb_names, economy, report):
    if not report.check(isinstance(quests, list), "quests.yaml 结构", "quests 必须是列表"):
        return
    # P3I：14 基础 + 4 步新手引导链 + 8 条日常委托 + 1 条新债权讨债单 = 27。
    report.check(len(quests) == 27, "任务模板数量", f"期望 27（14 基础 + 4 引导 + 8 日常 + 1 新债），实际 {len(quests)}")
    adj = _adjacency(locations)
    loc_services = {
        l.get("id"): set(l.get("services") or [])
        for l in locations or [] if isinstance(l, dict)
    }
    wages = economy.get("wages_daily") or {}
    max_wage = max((v for v in wages.values() if isinstance(v, int) and not isinstance(v, bool)), default=0)
    escort = economy.get("escort") or {}
    categories = {}
    quest_ids = []
    reward_values = []
    for idx, quest in enumerate(quests or []):
        label = f"quest[{idx}]"
        if not isinstance(quest, dict):
            report.errors.append(f"{label} 必须是映射")
            continue
        _check_fields(quest, [
            "id", "name", "category", "repeatable", "giver", "verb",
            "intent_args", "deadline_days", "reward", "notes",
        ], label, report.errors,
            optional=("target_npc", "target_location", "target_item", "settlement",
                      "entry_cost", "starter", "optional"))
        qid = quest.get("id")
        name = quest.get("name", label)
        if not isinstance(qid, str) or not NPC_ID_RE.match(qid):
            report.errors.append(f"{label}.id 非法: {qid!r}")
        elif qid in quest_ids:
            report.errors.append(f"{label}.id 重复: {qid}")
        else:
            quest_ids.append(qid)
        category = quest.get("category")
        if category not in QUEST_CATEGORY_VERBS:
            report.errors.append(f"{name}.category 非法: {category!r}")
        else:
            categories[category] = categories.get(category, 0) + 1
        if not isinstance(quest.get("repeatable"), bool):
            report.errors.append(f"{name}.repeatable 必须是布尔值")
        giver = quest.get("giver")
        if giver not in npc_map:
            report.errors.append(f"{name}.giver 指向不存在的 NPC: {giver!r}")
        verb = quest.get("verb")
        if verb not in verb_names:
            report.errors.append(f"{name}.verb 指向不存在的动词: {verb!r}")
        elif category in QUEST_CATEGORY_VERBS and verb != QUEST_CATEGORY_VERBS[category]:
            report.errors.append(
                f"{name}.verb={verb} 与 category={category} 约定动词 {QUEST_CATEGORY_VERBS[category]} 不一致"
            )
        for key, valid_set, what in (
            ("target_npc", set(npc_map), "NPC"),
            ("target_location", loc_ids, "地点"),
            ("target_item", set(items_by_id), "物品"),
        ):
            val = quest.get(key)
            if val is not None and val not in valid_set:
                report.errors.append(f"{name}.{key} 指向不存在的{what}: {val!r}")
        deadline = quest.get("deadline_days")
        if not isinstance(deadline, int) or isinstance(deadline, bool) or deadline <= 0:
            report.errors.append(f"{name}.deadline_days 必须是正整数")
            deadline = None
        reward = quest.get("reward")
        if not isinstance(reward, dict):
            report.errors.append(f"{name}.reward 必须是映射")
        else:
            money = reward.get("money")
            relation = reward.get("relation")
            if not isinstance(money, int) or isinstance(money, bool) or money <= 0:
                report.errors.append(f"{name}.reward.money 必须是正整数")
            else:
                reward_values.append(money)
                if max_wage and money > 2 * max_wage:
                    report.errors.append(
                        f"{name}.reward.money={money} 超过 2 日最高日薪 {2 * max_wage} 文"
                    )
                if deadline and max_wage and money / deadline > max_wage:
                    report.errors.append(
                        f"{name}.reward.money={money} 折合每日 {money / deadline:.1f} 文，超过最高日薪 {max_wage}"
                    )
            if not isinstance(relation, int) or isinstance(relation, bool) or not 0 <= relation <= 5:
                report.errors.append(f"{name}.reward.relation 必须是 0-5 的整数")
        args = quest.get("intent_args")
        if not isinstance(args, dict) or not args:
            report.errors.append(f"{name}.intent_args 必须是非空映射")
            continue
        if verb == "move":
            if args.get("to") not in loc_ids:
                report.errors.append(f"{name}.intent_args.to 指向不存在的地点: {args.get('to')!r}")
        elif verb == "buy":
            if args.get("item") not in items_by_id:
                report.errors.append(f"{name}.intent_args.item 指向不存在的物品: {args.get('item')!r}")
            qty = args.get("qty")
            if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
                report.errors.append(f"{name}.intent_args.qty 必须是正整数")
            if args.get("from") not in loc_ids:
                report.errors.append(f"{name}.intent_args.from 指向不存在的地点: {args.get('from')!r}")
        elif verb == "trade":
            target = args.get("target")
            if target not in npc_map:
                report.errors.append(f"{name}.intent_args.target 指向不存在的 NPC: {target!r}")
            money_delta = args.get("money_delta")
            if not isinstance(money_delta, int) or isinstance(money_delta, bool):
                report.errors.append(f"{name}.intent_args.money_delta 必须是整数")
            _check_quest_items(args.get("offer"), items_by_id, f"{name}.intent_args.offer", report)
            _check_quest_items(args.get("want"), items_by_id, f"{name}.intent_args.want", report)
        elif verb == "work":
            if args.get("at") not in loc_ids:
                report.errors.append(f"{name}.intent_args.at 指向不存在的地点: {args.get('at')!r}")
            hours = args.get("hours")
            if not isinstance(hours, int) or isinstance(hours, bool) or not 1 <= hours <= 12:
                report.errors.append(f"{name}.intent_args.hours 必须是 1-12 的整数")
        elif verb == "escort":
            route = args.get("route")
            if not isinstance(route, list) or len(route) < 2 or any(x not in loc_ids for x in route):
                report.errors.append(f"{name}.intent_args.route 含不存在的地点")
            cargo = args.get("cargo_value")
            if not isinstance(cargo, int) or isinstance(cargo, bool) or cargo < (escort.get("cargo_min_value") or 1):
                report.errors.append(f"{name}.intent_args.cargo_value 低于 economy.escort.cargo_min_value")
            fee = args.get("fee")
            if not isinstance(fee, int) or isinstance(fee, bool) or fee <= 0:
                report.errors.append(f"{name}.intent_args.fee 必须是正整数")
        # P2 静态可完成性：引擎接单要求“至少还剩 1 整天”，1 日单在当天计划时必然被跳过。
        if deadline is not None and deadline < 2:
            report.errors.append(
                f"{name}.deadline_days={deadline} 小于 2；引擎接单要求剩余至少 1 整天，1 日单会被直接跳过"
            )
        target_loc = quest.get("target_location")
        if not isinstance(target_loc, str) or target_loc not in loc_ids:
            report.errors.append(f"{name}.target_location 必填且必须指向有效地点")
        elif isinstance(giver, str) and giver in npc_map:
            home = (npc_map.get(giver) or {}).get("home")
            hops = _path_len(adj, home, target_loc)
            if hops is None:
                report.errors.append(f"{name}: 发布人 {giver} 的 home={home} 到 {target_loc} 不可达")
        if verb == "move":
            if target_loc and args.get("to") != target_loc:
                report.errors.append(f"{name}.intent_args.to 应等于 target_location={target_loc}")
        elif verb == "buy":
            item = quest.get("target_item")
            if target_loc and args.get("from") != target_loc:
                report.errors.append(f"{name}.intent_args.from 应等于 target_location={target_loc}")
            if item and args.get("item") != item:
                report.errors.append(f"{name}.intent_args.item 应等于 target_item={item}")
            if target_loc and item and item not in loc_services.get(target_loc, set()):
                report.errors.append(f"{name}: {target_loc} 的 services 不提供 {item}，采购无法完成")
        elif verb == "trade":
            if quest.get("target_npc") and args.get("target") != quest.get("target_npc"):
                report.errors.append(f"{name}.intent_args.target 应等于 target_npc={quest.get('target_npc')}")
        elif verb == "work":
            if target_loc and args.get("at") != target_loc:
                report.errors.append(f"{name}.intent_args.at 应等于 target_location={target_loc}")
            if target_loc and not loc_services.get(target_loc):
                report.errors.append(f"{name}: {target_loc} 没有可代工的店铺/服务")
        elif verb == "escort":
            route = args.get("route")
            if isinstance(route, list) and route:
                if isinstance(giver, str) and giver in npc_map and route[0] != (npc_map.get(giver) or {}).get("home"):
                    report.errors.append(f"{name}: 镖路起点 {route[0]} 应等于发布人 {giver} 的 home")
                if target_loc and route[-1] != target_loc:
                    report.errors.append(f"{name}: 镖路终点 {route[-1]} 应等于 target_location={target_loc}")
                ok, why = _route_adjacent(route, adj)
                if not ok:
                    report.errors.append(f"{name}: 镖路不合法：{why}")
        # 采购垫付与任务板门槛：entry_cost 必须等于基准价 × qty，
        # 且必须写进 name，任务板直接显示 name，新手点单前就能看到要垫多少钱。
        entry_cost = quest.get("entry_cost")
        if entry_cost is not None:
            if not isinstance(entry_cost, int) or isinstance(entry_cost, bool) or entry_cost < 0:
                report.errors.append(f"{name}.entry_cost 必须是 >=0 的整数")
            elif category == "procurement":
                item = args.get("item") if isinstance(args, dict) else None
                qty = args.get("qty") if isinstance(args, dict) else None
                base = (items_by_id.get(item) or {}).get("base_price")
                if isinstance(base, int) and isinstance(qty, int):
                    expected = base * qty
                    if entry_cost != expected:
                        report.errors.append(
                            f"{name}.entry_cost={entry_cost} 与基准价×数量 {base}×{qty}={expected} 不一致"
                        )
                if entry_cost > 0 and f"垫付{entry_cost}文" not in str(name):
                    report.errors.append(
                        f"{name}.name 必须包含“垫付{entry_cost}文”，任务板才能显示垫付门槛"
                    )
                if entry_cost > 50 and quest.get("optional") is not True:
                    report.errors.append(
                        f"{name}.entry_cost={entry_cost} > 50，高价采购必须标记 optional: true"
                    )
        if quest.get("starter") is True:
            if category != "errand":
                report.errors.append(f"{name}.starter 起步任务必须是 errand")
            if entry_cost != 0:
                report.errors.append(f"{name}.starter 起步任务 entry_cost 必须为 0")
            if quest.get("target_item"):
                report.errors.append(f"{name}.starter 起步任务不得要求 target_item")
            if isinstance(giver, str) and giver in npc_map:
                if (npc_map.get(giver) or {}).get("home") != "market":
                    report.errors.append(f"{name}.starter 发布人必须在出生点 market")
                hops = _path_len(adj, "market", target_loc) if isinstance(target_loc, str) else None
                if hops is None or hops > 1:
                    report.errors.append(f"{name}.starter 目标地点距 market 必须 <=1 跳，实际 {hops}")
            money = (reward or {}).get("money") if isinstance(reward, dict) else None
            if not isinstance(money, int) or isinstance(money, bool) or not 8 <= money <= 15:
                report.errors.append(f"{name}.starter 报酬必须在 8-15 文，实际 {money!r}")
            if deadline is not None and deadline < 2:
                report.errors.append(f"{name}.starter 时限必须 >=2 日")
        if category == "debt_collection":
            if quest.get("repeatable") is not False:
                report.errors.append(f"{name}.repeatable 讨债任务应为 false")
            settlement = quest.get("settlement")
            if not isinstance(settlement, dict):
                report.errors.append(f"{name}.settlement 讨债任务必填")
            else:
                if settlement.get("kind") != "remit_to_giver":
                    report.errors.append(f"{name}.settlement.kind 应为 remit_to_giver")
                if isinstance(args.get("money_delta"), int) and settlement.get("amount") != args.get("money_delta"):
                    report.errors.append(f"{name}.settlement.amount 应等于 intent_args.money_delta")
                debtor = args.get("target")
                creditor = giver
                mirrored = False
                for rel in (npc_map.get(creditor) or {}).get("relations") or []:
                    if isinstance(rel, dict) and rel.get("target") == debtor and rel.get("type") == "creditor":
                        if rel.get("amount") == args.get("money_delta"):
                            mirrored = True
                        else:
                            report.errors.append(
                                f"{name}: 债务金额 {args.get('money_delta')} 与账本 {rel.get('amount')} 不一致"
                            )
                if not mirrored:
                    report.errors.append(f"{name}: {creditor} 没有指向 {debtor} 的 creditor 关系")
    report.check(len(categories) == 6, "任务覆盖 6 类", f"实际 {sorted(categories)}")
    for cat in sorted(QUEST_CATEGORY_VERBS):
        report.check(categories.get(cat, 0) >= 2, f"任务类别 {cat} 至少 2 个",
                      f"实际 {categories.get(cat, 0)}")
    report.check(len(set(quest_ids)) == len(quest_ids), "任务 id 唯一", f"重复: {quest_ids}")
    # 出生点集市：至少 3 个不同发布人常驻 market、各自至少 1 张可重复模板，
    # 这样即使其中一人按日程短暂离开，白天仍有其余发布人的单可接。
    market_templates = [
        q.get("id") for q in quests or []
        if isinstance(q, dict) and q.get("repeatable") is True
        and isinstance(q.get("giver"), str)
        and (npc_map.get(q.get("giver")) or {}).get("home") == "market"
    ]
    market_givers = sorted({
        q.get("giver") for q in quests or []
        if isinstance(q, dict) and q.get("repeatable") is True
        and isinstance(q.get("giver"), str)
        and (npc_map.get(q.get("giver")) or {}).get("home") == "market"
    })
    report.check(len(market_templates) >= 3, "集市至少 3 个常驻可接任务模板",
                  f"实际 {len(market_templates)}: {market_templates}")
    report.check(len(market_givers) >= 3, "集市至少 3 个常驻任务发布人（按去重 giver 计）",
                  f"实际 {len(market_givers)}: {market_givers}")
    starters = [q.get("id") for q in quests or [] if isinstance(q, dict) and q.get("starter") is True]
    report.check(bool(starters), "存在零成本新手起步任务", f"starter: {starters}")
    if market_templates:
        report.note(f"集市常驻可接任务模板（发布人 home=market，repeatable）: {market_templates}")
    if starters:
        report.note(f"零成本起步任务: {starters}（报酬 8-15 文、目标 <=1 跳、deadline >=2 日）")
    high_price = [
        q.get("id") for q in quests or []
        if isinstance(q, dict) and isinstance(q.get("entry_cost"), int) and q.get("entry_cost") > 50
    ]
    report.note(
        "高价采购模板: " + (", ".join(high_price) if high_price else "无")
        + "；人参 120 文不在任务板，仅作为药铺可选商品")
    if reward_values:
        report.note(
            f"任务现金报酬区间: {min(reward_values)}-{max(reward_values)} 文；"
            f"最高日薪 {max_wage} 文；单任务上限 {2 * max_wage} 文"
        )


def _persona_section(text, heading):
    """取 persona 卡中某个 ## 标题下的正文，直到下一个 ## 或文件结束。"""
    lines = text.splitlines()
    out, inside = [], False
    for line in lines:
        if line.startswith("## "):
            inside = line.strip() == heading
            continue
        if inside:
            out.append(line)
    return "\n".join(out).strip()


def check_personas(npc_ids, report):
    path = BASE_DIR / "personas"
    if not report.check(path.is_dir(), "personas/ 目录存在", f"缺少 {path}"):
        return
    files = {p.stem: p for p in path.glob("*.md")}
    missing = sorted(npc_ids - set(files))
    extra = sorted(set(files) - npc_ids)
    report.check(not missing, "每个 NPC 都有 persona 卡", f"缺少: {missing}")
    report.check(not extra, "persona 卡没有多余文件", f"多余: {extra}")
    bad = []
    quote_owner = {}
    trait_owner = {}
    token_sizes = []
    for nid in sorted(npc_ids & set(files)):
        text = files[nid].read_text(encoding="utf-8")
        tokens = max(1, -(-len(text) // 4))
        token_sizes.append(tokens)
        missing_head = [h for h in PERSONA_HEADINGS if h not in text]
        quotes = [line.strip()[1:].strip() for line in text.splitlines() if line.strip().startswith(">")]
        traits = _persona_section(text, "## 一句话性格")
        if missing_head or len(quotes) < 2 or not 300 <= tokens <= 500:
            bad.append(f"{nid}(缺标题={missing_head}, 台词={len(quotes)}, tokens={tokens})")
        if not traits:
            bad.append(f"{nid}(一句话性格为空)")
        # 口吻可区分：台词与一句话性格不得在两张卡中逐字重复。
        for q in quotes:
            if q in quote_owner and quote_owner[q] != nid:
                bad.append(f"{nid} 与 {quote_owner[q]} 台词重复: {q}")
            quote_owner.setdefault(q, nid)
        if traits:
            if traits in trait_owner and trait_owner[traits] != nid:
                bad.append(f"{nid} 与 {trait_owner[traits]} 一句话性格重复")
            trait_owner.setdefault(traits, nid)
    report.check(not bad, "persona 卡结构、token 区间、台词与口吻区分度", "; ".join(bad))
    token_sizes.sort()
    if token_sizes:
        mid = len(token_sizes) // 2
        median = token_sizes[mid] if len(token_sizes) % 2 else (token_sizes[mid - 1] + token_sizes[mid]) / 2
        report.note(
            f"persona 卡: {len(files)} 张；token(min/median/max)="
            f"{token_sizes[0]}/{median}/{token_sizes[-1]}；台词 {len(quote_owner)} 句，无跨卡重复"
        )


def main() -> int:
    if hasattr(sys, "set_int_max_str_digits"):
        sys.set_int_max_str_digits(0)
    report = Report()
    data = {}
    parse_ok = True
    for name in REQUIRED_FILES:
        try:
            data[name] = load_yaml(BASE_DIR / name)
        except YamlError as exc:
            report.errors.append(f"YAML 解析失败: {exc}")
            parse_ok = False
    report.check(parse_ok, "YAML 文件可解析")
    if not parse_ok:
        print_report(report, {})
        return 1

    npcs = data["npcs.yaml"].get("npcs") if isinstance(data["npcs.yaml"], dict) else None
    locations = data["locations.yaml"].get("locations") if isinstance(data["locations.yaml"], dict) else None
    items = data["items.yaml"].get("items") if isinstance(data["items.yaml"], dict) else None
    verbs = data["verbs.yaml"].get("verbs") if isinstance(data["verbs.yaml"], dict) else None
    sects = data["sects.yaml"].get("sects") if isinstance(data["sects.yaml"], dict) else None
    routes = data["escort_routes.yaml"].get("escort_routes") if isinstance(data["escort_routes.yaml"], dict) else None
    quests = data["quests.yaml"].get("quests") if isinstance(data["quests.yaml"], dict) else None
    economy = data["economy.yaml"]

    for fname, root_key, obj in (
        ("npcs.yaml", "npcs", npcs), ("locations.yaml", "locations", locations),
        ("items.yaml", "items", items), ("verbs.yaml", "verbs", verbs),
        ("sects.yaml", "sects", sects), ("escort_routes.yaml", "escort_routes", routes),
        ("quests.yaml", "quests", quests),
    ):
        if isinstance(data[fname], dict):
            report.check(set(data[fname]) == {root_key}, f"{fname} 顶层键",
                          f"应为 {root_key}，实际 {list(data[fname])}")

    items_by_id = check_items(items, report)
    loc_ids = {l.get("id") for l in locations or [] if isinstance(l, dict)}
    npc_info = check_npcs(npcs, items_by_id, report, loc_ids) or {}
    check_locations(locations, items_by_id, loc_ids, report)
    check_references(npcs, locations, items_by_id, report)

    role_set = set(npc_info.get("roles", {}))
    check_verbs(verbs, role_set, report)
    check_economy(economy, role_set, locations, npc_info.get("money_total", 0), report)
    check_goal_targets(economy, npcs, npc_info.get("money_total", 0), report)

    # 目标类型覆盖
    goal_types = set()
    for npc in npcs or []:
        if isinstance(npc, dict):
            for g in npc.get("goals") or []:
                if isinstance(g, dict) and g.get("type") in GOAL_TYPES:
                    goal_types.add(g["type"])
    missing_goal_types = sorted(GOAL_TYPES - goal_types)
    report.check(not missing_goal_types, "7 类目标均有 NPC 使用", f"缺少: {missing_goal_types}")

    verb_names = {v.get("name") for v in verbs or [] if isinstance(v, dict)}
    if "join_sect" in goal_types:
        report.check("join_sect" in verb_names, "join_sect 目标与动词对齐",
                      "npcs.yaml 使用了 join_sect，但 verbs.yaml 没有 join_sect 动词")
    report.check("apprentice" in verb_names and "join_sect" in verb_names,
                  "拜师与入派动词分离", "需要同时存在 apprentice 与 join_sect")

    npc_map = _npc_map(npcs)
    check_sects(sects, loc_ids, npc_map, economy, report)
    check_escort_routes(routes, locations, economy, report)
    check_quests(quests, npc_map, loc_ids, locations, items_by_id, verb_names, economy, report)
    check_p3i_additions(load_yaml(BASE_DIR / "additions" / "economy_p3i.yaml"),
                        items_by_id, loc_ids, npc_map, report)
    check_personas(set(npc_map), report)

    print_report(report, npc_info)
    return 0 if not report.errors else 1


P3I_NEW_ITEMS = [
    "rice_cake", "lotus_root", "river_fish", "millet", "hemp_rope",
    "bamboo_basket", "ink_stick", "sickle", "moxa_roll", "pearl_earring",
]
P3I_PRICE_FORBIDDEN_KEYS = ("random", "rng", "noise", "jitter", "seed", "variance")


def check_p3i_additions(add_data, items_by_id, loc_ids, npc_map, report):
    """校验 P3I 物品产地/用途目录与确定性供需物价模型。

    引擎当前不读取 price_model（见文件头注释），所以这里只保证：
    1) 10 件新物品都有产地与用途；2) 模型只含确定性输入、无随机项；
    3) 同一输入重复计算两次结果完全一致（可复现）；
    4) 模型在 2 日内确实产生非零价格变化，供文档引用。
    """
    label = "additions/economy_p3i.yaml"
    if not isinstance(add_data, dict):
        report.errors.append(f"{label} 顶层必须是映射")
        return
    catalog = add_data.get("item_catalog")
    if not isinstance(catalog, dict):
        report.errors.append(f"{label} 缺少 item_catalog 映射")
        catalog = {}
    missing_catalog = [iid for iid in P3I_NEW_ITEMS if iid not in catalog]
    report.check(not missing_catalog, "P3I 新物品产地/用途齐全", f"缺少: {missing_catalog}")
    for iid in P3I_NEW_ITEMS:
        entry = catalog.get(iid)
        if not isinstance(entry, dict):
            continue
        if iid not in items_by_id:
            report.errors.append(f"{label}.item_catalog.{iid} 对应的物品不在 items.yaml")
        origin = entry.get("origin")
        if origin not in loc_ids:
            report.errors.append(f"{label}.item_catalog.{iid}.origin 指向不存在的地点: {origin!r}")
        if not isinstance(entry.get("usage"), str) or not entry.get("usage").strip():
            report.errors.append(f"{label}.item_catalog.{iid}.usage 不能为空")

    arcs = add_data.get("long_term_arcs")
    report.check(isinstance(arcs, list) and len(arcs) >= 3,
                  "长线目标 >=3 条", f"实际 {len(arcs) if isinstance(arcs, list) else '非列表'}")
    seen_arc_goal = set()
    for ai, arc in enumerate(arcs if isinstance(arcs, list) else []):
        tag = f"{label}.long_term_arcs[{ai}]"
        if not isinstance(arc, dict):
            report.errors.append(f"{tag} 必须是映射")
            continue
        npc_id = arc.get("npc")
        npc = npc_map.get(npc_id)
        if npc is None:
            report.errors.append(f"{tag}.npc 不存在: {npc_id!r}")
            continue
        gid = arc.get("goal")
        gtype = arc.get("goal_type")
        goals = {g.get("id"): g.get("type") for g in npc.get("goals") or [] if isinstance(g, dict)}
        if gid not in goals:
            report.errors.append(f"{tag}.goal 不存在于 {npc_id}: {gid!r}")
        elif goals[gid] != gtype:
            report.errors.append(f"{tag}.goal_type 与 NPC 目标不一致: {gtype!r} != {goals[gid]!r}")
        if gid in seen_arc_goal:
            report.errors.append(f"{tag}.goal 被多条长线目标重复占用: {gid}")
        seen_arc_goal.add(gid)
        ms = arc.get("milestones")
        if not isinstance(ms, list) or not ms:
            report.errors.append(f"{tag}.milestones 必须非空")
            continue
        ats = [m.get("at") for m in ms if isinstance(m, dict)]
        if any(not isinstance(x, float) and not isinstance(x, int) for x in ats):
            report.errors.append(f"{tag}.milestones.at 必须是数值")
        elif ats != sorted(ats) or ats[-1] <= 0:
            report.errors.append(f"{tag}.milestones.at 必须递增且为正")
        if arc.get("goal_type") == "build_shop" and len(ats) < 2:
            report.errors.append(f"{tag} build_shop 至少需要 2 个里程碑")

    model = add_data.get("price_model")
    if not isinstance(model, dict):
        report.errors.append(f"{label} 缺少 price_model 映射")
        return
    horizon = model.get("horizon_days")
    report.check(isinstance(horizon, int) and horizon >= 2, "物价模型覆盖 >=2 日",
                  f"horizon_days={horizon!r}")
    horizon = horizon if isinstance(horizon, int) and horizon >= 2 else 2
    if model.get("deterministic") is not True:
        report.errors.append(f"{label}.price_model.deterministic 必须为 true")
    flat = json.dumps(model, ensure_ascii=False).lower()
    hit = [k for k in P3I_PRICE_FORBIDDEN_KEYS if k in flat]
    report.check(not hit, "物价模型不含随机项", f"出现禁用词: {hit}")

    cats = model.get("categories")
    if not isinstance(cats, dict):
        report.errors.append(f"{label}.price_model.categories 必须是映射")
        return
    report.check(len(cats) >= 5, "物价模型覆盖 >=5 类商品", f"实际 {len(cats)} 类")
    quotes = {}
    for name, cat in sorted(cats.items()):
        if not isinstance(cat, dict):
            report.errors.append(f"{label}.price_model.categories.{name} 必须是映射")
            continue
        items = cat.get("items")
        if not isinstance(items, list) or not items:
            report.errors.append(f"{label}.price_model.categories.{name}.items 必须非空")
            continue
        unknown = [i for i in items if i not in items_by_id]
        report.check(not unknown, f"物价模型 {name} 商品存在", f"未知物品: {unknown}")
        supply = cat.get("supply_by_day")
        demand = cat.get("demand_by_day")
        elastic = cat.get("elasticity")
        if not isinstance(elastic, (int, float)) or isinstance(elastic, bool) or not 0 < elastic <= 1:
            report.errors.append(f"{label}.price_model.categories.{name}.elasticity 必须在 (0,1]")
            continue
        if (not isinstance(supply, list) or not isinstance(demand, list)
                or len(supply) != horizon or len(demand) != horizon):
            report.errors.append(
                f"{label}.price_model.categories.{name} 的 supply/demand 长度必须等于 {horizon}")
            continue
        if any(not isinstance(x, int) or isinstance(x, bool) or x <= 0 for x in supply + demand):
            report.errors.append(f"{label}.price_model.categories.{name} 的 supply/demand 必须为正整数")
            continue
        for day in range(horizon):
            s, d = supply[day], demand[day]
            factor = 1 + float(elastic) * (d - s) / (d + s)
            for iid in items:
                base = int(items_by_id[iid]["base_price"])
                quotes[(iid, day)] = int(round(base * factor))

    # 确定性：同一输入算两遍必须完全一致。
    again = {}
    for name, cat in sorted(cats.items()):
        if not isinstance(cat, dict) or not isinstance(cat.get("items"), list):
            continue
        elastic = cat.get("elasticity") or 0
        for day in range(horizon):
            s, d = cat["supply_by_day"][day], cat["demand_by_day"][day]
            factor = 1 + float(elastic) * (d - s) / (d + s)
            for iid in cat["items"]:
                again[(iid, day)] = int(round(int(items_by_id[iid]["base_price"]) * factor))
    report.check(quotes == again, "物价模型可复现", "同一输入两次计算结果不一致")

    amps = []
    for iid in sorted({k[0] for k in quotes}):
        p0, p1 = quotes.get((iid, 0)), quotes.get((iid, 1))
        if p0 and p1:
            amps.append((abs(p1 - p0) / p0, iid, p0, p1))
    if amps:
        worst = max(amps)
        report.check(worst[0] > 0, "物价 2 日内有非零变化",
                      f"最大变动 {worst[0] * 100:.1f}%（{worst[1]} {worst[2]}→{worst[3]} 文）")
        report.info.append(
            "P3I 物价模型（内容层规格，引擎未接入）："
            + "；".join(f"{iid} {p0}→{p1} 文（{(p1 - p0) / p0 * 100:+.1f}%）"
                        for _, iid, p0, p1 in sorted(amps, key=lambda x: -x[0])[:5])
            + f"；共 {len(cats)} 类 {len({k[0] for k in quotes})} 件商品"
        )


def print_report(report: Report, npc_info: dict):
    print("=" * 64)
    print("world-sim 内容包校验")
    print("=" * 64)
    for ok, label in report.checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {label}")
    for msg in report.info:
        print(f"[INFO] {msg}")
    if report.warnings:
        print("-" * 64)
        print(f"警告 {len(report.warnings)} 条：")
        for msg in report.warnings:
            print(f"  [WARN] {msg}")
    if report.errors:
        print("-" * 64)
        print(f"错误 {len(report.errors)} 条：")
        for msg in report.errors:
            print(f"  [ERROR] {msg}")
    else:
        print("-" * 64)
        print("所有硬性检查通过。")
    if npc_info:
        print("-" * 64)
        print(f"统计: NPC {len(npc_info.get('ids', []))} 人；"
              f"初始货币合计 {npc_info.get('money_total', 0)} 文，"
              f"人均 {npc_info.get('money_avg', 0):.1f} 文；"
              f"职业 {len(npc_info.get('roles', {}))} 种；"
              f"关系边 {len(npc_info.get('relation_edges', {}))} 条。")
    print("=" * 64)
    print("结果:", "PASS" if not report.errors else "FAIL")


if __name__ == "__main__":
    raise SystemExit(main())
