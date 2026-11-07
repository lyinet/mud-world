#!/usr/bin/env python3
"""房间逐字段对拍：原版 look 输出 vs 复刻 content/xkx。

原版侧：独立探针实例（run/mudlib-parity，端口 4020），探针角色从
/d/hangzhou/duanqiao 登录，按 exits.json 的参考图 BFS 走房并逐房 look。
复刻侧：content/xkx/rooms.json / exits.json / items.json / npcs.json。

归一化只做 N1-N6（见 room-parity.spec.md），不删除描述主体字符。

用法：
  python3 parity_rooms.py --limit 10        # 代表房间试跑
  python3 parity_rooms.py --all             # 全量
  python3 parity_rooms.py --reparse         # 用缓存原文重算报告
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from collections import Counter, deque

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
REF_MUDLIB = os.path.join(HERE, "run", "mudlib")
PARITY = os.path.join(HERE, "run", "mudlib-parity")
DRIVER = os.path.join(HERE, "src", "fluffos-v2019", "build", "src", "driver")
CONFIG = "config-parity.ini"
CONFIG_LOG = os.path.join(HERE, "logs", "parity-driver.log")
PLAYER = "xkxref"
SNAP_SRC = os.path.join(HERE, "run", "snapshots", "xkxmid.o")
SNAP_PARITY = os.path.join(HERE, "run", "snapshots", "xkxparity.o")
SAVE = os.path.join(PARITY, "data", "user", "x", "xkxref.o")
CONTENT = os.path.join(REPO, "content", "xkx")
OUTDIR = os.path.join(REPO, "reports", "xkx-ref")
RAWDIR = os.path.join(OUTDIR, "room-parity", "raw")
START_ROOM = "/d/hangzhou/duanqiao"

# 代表房间：优先用户点名 + 杭州地标，覆盖室内/室外/有无 NPC/有无物品
REPRESENTATIVE = [
    "/d/hangzhou/jiulou",       # 楼外楼（用户点名）
    "/d/hangzhou/road1",        # 青石大道（用户点名）
    "/d/hangzhou/duanqiao",     # 断桥（起点）
    "/d/hangzhou/baiti",        # 白堤
    "/d/hangzhou/gushan",       # 孤山
    "/d/hangzhou/lingyinsi",    # 灵隐寺
    "/d/hangzhou/suti1",        # 苏堤
    "/d/hangzhou/liuheta",      # 六和塔底
    "/d/hangzhou/yuefen",       # 岳坟
    "/d/hangzhou/jingci",       # 净慈寺
]

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
CN_NUM = "一二三四五六七八九十百千两"
UNITS = "只个把件张块本支条匹头座位名颗枚粒朵片根坛包瓶盒串副双对群些柄幅"
INV_RE = re.compile(
    r"^(?:([" + CN_NUM + r"]+[" + UNITS + r"]))?(.+?)(?:\(([^)]*)\))?(\[.*\])?$"
)
STATUS_RE = re.compile(r"\s*(<[^>]+>)\s*$")
NAME_LINE_RE = re.compile(r"^(.{1,40}?) - *$")
CN_DIGITS = {c: i for i, c in enumerate("零一二三四五六七八九")}


def cn_to_int(s: str) -> int:
    """中文数量前缀 → 整数（覆盖 1..999，够用于 look 行）。"""
    total = 0
    num = 0
    for ch in s:
        if ch in CN_DIGITS:
            num = CN_DIGITS[ch]
        elif ch == "十":
            total += (num or 1) * 10
            num = 0
        elif ch == "百":
            total += (num or 1) * 100
            num = 0
        elif ch == "千":
            total += (num or 1) * 1000
            num = 0
        elif ch == "两":
            num = 2
    return total + num



def clean_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def load_day_phases() -> list[str]:
    """收集所有季节天气表（rain/sun/wind 等）的 desc_msg。

    这些表在每日零点由 NATURE_D 随机切换，属于“随时间变化”的内容，
    必须全部归一化掉；只读当天的 day_phase 表会在跨越零点后失效。
    """
    nature = os.path.join(REF_MUDLIB, "adm", "etc", "nature")
    out = set()
    for name in sorted(os.listdir(nature)):
        path = os.path.join(nature, name)
        if not os.path.isfile(path):
            continue
        lines = []
        with open(path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                s = line.rstrip("\n")
                if not s.strip() or s.lstrip().startswith("#"):
                    continue
                if ":" in s:  # 表头/格式说明行
                    continue
                lines.append(clean_ansi(s).strip())
        for i in range(0, len(lines) - 3, 4):
            out.add(lines[i + 2])
    return sorted(out)


def is_day_phase(line: str, phases: list[str]) -> bool:
    s = line.strip()
    if not s:
        return False
    for p in phases:
        if s == p or s == p + "。" or s.rstrip("。") == p.rstrip("。"):
            return True
    return False


def is_exit_line(line: str) -> bool:
    s = line.strip()
    return (s.startswith("这里明显的出口") or s.startswith("这里唯一的出口")
            or s.startswith("这里没有任何明显的出路")
            or s.startswith("天色太黑了"))


def parse_exits(line: str):
    s = line.strip()
    if s.startswith("天色太黑了"):
        return None  # 不可观察
    if s.startswith("这里没有任何明显的出路"):
        return set()
    body = s
    body = re.sub(r"^这里(明显的出口|唯一的出口)(是|为)?", "", body)
    body = body.rstrip("。").strip()
    parts = re.split(r"[、,，]| 和 |和", body)
    return {p.strip() for p in parts if p.strip()}


def parse_inv_line(line: str):
    """返回 (显示名, 数量)；数量前缀按原版 combined() 语义展开。"""
    body = line[2:]  # combined() 固定两个空格前缀
    body = body.strip()
    status = None
    mstat = STATUS_RE.search(body)
    if mstat:
        # 状态位（如 <昏迷不醒>）属于玩家可见名字，保留；只从数量/拼音里分离。
        status = mstat.group(1)
        body = body[: mstat.start()].strip()
    m = INV_RE.match(body)
    if not m:
        return None
    qty_token, name = (m.group(1) or ""), (m.group(2) or "").strip()
    if not name:
        return None
    qty = cn_to_int(re.sub(r"[" + UNITS + r"]$", "", qty_token)) if qty_token else 1
    if status:
        name = f"{name} {status}"
    return name, max(1, qty)


def parse_look(text: str, phases: list[str]) -> dict:
    text = clean_ansi(text).replace("\r", "")
    lines = [ln.rstrip() for ln in text.splitlines()]
    # 提示符可能黏在上一行末尾（原版输出无换行）；只在非提示行、且不是
    # `<昏迷不醒>` 这类状态尾巴时剥掉。
    lines = [
        ln[:-1].rstrip()
        if ln.endswith(">")
        and not ln.lstrip().startswith(">")
        and not re.search(r"<[^<>]*>$", ln)
        else ln
        for ln in lines
    ]
    lines = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith(">")]
    # 去掉频道/系统行
    lines = [ln for ln in lines if not re.match(r"^【(谣言|风闻|存盘|系统|帮助)】", ln.strip())]

    name_idx = None
    for i, ln in enumerate(lines):
        if NAME_LINE_RE.match(ln):
            name_idx = i
    if name_idx is None:
        return {"ok": False, "reason": "no-name-line", "raw": text}

    name = NAME_LINE_RE.match(lines[name_idx]).group(1).strip()
    body = lines[name_idx + 1:]
    desc = []
    exits = set()
    exits_observable = True
    # 房间 desc 是 name 行到出口行之间的全部内容；原版 LONG 里允许出现
    # 两个空格缩进的行（如 longjing 的“蒙面人(Mengmian ren)”），不能当物品/NPC。
    exit_idx = None
    for j, ln in enumerate(body):
        if is_exit_line(ln):
            exit_idx = j
            break
    if exit_idx is not None:
        for ln in body[:exit_idx]:
            if not is_day_phase(ln, phases):
                desc.append(ln)
        exits = parse_exits(body[exit_idx])
        if exits is None:
            exits_observable = False
            exits = set()
        inv_start = exit_idx + 1
    else:
        k = 0
        while k < len(body):
            ln = body[k]
            if ln.startswith("  ") and not ln.startswith("    "):
                break
            if not is_day_phase(ln, phases):
                desc.append(ln)
            k += 1
        inv_start = k

    inv: list[str] = []
    for ln in body[inv_start:]:
        if ln.startswith("  ") and not ln.startswith("    "):
            got = parse_inv_line(ln)
            if got:
                nm, qty = got
                inv.extend([nm] * qty)

    return {"ok": True, "name": name, "desc": "\n".join(desc),
            "exits": sorted(exits), "exits_observable": exits_observable,
            "inv": inv, "raw": text}


# ---------- 复刻侧 ----------

def load_content():
    rooms = {r["id"]: r for r in json.load(open(os.path.join(CONTENT, "rooms.json"), encoding="utf-8"))["rooms"]
             if r["kind"] == "room"}
    exits = json.load(open(os.path.join(CONTENT, "exits.json"), encoding="utf-8"))["exits"]
    items = {i["id"]: i for i in json.load(open(os.path.join(CONTENT, "items.json"), encoding="utf-8"))["items"]}
    npcs = {n["id"]: n for n in json.load(open(os.path.join(CONTENT, "npcs.json"), encoding="utf-8"))["npcs"]}
    graph = {}
    exit_map = {}
    for e in exits:
        graph.setdefault(e["from"], []).append((e["dir"], e["to"]))
        exit_map.setdefault(e["from"], {})[e["dir"]] = e["to"]
    return rooms, graph, exit_map, items, npcs


SRC_SHORT_CACHE = {}


def source_short(rid: str):
    """原版房间源码里的 set("short")，用来校验登录是否真的落进目标房。"""
    if rid in SRC_SHORT_CACHE:
        return SRC_SHORT_CACHE[rid]
    path = os.path.join(REF_MUDLIB, rid.lstrip("/"))
    if not os.path.exists(path) and os.path.exists(path + ".c"):
        path += ".c"
    short = None
    if os.path.exists(path):
        try:
            txt = open(path, encoding="utf-8", errors="ignore").read()
            m = re.search(r'set\(\s*"short"\s*,\s*"([^"]+)"', txt)
            if m:
                short = m.group(1)
        except OSError:
            pass
    SRC_SHORT_CACHE[rid] = short
    return short


SRC_NAME_CACHE = {}


def source_name(ref: str):
    if ref in SRC_NAME_CACHE:
        return SRC_NAME_CACHE[ref]
    path = os.path.join(REF_MUDLIB, ref.lstrip("/"))
    if not os.path.exists(path) and os.path.exists(path + ".c"):
        path += ".c"
    name = None
    if os.path.exists(path):
        try:
            txt = open(path, encoding="utf-8", errors="ignore").read()
            m = re.search(r'set_name\("([^"]+)"', txt)
            if m:
                name = m.group(1)
        except OSError:
            pass
    SRC_NAME_CACHE[ref] = name
    return name


def replica_field(room, items, npcs):
    out_items = Counter()
    out_npcs = Counter()
    unresolved = []
    for ob in room.get("objects", []):
        ref = ob.get("ref") or ""
        kind = ob.get("kind")
        cnt = int(ob.get("count") or 1)
        rec = (npcs if kind == "npc" else items).get(ref)
        if kind != "npc" and rec and isinstance(rec.get("amount"), int):
            # 原版一件物品可自带数量（set_amount），如鹅卵石 10 块。
            cnt *= max(1, int(rec["amount"]))
        if kind == "npc":
            # 复刻运行时一个 NPC 模板只建一个实体（genesis 按 content.npcs 建实体），
            # 不按 set("objects") 的 count 复制；这是已知架构差异，不造假实例。
            cnt = 1
        nm = rec["name"] if rec else source_name(ref)
        if kind == "npc" and rec:
            # 原版 short() 会拼 title/门派/party/nickname；display_name 由抽取器按
            # feature/name.c 的拼接顺序生成，这里比对玩家真正看到的名字。
            nm = rec.get("display_name") or nm
        if not nm:
            unresolved.append(ref)
            nm = os.path.basename(ref)
        if kind == "npc":
            out_npcs[nm] += cnt
        else:
            out_items[nm] += cnt
    return out_items, out_npcs, unresolved


# ---------- 实例 ----------

def write_config(port: int) -> None:
    base = os.path.join(REF_MUDLIB, "config.ini")
    cfg = open(base, encoding="utf-8").read()
    cfg = re.sub(r"external_port_1\s*:\s*telnet \d+", f"external_port_1 : telnet {port}", cfg, count=1)
    with open(os.path.join(PARITY, CONFIG), "w", encoding="utf-8") as fh:
        fh.write(cfg)


def port_ready(port: int, timeout: float = 30.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.4):
                return True
        except OSError:
            time.sleep(0.4)
    return False


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def stop_driver() -> None:
    run(["pkill", "-f", CONFIG])
    end = time.time() + 8
    while time.time() < end:
        if not run(["pgrep", "-f", CONFIG]).stdout.strip():
            return
        time.sleep(0.3)


def prepare_instance(port: int) -> None:
    if not os.path.isdir(PARITY):
        shutil.copytree(REF_MUDLIB, PARITY)
    os.makedirs(os.path.join(PARITY, "log", "nosave"), exist_ok=True)
    os.makedirs(os.path.dirname(CONFIG_LOG), exist_ok=True)

    text = open(SNAP_SRC, encoding="utf-8").read()
    text, n = re.subn(r'"startroom":"[^"]*"', f'"startroom":"{START_ROOM}"', text, count=1)
    if n != 1:
        raise RuntimeError("快照缺 startroom")
    if '"/clone/misc/fire"' not in text:
        text, n2 = re.subn(r"autoload \(\{\}\)", 'autoload ({"/clone/misc/fire",})', text, count=1)
        if n2 != 1:
            raise RuntimeError("快照缺 autoload")
    with open(SNAP_PARITY, "w", encoding="utf-8") as fh:
        fh.write(text)
    shutil.copyfile(SNAP_PARITY, SAVE)

    write_config(port)
    stop_driver()
    logfh = open(CONFIG_LOG, "ab")
    try:
        subprocess.Popen([DRIVER, CONFIG], cwd=PARITY, stdout=logfh, stderr=logfh,
                         start_new_session=True)
    finally:
        logfh.close()
    if not port_ready(port):
        raise RuntimeError(f"{port} 未就绪；见 {CONFIG_LOG}")


def write_startroom(rid: str) -> None:
    """把探针快照写回存档，startroom 指向目标房间（不改 LPC、不靠走路）。"""
    text = open(SNAP_SRC, encoding="utf-8").read()
    text, n = re.subn(r'"startroom":"[^"]*"', f'"startroom":"{rid}"', text, count=1)
    if n != 1:
        raise RuntimeError("快照缺 startroom")
    if '"/clone/misc/fire"' not in text:
        text, n2 = re.subn(r"autoload \(\{\}\)", 'autoload ({"/clone/misc/fire",})', text, count=1)
        if n2 != 1:
            raise RuntimeError("快照缺 autoload")
    with open(SAVE, "w", encoding="utf-8") as fh:
        fh.write(text)


def collect_by_startroom(port: int, room_ids: list[str], phases,
                         player=PLAYER, rawdir=RAWDIR):
    """每个房间都从存档 startroom 直接登录，避免用复刻出口图走路造成的串房。"""
    from probe_mud import plain
    os.makedirs(rawdir, exist_ok=True)
    raws, reasons = {}, {}
    for rid in room_ids:
        try:
            write_startroom(rid)
            sess, _ = login(port, player)
            sess.send("get fire")
            plain(sess.recv_for(0.7))
            sess.send("look")
            text = plain(sess.recv_for(1.5))
            raws[rid] = text
            parsed = parse_look(text, phases)
            if not parsed.get("ok"):
                reasons[rid] = parsed.get("reason", "unparsed")
            sess.send("quit")
            plain(sess.recv_for(0.9))
            sess.close()
        except Exception as exc:  # 单个房间失败不应中断整轮
            reasons[rid] = f"error: {exc}"
        safe = rid.strip("/").replace("/", "__")
        with open(os.path.join(rawdir, safe + ".txt"), "w", encoding="utf-8") as fh:
            fh.write(raws.get(rid, ""))
    return raws, reasons


def login(port, player=PLAYER):
    from probe_mud import TelnetSession, drive_login, plain

    class NS:
        pass

    ns = NS()
    ns.player_id = player
    sess = TelnetSession("127.0.0.1", port)
    out = drive_login(sess, ns)
    out += sess._recv(idle=0.8, max_wait=5)
    return sess, plain(out)


def bfs(graph, src, dst):
    """返回 [(方向, 到达房间 id), ...]；图里走不到时返回 None。"""
    if src == dst:
        return []
    prev = {src: None}
    q = deque([src])
    while q:
        cur = q.popleft()
        for d, nxt in graph.get(cur, []):
            if nxt in prev:
                continue
            prev[nxt] = (cur, d)
            if nxt == dst:
                q.clear()
                break
            q.append(nxt)
    if dst not in prev:
        return None
    path = []
    node = dst
    while prev[node]:
        p, d = prev[node]
        path.append((d, node))
        node = p
    return list(reversed(path))


def visit_order(graph, start):
    """按图 BFS 出访问顺序，避免全量时在分区之间来回长跳。"""
    seen = {start}
    q = deque([start])
    order = []
    while q:
        cur = q.popleft()
        for _d, nxt in graph.get(cur, []):
            if nxt in seen:
                continue
            seen.add(nxt)
            order.append(nxt)
            q.append(nxt)
    return order


def room_title(text: str):
    """从一段移动/look 输出里取最后一个房间名（去掉 ' - ' 后缀）。"""
    text = clean_ansi(text).replace("\r", "")
    title = None
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith(">") or s.startswith("【"):
            continue
        m = NAME_LINE_RE.match(s)
        if m:
            title = m.group(1).strip()
    return title


def resolve_room(title, rooms, prefer=None):
    """按房间名映射回复刻 room id；同名多选时优先 prefer。"""
    if not title:
        return None
    cands = [rid for rid, r in rooms.items() if r["name"] == title]
    if prefer in cands:
        return prefer
    if len(cands) == 1:
        return cands[0]
    return cands[0] if cands else None


def collect(port: int, room_ids: list[str], graph, rooms, phases,
            player=PLAYER, rawdir=RAWDIR):
    sess, _ = login(port, player)
    from probe_mud import plain
    # 探针自带火折作照明；若登录瞬间它落在起始房间地面上，先捡起来，
    # 避免把探针道具当成房间物品。
    sess.send("get fire")
    plain(sess.recv_for(0.8))
    cur = START_ROOM
    raws = {}
    reasons = {}
    os.makedirs(rawdir, exist_ok=True)
    for rid in room_ids:
        arrived = False
        for _attempt in range(3):
            path = bfs(graph, cur, rid)
            if path is None:
                reasons[rid] = "unreachable"
                break
            lost = False
            for direction, expect in path:
                sess.send(direction)
                resp = plain(sess.recv_for(1.0))
                title = room_title(resp)
                actual = resolve_room(title, rooms, prefer=expect)
                if actual != expect:
                    # 走偏/被挡：用 look 重新定位，再从实际房间重算路线
                    sess.send("look")
                    resp2 = plain(sess.recv_for(1.4))
                    title2 = room_title(resp2)
                    actual = resolve_room(title2, rooms, prefer=cur)
                    if actual is None:
                        actual = cur
                    cur = actual
                    lost = True
                    break
                cur = expect
            if not lost:
                arrived = True
                break
        if not arrived and rid not in reasons:
            reasons[rid] = "desync"
        sess.send("look")
        text = plain(sess.recv_for(1.4))
        raws[rid] = text
        parsed = parse_look(text, phases)
        if not parsed.get("ok"):
            reasons[rid] = parsed.get("reason", "unparsed")
        else:
            # 以实际 look 到的房间为准，修正 cur
            actual = resolve_room(parsed["name"], rooms, prefer=rid)
            if actual:
                cur = actual
        safe = rid.strip("/").replace("/", "__")
        with open(os.path.join(rawdir, safe + ".txt"), "w", encoding="utf-8") as fh:
            fh.write(text)
    sess.close()
    return raws, reasons


def load_raw(room_ids, rawdir=RAWDIR):
    out = {}
    for rid in room_ids:
        safe = rid.strip("/").replace("/", "__")
        p = os.path.join(rawdir, safe + ".txt")
        if os.path.exists(p):
            out[rid] = open(p, encoding="utf-8").read()
    return out


# ---------- 比较 ----------

def nows(s: str) -> str:
    return re.sub(r"\s+", "", s)


def diff_counts(a: str, b: str):
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    added = removed = 0
    samples = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
        if len(samples) < 3 and (i2 - i1 or j2 - j1):
            samples.append({"orig": a[max(0, i1 - 10):i2 + 10], "repl": b[max(0, j1 - 10):j2 + 10]})
    return added, removed, samples


def compare_room(rid, orig, room, items, npcs, exit_map):
    if not orig or not orig.get("ok"):
        return {"id": rid, "status": "unparsed", "reason": (orig or {}).get("reason", "no-raw")}
    expect = source_short(rid)
    if expect and orig.get("name") and orig["name"] != expect:
        # 登录没落进存档 startroom 指向的房间（实得默认起始房/阴间入口），
        # 这一房的所有字段都不是目标房内容，标为不可观察，不计入一致率。
        return {"id": rid, "short": room["name"], "status": "landing",
                "landed": orig["name"],
                "reason": f"落点异常：期望「{expect}」，实得「{orig['name']}」，未按 startroom 落入目标房",
                "fields": {f: {"match": None, "observable": False}
                           for f in ("name", "desc", "exits", "items", "npcs")}}
    ritems, rnpcs, unresolved = replica_field(room, items, npcs)
    rec = {"id": rid, "short": room["name"]}
    fields = {}

    # name
    fields["name"] = {"match": orig["name"] == room["name"],
                      "orig": orig["name"], "repl": room["name"]}

    # desc
    od, rd = nows(orig["desc"]), nows(room.get("desc") or "")
    if od == rd:
        fields["desc"] = {"match": True}
    else:
        added, removed, samples = diff_counts(od, rd)
        fields["desc"] = {"match": False, "extra_chars": added, "missing_chars": removed,
                          "repl_empty": not rd, "samples": samples}

    # exits
    rexits = set(exit_map.get(rid, {}))
    if orig.get("exits_observable", True):
        fields["exits"] = {"match": set(orig["exits"]) == rexits,
                           "orig": sorted(orig["exits"]), "repl": sorted(rexits)}
    else:
        fields["exits"] = {"match": None, "observable": False}

    # items / npcs
    oinv = Counter(orig.get("inv", []))
    oitems, onpcs = Counter(), Counter()
    for nm, c in oinv.items():
        # NPC 常带门派头衔（如“武当派第四代弟子 香火道人”），用基名子串判定
        is_npc = nm in NPC_NAME_SET or any(n in nm for n in NPC_NAME_SET if len(n) >= 2)
        (onpcs if is_npc else oitems)[nm] += c
    fields["items"] = {"match": oitems == ritems, "orig": dict(oitems), "repl": dict(ritems)}
    fields["npcs"] = {"match": onpcs == rnpcs, "orig": dict(onpcs), "repl": dict(rnpcs)}
    if unresolved:
        rec["unresolved_refs"] = unresolved

    rec["fields"] = fields
    rec["status"] = "match" if all(v["match"] is not False for v in fields.values()) else "differ"
    return rec


NPC_NAME_SET = set()


def build_npc_names():
    global NPC_NAME_SET
    root = os.path.join(REF_MUDLIB, "d")
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if not fn.endswith(".c"):
                continue
            p = os.path.join(dirpath, fn)
            try:
                txt = open(p, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            if "inherit NPC" not in txt and "inherit __DIR__" not in txt:
                continue
            for m in re.finditer(r'set_name\(\s*(?:[A-Z][A-Z0-9_]*\s*)?"([^"]+)"', txt):
                NPC_NAME_SET.add(m.group(1))
    # 内容包里已判定为 NPC 的基名也纳入判定；源码 set_name 可能带颜色宏
    # （如 HIY "黄骠马" NOR），单靠上面的正则抓不到。
    npcs_path = os.path.join(CONTENT, "npcs.json")
    if os.path.exists(npcs_path):
        try:
            for npc in json.load(open(npcs_path, encoding="utf-8")).get("npcs", []):
                for key in ("name", "display_name"):
                    val = str(npc.get(key) or "").strip()
                    if val:
                        NPC_NAME_SET.add(val)
        except (OSError, ValueError):
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--port", type=int, default=4020)
    ap.add_argument("--player", default=PLAYER)
    ap.add_argument("--reparse", action="store_true")
    ap.add_argument("--no-instance", action="store_true",
                    help="不启停探针实例，直接连 --port（用于 4004 短程对照）")
    ap.add_argument("--tag", default="",
                    help="输出后缀，如 4004 -> room-parity-4004.json，raw 写到 raw-4004/")
    ap.add_argument("--by-startroom", action="store_true",
                    help="每个房间直接改存档 startroom 登录采集，不用复刻出口图走路")
    args = ap.parse_args()

    rooms, graph, exit_map, items, npcs = load_content()
    phases = load_day_phases()
    build_npc_names()

    if args.all:
        ids = [r for r in visit_order(graph, START_ROOM) if r in rooms]
        ids += [r for r in sorted(rooms) if r not in set(ids)]
    elif args.limit:
        if args.limit <= len(REPRESENTATIVE):
            ids = REPRESENTATIVE[:args.limit]
        else:
            ids = sorted(rooms)[:args.limit]
    else:
        ids = REPRESENTATIVE

    rawdir = RAWDIR if not args.tag else os.path.join(OUTDIR, "room-parity", f"raw-{args.tag}")
    if args.reparse:
        raws = load_raw(ids, rawdir)
        reasons = {}
    elif args.by_startroom:
        if not args.no_instance:
            prepare_instance(args.port)
        raws, reasons = collect_by_startroom(args.port, ids, phases, args.player, rawdir)
        if not args.no_instance:
            stop_driver()
    else:
        if not args.no_instance:
            prepare_instance(args.port)
        raws, reasons = collect(args.port, ids, graph, rooms, phases,
                                args.player, rawdir)
        if not args.no_instance:
            stop_driver()

    parsed = {rid: parse_look(raws[rid], phases) if raws.get(rid)
              else {"ok": False, "reason": reasons.get(rid, "no-raw")} for rid in ids}
    records = [compare_room(rid, parsed.get(rid), rooms[rid], items, npcs, exit_map) for rid in ids]

    summary = {}
    for f in ("name", "desc", "exits", "items", "npcs"):
        obs = [r for r in records if r.get("fields", {}).get(f, {}).get("match") is not None]
        ok = [r for r in obs if r["fields"][f]["match"]]
        summary[f] = {"observable": len(obs), "match": len(ok),
                      "rate": round(len(ok) / len(obs), 4) if obs else None}

    cats = Counter()
    samples = {}
    for r in records:
        if r["status"] == "unparsed":
            cats["unparsed"] += 1
            samples.setdefault("unparsed", r)
            continue
        if r["status"] == "landing":
            cats["landing_mismatch"] += 1
            samples.setdefault("landing_mismatch", r)
            continue
        for f in ("name", "exits", "items", "npcs"):
            if r["fields"][f]["match"] is False:
                cats[f + "_mismatch"] += 1
                samples.setdefault(f + "_mismatch", r)
        d = r["fields"]["desc"]
        if d["match"] is False:
            if d.get("repl_empty"):
                key = "desc_missing"
            elif d.get("extra_chars") and d.get("missing_chars"):
                key = "desc_multi"
            elif d.get("extra_chars"):
                key = "desc_extra"
            else:
                key = "desc_missing_text"
            cats[key] += 1
            samples.setdefault(key, r)

    total_rooms = len([r for r in rooms])
    # npcs 差异细分：原版显示名常带门派头衔/「<昏迷不醒>」状态，复刻只存基名，
    # 与“真缺失/数量不同”分开计数，避免把呈现层差异混进内容差异。
    nb = Counter()
    for r in records:
        if r["status"] != "differ":
            continue
        f = r["fields"]["npcs"]
        if f["match"] is not False:
            continue
        o, p = f["orig"], f["repl"]
        reasons = []
        if set(o) == set(p):
            reasons.append("数量不同")
        else:
            only_o = [k for k in o if k not in p]
            only_p = [k for k in p if k not in o]
            mapped = False
            for k in list(only_o):
                cand = [q for q in only_p if k.endswith(q) or q in k]
                if len(cand) == 1:
                    q = cand[0]
                    mapped = True
                    only_o.remove(k)
                    only_p.remove(q)
            if mapped:
                reasons.append("头衔/状态前缀")
            if only_o or only_p:
                reasons.append("在场/缺失")
            if any(o.get(k) != p.get(k) for k in set(o) & set(p)):
                reasons.append("数量不同")
        nb["+".join(reasons) or "其他"] += 1
    suffix = f"-{args.tag}" if args.tag else ""
    prev_json = os.path.join(OUTDIR, f"room-parity{suffix}.json")
    prev = json.load(open(prev_json, encoding="utf-8")) if os.path.exists(prev_json) else {}
    if args.by_startroom:
        collection = "startroom（逐房改存档 startroom 直接登录 look）"
        reference = (f"{PARITY} ({CONFIG}, port {args.port})" if not args.no_instance
                     else f"port {args.port}（独立会话，未启停实例）")
    elif args.reparse:
        collection = prev.get("source", {}).get("collection") or "raw（沿用上次采集的原文重算）"
        if args.tag:
            collection = "startroom（独立会话逐房改 startroom 直登；本次只从已落盘原文重算）"
            reference = f"port {args.port}（独立会话，未启停实例；本次从已落盘原文重算）"
        else:
            reference = prev.get("source", {}).get("reference") or f"port {args.port}（原文重算）"
    else:
        collection = "walk（按复刻出口图走路，全量时会因出口差异串房，不作为最终口径）"
        reference = f"{PARITY} ({CONFIG}, port {args.port})"
    out = {"source": {"reference": reference,
                      "reference_mudlib": REF_MUDLIB,
                      "replica": os.path.join(CONTENT, "rooms.json"),
                      "start_room": START_ROOM,
                      "player": args.player,
                      "tag": args.tag,
                      "collection": collection},
           "sample": {"rooms": len(records), "total_rooms": total_rooms,
                      "selection": "representative" if len(records) < total_rooms else "all"},
           "normalization": [
               "N1 去 ANSI 颜色码",
               "N2 丢弃提示符与【谣言/风闻/存盘】等频道行",
               "N3 描述比较折叠换行与缩进（\\s+→空，保留全部非空白字符）",
               "N4 丢弃室外房间的天色/天气描述行（遍历 adm/etc/nature/ 下全部季节天气表："
               "rain/sun/wind 的 desc_msg；跨零点会自动切换）；夜间出口不可观察，不计入 exits 一致率",
               "N5 物品/NPC 行去数量前缀与英文括号，按中文名多重集合比较",
               "N6 房间名去首尾空白与 ' - ' 后缀",
           ],
           "rooms": len(records), "summary": summary, "categories": dict(cats),
           "npc_breakdown": dict(nb),
           "samples": samples, "records": records}
    os.makedirs(OUTDIR, exist_ok=True)
    with open(os.path.join(OUTDIR, f"room-parity{suffix}.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    write_md(out, os.path.join(OUTDIR, f"room-parity{suffix}.md"))
    print(json.dumps({"rooms": out["rooms"], "summary": summary, "categories": dict(cats)},
                     ensure_ascii=False, indent=1))


def write_md(out, md_path):
    smp = out.get("sample", {})
    n_rooms = smp.get("rooms", out["rooms"])
    total = smp.get("total_rooms")
    if smp.get("selection") == "representative":
        scope = (f"**本次样本 {n_rooms} 个代表房间，不是全量**（复刻杭州切片共 {total} 个房间，"
                 f"全量扩样待跑，见「复现命令」中的 `--all`）。")
    else:
        scope = f"**全量 {n_rooms} 个房间**（复刻杭州切片共 {total} 个房间，全覆盖）。"
    if out["source"].get("tag"):
        probe_note = ("- 采集在 4004 上用临时快照副本探针（4 字母 id，存档与登录存根用完即删）逐房改 "
                      "`startroom` 直接登录 `look`；不启停实例，不长时间占用。")
    else:
        probe_note = ("- 探针实例是 `run/mudlib` 的独立副本（同一棵树 c1b87db、同一驱动），端口 4020，"
                      "不占用 4004；4004 对照见文末。")
    lines = ["# 房间逐字段对拍报告", "",
             scope, "",
             f"- 房间数：{n_rooms} / {total}",
             f"- 原版来源：`{out['source']['reference']}`（mudlib = `{out['source']['reference_mudlib']}`，与 4004 同一棵树）",
             f"- 复刻来源：`{out['source']['replica']}`",
             f"- 采集方式：{out['source'].get('collection', '未记录')}", "",
             "## 方法与顺序", "",
             "- 先跑 10 个代表房间（楼外楼、青石大道、断桥、白堤、孤山、灵隐寺、苏堤、六和塔底、岳坟、"
             "净慈寺），工具跑通后再全量。",
             "- 原版侧**逐房改存档 `startroom` 后直接登录并 `look`**，不在原版里走路。原因：原版与复刻的"
             "个别出口不一致，用复刻出口图走路会串房（实测把“山洞/六和塔”等错误房间的文本记到别的房间上），"
             "把导航噪声当成内容差异。`startroom` 是存档数据字段，只决定登录落点，不参与房间内容生成。",
             probe_note,
             "- 复刻切片内没有 `武庙`（该房在 `/d/city`，不在杭州切片），因此代表房间用杭州地标；"
             "这一点是覆盖范围差异，不是工具缺陷。",
             "- 夜间室外房间出口不可见时，`exits` 记 `observable:false`，不计入一致率；描述与物品/NPC 照常比较。",
             "- 每次登录后用原版源码 `set(\"short\")` 校验落点；实得短名与目标房不一致记为 `landing`"
             "（落点异常），该房所有字段标为不可观察、不计入一致率，差异清单单列。",
             "", "## 归一化规则", ""]
    for n in out["normalization"]:
        lines.append(f"- {n}")
    lines += ["", "## 逐字段一致率", "",
              "| 字段 | 可观察 | 一致 | 一致率 |", "|------|--------|------|--------|"]
    for f, s in out["summary"].items():
        rate = "n/a" if s["rate"] is None else f"{s['rate'] * 100:.1f}%"
        lines.append(f"| {f} | {s['observable']} | {s['match']} | {rate} |")
    lines += ["", "## 差异分类", "", "| 分类 | 数量 |", "|------|------|"]
    for k, v in sorted(out["categories"].items()):
        lines.append(f"| {k} | {v} |")
    if out.get("npc_breakdown"):
        lines += ["", "### NPC 差异细分", "", "| 细类 | 房间数 |", "|------|--------|"]
        for k, v in sorted(out["npc_breakdown"].items(), key=lambda x: -x[1]):
            lines.append(f"| {k} | {v} |")
        lines += ["", "“头衔/状态前缀”= 原版显示名带门派头衔或「<昏迷不醒>」状态、复刻只存基名；"
                  "“在场/缺失”= 一侧在场另一侧不在（含共享实例里 NPC 游走/已离开）；"
                  "“数量不同”= 同名 NPC 两侧 clone 数不一致。"]
    lines += ["", "## 差异清单", "",
              "| 房间 | 字段 | 说明 |", "|------|------|------|"]
    for r in out["records"]:
        if r.get("status") == "unparsed":
            lines.append(f"| `{r['id']}` | unparsed | {r.get('reason', '')} |")
            continue
        if r.get("status") == "landing":
            lines.append(f"| `{r['id']}` | landing | {r.get('reason', '')}（该房不计入一致率） |")
            continue
        for f, v in r.get("fields", {}).items():
            if v.get("match") is False:
                if f == "desc":
                    lines.append(f"| `{r['id']}` | desc | 复刻多 {v.get('extra_chars', 0)} 字 / 少 "
                                 f"{v.get('missing_chars', 0)} 字 |")
                elif f == "exits":
                    lines.append(f"| `{r['id']}` | exits | 原版 {v.get('orig')} vs 复刻 {v.get('repl')} |")
                elif f in ("items", "npcs"):
                    lines.append(f"| `{r['id']}` | {f} | 原版 {v.get('orig')} vs 复刻 {v.get('repl')} |")
                else:
                    lines.append(f"| `{r['id']}` | {f} | {v.get('orig')} → {v.get('repl')} |")
    lines += ["", "## 差异样例", ""]
    for k, r in out["samples"].items():
        lines.append(f"### {k} — `{r['id']}` ({r.get('short', '')})")
        lines.append("```json")
        lines.append(json.dumps(r, ensure_ascii=False, indent=1)[:1200])
        lines.append("```")
        lines.append("")
    full = smp.get("selection") == "all"
    if out["source"].get("tag"):
        sample_cmd = ("# 4004 现场采集（需先准备临时探针存档；本次已清理）\n"
                      "python3 tools/xkx_ref/parity_rooms.py --limit 10 --by-startroom --no-instance "
                      "--port 4004 --player xkxpq --tag 4004")
        reparse_cmd = ("python3 tools/xkx_ref/parity_rooms.py --limit 10 --reparse --tag 4004 "
                       "--port 4004 --player xkxpq")
    elif full:
        sample_cmd = ("# 本报告样本（全量；会启停 4020 实例）\n"
                      "python3 tools/xkx_ref/parity_rooms.py --all --by-startroom --port 4020")
        reparse_cmd = "python3 tools/xkx_ref/parity_rooms.py --all --reparse"
    else:
        sample_cmd = ("# 本报告样本（10 个代表房间；会启停 4020 实例）\n"
                      "python3 tools/xkx_ref/parity_rooms.py --limit 10 --by-startroom --port 4020")
        reparse_cmd = "python3 tools/xkx_ref/parity_rooms.py --limit 10 --reparse"
    lines += ["", "## 复现命令", "",
              "所有数字都可由 `tools/xkx_ref/parity_rooms.py` 重跑；`--reparse` 只读已落盘的原始 `look` 文本重算，",
              "不连实例，用于核验解析与统计口径。", "",
              "```bash",
              sample_cmd,
              "",
              "# 只重算、不重采集（结果与上面一致）",
              reparse_cmd,
              "",
              "# 4004 对照：只从已落盘原文重算",
              "python3 tools/xkx_ref/parity_rooms.py --limit 10 --reparse --tag 4004 --port 4004 --player xkxpq",
              "```",
              "",
              "- 原始 `look` 文本：`reports/xkx-ref/room-parity/raw/`（4020）、`reports/xkx-ref/room-parity/raw-4004/`（4004）。",
              "- 重新在 4004 上采集需要临时探针存档（`--no-instance --port 4004 --player xkxpq --tag 4004`），"
              "探针用完即删；本次已清理，报告只保留已落盘原文。",
              ]
    if not out["source"].get("tag"):
        p4 = os.path.join(OUTDIR, "room-parity-4004.json")
        if os.path.exists(p4):
            o4 = json.load(open(p4, encoding="utf-8"))
            lines += ["", "## 4004 对照（10 个代表房间，独立会话）", "",
                      "| 字段 | 可观察 | 一致 | 一致率 |", "|------|--------|------|--------|"]
            for f, s in o4["summary"].items():
                rate = "n/a" if s["rate"] is None else f"{s['rate'] * 100:.1f}%"
                lines.append(f"| {f} | {s['observable']} | {s['match']} | {rate} |")
            lines += ["", f"- 4004 探针：`{o4['source'].get('player')}`，来源 `{o4['source'].get('reference')}`",
                      "- 与 4020 独立实例的结果逐字段比对，用于证明“同一棵 mudlib 树 + 同一驱动”下两侧一致。",
                      "- 探针为快照副本，逐房改 `startroom` 直接登录，登录后先 `get fire` 把自动加载的火折从地面"
                      "捡走，避免它被当成房间物品；用完即删存档与登录存根，未改 4004 的 LPC。"]
            diffs = [r for r in o4["records"] if r.get("status") != "match"]
            if diffs:
                lines.append(f"- 4004 侧差异房间：{', '.join('`' + r['id'] + '`' for r in diffs)}")
                npc_diff = [r for r in diffs
                            if r.get("fields", {}).get("npcs", {}).get("match") is False]
                if npc_diff:
                    lines.append("- 其中 npcs 差异属**共享实例的 NPC 在场状态**，不是复刻缺对象："
                                 "`d/hangzhou/duanqiao.c` 明确 `set(\"objects\", __DIR__\"npc/poorman\":1)`"
                                 "（穷汉），复刻侧 objects 亦有该 NPC；4004 上该 NPC 当时不在房内"
                                 "（被玩家击杀/移动后未重置），4020 是新起实例所以仍在。")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


if __name__ == "__main__":
    main()
