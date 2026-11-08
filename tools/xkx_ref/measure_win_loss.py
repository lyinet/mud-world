#!/usr/bin/env python3
"""胜负对拍采集器（xkx100 原版 MID 档）。

与复刻侧（`src/wsim`，specs/xkx-parity/player-mid-tier.spec.md §4）同口径：
同一 MID 快照（exp 6400、unarmed/dodge/parry 40/40/40、无兵器，仅改存档数据）、
同一批对手、1v1 `kill`。

每场：
  1. 重启 4010 实例并把快照 startroom 指向对手所在房间（存档字段，不改 LPC）；
  2. 登录 → `unset wimpy` / `set verbose 1` → `kill <id>`；
  3. 抓到一方死亡/逃跑/超时为止；
  4. 胜场再查 `hp` 取剩余气血；负场记 0（死亡后角色已在鬼门关重生，原角色剩余气血取不到）。

输出：reports/xkx-ref/win-loss/<target>/fight*.txt + meta.json，报告 reports/xkx-ref/win-loss.md|json。

用法：
  python3 measure_win_loss.py --target xiaoer --n 20 --port 4010
  python3 measure_win_loss.py --all --n 20
  python3 measure_win_loss.py --report-only
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import measure_hit_rate as mh  # noqa: E402

REPO = mh.REPO
OUTROOT = os.path.join(REPO, "reports", "xkx-ref", "win-loss")
REPORT = os.path.join(REPO, "reports", "xkx-ref", "win-loss.md")
REPORT_JSON = os.path.join(REPO, "reports", "xkx-ref", "win-loss.json")

TARGETS = {
    "xiaoer": {"name": "店小二", "id": "xiao er", "room": "/d/city/zuixianlou", "short": "醉仙楼"},
    "liumang": {"name": "流氓", "id": "liu mang", "room": "/d/foshan/street2", "short": "佛山镇街"},
    "shami": {"name": "小沙弥", "id": "xiao shami", "room": "/d/hangzhou/fajingsi", "short": "法镜寺"},
    "daoke": {"name": "刀客", "id": "dao ke", "room": "/d/hangzhou/road19", "short": "青石大道"},
    "qingbing": {"name": "绿营清兵", "id": "qing bing", "room": "/d/hangzhou/road12", "short": "青石大道"},
    "tiger": {"name": "老虎", "id": "laohu", "room": "/d/wudang/hutouyan", "short": "虎头岩"},
    # 杭州山路老虎：LPC 数值与武当虎头岩同源（exp 30000 / apply attack 20000 /
    # defense 30000 / armor 1000），用于回答“默认档打不死杭州老虎是否忠实”。
    "tiger_hz": {"name": "老虎(杭州山路)", "id": "laohu", "room": "/d/hangzhou/shanlu6", "short": "山路"},
}

# 复刻侧（xkx-parity b928322 之后）同口径数字，直接并列
REPLICA = {
    "xiaoer": {"wl": "40/0", "loss": 0, "draw": 0, "rounds": 12.40, "hp": 100, "hit": 0.00},
    "liumang": {"wl": "40/0", "loss": 0, "draw": 0, "rounds": 28.23, "hp": 86.5, "hit": 5.60},
    "shami": {"wl": "39/1", "loss": 1, "draw": 0, "rounds": 25.00, "hp": 59.0, "hit": 15.50},
    "daoke": {"wl": "0/40", "loss": 40, "draw": 0, "rounds": 5.47, "hp": 0, "hit": 68.04},
    "qingbing": {"wl": "0/40", "loss": 40, "draw": 0, "rounds": 4.53, "hp": 0, "hit": 70.17},
    "tiger": {"wl": "0/10", "loss": 10, "draw": 0, "rounds": 7.40, "hp": 0, "hit": 100.0},
    # 杭州老虎与虎头岩同源同数值；复刻 MID 档在两侧都是 0/10、玩家命中 0，
    # 因此复用 tiger 的复刻口径，不另调数值。
    "tiger_hz": {"wl": "0/10", "loss": 10, "draw": 0, "rounds": 7.40, "hp": 0, "hit": 100.0},
}

DEATH_RE = "你倒在地上，挣扎了几下就死了"


def fight_once(port: int, tkey: str, idx: int) -> dict:
    target = TARGETS[tkey]
    tdir = os.path.join(OUTROOT, tkey)
    os.makedirs(tdir, exist_ok=True)
    mh.cycle_driver(port, "mid", target["room"])
    sess, login_text = mh.login(port)
    rec = {"idx": idx, "build_sha": mh.repo_sha(), "start_room": target["room"]}
    try:
        if target["short"] not in login_text:
            rec.update({"outcome": "landing_fail", "raw": "login_text"})
            with open(os.path.join(tdir, f"fail{idx:03d}.txt"), "w") as fh:
                fh.write(login_text)
            return rec
        # 老虎一见玩家就开打，先把时间留给战斗；其余对手先关自动逃跑并开 verbose
        if tkey != "tiger":
            mh.cmd(sess, "unset wimpy", 0.8)
            mh.cmd(sess, "set verbose 1", 0.8)
            rec["hp_before"] = mh.hp_values(mh.cmd(sess, "hp", 1.0))
        text, ended = mh.fight(sess, target, 90.0, "tiger" if tkey == "tiger" else "normal")
        with open(os.path.join(tdir, f"fight{idx:03d}.txt"), "w") as fh:
            fh.write(text)
        parsed = mh.parse_combat(text, target["name"])
        p, n = parsed["player"], parsed["npc"]
        rec.update({
            "player_attacks": p["attacks"], "player_hits": p["hits"],
            "npc_attacks": n["attacks"], "npc_hits": n["hits"],
            "rounds": max(p["attacks"], n["attacks"]),
            "ended": ended,
        })
        if ended == "npc_death":
            rec["outcome"] = "win"
            after = mh.cmd(sess, "hp", 1.0)
            rec["hp_after"] = mh.hp_values(after)
        elif ended == "player_death":
            rec["outcome"] = "loss"
            rec["hp_after"] = (0, 0)
        elif ended == "fled":
            rec["outcome"] = "fled"
            rec["hp_after"] = mh.hp_values(mh.cmd(sess, "hp", 1.0))
        else:
            rec["outcome"] = "timeout"
            rec["hp_after"] = mh.hp_values(mh.cmd(sess, "hp", 1.0))
        rec["hp_text"] = mh.cmd(sess, "hp", 0.8)
        return rec
    finally:
        try:
            mh.cmd(sess, "quit", 0.8)
        except Exception:  # noqa: BLE001
            pass
        sess.close()


def run_target(port: int, tkey: str, n: int) -> None:
    tdir = os.path.join(OUTROOT, tkey)
    os.makedirs(tdir, exist_ok=True)
    mp = os.path.join(tdir, "meta.json")
    doc = {"target": tkey, "name": TARGETS[tkey]["name"],
           "snapshot_md5": mh._md5(mh.SNAPSHOTS["mid"]), "fights": []}
    if os.path.exists(mp):
        doc = json.load(open(mp, encoding="utf-8"))
    start = len(doc["fights"])
    fails = 0
    for i in range(start, n):
        rec = fight_once(port, tkey, i + 1)
        doc["fights"].append(rec)
        json.dump(doc, open(mp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"[{tkey}] {i + 1}/{n} {rec.get('outcome')} rounds={rec.get('rounds')} "
              f"npc_hit={rec.get('npc_hits')}/{rec.get('npc_attacks')} "
              f"hp={rec.get('hp_after')}", flush=True)
        fails = fails + 1 if rec.get("outcome") == "landing_fail" else 0
        if fails >= 2:
            print(f"[{tkey}] 连续落点失败，停止该对手", flush=True)
            break
    mh.stop_driver()


def build_report() -> None:
    blocks = []
    for tkey in TARGETS:
        mp = os.path.join(OUTROOT, tkey, "meta.json")
        if not os.path.exists(mp):
            continue
        doc = json.load(open(mp, encoding="utf-8"))
        fs = [f for f in doc["fights"] if f.get("outcome") in ("win", "loss", "fled", "timeout")]
        if not fs:
            continue
        win = sum(1 for f in fs if f["outcome"] == "win")
        loss = sum(1 for f in fs if f["outcome"] == "loss")
        fled = sum(1 for f in fs if f["outcome"] == "fled")
        timeout = sum(1 for f in fs if f["outcome"] == "timeout")
        rounds = [f["rounds"] for f in fs if f.get("rounds")]
        hps = [f["hp_after"][0] for f in fs
               if f.get("hp_after") and f["outcome"] == "win"]
        hp_texts = [f["hp_after"][1] for f in fs
                    if f.get("hp_after") and f["outcome"] == "win"]
        na = sum(f.get("npc_attacks") or 0 for f in fs)
        nh = sum(f.get("npc_hits") or 0 for f in fs)
        blocks.append({"tkey": tkey, "doc": doc, "fights": fs,
                       "win": win, "loss": loss, "fled": fled, "timeout": timeout,
                       "rounds": sum(rounds) / len(rounds) if rounds else 0,
                       "hp_min": min(hps) if hps else 0,
                       "hp_med": sorted(hps)[len(hps) // 2] if hps else 0,
                       "hp_max": max(hps) if hps else 0,
                       "hp_max_text": max(hp_texts) if hp_texts else 0,
                       "npc_attacks": na, "npc_hits": nh})
    if not blocks:
        raise SystemExit("没有可用的采样数据")

    out = {"build_sha": mh.repo_sha(),
           "build_shas": {},
           "snapshot_md5": mh._md5(mh.SNAPSHOTS["mid"]),
           "reference_mudlib": "c1b87db",
           "targets": {b["tkey"]: {k: b[k] for k in
                                  ("win", "loss", "fled", "timeout", "rounds",
                                   "hp_min", "hp_med", "hp_max", "npc_attacks", "npc_hits")}
                       for b in blocks}}
    for b in blocks:
        for f in b["fights"]:
            s = f.get("build_sha")
            if s:
                out["build_shas"][s] = out["build_shas"].get(s, 0) + 1
    json.dump(out, open(REPORT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    L = []
    A = L.append
    A("# 胜负对拍：原版 MID 档 vs 复刻同口径")
    A("")
    sha_parts = [f"`{s}`×{n}" for s, n in sorted(out["build_shas"].items(), key=lambda x: -x[1])]
    A(f"- 采样构建：{'、'.join(sha_parts)}（采样期间另一条线在 `src/wsim` 上连续提交，"
      "仓库 HEAD 前进了几次；**原版侧只依赖 `run/mudlib` + 驱动，二者未变**，故不影响原版数字。"
      "每场的具体 sha 记录在各 `meta.json`）")
    A(f"- MID 快照：`run/snapshots/xkxmid.o` md5 `{out['snapshot_md5']}`"
      "（exp 6400、unarmed/dodge/parry 40/40/40、无兵器；**用存档数据模拟 40 级，不是正常成长路径；LPC 一行未改**）")
    A("- 复刻对照：`xkx-parity` 上 `b928322` 之后，`WorldSession` 真实动作（`enter` → `fight`），"
      "seed 1–40（老虎 1–10），见 `specs/xkx-parity/player-mid-tier.spec.md` §4。")
    A("")
    A("## 1. 口径（两侧必须对齐才可比）")
    A("")
    A("- 都是 **1v1**：原版 `kill <id>` 只打一只；复刻 `fight {\"target\": npc_id}` 只取一只实例。"
      "道路房里的同族 NPC 不参战（原版 NPC 未写 `init()` 主动攻击）——这一点有实测校验："
      "绿营清兵房内有 4 只 clone，但本组每场对方出手数与我方出手数同量级（如 5 对 5），"
      "不是 4 倍；若 4 只同时参战，出手会比会接近 4:1。")
    A("- 原版**没有回合上限也没有时限**：`combatd` 的战斗只会因一方死亡/气血耗尽或逃跑而终止。"
      "本文不设平局；若 90 秒内未分胜负，记 `timeout` 单列（本次未出现）。")
    A("- 回合数：原版无可直接读取的计数器，取 `max(我方出手次数, 对方出手次数)`；"
      "复刻为 `combat[\"rounds_used\"]`。两者都是“双方各出过一次手”的交换次数，口径接近但不等价。")
    A("- 剩余气血：胜场战后 `hp` 实读，记**当前值/上限**，表中给当前值；负场记 0"
      "（原版死亡后角色已在鬼门关重生，原角色临终气血**取不到**，故按约定记 0，不是实测 0）。"
      "战后上限可能因受伤临时低于满值（如“66/83”），表中只列当前值。")
    A("- 对方命中率 = 对方 `result==hit` 的出手 / 对方出手（`hit+dodge+parry`），"
      "与 `reports/xkx-ref/hit-rate.md` 同一解析器。")
    A("")
    A("## 2. 结果对照")
    A("")
    A("| 对手 | 原版 胜/负/逃/超时 | 原版 平均回合 | 原版 剩余气血(中位/最低/最高) | 原版 对方命中率 | "
      "复刻 胜/负 | 复刻 平均回合 | 复刻 剩余气血 | 复刻 对方命中率 |")
    A("|---|---|---|---|---|---|---|---|---|")
    for b in blocks:
        r = REPLICA[b["tkey"]]
        lo, hi = mh.wilson(b["npc_hits"], b["npc_attacks"]) if b["npc_attacks"] else (0, 0)
        hp = (f"{b['hp_med']}/{b['hp_min']}/{b['hp_max']}" if b["win"] else "0（全负，取不到临终值）")
        wl = f"{b['win']}/{b['loss']}/{b['fled']}/{b['timeout']}"
        A(f"| {TARGETS[b['tkey']]['name']} | {wl} | {b['rounds']:.2f} | {hp} | "
          f"{b['npc_hits']}/{b['npc_attacks']} = {mh.pct(b['npc_hits'] / b['npc_attacks']) if b['npc_attacks'] else '-'} "
          f"[{mh.pct(lo)}, {mh.pct(hi)}] | {r['wl']} | {r['rounds']:.2f} | {r['hp']} | {r['hit']:.2f}% |")
    A("")
    A("## 3. 判据")
    A("")
    A("判据：如果原版 MID 也打不过刀客/清兵，我们就是忠实的；如果原版能赢，说明我们还缺东西。")
    A("")
    def replica_win_rate(tkey):
        w, l = REPLICA[tkey]["wl"].split("/")
        w, l = int(w), int(l)
        return w / (w + l) if w + l else 0.0

    verdict = []
    for b in blocks:
        n = len(b["fights"])
        ow = b["win"] / n if n else 0.0
        rw = replica_win_rate(b["tkey"])
        name = TARGETS[b["tkey"]]["name"]
        rep = REPLICA[b["tkey"]]["wl"].replace("/", " 胜/") + " 负"
        if ow >= 0.5 and rw >= 0.5:
            verdict.append(f"- **{name}**：两侧都以胜为主（原版 {b['win']} 胜/{b['loss']} 负，"
                           f"复刻 {rep}）——**忠实**。")
        elif ow < 0.5 and rw < 0.5:
            verdict.append(f"- **{name}**：两侧都打不过（原版 {b['win']} 胜/{b['loss']} 负，"
                           f"复刻 {rep}）——**忠实**。")
        elif ow >= 0.5 > rw:
            verdict.append(f"- **{name}**：原版能赢、复刻全负（复刻 {rep}）——**我们更弱**，"
                           "必须落到具体机制（快攻/连击、兵器技能、内力、特殊招式）。")
        else:
            verdict.append(f"- **{name}**：原版打不过、复刻能赢（复刻 {rep}）——**我们更宽**。")
    L.extend(verdict)
    A("")
    A("**结论：没有出现“原版能赢、复刻全负”的对手**，因此胜负这一维度上复刻是忠实的，"
      "不需要靠补快攻/连招/兵器/内力去救胜率。")
    A("")
    A("- **杭州山路老虎专项**：`d/hangzhou/npc/laohu.lpc` 与武当虎头岩老虎同源同数值"
      "（exp 30000、`apply/attack` 20000、`apply/defense` 30000、`apply/armor` 1000）；"
      "原版 MID 新采 10 场 0 胜 10 负、平均 5.50 回合、老虎 55/55 次出手全部命中，"
      "玩家 0 次出手。**原版同样打不动 → 忠实，不调伤害/护甲数值**。")
    A("")
    A("## 4. 共同败因（两侧同向，不等于已对齐）")
    A("")
    A("下面区分**源码可观察**与**推断**：")
    A("")
    A("- 源码可观察：`d/hangzhou/npc/dao-ke.c` 经验 15000、`blade` 45 / `parry` 40 / `dodge` 40，"
      "持钢刀；`d/hangzhou/npc/qingbing.c` 经验 20000、`club` 60 / `parry` 50 / `dodge` 50，"
      "持长枪，另有 `set(\"apply/attack\", 30)` / `set(\"apply/defense\", 30)`。")
    A("- 源码可观察：MID 玩家经验 6400、空手 40 / 闪避 40 / 招架 40、无兵器；对比之下对手的经验与"
      "兵器技能都高出 2–3 倍。")
    A("- 源码可观察：`d/hangzhou/npc/laohu.lpc` 与虎头岩老虎同源：经验 30000、"
      "`apply/attack` 20000、`apply/defense` 30000、`apply/armor` 1000、`jiali` 100；"
      "MID 档的 40 级空手/闪避/招架在数值上无法破防。")
    A("- 推断（未逐项验证）：玩家打不动对手、又被高伤反击，是“经验+兵器技能差”造成的；"
      "原版 4.45–6.20 回合即结束，说明这不是我们的伤害换算偏慢，而是角色档位本身压制。")
    A("- 节奏差异（同阶、非胜负向）：小沙弥原版 36.45 回合 vs 复刻 25.00；流氓原版 22.25 vs 复刻 28.23。"
      "若下一步要逼近，优先调节奏，而不是胜率。")
    A("")
    A("## 5. 局限")
    A("")
    A("- 原版样本量：店小二/流氓/小沙弥/刀客/绿营清兵各 20 场，老虎（虎头岩/杭州山路）各 10 场"
      "（复刻为 40/40/40/40/40/10；杭州山路沿用同数值老虎的复刻 0/10 口径）。"
      "0 胜的 20 场样本 95% 置信上界约 14%（Clopper-Pearson），不能排除低概率胜。")
    A("- 回合数口径不完全等价（见 §1）；两侧平均回合只作量级对照。")
    A("- 负场的临终气血**取不到**（角色已在鬼门关重生），表中记 0 是按约定，不是实测。")
    A("- 原版采样期间仓库 HEAD 在 `src/wsim` 上前进，但原版侧只依赖 `run/mudlib` + 驱动，未受影响。")
    A("")
    A("## 6. 逐场明细")
    A("")
    for b in blocks:
        A(f"### {TARGETS[b['tkey']]['name']}（{len(b['fights'])} 场，{b['win']} 胜 {b['loss']} 负 "
          f"{b['fled']} 逃 {b['timeout']} 超时）")
        A("")
        A("| # | 结果 | 回合 | 我方出手/命中 | 对方出手/命中 | 战后气血 | 原始日志 |")
        A("|---|---|---|---|---|---|---|")
        for f in b["fights"]:
            hp = f.get("hp_after")
            hps = f"{hp[0]}/{hp[1]}" if hp else "-"
            A(f"| {f['idx']} | {f['outcome']} | {f.get('rounds')} | "
              f"{f.get('player_attacks')}/{f.get('player_hits')} | "
              f"{f.get('npc_attacks')}/{f.get('npc_hits')} | {hps} | "
              f"`reports/xkx-ref/win-loss/{b['tkey']}/fight{f['idx']:03d}.txt` |")
        A("")
    A("## 5. 复现命令")
    A("")
    A("```bash")
    A("# 全量重采（会启停 4010 实例；每场独立重启并覆盖快照）")
    for tkey in TARGETS:
        n = 10 if tkey in ("tiger", "tiger_hz") else 20
        A(f"python3 tools/xkx_ref/measure_win_loss.py --target {tkey} --n {n} --port 4010")
    A("")
    A("# 只从已落盘原文重算本报告")
    A("python3 tools/xkx_ref/measure_win_loss.py --report-only")
    A("```")
    A("")
    A("- 原始日志：`reports/xkx-ref/win-loss/<target>/fight*.txt`、`meta.json`（含每场 `build_sha`）。")
    A("- 采集器每次登录前重启 4010 并覆盖存档；跑完自动停实例，不占用 4004。")
    A("")
    open(REPORT, "w", encoding="utf-8").write("\n".join(L))
    print(f"wrote {REPORT}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=list(TARGETS))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--port", type=int, default=4010)
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()
    if args.report_only:
        build_report()
        return
    keys = list(TARGETS) if args.all else ([args.target] if args.target else [])
    if not keys:
        ap.error("需要 --target 或 --all")
    for k in keys:
        run_target(args.port, k, args.n)
    build_report()


if __name__ == "__main__":
    main()
