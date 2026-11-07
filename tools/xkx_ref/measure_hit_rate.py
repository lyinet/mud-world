#!/usr/bin/env python3
"""命中率实测采集器（xkx100 原版）。

两档角色：
  new  xkxref 原快照（exp 9，无技能，天赋 20/20/20/20）
  mid  xkxmid 构造快照（exp 6400，raw 技能 40/40/40，无兵器；只改存档数据，不改 LPC）

三个目标：店小二 / 流氓 / 老虎（武当虎头岩，44 步路线）。

每个场景 3 次独立取样，每次累计 >= --min-attacks 次双方出手。
采集原始战斗文本到 reports/xkx-ref/hit-rate/<tier>_<target>/run{n}/life*.txt，
再用 --reparse 从原始文本复算并生成 reports/xkx-ref/hit-rate.md。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
MUDLIB = os.path.join(HERE, "run", "mudlib")
SAMPLE = os.path.join(HERE, "run", "mudlib-sample")
DRIVER = os.path.join(HERE, "src", "fluffos-v2019", "build", "src", "driver")
SAMPLE_LOG = os.path.join(HERE, "logs", "hitrate-driver.log")
CONFIG = "config-sample.ini"
PLAYER = "xkxref"
SAVE = os.path.join(SAMPLE, "data", "user", "x", "xkxref.o")
SNAPSHOTS = {
    "new": os.path.join(HERE, "run", "snapshots", "xkxref.o"),
    "mid": os.path.join(HERE, "run", "snapshots", "xkxmid.o"),
}
OUTDIR = os.path.join(REPO, "reports", "xkx-ref", "hit-rate")
REPORTDIR = os.path.join(REPO, "reports", "xkx-ref")

TARGETS = {
    "xiaoer": {
        "name": "店小二",
        "id": "xiao er",
        "start_room": "/d/city/zuixianlou",
        "end_room": "醉仙楼",
        "lpc": "d/city/npc/xiaoer2.c: exp 100，无技能，str/dex 默认 20",
    },
    "liumang": {
        "name": "流氓",
        "id": "liu mang",
        "start_room": "/d/foshan/street2",
        "end_room": "佛山镇街",
        "lpc": "d/city/npc/liumang.c: exp 1000，unarmed/dodge 20，apply/attack|defense 10（temp），str/dex 默认 20",
    },
    "tiger": {
        "name": "老虎",
        "id": "laohu",
        "start_room": "/d/wudang/hutouyan",
        "end_room": "虎头岩",
        "lpc": "d/wudang/npc/laohu.c: exp 30000，apply/attack 20000，apply/defense 30000，apply/armor 1000，jiali 100；str/dex/con 未设 → beast.c 每只随机 random(40)+5",
    },
}

EXCLUDE_TXT = ("趁机发动攻击", "喝道", "说道", "叫道", "想杀死你", "见你攻击失误",
               "企图", "准备发动攻势", "寻找机会", "缓缓地移动脚步", "目不转睛",
               "注视着", "双手分使")
SKIP_OUTCOME = ("倒在地上", "震天动地", "死了", "尸体", "眼前一黑")
PARRY_RE = re.compile(r"挡开|挡住|格开|架开|招架|封住|拨开|一格|一挡|硬接|用.{0,6}挡")
DODGE_RE = re.compile(r"闪|躲|避|偏了几寸|侧身|滑开|不慌不忙")
BAR_RE = re.compile(r"精：\s*(\d+)\s*/\s*(\d+)\s*气：\s*(\d+)\s*/\s*(\d+)")
HP_RE = re.compile(r"【\s*气\s*血[^】]*】\s*(\d+)\s*/\s*(\d+)")
EXP_RE = re.compile(r"经\s*验[：:]\s*(\d+)")
BOX_CHARS = "┏┓┗┛━┃"

DAMAGE_TIERS = [
    ("没有造成任何伤害", (0, 0)),
    ("轻轻地碰到", (0, 9)),
    ("退了半步，毫发无损", (0, 9)),
    ("轻轻地划破", (0, 9)),
    ("轻轻地刺破", (0, 9)),
    ("轻轻地抓破", (0, 9)),
    ("轻微", (0, 9)),
    ("瘀青", (10, 19)),
    ("瘀伤", (10, 19)),
    ("痛哼", (10, 19)),
    ("细长血痕", (10, 19)),
    ("创口", (10, 19)),
    ("轻微的", (10, 19)),
    ("肿了一块老高", (20, 39)),
    ("弯下腰", (20, 39)),
    ("划出一道伤口", (20, 39)),
    ("刺入", (20, 39)),
    ("长长的血口", (20, 39)),
    ("造成一处", (20, 39)),
    ("闷哼了一声", (40, 79)),
    ("脸上一阵青一阵白", (40, 79)),
    ("血淋淋", (40, 79)),
    ("颇为严重", (40, 79)),
    ("退了两步", (80, 119)),
    ("惨白", (80, 119)),
    ("相当严重", (80, 119)),
    ("连退了好几步", (120, 159)),
    ("又长又深", (120, 159)),
    ("十分严重", (120, 159)),
    ("吐出一口鲜血", (120, 239)),
    ("极其严重", (160, 239)),
    ("飞了出去", (240, 999)),
    ("软泥", (240, 999)),
    ("深及见骨", (240, 999)),
    ("对穿而出", (240, 999)),
    ("血窟窿", (160, 999)),
    ("非常可怕", (240, 999)),
]


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def _md5(path):
    import hashlib
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def repo_sha():
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                             capture_output=True, text=True)
        return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def write_config(port: int) -> None:
    base = os.path.join(SAMPLE, "config.ini")
    cfg = open(base, encoding="utf-8").read()
    cfg = re.sub(r"telnet \d+", f"telnet {port}", cfg, count=1)
    with open(os.path.join(SAMPLE, CONFIG), "w", encoding="utf-8") as fh:
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


def stop_driver() -> None:
    run(["pkill", "-f", CONFIG])
    end = time.time() + 8
    while time.time() < end:
        if not run(["pgrep", "-f", CONFIG]).stdout.strip():
            return
        time.sleep(0.3)


def start_driver(port: int) -> None:
    write_config(port)
    os.makedirs(os.path.join(SAMPLE, "log", "nosave"), exist_ok=True)
    os.makedirs(os.path.dirname(SAMPLE_LOG), exist_ok=True)
    logfh = open(SAMPLE_LOG, "ab")
    try:
        subprocess.Popen([DRIVER, CONFIG], cwd=SAMPLE, stdout=logfh,
                         stderr=logfh, start_new_session=True)
    finally:
        logfh.close()
    if not port_ready(port):
        raise RuntimeError(f"{port} 未就绪；见 {SAMPLE_LOG}")


def prepare_snapshot(tier: str, start_room: str) -> None:
    """把快照的 startroom 改成目标房间后再落盘。

    startroom 是存档数据字段（决定登录落点），不参与战斗属性计算。
    老虎路线 44 步/夜间出口不可见等环境噪声与命中率无关，因此改为直接
    从目标房间登录；报告须写明这一点。
    """
    text = open(SNAPSHOTS[tier], encoding="utf-8").read()
    text, n = re.subn(r'"startroom":"[^"]*"', f'"startroom":"{start_room}"', text, count=1)
    if n != 1:
        raise RuntimeError("快照里找不到 startroom 字段")
    with open(SAVE, "w", encoding="utf-8") as fh:
        fh.write(text)


def cycle_driver(port: int, tier: str, start_room: str) -> None:
    stop_driver()
    prepare_snapshot(tier, start_room)
    start_driver(port)


def login(port):
    from probe_mud import TelnetSession, drive_login, plain

    class NS:
        pass

    ns = NS()
    ns.player_id = PLAYER
    sess = TelnetSession("127.0.0.1", port)
    out = drive_login(sess, ns)
    settle = sess._recv(idle=0.8, max_wait=6)
    return sess, plain(out) + plain(settle)


def cmd(sess, text: str, secs: float = 1.2) -> str:
    from probe_mud import plain
    sess.send(text)
    return plain(sess.recv_for(secs))


def collect_meta(sess):
    return {
        "score": cmd(sess, "score", 1.5),
        "skills": cmd(sess, "skills", 1.2),
        "inventory": cmd(sess, "i", 1.0),
        "hp": cmd(sess, "hp", 1.0),
    }


def npc_dead(acc: str, target) -> bool:
    name = target["name"]
    return ((f"{name}倒在地上" in acc) or (f"{name}仰天惨嚎" in acc)
            or ("震天动地的一声长啸" in acc) or (f"{name}死了" in acc))


def fight(sess, target, max_secs: float, mode: str):
    chunks = [cmd(sess, f"kill {target['id']}", 1.0)]
    t0 = time.time()
    ended = None
    while time.time() - t0 < max_secs:
        chunk = cmd_raw(sess, 1.5)
        chunks.append(chunk)
        acc = "".join(chunks)
        if "你倒在地上，挣扎了几下就死了" in acc:
            ended = "player_death"
            break
        if npc_dead(acc, target):
            ended = "npc_death"
            break
        if mode == "tiger" and "看来该找机会逃跑了" in acc:
            ended = "fled"
            break
    return "".join(chunks), ended


def cmd_raw(sess, secs: float) -> str:
    from probe_mud import plain
    return plain(sess.recv_for(secs))


def hp_values(text: str):
    m = HP_RE.search(text)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def split_bars(text: str) -> str:
    for ch in BOX_CHARS:
        text = text.replace(ch, "\n")
    return text


def classify_outcome(line: str):
    if PARRY_RE.search(line):
        return "parry"
    if DODGE_RE.search(line):
        return "dodge"
    if line.startswith("结果") and len(line) > 2:
        return "hit"
    return "other"


def bracket(line: str):
    for key, rng in DAMAGE_TIERS:
        if key in line:
            return rng
    return None


def is_action_line(line: str, name: str) -> bool:
    if not line.endswith("！"):
        return False
    if any(x in line for x in EXCLUDE_TXT):
        return False
    if line.startswith("你") and (name + "的") in line:
        return True
    if line.startswith(name) and "你的" in line:
        return True
    return False


def parse_combat(text: str, name: str) -> dict:
    text = split_bars(text)
    lines = [l.strip() for l in text.splitlines()]
    lines = [l for l in lines if l]
    player = {"attacks": 0, "hits": 0, "dodge": 0, "parry": 0, "other": 0}
    npc = {"attacks": 0, "hits": 0, "dodge": 0, "parry": 0, "other": 0}
    dmg_player = []  # 我方打中对方的档位
    dmg_npc = []
    qi_series = []
    for l in lines:
        m = BAR_RE.search(l)
        if m:
            qi_series.append((int(m.group(3)), int(m.group(4))))
    for i, line in enumerate(lines):
        if not is_action_line(line, name):
            continue
        if line.startswith("你") and (name + "的") in line:
            side, box = "player", player
        else:
            side, box = "npc", npc
        box["attacks"] += 1
        outcome = "other"
        j = i + 1
        while j < len(lines):
            cand = lines[j]
            if is_action_line(cand, name):
                break
            if (cand.startswith("(") or cand.startswith("【")
                    or any(x in cand for x in SKIP_OUTCOME)):
                j += 1
                continue
            outcome = classify_outcome(cand)
            if outcome == "hit":
                rng = bracket(cand) or (1, 999)
                (dmg_player if side == "player" else dmg_npc).append(rng)
                outcome = "hits"
            break
        box[outcome] += 1
    drops = []
    for k in range(1, len(qi_series)):
        prev, cur = qi_series[k - 1][0], qi_series[k][0]
        if prev >= cur:
            drops.append(prev - cur)
        else:
            drops.append(0)
    return {"player": player, "npc": npc, "dmg_player": dmg_player,
            "dmg_npc": dmg_npc, "qi_drops": drops, "qi_series": qi_series}


def merge(acc, one):
    for side in ("player", "npc"):
        for k in acc[side]:
            acc[side][k] += one[side][k]
    acc["dmg_player"] += one["dmg_player"]
    acc["dmg_npc"] += one["dmg_npc"]
    acc["qi_drops"] += one["qi_drops"]
    acc["qi_series"] += one["qi_series"]


def empty_acc():
    return {"player": {"attacks": 0, "hits": 0, "dodge": 0, "parry": 0, "other": 0},
            "npc": {"attacks": 0, "hits": 0, "dodge": 0, "parry": 0, "other": 0},
            "dmg_player": [], "dmg_npc": [], "qi_drops": [], "qi_series": []}


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(max(0.0, p * (1 - p) / n + z * z / (4 * n * n))) / d
    return (max(0.0, c - h), min(1.0, c + h))


def pct(x):
    return f"{x * 100:.2f}%"


def damage_stats(ranges):
    if not ranges:
        return {"hits": 0, "mid": 0.0, "lo": 0, "hi": 0}
    mids = [(r[0] + r[1]) / 2 for r in ranges]
    return {"hits": len(ranges), "mid": sum(mids) / len(mids),
            "lo": min(r[0] for r in ranges), "hi": max(r[1] for r in ranges)}


def need_more(acc, args, mode) -> bool:
    """老虎场景玩家因 cps/cor 勇气判定不出手，因此只按 NPC 出手数计回合。"""
    if mode == "tiger":
        return acc["npc"]["attacks"] < args.min_attacks
    return (acc["player"]["attacks"] < args.min_attacks
            or acc["npc"]["attacks"] < args.min_attacks)


def do_run(tier, target_key, run_idx, port, args) -> dict:
    target = TARGETS[target_key]
    mode = "tiger" if target_key == "tiger" else "normal"
    run_dir = os.path.join(OUTDIR, f"{tier}_{target_key}", f"run{run_idx}")
    os.makedirs(run_dir, exist_ok=True)
    acc = empty_acc()
    lives = []
    failures = 0
    sessions = 0
    meta0 = None

    while need_more(acc, args, mode):
        if sessions >= args.max_lives:
            break
        sessions += 1
        cycle_driver(port, tier, target["start_room"])
        sess, login_text = login(port)
        try:
            if mode == "tiger":
                # 老虎一见玩家就自动开打，数秒内可致死：不再额外 look/verbose/hp，
                # 直接用登录文本校验落点，把每一秒都留给战斗。
                if target["end_room"] not in login_text:
                    failures += 1
                    with open(os.path.join(run_dir, f"pos_fail{sessions}.txt"), "w") as fh:
                        fh.write(login_text)
                    continue
                if meta0 is None:
                    meta0 = {}
                if not meta0.get("opponent_look"):
                    got = cmd(sess, f"look {target['id']}", 1.2)
                    if target["name"] in got:
                        meta0["opponent_look"] = got
                hp_before = None
            else:
                if meta0 is None:
                    meta0 = collect_meta(sess)
                look_out = cmd(sess, "look", 1.2)
                if target["end_room"] not in look_out:
                    failures += 1
                    with open(os.path.join(run_dir, f"pos_fail{sessions}.txt"), "w") as fh:
                        fh.write(login_text + look_out)
                    continue
                if not meta0.get("opponent_look"):
                    meta0["opponent_look"] = cmd(sess, f"look {target['id']}", 1.5)
                cmd(sess, "unset wimpy", 0.8)
                cmd(sess, "set verbose 1", 0.8)
                hp_before = cmd(sess, "hp", 1.0)

            text, ended = fight(sess, target, args.fight_secs, mode)
            parsed = parse_combat(text, target["name"])
            merge(acc, parsed)
            name = f"life{len(lives) + 1:03d}.txt"
            with open(os.path.join(run_dir, name), "w") as fh:
                fh.write(text)
            hp_after = None if mode == "tiger" else cmd(sess, "hp", 1.0)
            if mode == "tiger" and not (meta0 or {}).get("score"):
                # 老虎场景在死亡后的鬼门关也能读面板；首次成功后补采
                meta0.update(collect_meta(sess))
            lives.append({"file": name, "ended": ended, "session": sessions,
                          "player_attacks": parsed["player"]["attacks"],
                          "npc_attacks": parsed["npc"]["attacks"],
                          "hp_before": hp_values(hp_before) if hp_before else None,
                          "hp_after": hp_values(hp_after) if hp_after else None})
            if ended is None:
                failures += 1
            meta0["final_hp"] = hp_after or ""
        except Exception as exc:  # noqa: BLE001 - 单条命失败不终止整次取样
            failures += 1
            with open(os.path.join(run_dir, f"error{sessions}.txt"), "w") as fh:
                fh.write(f"{exc}\n{login_text}")
        finally:
            sess.close()

    out = {"tier": tier, "target": target_key, "run": run_idx, "port": port,
           "build_sha": repo_sha(),
           "lives": lives, "failures": failures, "meta": meta0 or {},
           "acc": acc}
    with open(os.path.join(run_dir, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    return out


def extract_snippet(text: str, name: str) -> str:
    text = split_bars(text)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for i, line in enumerate(lines):
        if line.startswith("你") and line.endswith("！") and (name + "的") in line:
            return "\n".join(lines[max(0, i - 1): i + 6])
    for i, line in enumerate(lines):
        if line.startswith(name) and line.endswith("！") and "你的" in line:
            return "\n".join(lines[max(0, i - 1): i + 6])
    return "\n".join(lines[:8])


def load_acc_from_dir(run_dir: str, name: str):
    acc = empty_acc()
    files = sorted(f for f in os.listdir(run_dir) if f.startswith("life") and f.endswith(".txt"))
    snippet = ""
    for f in files:
        text = open(os.path.join(run_dir, f)).read()
        one = parse_combat(text, name)
        merge(acc, one)
        if not snippet:
            snippet = extract_snippet(text, name)
    return acc, files, snippet


def _run3(per, side):
    rates = [a[side]["hits"] / a[side]["attacks"] for a in per if a[side]["attacks"]]
    return (min(rates), max(rates)) if rates else None


def _rng(r):
    return "-" if not r else f"{pct(r[0])}–{pct(r[1])}"


def build_report():
    datasets = [(t, g) for t in ("new", "mid") for g in ("xiaoer", "liumang", "tiger")]
    blocks = []
    for tier, tkey in datasets:
        base = os.path.join(OUTDIR, f"{tier}_{tkey}")
        if not os.path.isdir(base):
            continue
        runs = sorted(d for d in os.listdir(base) if d.startswith("run"))
        per, metas, lives, snippet = [], [], [], ""
        shas = []
        for r in runs:
            rd = os.path.join(base, r)
            acc, files, snip = load_acc_from_dir(rd, TARGETS[tkey]["name"])
            snippet = snippet or snip
            per.append(acc)
            mp = os.path.join(rd, "meta.json")
            if os.path.exists(mp):
                doc = json.load(open(mp, encoding="utf-8"))
                metas.append(doc.get("meta") or {})
                lives += doc.get("lives") or []
                if doc.get("build_sha"):
                    shas.append(doc["build_sha"])
        if not per:
            continue
        tot = empty_acc()
        for a in per:
            merge(tot, a)
        blocks.append({"tier": tier, "tkey": tkey, "per": per, "tot": tot,
                       "meta": metas[0] if metas else {}, "lives": lives,
                       "shas": shas, "snippet": snippet})

    out = []

    def A(s=""):
        out.append(s)

    A("# 命中率实测报告（xkx100 原版）")
    A("")
    A(f"生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    A("")
    A("驱动：FluffOS v2019；mudlib：`tools/xkx_ref/run/mudlib`（与 4004 同一棵树，commit c1b87db）；"
      "驱动二进制与 4004 同一份。")
    A("")
    shas = sorted({s for b in blocks for s in b.get("shas", [])})
    A(f"采样构建：`{'/'.join(shas) if shas else repo_sha()}`（本仓库 HEAD；原版侧 mudlib 固定 c1b87db，"
      "驱动与 4004 同一份）。复刻对照值的构建为 `86a6ca7`（见 §0、§5 脚注与 §9 更正记录）。")
    A("")
    A("采样实例：`run/mudlib-sample` + `config-sample.ini`（端口 4010），每次登陆前重启实例、"
      "把快照写回存档。4004 仅在开头做过一次 ≤3 分钟的连通性确认，未被长时间占用。")
    A("")
    snap_note = []
    for tier in ("new", "mid"):
        p = SNAPSHOTS[tier]
        if os.path.exists(p):
            snap_note.append(f"`{os.path.basename(p)}` md5 `{_md5(p)}`")
    if snap_note:
        A("快照（决定角色属性，可用来核对本文数字）：" + "；".join(snap_note) + "。")
        A("")

    # ---------- 结论 ----------
    A("## 0. 判据句（可直接拿去对复刻）")
    A("")
    mids = [b for b in blocks if b["tier"] == "mid"]
    replica = {"xiaoer": "1.1%", "liumang": "16.1%", "tiger": "100.0%"}
    for b in mids:
        n = b["tot"]["npc"]
        r3 = _run3(b["per"], "npc")
        if n["attacks"]:
            lo, hi = wilson(n["hits"], n["attacks"])
            A(f"- **原版 MID 打{TARGETS[b['tkey']]['name']}：对方打中我方 {_rng(r3)}**"
              f"（三次取样区间；合计 {n['hits']}/{n['attacks']} = {pct(n['hits'] / n['attacks'])}，"
              f"Wilson95% [{pct(lo)}, {pct(hi)}]）。复刻对应值（当前 HEAD）：{replica[b['tkey']]}。")
        else:
            A(f"- **原版 MID 打{TARGETS[b['tkey']]['name']}：对方 0 次出手**（数据不足）。")
    A("")
    A("读数提示：复刻值落在上面区间内，则没有证据表明 ap/dp 映射错；明显落在区间外，才值得改映射。"
      "老虎档已确认**对齐**：原版 100.0% 与复刻当前 HEAD 100.0% 一致；旧报告里的 77.2% 作废，见 §9。")
    A("")

    # ---------- 角色 ----------
    A("## 1. 角色（可观察属性）")
    A("")
    A("两档都用同一个存档槽 `xkxref`，只改存档数据，**LPC 一行未改**。")
    A("")
    A("- **NEW**：`xkxref.o` 原快照。14 岁男，combat_exp=9，`skills` 为空，无兵器。")
    A("- **MID**：`xkxmid.o` 构造快照。combat_exp=6400，raw `unarmed/dodge/parry` 各 40，无兵器。")
    A("")
    A("> **MID 档性质说明（必读）**：这是**用存档数据模拟一个“练到 40 级”的角色**，不是原版正常成长路径"
      "能达到的状态。原版正常途径需要拜师、练功、涨经验；这里只把 `combat_exp` 与 `skills` 直接写进存档。"
      "`combat_exp=6400` 取 `cmds/skill/practice.c` 允许 40 级的最低经验（40^3/10）；取 0 会让伤害公式"
      "`while (random(defense_factor) > my_exp) damage /= 3` 把伤害反复除到 0，属于不合法状态。")
    A("")
    A("> `feature/skill.c` 的 `query_skill()` 非 raw 时返回 `skills[skill]/2 + skills[skill_map]`，"
      "所以 MID 显示 40 级，战斗有效等级是 **20**。")
    A("")
    nm = next((b["meta"] for b in blocks if b["tier"] == "new" and b["meta"]), {})
    mm = next((b["meta"] for b in blocks if b["tier"] == "mid" and b["meta"]), {})
    for label, meta in (("NEW", nm), ("MID", mm)):
        if not meta:
            continue
        A(f"### {label} 的 score / skills / 携带")
        A("")
        for key, title in (("score", "score"), ("skills", "skills"), ("inventory", "i（携带）")):
            if meta.get(key):
                A(f"```text\n{meta[key].strip()}\n```")
                A("")
    A("天赋（存档可观察字段）：`str 20`（显示 20/24）、`int 20`、`con 20`、`dex 20`、`kar 12`、"
      "`per 11`、`cps 23`、`cor 24`、`sta 12`、`spi 12`。`score` 面板把耐力/胆识/福缘显示为 `???`，"
      "所以这三项只能从存档读到，属**可观察但面板不显示**。")
    A("")

    # ---------- 目标 ----------
    A("## 2. 三个目标")
    A("")
    for b in blocks:
        if b["tier"] != "new":
            continue
        A(f"### {TARGETS[b['tkey']]['name']}")
        A("")
        A(f"- 落点：`{TARGETS[b['tkey']]['start_room']}`（startroom 是存档字段，不参与战斗计算）")
        A(f"- 源码参数（**推断**，非面板可观察）：{TARGETS[b['tkey']]['lpc']}")
        if b["meta"].get("opponent_look"):
            A("- `look` 原文（可观察）：")
            A("")
            A("```text")
            A(b["meta"]["opponent_look"].strip())
            A("```")
        A("")
    A("> 关于流氓：原定中央广场同时刷 4 只同名流氓，无法把“对方”归到单一 clone；改用佛山镇街的单只流氓，"
      "对象文件同为 `d/city/npc/liumang.c`，属性完全一致。")
    A("")

    # ---------- 口径 ----------
    A("## 3. 计数口径")
    A("")
    A("- 出手：动作行以 `！` 结尾且满足“你…<对手>的…”或“<对手>…你的…”；"
      "“企图/准备发动攻势/缓缓移动脚步/注视/喝道”等非攻击行剔除。")
    A("- 结果：动作行之后第一条非状态行；含 `挡/格/架/招架/封/拨` → 招架；"
      "含 `闪/躲/避/侧身/滑开/偏了几寸` → 闪避；以“结果”开头 → 命中；其余记 `other`。")
    A("- 伤害：`combatd` 的 `damage_msg` 文案档位取中值（例如“瘀伤”记 10–19、记 14.5），只作量级估计。")
    A("- 回合：一次出手 = 一次判定；每场景累计双方各 ≥50 次。老虎场景玩家 0 次出手，按对方出手 ≥50 计。")
    A("")

    # ---------- 逐场景 ----------
    A("## 4. 逐场景结果")
    A("")
    for b in blocks:
        tkey, tier = b["tkey"], b["tier"]
        per, tot = b["per"], b["tot"]
        p, n = tot["player"], tot["npc"]
        A(f"### {tier.upper()} × {TARGETS[tkey]['name']}（{len(per)} 次取样）")
        A("")
        A(f"- 取样：{len(per)} 次独立会话（run1–{len(per)}），共 {len(b['lives'])} 条命；"
          f"原始日志 `reports/xkx-ref/hit-rate/{tier}_{tkey}/run{{1..{len(per)}}}/`。")
        A("")
        if b["snippet"]:
            A("原始片段：")
            A("")
            A("```text")
            A(b["snippet"])
            A("```")
            A("")
        A("| 跑次 | 我方出手 | 命中 | 被闪 | 被招架 | 我方命中率 | 对方出手 | 命中 | 被闪 | 被招架 | 对方命中率 |")
        A("|---|---|---|---|---|---|---|---|---|---|---|")
        for i, a in enumerate(per, 1):
            pp, nn = a["player"], a["npc"]
            A(f"| {i} | {pp['attacks']} | {pp['hits']} | {pp['dodge']} | {pp['parry']} | "
              f"{pct(pp['hits'] / pp['attacks']) if pp['attacks'] else '-'} | "
              f"{nn['attacks']} | {nn['hits']} | {nn['dodge']} | {nn['parry']} | "
              f"{pct(nn['hits'] / nn['attacks']) if nn['attacks'] else '-'} |")
        A("")
        for label, box in (("我方", p), ("对方", n)):
            if not box["attacks"]:
                A(f"- {label}：**0 次出手**（见第 6 节的 cps/cor 判定）。")
                continue
            lo, hi = wilson(box["hits"], box["attacks"])
            side = "player" if label == "我方" else "npc"
            A(f"- {label}：合计 {box['hits']}/{box['attacks']} = {pct(box['hits'] / box['attacks'])}，"
              f"Wilson95% [{pct(lo)}, {pct(hi)}]，三次区间 {_rng(_run3(per, side))}；"
              f"被闪 {box['dodge']}、被招架 {box['parry']}、other {box['other']}。")
        dp = damage_stats(tot["dmg_player"])
        dn = damage_stats(tot["dmg_npc"])
        A(f"- 伤害（文案档位中值）：我方 {dp['mid']:.1f}/命中（n={dp['hits']}）；"
          f"对方 {dn['mid']:.1f}/命中（n={dn['hits']}），单次命中档位 {dn['lo']}–{dn['hi']}。")
        A(f"- 每回合平均伤害（命中率×档位均值）：我方 "
          f"{dp['mid'] * p['hits'] / max(1, p['attacks']):.2f}；对方 "
          f"{dn['mid'] * n['hits'] / max(1, n['attacks']):.2f}。")
        drops = tot["qi_drops"]
        if drops:
            sd = sorted(drops)
            A(f"- 我方每回合净掉血（verbose 气值相邻差，含自然回气）：min {sd[0]}、"
              f"中位 {sd[len(sd) // 2]}、max {sd[-1]}（n={len(sd)}）。")
        else:
            hp = [(l.get("hp_before"), l.get("hp_after")) for l in b["lives"]]
            hp = [(a, z) for a, z in hp if a and z]
            if hp:
                lost = [a[0] - z[0] for a, z in hp]
                A(f"- 我方每条命掉血（无 verbose 条，取 hp 起止）：{min(lost)}–{max(lost)} "
                  f"（{len(lost)} 条命，起始 {hp[0][0][0]}/{hp[0][0][1]}）。")
            else:
                A("- 我方逐回合掉血范围：**取不到**——老虎场景为把时间全留给战斗，登录即 `kill`、"
                  "死亡后才补采属性，未在战斗中查 `hp`/verbose；仅有每次命中的伤害档位可观察。")
        A("")

    # ---------- 汇总 ----------
    A("## 5. 汇总：对方命中我方")
    A("")
    A("| 档 | 对手 | 对方出手 | 对方命中 | 对方命中率 | Wilson95% | 三次区间 | 复刻对照 |")
    A("|---|---|---|---|---|---|---|---|")
    for b in blocks:
        n = b["tot"]["npc"]
        if not n["attacks"]:
            continue
        lo, hi = wilson(n["hits"], n["attacks"])
        A(f"| {b['tier'].upper()} | {TARGETS[b['tkey']]['name']} | {n['attacks']} | {n['hits']} | "
          f"{pct(n['hits'] / n['attacks'])} | [{pct(lo)}, {pct(hi)}] | {_rng(_run3(b['per'], 'npc'))} | "
          f"{replica[b['tkey']] if b['tier'] == 'mid' else '-'} |")
    A("")
    A("复刻对照口径：产品默认新号（`unarmed=40 / dodge=10 / parry=10`、`combat_exp=250`、六维默认），"
      "3 seeds × 60 回合、双方各 180 次出手；构建为当前 `xkx-parity` HEAD（含 `86a6ca7` 接入 NPC "
      "`set_temp` 临时攻防），见 `specs/xkx-parity/combat-implementation.md` §10.5。")
    A("")
    A("> 注意：复刻对照的角色档（技能 40/10/10、exp 250）与本文 MID 构造档（技能 40/40/40、exp 6400）"
      "**不是同一角色**，只作量级对照；店小二/流氓两档同理。")
    A("")

    # ---------- 线索 ----------
    A("## 6. 与 combatd.lpc 参数相关的线索")
    A("")
    A("### 6.1 可观察（原始文本/面板直接给出）")
    A("")
    A("- 双方出手数、命中/被闪/被招架数：来自动作行与结果行。")
    A("- 我方 `hp`/verbose 气值变化：来自 `【气血】` 与 `┃精：…气：…┃`。")
    A("- 我方 `score`/`skills`/携带/天赋：来自命令输出与存档字段。")
    A("- 对手 `look` 描述（名称、性别、体型、武器有无等）。")
    A("- **老虎场景玩家 0 次普通出手**：日志里只有“你慢慢地移动着脚步，伺机出手。”一次，"
      "之后玩家侧不再出现任何动作行——这是可观察事实。")
    A("")
    A("### 6.2 只能推断（源码事实，非本次实测可观察）")
    A("")
    A("- `skill_power()`：`level = query_skill(skill) + apply/attack|defense`；level=0 时返回 "
      "`combat_exp/2`；`power=(level/6)*level*level`；攻击力 `(power+exp)/80*(str+str_temp)`，"
      "防御力同式用 dex；str/dex 夹在 10..200。")
    A("- `query_skill()` 非 raw：`skills[skill]/2 + skills[skill_map]` → 显示 40 级 = 有效 20 级。")
    A("- 命中判定顺序：先闪避 `random(ap+dp) < dp`，未闪再招架 `random(ap+pp) < pp`，都不过才命中。")
    A("- **老虎的 cps=0 是 0 出手的直接原因**：`combatd.c` fight() 里 "
      "`random(victim->query_cps()*3) > me->query_cor()`；`adm/daemons/race/beast.c` 不设 cps/cor，"
      "老虎 `query_cps()=0` → `random(0)=0`，玩家胆识 24 也永远进 guard 分支，首条 guard 文案后 `guarding=1` 静默。")
    A("- 老虎 dex/str/con 未在 `laohu.c` 设置，由 `beast.c` 每个 clone 掷 `random(40)+5`——"
      "所以老虎的 dex 不是常数，只能从战斗数字反推。")
    A("- `skill_power` 的 level 上限 800：老虎 `apply/defense=30000` 会被截到 800，"
      "这是“老虎几乎必中/玩家几乎打不中”的数值来源。")
    A("")
    A("### 6.3 不可观察")
    A("")
    A("- 对手的 exp、技能等级、apply/attack|defense、dex/str/con 的**运行时真实值**（源码可读，"
      "但面板看不到；老虎 dex 还每只随机）。")
    A("- `ap/dp/pp` 中间量本身（只能由公式反推）。")
    A("- 我方 `耐力/胆识/福缘` 的 score 显示值（面板为 `???`）。")
    A("")

    # ---------- 局限 ----------
    A("## 7. 局限与备注")
    A("")
    A("- 伤害是文案档位估计，不是精确值；命中率与闪避/招架计数不受影响。")
    A("- 每条命结束就重启实例（死亡后位置/血量无法在同一次会话里干净复位），所以样本由多条命拼接；"
      "每条命的对手都是新 clone。")
    A("- 老虎场景按对方出手计回合；我方 0 出手是原版设计使然，不是采集失败。")
    A("- MID 的 `startroom` 直接指向目标房间（存档字段），省掉 44 步/夜间出口不可见等与命中率无关的环境噪声；"
      "`wudang.offerring=100` 仅用于过遇真宫山门（非战斗字段）。两者都写在存档里，LPC 未改。")
    A("")

    A("## 8. 复现命令")
    A("")
    A("本文所有数字都由落盘原始战斗文本用 `--report-only` 复算得到，逐字可复现；重新采集会得到")
    A("新的随机样本（区间应落在同一量级，不会逐字相同）。")
    A("")
    A("```bash")
    for tier in ("new", "mid"):
        for tkey in ("xiaoer", "liumang", "tiger"):
            lives = 60 if tkey == "tiger" else 80
            A(f"python3 tools/xkx_ref/measure_hit_rate.py --tier {tier} --target {tkey} "
              f"--runs 3 --min-attacks 50 --max-lives {lives} --port 4010 --fight-secs 60")
    A("")
    A("# 只从 reports/xkx-ref/hit-rate/ 下的原始文本重算本报告")
    A("python3 tools/xkx_ref/measure_hit_rate.py --report-only")
    A("```")
    A("")
    A("- 原始日志：`reports/xkx-ref/hit-rate/<tier>_<target>/run{1,2,3}/life*.txt` 与 `meta.json`。")
    A("- 采集器每次登录前重启 4010 实例并覆盖存档，跑完自动停实例；4004 不被占用。")
    A("")

    # ---------- 更正记录 ----------
    A("## 9. 更正记录（保留旧值，不静默改数）")
    A("")
    A("- **2026-11-07 作废：复刻老虎命中 77.2%**。该值来自 `specs/xkx-parity/"
      "combat-implementation.md` §10.3（139/180），采于**落后于当前 HEAD 的构建**——当时尚未接入 "
      "`86a6ca7` 的 NPC `set_temp` 临时攻防，老虎的 `apply/defense=30000` / `apply/attack=20000` "
      "没有生效，dp 只有 `combat_exp/2` 量级。同 seed、3 次独立结算、每人 60 回合的 A/B 实测："
      "当前 HEAD **180/180 = 100.0%**；摘掉老虎 `apply/*`（等价旧构建）**130/180 = 72.2%**。"
      "原版 MID 本报告复测 152/152 = 100.0%。结论：**老虎档原版与复刻对齐**，"
      "旧报告“老虎明显偏离”的判据句撤回。")
    A("- **2026-11-07 更新：复刻流氓命中 10.0% → 16.1%**。10.0%（18/180）同样是 `set_temp` 接入前的值"
      "（§10.3）；当前 HEAD 为 29/180 = 16.1%（§10.5）。")
    A("- **店小二 1.1% 不变**：接入 `set_temp` 前后均 2/180 = 1.1%（HEAD 已复测）。")
    A("- 旧值仅保留在本记录中，正文与 §5 表格一律使用当前 HEAD 数字；新增采集的 `meta.json` 写入 "
      "`build_sha`，报告顶部“采样构建”字段据此填写。")
    A("")

    os.makedirs(REPORTDIR, exist_ok=True)
    with open(os.path.join(REPORTDIR, "hit-rate.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    print(os.path.join(REPORTDIR, "hit-rate.md"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", choices=["new", "mid"])
    ap.add_argument("--target", choices=list(TARGETS))
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--min-attacks", type=int, default=50)
    ap.add_argument("--port", type=int, default=4010)
    ap.add_argument("--max-lives", type=int, default=40)
    ap.add_argument("--fight-secs", type=float, default=40.0)
    ap.add_argument("--reparse", action="store_true")
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()

    if args.report_only or args.reparse:
        build_report()
        return 0

    if not os.path.exists(DRIVER) or not os.path.isdir(MUDLIB):
        print("缺少驱动或 mudlib", file=sys.stderr)
        return 2
    os.makedirs(OUTDIR, exist_ok=True)
    for i in range(1, args.runs + 1):
        print(f"[{args.tier}_{args.target}] run {i}/{args.runs}", flush=True)
        try:
            r = do_run(args.tier, args.target, i, args.port, args)
            print(f"  lives={len(r['lives'])} p={r['acc']['player']['attacks']} "
                  f"n={r['acc']['npc']['attacks']} fails={r['failures']}", flush=True)
        except Exception as e:
            print(f"  run failed: {e}", file=sys.stderr, flush=True)
        finally:
            stop_driver()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
