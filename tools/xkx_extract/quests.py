"""任务抽取：195 房切片内可完成的 4 条 + 清单留痕。

只收能闭环的：
- 2 条动态任务（`quest/dynamic_quest` 里 owner 落在切片内的条目）；
- 2 条切片 NPC 的 ask/交付支线（阿土→阿凤、温仪→夏雪宜）。

静态任务清单（qslist/qwlist/qwlist1）只统计，不入库：发放 NPC 全在切片外，
按显示名匹配目标也不可靠。抽不出的交付候选（如傻姑要的《杖法图解》）
如实进 diagnostics，不编任务。
"""

from __future__ import annotations

import re

from . import lpc
from .skills_actions import _line_at, _match, _read, _strip_comments

SLICE_PREFIXES = ("/d/hangzhou/", "/d/jiaxing/", "/d/shiliang/")
STATIC_LISTS = ("quest/qslist.lpc", "quest/qwlist.lpc", "quest/qwlist1.lpc")
DYNAMIC_FILE = "quest/dynamic_quest"
REWARD_FILE = "adm/daemons/questd.lpc"

_INQUIRY_RE = re.compile(r'set\(\s*"inquiry"\s*,')
_FUNC_REF_RE = re.compile(r'"([^"]+)"\s*:\s*\(\s*:\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*\)')
_FUNC_RE_TEMPLATE = r"(?:string|int|void|mixed)\s+{name}\s*\([^)]*\)\s*\{{"
_NEW_RE = re.compile(r'new\s*\(\s*"([^"]+)"\s*\)')
_SET_TEMP_RE = re.compile(r'set_temp\s*\(\s*"([^"]+)"\s*,\s*(-?\d+)\s*\)')
_QUERY_TEMP_ZERO_RE = re.compile(r'query_temp\s*\(\s*"([^"]+)"\s*\)\s*==\s*0')
_ITEM_ID_RE = re.compile(r'query\s*\(\s*"id"\s*\)\s*(==|!=)\s*"([^"]+)"')
_ADD_RE = re.compile(r'add\s*\(\s*"([^"]+)"\s*,\s*(-?\d+)\s*\)')
_EXIT_SET_RE = re.compile(r'"(/[^"]+)"\s*->\s*set\s*\(\s*"(exits/[^"]+)"')

# 已人工核对的非任务候选：各自写明文件、定位函数行与原因。
NON_TASK_CANDIDATES = (
    {
        "npc": "/d/jiaxing/npc/shagu",
        "path": "d/jiaxing/npc/shagu.lpc",
        "func": "accept_object",
        "reason_code": "item_source_outside_slice",
        "reason": "交付物《杖法图解》只由 /d/shenlong/sgjob/sgjob50000.lpc 产出，切片内无来源",
    },
    {
        "npc": "/d/hangzhou/npc/huqingyu",
        "path": "d/hangzhou/npc/huqingyu.lpc",
        "func": "accept_object",
        "reason_code": "paid_service",
        "reason": "收钱改名，属于付费服务，没有任务目标与完成条件",
    },
    {
        "npc": "/d/hangzhou/npc/ouyezi",
        "path": "d/hangzhou/npc/ouyezi.lpc",
        "func": "accept_object",
        "reason_code": "paid_service",
        "reason": "收钱铸剑/改名，属于付费服务，没有任务目标与完成条件",
    },
    {
        "npc": "/d/hangzhou/npc/qianlong",
        "path": "d/hangzhou/npc/qianlong.lpc",
        "anchor": 'add_action("do_kneel"',
        "reason_code": "one_off_event",
        "reason": "磕头一次性给经验，不是 ask 触发的可重复任务",
    },
    {
        "npc": "/d/jiaxing/npc/hai",
        "path": "d/jiaxing/npc/hai.lpc",
        "anchor": 'add_action("do_decide"',
        "reason_code": "one_off_event",
        "reason": "自宫/领工资一次性事件或服务，无任务目标与交付校验",
    },
)


def _slice_path(path: str) -> bool:
    return path.startswith(SLICE_PREFIXES)


def _func_body(text: str, name: str):
    """返回 (函数体, 函数体起始下标, 函数定义行号)。"""
    m = re.search(_FUNC_RE_TEMPLATE.format(name=re.escape(name)), text)
    if not m:
        return None, -1, None
    start = text.find("{", m.end() - 1)
    if start < 0:
        return None, -1, None
    end = _match(text, start)
    if end < 0:
        return None, -1, None
    return text[start + 1:end], start + 1, _line_at(text, m.start())


def _item_meta(src_root: str, item_path: str):
    """从物品源码读名称与 id 别名（走 LPC 扫描器，兼容颜色宏）。"""
    rel = item_path.lstrip("/") + ".lpc"
    text = _read(src_root, rel)
    if text is None:
        return None, []
    ctx = lpc.ParseContext(lpc.mudlib_id(rel), lpc.mudlib_dir(rel), lpc.extract_defines(text))
    calls, _errors = lpc.scan_calls(text, ctx)
    for cname, args, _line, _method in calls:
        if cname != "set_name" or not args:
            continue
        name = lpc.clean_text(args[0]) if isinstance(args[0], str) else None
        ids = [str(x) for x in (args[1] or [])] if len(args) > 1 and isinstance(args[1], list) else []
        return name, ids
    return None, []


def _inquiry_entries(text: str):
    """返回 [(topic, func, line)]，只解析函数引用形式的 inquiry。"""
    entries = []
    for m in _FUNC_REF_RE.finditer(text):
        entries.append((m.group(1), m.group(2), _line_at(text, m.start())))
    return entries


def _givers_from_text(src_root, rel_path, text):
    """找 ask 里给物品的支线：inquiry -> 函数体 new(item)->move(player)。"""
    out = []
    for topic, func, topic_line in _inquiry_entries(text):
        body, base, func_line = _func_body(text, func)
        if body is None:
            continue
        news = [p for p in _NEW_RE.findall(body)]
        if not news or "->move(" not in body:
            continue
        flag = None
        consumed = _SET_TEMP_RE.findall(body)
        for key, val in consumed:
            if int(val) == 0:
                flag = key
                break
        if flag is None:
            continue
        item_path = "/" + news[0].lstrip("/")
        maybe = _QUERY_TEMP_ZERO_RE.search(body)
        if maybe and maybe.group(1) != flag:
            flag = maybe.group(1)
        out.append({
            "topic": topic,
            "func": func,
            "topic_line": topic_line,
            "func_line": func_line,
            "func_body": body,
            "item_path": item_path,
            "flag": flag,
        })
    return out


def _receiver_from_text(text):
    """解析 accept_object：接受的 id、奖励、解锁的出口。"""
    body, base, line = _func_body(text, "accept_object")
    if body is None:
        return None
    accepted = []
    for op, ident in _ITEM_ID_RE.findall(body):
        # `!= "x"` 后紧跟 notify_fail，语义是“只收 x”。
        accepted.append(ident)
    rewards = []
    for path in _NEW_RE.findall(body):
        rewards.append("/" + path.lstrip("/"))
    adds = {}
    for key, val in _ADD_RE.findall(body):
        adds[key] = int(val)
    exits = [f"{room}:{slot}" for room, slot in _EXIT_SET_RE.findall(body)]
    return {
        "line": line,
        "accepted_ids": accepted,
        "reward_items": rewards,
        "reward_adds": adds,
        "unlock_exits": exits,
    }


def _dynamic_records(src_root):
    text = _read(src_root, DYNAMIC_FILE)
    if text is None:
        return [], 0
    data = []
    started = False
    for idx, raw in enumerate(text.splitlines(), start=1):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("%s:"):
            started = True
            continue
        if not started:
            continue
        data.append((s, idx))
    records = []
    for i in range(0, len(data) - 2, 3):
        (item, item_line), (owner, _owner_line), (fin, _fin_line) = data[i], data[i + 1], data[i + 2]
        if not owner.startswith("/"):
            continue
        records.append({
            "item": item,
            "owner": owner,
            "fin_func": None if fin == "NULL" else fin,
            "line": item_line,
        })
    return records, len(data)


def _reward_formulas(src_root):
    """从 questd 原样抽三条奖励算式与行号。"""
    text = _read(src_root, REWARD_FILE)
    if text is None:
        return None
    pats = {
        "exp": re.compile(r"exp\s*=\s*(\d+)\s*\+\s*random\s*\(\s*(\d+)\s*\)"),
        "pot": re.compile(r"pot\s*=\s*exp\s*/\s*\(\s*random\s*\(\s*(\d+)\s*\)\s*\+\s*(\d+)\s*\)"),
        "score": re.compile(r"score\s*=\s*(\d+)\s*\+\s*random\s*\(\s*(\d+)\s*\)"),
    }
    out = {}
    for key, pat in pats.items():
        m = pat.search(text)
        if not m:
            return None
        if key == "pot":
            out[key] = {
                "random": int(m.group(1)),
                "plus": int(m.group(2)),
                "expr": m.group(0).split("=", 1)[1].strip(),
                "line": _line_at(text, m.start()),
            }
            continue
        out[key] = {
            "base": int(m.group(1)),
            "span": int(m.group(2)),
            "expr": m.group(0).split("=", 1)[1].strip(),
            "line": _line_at(text, m.start()),
        }
    return out


def _static_counts(src_root):
    entries = 0
    unique: set[str] = set()
    kill_names: list[str] = []
    for rel in STATIC_LISTS:
        text = _read(src_root, rel)
        if text is None:
            continue
        names = re.findall(r'"quest"\s*:\s*"([^"]+)"', text)
        entries += len(names)
        unique.update(names)
        if rel.endswith("qslist.lpc"):
            kill_names = names
    return entries, len(unique), kill_names


def extract_quests(src_root: str, npcs: list[dict]):
    diags: list[dict] = []
    quests: list[dict] = []

    static_entries, static_unique, kill_names = _static_counts(src_root)
    slice_names = {n.get("name") for n in npcs if n.get("origin") == "slice" and n.get("name")}
    kill_hits = [x for x in kill_names if x in slice_names]

    # 1. 动态任务：owner 在切片内才入库
    dyn, dyn_lines = _dynamic_records(src_root)
    reward = _reward_formulas(src_root)
    if reward is None:
        diags.append({
            "rule": "E_QUEST_REWARD_FORMULA", "severity": "error",
            "path": REWARD_FILE, "line": None,
            "message": "动态任务奖励公式未找到，无法按原版抽取",
        })
    in_slice = [d for d in dyn if _slice_path(d["owner"])]
    npc_by_id = {n["id"]: n for n in npcs}
    for rec in in_slice:
        item_name, _ids = _item_meta(src_root, rec["item"])
        owner = npc_by_id.get(rec["owner"], {})
        qid = "q_dynamic_" + re.sub(r"[^a-z0-9]+", "_", rec["item"].rsplit("/", 1)[-1].lower()).strip("_")
        quests.append({
            "id": qid,
            "name": item_name or rec["item"].rsplit("/", 1)[-1],
            "type": "deliver",
            "dynamic": True,
            "giver": None,
            "target": {"id": rec["owner"], "name": owner.get("name")},
            "required_item": {
                "id": rec["item"],
                "name": item_name,
                "source": "dynamic_spawn",
            },
            "reward": {
                "exp": ({"base": reward["exp"]["base"], "random": reward["exp"]["span"]}
                        if reward else None),
                "pot": ({"formula": "exp/(random(n)+1)",
                         "random": reward["pot"]["random"], "plus": reward["pot"]["plus"]}
                        if reward else None),
                "score": ({"base": reward["score"]["base"], "random": reward["score"]["span"]}
                          if reward else None),
                "formula_source": {
                    "path": REWARD_FILE,
                    "line": reward["exp"]["line"] if reward else None,
                },
            },
            "preconditions": [
                {"kind": "dynamic_quest_item", "item": rec["item"]},
                {"kind": "target_owner", "npc": rec["owner"]},
            ],
            "source": {"path": DYNAMIC_FILE, "line": rec["line"]},
        })
        if reward:
            for key in ("exp", "pot", "score"):
                diags.append({
                    "rule": "I_QUEST_REWARD_SOURCE", "severity": "info",
                    "path": REWARD_FILE, "line": reward[key]["line"],
                    "message": f"{qid} 奖励 {key} 照原式 {reward[key]['expr']}",
                    "id": qid, "key": key,
                })

    # 2. 切片 NPC 的 ask/交付支线
    givers = []
    for npc in npcs:
        if npc.get("origin") != "slice":
            continue
        rel = npc["source"]["path"]
        text = _read(src_root, rel)
        if text is None:
            continue
        for g in _givers_from_text(src_root, rel, text):
            item_name, ids = _item_meta(src_root, g["item_path"])
            givers.append({
                "npc": npc,
                "rel": rel,
                "text": text,
                "item_name": item_name,
                "item_ids": ids,
                **g,
            })

    receivers = {}
    for npc in npcs:
        if npc.get("origin") != "slice":
            continue
        rel = npc["source"]["path"]
        text = _read(src_root, rel)
        if text is None:
            continue
        rec = _receiver_from_text(text)
        if rec is not None:
            receivers[npc["id"]] = {"npc": npc, "rel": rel, "rec": rec}

    for g in givers:
        matched = None
        for rid, r in receivers.items():
            if rid == g["npc"]["id"]:
                continue
            accepted = set(r["rec"]["accepted_ids"])
            if accepted & set(g["item_ids"]):
                matched = (rid, r)
                break
        if matched is None:
            diags.append({
                "rule": "W_QUEST_DELIVERY_TARGET", "severity": "warning",
                "path": g["rel"], "line": g["func_line"],
                "message": f"{g['npc']['id']} 的 {g['item_path']} 在切片内找不到接收 NPC",
                "item": g["item_path"],
            })
            continue
        rid, r = matched
        quests.append({
            "id": "q_" + re.sub(r"[^a-z0-9]+", "_", g["npc"]["id"].rsplit("/", 1)[-1]).strip("_")
                   + "_" + re.sub(r"[^a-z0-9]+", "_", rid.rsplit("/", 1)[-1]).strip("_"),
            "name": f"{g['npc'].get('name') or g['npc']['id']} → {r['npc'].get('name') or rid}",
            "type": "talk+deliver",
            "dynamic": False,
            "giver": {
                "id": g["npc"]["id"],
                "name": g["npc"].get("name"),
                "topic": g["topic"],
                "source": {"path": g["rel"], "line": g["topic_line"]},
            },
            "target": {
                "id": rid,
                "name": r["npc"].get("name"),
                "source": {"path": r["rel"], "line": r["rec"]["line"]},
            },
            "required_item": {
                "id": g["item_path"],
                "name": g["item_name"],
                "source": {"path": g["rel"], "line": g["func_line"]},
            },
            "reward": {
                "exp": int(r["rec"]["reward_adds"].get("combat_exp", 0)),
                "money": int(r["rec"]["reward_adds"].get("balance", 0)),
                "items": r["rec"]["reward_items"],
                "unlock_exits": r["rec"]["unlock_exits"],
                "note": (
                    "原版只给口述秘密，无经验/钱/物品"
                    if not r["rec"]["reward_adds"] and not r["rec"]["reward_items"]
                    else None
                ),
            },
            "preconditions": [
                {"kind": "npc_temp", "npc": g["npc"]["id"],
                 "temp": g["flag"], "value": 1,
                 "source": {"path": g["rel"], "line": g["func_line"]}},
                {"kind": "target_owner", "npc": rid},
            ],
            "steps": [
                {"op": "ask", "npc": g["npc"]["id"], "topic": g["topic"]},
                {"op": "deliver", "item": g["item_path"], "to": rid},
            ],
            "source": {"path": g["rel"], "line": g["topic_line"]},
        })

    # 3. 非任务候选：逐条留痕
    for cand in NON_TASK_CANDIDATES:
        text = _read(src_root, cand["path"])
        line = None
        if text is not None:
            if cand.get("func"):
                pat = re.compile(
                    r"(?:int|void|mixed)\s+" + re.escape(cand["func"]) + r"\s*\("
                )
            else:
                pat = re.compile(re.escape(cand["anchor"]))
            m = pat.search(text)
            if m:
                line = _line_at(text, m.start())
        diags.append({
            "rule": "I_QUEST_NON_TASK", "severity": "info",
            "path": cand["path"], "line": line,
            "message": f"{cand['npc']} 不计任务：{cand['reason']}",
            "id": cand["npc"], "reason_code": cand["reason_code"],
        })

    diags.append({
        "rule": "I_QUEST_STATIC_SKIPPED", "severity": "info",
        "path": ",".join(STATIC_LISTS), "line": None,
        "message": (
            f"静态任务清单共 {static_entries} 条（去重 {static_unique}），"
            "发放 NPC 全在切片外，按显示名匹配切片内同名 NPC 不可靠，不入库"
        ),
        "entries": static_entries,
        "unique": static_unique,
        "reason_code": "giver_outside_slice",
    })
    diags.append({
        "rule": "I_QUEST_DYNAMIC_OUTSIDE_SLICE", "severity": "info",
        "path": DYNAMIC_FILE, "line": None,
        "message": (
            f"动态任务共 {len(dyn)} 条，其中 {len(dyn) - len(in_slice)} 条 owner 在切片外，不入库"
        ),
        "total": len(dyn),
        "outside_slice": len(dyn) - len(in_slice),
        "reason_code": "owner_outside_slice",
    })

    counts = {
        "quests_total": len(quests),
        "quests_dynamic": sum(1 for q in quests if q["dynamic"]),
        "quests_talk_gated": sum(1 for q in quests if q["type"] == "talk+deliver"),
        "quests_static_entries": static_entries,
        "quests_static_unique": static_unique,
        "quests_static_kill_targets_in_slice": len(kill_hits),
        "quests_dynamic_total": len(dyn),
        "quests_dynamic_outside_slice": len(dyn) - len(in_slice),
        "quests_non_task": len(NON_TASK_CANDIDATES),
    }
    return quests, diags, counts
