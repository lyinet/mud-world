"""技能招式（ACTION_SEQUENCE）与属性（SKILL_HOOKS）静态抽取。

数据来源：`kungfu/skill/<id>.lpc` 及 `kungfu/skill/<id>/*.lpc`（perform 文件）。

来源标记：
- `lpc`：直接从源码读到的数字/文本；
- `derived`：按源码公式或结构推导（公式、序号、对照关系可追溯）；
- `default`：源码没有、只能采用引擎现有默认值，必须写明原因；
- `missing`：源码确实没有，且不能默认。
解析不出来的内容一律进 diagnostics，不臆造系数。
"""

from __future__ import annotations

import os
import re

from . import lpc

WEAPON_TYPES = {
    "sword", "blade", "staff", "whip", "club", "throwing",
    "hammer", "fork", "axe", "mace", "dagger",
}
UNARMED_TYPES = {"unarmed", "cuff", "claw", "strike", "hand", "leg", "finger"}
BASE_WEAPON_IDS = {"sword", "blade", "staff", "whip", "club", "throwing"}
BASE_UNARMED_IDS = {"unarmed", "cuff", "claw", "strike", "hand"}
KNOWLEDGE_IDS = {"literate", "qimen-wuxing", "yunlong-xinfa"}

CATEGORY_ZH = {
    "force": "内功",
    "dodge": "轻功",
    "weapon": "兵器",
    "unarmed": "拳脚",
    "action": "招式",
    "knowledge": "知识",
}

COMBAT_FLAGS = {
    "bixie/cizu": ("victim_cannot_dodge", "命中后目标无法闪避"),
    "bixie/ciwan": ("victim_cannot_parry", "命中后目标无法招架"),
}

HOOK_NAMES = (
    "force_hit",
    "victim_force_hit",
    "martial_hit",
    "victim_parry_hit",
    "weapon_hit",
)

COEFF_KEYS = ("dodge", "parry", "force", "damage")


def _read(src_root: str, rel_path: str) -> str | None:
    try:
        with open(os.path.join(src_root, rel_path), "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _line_at(text: str, idx: int) -> int:
    return text.count("\n", 0, max(0, idx)) + 1


def _lpc_div(a: int, b: int) -> int | None:
    """LPC 整数除法：向零截断。"""
    if b == 0:
        return None
    q = abs(a) // abs(b)
    return q if a * b >= 0 else -q


def _text(value) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, lpc.Expr):
        return value.text
    return None


def _types_from(value) -> list[str]:
    raw = _text(value)
    if not raw:
        return []
    return re.findall(r'"([^"]+)"', raw)


def _strip_comments(text: str) -> str:
    """把注释替换成空格，保留换行与字符串内容，便于按行号做正则。"""
    out = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            out.append(ch)
            i += 1
            while i < n:
                out.append(text[i])
                if text[i] == "\\" and i + 1 < n:
                    out.append(text[i + 1])
                    i += 2
                    continue
                if text[i] == '"':
                    i += 1
                    break
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                out.append("\n" if text[i] == "\n" else " ")
                i += 1
            out.append("  ")
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _match(text: str, start: int) -> int:
    """从 `(`/`[`/`{` 位置开始，返回配对闭合符下标。"""
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack = []
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == '"':
                    i += 1
                    break
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        if ch in pairs:
            stack.append(pairs[ch])
        elif stack and ch == stack[-1]:
            stack.pop()
            if not stack:
                return i
        i += 1
    return -1


def _split_array(text: str, open_idx: int) -> list[tuple[int, int]]:
    """把 `({ ... })` 拆成每个 `([ ... ])` 的 (start, end)。"""
    entries = []
    stack = []
    i = open_idx
    n = len(text)
    entry_start = None
    while i < n:
        ch = text[i]
        if ch == '"':
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == '"':
                    i += 1
                    break
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        if ch in "([{":
            if ch == "(" and len(stack) == 2 and stack[-1] == "}":
                entry_start = i
            stack.append({"(": ")", "[": "]", "{": "}"}[ch])
        elif stack and ch == stack[-1]:
            stack.pop()
            if entry_start is not None and len(stack) == 2:
                entries.append((entry_start, i))
                entry_start = None
            if not stack:
                break
        i += 1
    return entries


def _nearest_if(body: str, ret_idx: int) -> str | None:
    idx = body.rfind("if", 0, ret_idx)
    if idx < 0:
        return None
    paren = body.find("(", idx)
    if paren < 0:
        return None
    end = _match(body, paren)
    if end < 0:
        return None
    return " ".join(body[paren + 1:end].split())


def _coeff_key(block: str, key: str) -> str | None:
    m = re.search(r'"' + key + r'"\s*:\s*([^,\n]+)', block)
    return m.group(1).strip() if m else None


def _coeffs_from_block(block: str) -> dict:
    """解析 `return ([ ... ])` 的四个系数。"""
    out = {}
    for key in COEFF_KEYS:
        linear = re.search(
            r'"' + key + r'"\s*:\s*([a-z])_e1\s*\+\s*\(\1_e2\s*-\s*\1_e1\)\s*\*\s*seq\s*/\s*ttl',
            block,
        )
        if linear:
            out[key] = {"kind": "linear", "var": linear.group(1)}
            continue
        raw = _coeff_key(block, key)
        if raw is None:
            out[key] = {"kind": "missing"}
            continue
        if re.fullmatch(r"-?\d+", raw):
            out[key] = {"kind": "constant", "value": int(raw)}
            continue
        rng = re.fullmatch(r"(-?\d+)\s*\+\s*random\((\d+)\)", raw)
        if rng:
            base, size = int(rng.group(1)), int(rng.group(2))
            out[key] = {"kind": "range", "min": base, "max": base + size - 1, "expr": raw}
            continue
        out[key] = {"kind": "unresolved", "expr": raw}
    return out


def _coeff_values(keys: dict) -> dict:
    out = {}
    for key, meta in keys.items():
        if meta["kind"] == "constant":
            out[key] = meta["value"]
        elif meta["kind"] == "range":
            out[key] = {"min": meta["min"], "max": meta["max"]}
        elif meta["kind"] == "linear":
            out[key] = None
        elif meta["kind"] == "unresolved":
            out[key] = {"expr": meta["expr"]}
        else:
            out[key] = 0
    return out


def _fallback_entry(snippet: str) -> dict | None:
    """含 `order[random(13)]` 之类动态片段的条目：逐字段取静态部分。"""
    if ('"skill_name"' not in snippet and '"skills_name"' not in snippet) or '"lvl"' not in snippet:
        return None
    name = re.search(r'"skill[s]?_name"\s*:\s*"([^"]+)"', snippet)
    lvl = re.search(r'"lvl"\s*:\s*(\d+)', snippet)
    action = re.search(r'"action"\s*:\s*(.*?)\s*,\s*\n\s*"lvl"', snippet, re.S)
    entry = {}
    if name:
        entry["skill_name"] = name.group(1)
    if lvl:
        entry["lvl"] = int(lvl.group(1))
    if action:
        raw = " ".join(action.group(1).split())
        entry["action"] = "".join(re.findall(r'"([^"]*)"', raw))
        entry["_action_expr"] = raw
    dt = re.search(r'"damage_type"\s*:\s*([^,\n]+)', snippet)
    if dt:
        entry["damage_type"] = dt.group(1).strip()
    return entry


def _extract_actions(src_root, skill):
    rel_path = skill["source"]["path"]
    text = _read(src_root, rel_path)
    if text is None:
        return None, [{
            "rule": "E_SKILL_ACTION_READ", "severity": "error",
            "path": rel_path, "line": skill["source"]["line"],
            "message": f"技能源码不可读取：{skill['id']}",
            "id": skill["id"],
        }]

    ctx = lpc.ParseContext(
        "/" + rel_path[:-4], "/" + os.path.dirname(rel_path) + "/",
        lpc.extract_defines(text),
    )
    diags = []
    entries = []
    action_line = None
    dynamic_text_seen = False
    m = re.search(r"mapping\s*\*?\s*action\s*=\s*", text)
    if m:
        brace = text.find("({", m.end())
        if brace >= 0 and _match(text, brace + 1) >= 0:
            action_line = _line_at(text, brace)
            for start, end in _split_array(text, brace):
                snippet = text[start:end + 1]
                try:
                    tokens = lpc.lex(snippet)
                    value, _ = lpc.parse_value(tokens, 0, ctx)
                    if not isinstance(value, dict):
                        raise lpc.ParseError("不是 mapping")
                    entries.append((start, value))
                except Exception as exc:  # noqa: BLE001 - 结构异常统一记缺口
                    fallback = _fallback_entry(snippet)
                    if fallback:
                        entries.append((start, fallback))
                        if not dynamic_text_seen:
                            dynamic_text_seen = True
                            diags.append({
                                "rule": "I_ACTION_TEXT_DYNAMIC", "severity": "info",
                                "path": rel_path, "line": _line_at(text, start),
                                "message": "招式文案含 order[random()] 等动态片段；静态字段照抽，文案保留表达式",
                                "detail": fallback.get("_action_expr"), "id": skill["id"],
                            })
                    else:
                        diags.append({
                            "rule": "W_ACTION_ENTRY_PARSE", "severity": "warning",
                            "path": rel_path, "line": _line_at(text, start),
                            "message": "招式条目解析失败，已跳过",
                            "detail": str(exc), "id": skill["id"],
                        })

    body_match = re.search(r"mapping\s+query_action\s*\([^)]*\)\s*\{", text)
    body = ""
    body_start = 0
    if body_match:
        body_start = body_match.end() - 1
        end = _match(text, body_start)
        if end > 0:
            body = text[body_start + 1:end]

    params: dict[str, int] = {}
    for pm in re.finditer(r"int\s+(\w+)\s*=\s*(-?\d+)\s*;", body):
        params[pm.group(1)] = int(pm.group(2))

    returns = []
    for rm in re.finditer(r"return\s*\(\[", body):
        open_idx = rm.end() - 1
        close = _match(body, open_idx)
        if close < 0:
            continue
        returns.append((rm.start(), body[open_idx:close + 1], _nearest_if(body, rm.start())))

    model = None
    specials = []
    if returns:
        last_block = returns[-1][1]
        keys = _coeffs_from_block(last_block)
        if any(v["kind"] == "linear" for v in keys.values()):
            mode = "linear_seq"
        elif all(v["kind"] in ("constant", "range", "missing") for v in keys.values()):
            mode = "fixed"
        else:
            mode = "unresolved"
        model = {
            "mode": mode,
            "formula": "e1 + (e2 - e1) * seq / ttl" if mode == "linear_seq" else None,
            "params": {
                k: {"e1": params.get(v.get("var", "") + "_e1"),
                    "e2": params.get(v.get("var", "") + "_e2")}
                for k, v in keys.items() if v["kind"] == "linear"
            },
            "default_return": _coeff_values(keys),
        }
        for start, block, cond in returns[:-1]:
            sk = _coeffs_from_block(block)
            specials.append({
                "condition": cond,
                "condition_source": "lpc" if cond else "unresolved",
                "damage_types": _types_from(_coeff_key(block, "damage_type")),
                "coefficients": _coeff_values(sk),
                "source": {"path": rel_path, "line": _line_at(text, body_start + 1 + start)},
            })
    elif entries:
        embedded = any(k in entries[0][1] for k in COEFF_KEYS)
        model = {"mode": "embedded" if embedded else "unresolved", "formula": None,
                 "params": {}, "default_return": {}}

    ttl = max(1, len(entries))
    actions = []
    missing_keys = set()
    for index, (start, entry) in enumerate(entries):
        coeffs = {}
        source_kinds = {}
        if model and model["mode"] == "linear_seq":
            for key, meta in _coeffs_from_block(
                returns[-1][1] if returns else ""
            ).items():
                if meta["kind"] == "linear":
                    e1 = params.get(meta["var"] + "_e1")
                    e2 = params.get(meta["var"] + "_e2")
                    if e1 is not None and e2 is not None:
                        coeffs[key] = e1 + _lpc_div((e2 - e1) * index, ttl)
                        source_kinds[key] = "derived"
                        continue
                elif meta["kind"] == "constant":
                    coeffs[key] = meta["value"]
                    source_kinds[key] = "lpc"
                    continue
                elif meta["kind"] == "range":
                    coeffs[key] = {"min": meta["min"], "max": meta["max"]}
                    source_kinds[key] = "lpc"
                    continue
                coeffs[key] = 0
                source_kinds[key] = "default"
                missing_keys.add(key)
        elif model and model["mode"] == "embedded":
            for key in COEFF_KEYS:
                value = entry.get(key)
                if isinstance(value, int):
                    coeffs[key] = value
                    source_kinds[key] = "lpc"
                elif isinstance(value, lpc.Expr):
                    coeffs[key] = {"expr": value.text}
                    source_kinds[key] = "lpc"
                else:
                    coeffs[key] = 0
                    source_kinds[key] = "default"
                    missing_keys.add(key)
        else:
            for key in COEFF_KEYS:
                coeffs[key] = 0
                source_kinds[key] = "default"
                missing_keys.add(key)

        name = _text(entry.get("skill_name") or entry.get("skills_name"))
        if not name:
            diags.append({
                "rule": "W_ACTION_NAME_MISSING", "severity": "warning",
                "path": rel_path, "line": _line_at(text, start),
                "message": f"招式 #{index} 没有 skill_name/skills_name，name 记 null，不编造招式名",
                "id": skill["id"], "action_index": index,
            })
        action_text = _text(entry.get("action"))
        lvl = entry.get("lvl")
        actions.append({
            "index": index,
            "name": lpc.clean_text(name) if isinstance(name, str) else None,
            "name_source": "lpc" if name else "missing",
            "text": lpc.clean_text(action_text) if isinstance(action_text, str) else None,
            "text_source": "derived" if entry.get("_action_expr") else (
                "lpc" if isinstance(action_text, str) else "missing"
            ),
            "text_expr": entry.get("_action_expr"),
            "skill_level_required": int(lvl) if isinstance(lvl, int) else 0,
            "skill_level_source": "lpc" if isinstance(lvl, int) else "default",
            "damage_types": _types_from(entry.get("damage_type")),
            "coefficients": coeffs,
            "coefficient_source": source_kinds,
            "source": {"path": rel_path, "line": _line_at(text, start)},
        })

    if model and model["mode"] == "unresolved":
        diags.append({
            "rule": "W_ACTION_UNRESOLVED", "severity": "warning",
            "path": rel_path, "line": action_line or skill["source"]["line"],
            "message": "招式系数既非内嵌常量、也无法按源码公式推导，未编造系数",
            "id": skill["id"],
        })
    for key in sorted(missing_keys):
        diags.append({
            "rule": "W_ACTION_COEFF_DEFAULT", "severity": "warning",
            "path": rel_path, "line": action_line or skill["source"]["line"],
            "message": f"query_action 未返回 \"{key}\"，按 LPC 缺键取 0（引擎默认值）",
            "id": skill["id"], "coefficient": key,
        })

    return {
        "id": skill["id"],
        "name": skill["name"],
        "martialtype": skill["martialtype"],
        "enable": skill["enable"],
        "action_count": len(actions),
        "coefficient_model": model,
        "coefficient_gaps": sorted(missing_keys),
        "actions": actions,
        "special_actions": specials,
        "source": skill["source"],
    }, diags


def _perform_requirements(text: str, skill_id: str) -> dict:
    out = {"skills": [], "parsed": False}
    fm = re.search(r'fskill\s*=\s*"([^"]+)"', text)
    force_skill = fm.group(1) if fm else None
    wm = re.search(r'skill_type"\s*\)\s*!=\s*"(\w+)"', text)
    weapon = wm.group(1) if wm else None
    for m in re.finditer(r'query_skill\(\s*(fskill|sskill|"([^"]+)")\s*,\s*1\s*\)\s*<\s*(\d+)', text):
        var, amount = m.group(1), int(m.group(3))
        if var == "fskill":
            target = force_skill
        elif var == "sskill":
            target = skill_id
        else:
            target = m.group(2)
        if target:
            out["skills"].append({"skill": target, "min_level": amount})
            out["parsed"] = True
    for key in ("max_neili", "neili", "combat_exp"):
        m = re.search(r'query\(\s*"' + key + r'"\s*\)\s*<\s*(\d+)', text)
        if m:
            out[key] = int(m.group(1))
            out["parsed"] = True
    if force_skill:
        out["force_skill"] = force_skill
    if weapon:
        out["weapon_skill_type"] = weapon
        out["parsed"] = True
    return out


def _extract_effects(src_root, skill, diags):
    sid = skill["id"]
    rel_path = skill["source"]["path"]
    folder_rel = os.path.join(os.path.dirname(rel_path), sid)
    folder = os.path.join(src_root, folder_rel)
    if not os.path.isdir(folder):
        return []
    effects = []
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".lpc"):
            continue
        eff_rel = (folder_rel + "/" + name).replace(os.sep, "/")
        text = _read(src_root, eff_rel)
        if text is None:
            continue
        flags = re.findall(r'set_temp\(\s*"([^"]+)"\s*,\s*1\s*\)', text)
        combat = [f for f in flags if f in COMBAT_FLAGS]
        if not combat:
            continue
        pm = re.search(r'#define\s+PNAME\s+"([^"]+)"', text)
        pname = pm.group(1).strip("「」") if pm else None
        reqs = _perform_requirements(text, sid)
        dm = re.search(
            r'call_out\(\s*"[^"]*_end"\s*,\s*(?:\(int\))?\s*(\d+)\s*\+\s*random\((\d+)\)',
            text,
        )
        duration = {"base": int(dm.group(1)), "random": int(dm.group(2))} if dm else None
        for flag in combat:
            effect, meaning = COMBAT_FLAGS[flag]
            idx = text.find('set_temp("' + flag)
            effects.append({
                "perform": name[:-4],
                "name": pname,
                "flag": flag,
                "effect": effect,
                "meaning": meaning,
                "effect_source": "derived",
                "duration": duration,
                "requirements": reqs,
                "requirements_source": "lpc" if reqs.get("parsed") else "default",
                "source": {"path": eff_rel, "line": _line_at(text, idx if idx >= 0 else 0)},
            })
    if effects and not any(e["requirements"].get("parsed") for e in effects):
        diags.append({
            "rule": "W_EFFECT_REQUIREMENTS", "severity": "warning",
            "path": rel_path, "line": skill["source"]["line"],
            "message": "perform 文件的条件未能解析，效果前置条件留空",
            "id": sid,
        })
    return effects


def _valid_learn_requirements(text: str) -> dict:
    out = {"parsed": False, "skills": []}
    m = re.search(r"int\s+valid_learn\s*\([^)]*\)\s*\{", text)
    if not m:
        return out
    start = m.end() - 1
    end = _match(text, start)
    body = text[start:end + 1] if end > 0 else ""
    for sm in re.finditer(r'query_skill\(\s*"([^"]+)"\s*(?:,\s*1\s*)?\)\s*<\s*(\d+)', body):
        out["skills"].append({"skill": sm.group(1), "min_level": int(sm.group(2))})
        out["parsed"] = True
    for key in ("max_neili", "neili", "combat_exp"):
        mm = re.search(r'query\(\s*"' + key + r'"\s*\)\s*<\s*(\d+)', body)
        if mm:
            out[key] = int(mm.group(1))
            out["parsed"] = True
    return out


def _category(skill) -> tuple[str, str]:
    sid = skill["id"]
    enable = skill["enable"]
    if skill["type"] == "knowledge" or sid in KNOWLEDGE_IDS:
        return "knowledge", f"type={skill['type']}"
    if skill["martialtype"] == "force" or sid == "force":
        return "force", f"martialtype={skill['martialtype']}"
    if skill["martialtype"] == "dodge" or sid == "dodge":
        return "dodge", f"martialtype={skill['martialtype']}"
    if sid in BASE_WEAPON_IDS:
        return "weapon", f"base skill id={sid}"
    if sid in BASE_UNARMED_IDS:
        return "unarmed", f"base skill id={sid}"
    for item in enable:
        if item in WEAPON_TYPES:
            return "weapon", f"enable={enable}"
    for item in enable:
        if item in UNARMED_TYPES:
            return "unarmed", f"enable={enable}"
    return "action", f"enable={enable}"


def _curve(text: str) -> tuple[dict, dict]:
    curve = {}
    for fn in ("learn_bonus", "practice_bonus"):
        m = re.search(fn + r"\(\s*\)\s*\{\s*return\s+(-?\d+)\s*;\s*\}", text, re.S)
        if m:
            curve[fn] = {"value": int(m.group(1)), "source_kind": "lpc"}
        else:
            curve[fn] = {"value": 0, "source_kind": "default",
                         "reason": f"源码没有可直接读取的 {fn}，按引擎中性默认 0"}
    return curve, _power_point(text)


def _power_point(text: str) -> dict:
    m = re.search(r"(\w+)\s+power_point\s*\([^)]*\)\s*\{", text)
    if not m:
        return {"kind": "constant", "value": 1.0, "source_kind": "default",
                "reason": "源码没有 power_point；按引擎默认 1.0"}
    start = m.end() - 1
    end = _match(text, start)
    body = text[start + 1:end] if end > 0 else ""
    rules = []
    default = None
    default_expr = None
    cond = None
    for line in body.splitlines():
        cm = re.search(r"if\s*\(", line)
        if cm:
            paren = line.find("(", cm.start())
            close = _match(line, paren) if paren >= 0 else -1
            if close > 0:
                cond = " ".join(line[paren + 1:close].split())
        rm = re.search(r"return\s+([^;]+);", line)
        if not rm:
            continue
        raw = " ".join(rm.group(1).split())
        is_number = re.fullmatch(r"[0-9.]+", raw)
        value = float(raw) if is_number else None
        if cond:
            rules.append({"condition": cond, "value": value, "expr": None if is_number else raw})
            cond = None
        elif re.search(r"else", line) or default is None:
            if is_number:
                default = value
            else:
                default_expr = raw
    if rules:
        return {
            "kind": "rules",
            "rules": rules,
            "default": default,
            "default_expr": default_expr,
            "source_kind": "lpc",
            "derivation": "按 if 条件逐行扫描；未命中取 default/default_expr",
        }
    if default is not None or default_expr:
        return {
            "kind": "constant" if default is not None else "expression",
            "value": default,
            "expr": default_expr,
            "source_kind": "lpc",
        }
    return {"kind": "constant", "value": 1.0, "source_kind": "default",
            "reason": "power_point 函数体无法静态取值；按引擎默认 1.0"}


def _attack_defense(action_entry, category):
    if not action_entry or not action_entry["actions"]:
        zero = {"min": 0, "max": 0}
        return (
            {"damage": zero, "force": zero, "source_kind": "default",
             "reason": "该技能不参与普通出招（无 action 序列），引擎按 0"},
            {"dodge": zero, "parry": zero, "source_kind": "default",
             "reason": "该技能不参与普通出招（无 action 序列），引擎按 0"},
        )

    def span(key):
        vals = []
        for act in action_entry["actions"]:
            v = act["coefficients"].get(key)
            if isinstance(v, int):
                vals.append(v)
            elif isinstance(v, dict) and "min" in v:
                vals.extend([v["min"], v["max"]])
            elif isinstance(v, dict) and "expr" in v:
                continue
        return {"min": min(vals), "max": max(vals)} if vals else zero

    attack = {"damage": span("damage"), "force": span("force"),
              "source_kind": "derived", "reason": "取 action 序列系数的首末范围"}
    defense = {"dodge": span("dodge"), "parry": span("parry"),
               "source_kind": "derived", "reason": "取 action 序列系数的首末范围"}
    return attack, defense


def _apply_bonuses(text: str):
    found = re.findall(r'add_temp\(\s*"apply/([^"]+)"\s*,\s*(-?\d+)', text)
    if not found:
        return {}, "default", "源码没有 add_temp(\"apply/*\") 加成，按空表 0"
    return {k: int(v) for k, v in found}, "lpc", None


def _hook_body(text: str, fn: str):
    """抽出钩子函数体（去注释、去空行），用于把运行时表达式交给引擎。"""
    m = re.search(r"(?:mixed|int|void)\s+" + fn + r"\s*\(", text)
    if not m:
        return None
    start = text.find("{", m.end() - 1)
    if start < 0:
        return None
    end = _match(text, start)
    if end < 0:
        return None
    body = text[start + 1:end]
    body = re.sub(r"//[^\n]*", "", body)
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    return "\n".join(lines)


def _hook_entry(implemented, owner, expr, owner_line=None):
    if implemented:
        return {
            "implemented": True,
            "owner": owner,
            "owner_line": owner_line,
            "value": None,
            "value_kind": "code",
            "expr": expr,
            "source_kind": "lpc",
            "reason": "钩子函数存在；值由源码表达式实时计算，静态不折算常量",
        }
    return {
        "implemented": False,
        "owner": owner,
        "owner_line": None,
        "value": 0,
        "value_kind": "default",
        "expr": None,
        "source_kind": "default",
        "reason": owner,
    }


def _hooks(text: str, category: str, force_text: str) -> dict:
    own_hit = _hook_body(text, "hit_ob")
    own_be_hit = _hook_body(text, "be_hit_ob")
    inherited_hit = _hook_body(force_text, "hit_ob")
    out = {}
    for name in HOOK_NAMES:
        if name == "force_hit":
            if category == "force" and own_hit:
                out[name] = _hook_entry(True, "kungfu/skill/<self>.lpc hit_ob", own_hit,
                                        "技能自身覆盖 hit_ob")
            elif category == "force" and inherited_hit:
                out[name] = _hook_entry(True, "inherit/skill/force.lpc hit_ob", inherited_hit,
                                        "继承 FORCE 的 hit_ob")
            elif category == "force":
                out[name] = _hook_entry(False, "源码无可执行的 hit_ob（或仅注释），按 0", None)
            else:
                out[name] = _hook_entry(False, "非内功技能不作为 mapped force；按 0", None)
        elif name == "victim_force_hit":
            out[name] = _hook_entry(
                False, "inherit/skill/force.lpc 的 be_hit_ob 在源码中被注释，所有内功均未实现；按 0", None
            )
        elif name == "martial_hit":
            if category != "force" and own_hit:
                out[name] = _hook_entry(True, "kungfu/skill/<self>.lpc hit_ob", own_hit,
                                        "技能可用作 mapped attack skill，自身定义 hit_ob")
            else:
                out[name] = _hook_entry(False, "该技能未定义 hit_ob；按 0", None)
        elif name == "victim_parry_hit":
            if own_be_hit:
                out[name] = _hook_entry(True, "kungfu/skill/<self>.lpc be_hit_ob", own_be_hit,
                                        "技能定义 be_hit_ob")
            else:
                out[name] = _hook_entry(False, "该技能未定义 be_hit_ob；按 0", None)
        else:  # weapon_hit
            out[name] = _hook_entry(False, "武器对象提供，技能表不可得；按 0", None)
    return out


def extract_skill_tables(src_root, skills, npcs):
    """返回 (skills_actions, skills_stats, diagnostics, counts)。"""
    rev_from = {}
    rev_to = {}
    for npc in npcs:
        for base, mapped in (npc.get("map_skills") or {}).items():
            rev_from.setdefault(mapped, set()).add(base)
            rev_to.setdefault(base, set()).add(mapped)

    actions_table = []
    stats_table = []
    diags = []
    force_text = _read(src_root, "inherit/skill/force.lpc") or ""
    counts = {
        "action_skills": 0,
        "action_entries": 0,
        "special_actions": 0,
        "action_gaps": 0,
        "unresolved_action_skills": 0,
        "stat_rows": 0,
        "stat_default_fields": 0,
        "hook_defaults": 0,
    }
    for skill in skills:
        action_entry, action_diags = _extract_actions(src_root, skill)
        diags.extend(action_diags)
        if action_entry:
            action_entry["effects"] = _extract_effects(src_root, skill, diags)
            action_entry["requirements"] = {
                "weapon_skill_type": next(
                    (e for e in skill["enable"] if e in WEAPON_TYPES), None
                ),
                "learn": _valid_learn_requirements(
                    _read(src_root, skill["source"]["path"]) or ""
                ),
                "source_kind": "lpc",
            }
            actions_table.append(action_entry)
            if action_entry["action_count"]:
                counts["action_skills"] += 1
                counts["action_entries"] += action_entry["action_count"]
            counts["special_actions"] += len(action_entry["special_actions"])
            counts["action_gaps"] += len(action_entry["coefficient_gaps"])
            if action_entry["coefficient_model"] and action_entry["coefficient_model"]["mode"] == "unresolved":
                counts["unresolved_action_skills"] += 1

        text = _read(src_root, skill["source"]["path"]) or ""
        category, basis = _category(skill)
        curve, power_point = _curve(text)
        attack, defense = _attack_defense(action_entry, category)
        apply_map, apply_source, apply_reason = _apply_bonuses(text)
        stats = {
            "id": skill["id"],
            "name": skill["name"],
            "type": skill["type"],
            "martialtype": skill["martialtype"],
            "enable": skill["enable"],
            "category": category,
            "category_zh": CATEGORY_ZH[category],
            "category_source": "derived",
            "category_basis": basis,
            "action_count": action_entry["action_count"] if action_entry else 0,
            "attack": attack,
            "defense": defense,
            "apply": apply_map,
            "apply_source": apply_source,
            "apply_reason": apply_reason,
            "map_skills": {
                "mapped_from": sorted(rev_from.get(skill["id"], [])),
                "mapped_to": sorted(rev_to.get(skill["id"], [])),
                "source_kind": "derived",
                "basis": "由 npcs.json 的 map_skill 关系反推",
            },
            "max_level": {
                "value": 800,
                "source_kind": "default",
                "reason": "源码未定义等级上限，沿用引擎 SKILL_MAX=800",
            },
            "curve": {
                "learn_bonus": curve["learn_bonus"],
                "practice_bonus": curve["practice_bonus"],
                "power_point": power_point,
                "source_kind": "lpc",
            },
            "hooks": _hooks(text, category, force_text),
            "gaps": [],
            "source": skill["source"],
        }
        if action_entry and action_entry["coefficient_gaps"]:
            stats["gaps"].append({
                "kind": "action_coefficient_default",
                "detail": "缺 " + ",".join(action_entry["coefficient_gaps"]) + "，按 0",
            })
        if apply_source == "default":
            counts["stat_default_fields"] += 1
        if any(v["value_kind"] == "default" for v in stats["hooks"].values()):
            counts["hook_defaults"] += 1
        stats_table.append(stats)
        counts["stat_rows"] += 1

    return actions_table, stats_table, diags, counts
