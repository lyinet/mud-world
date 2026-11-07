#!/usr/bin/env python3
"""用固定角色快照跑可重复的 xkx 参照场景，产出可 diff 的文本样本。

固定条件：
  - 角色 xkxref（测试侠客，男，天赋 20/20/20/20），存档快照 run/snapshots/xkxref.o
  - 每次采样前：停采样实例 → 用快照覆盖存档 → 起采样实例 → 等端口就绪 → 登录
  - 每个场景跑 3 次，保存去 ANSI 的原始输出与归一化输出

采样使用 run/mudlib-sample + config-sample.ini 的独立实例（端口 4005），
全程不碰 4004 参照服务，避免公式对拍方掉线。

场景选点刻意避开会随昼夜/天气变化的环境描述：
  - 房间与 exits 取武庙二楼（室内，无 NPC）
  - score 取出生点武庙（面板本身不含天时）
  - ask 取杂货铺杨永福（固定 inquiry，无闲聊概率）
  - 战斗取中央广场流氓（对手固定，RNG 行在报告里单列）

不修改任何 LPC 逻辑；只覆盖 data/user/x/xkxref.o。
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(ROOT))
MUDLIB = os.path.join(ROOT, "run", "mudlib")
SAMPLE_DIR = os.path.join(ROOT, "run", "mudlib-sample")
SAMPLE_CONFIG = "config-sample.ini"
SNAPSHOT = os.path.join(ROOT, "run", "snapshots", "xkxref.o")
SAMPLE_SAVE = os.path.join(SAMPLE_DIR, "data", "user", "x", "xkxref.o")
PROBE = os.path.join(ROOT, "probe_mud.py")
DRIVER = os.path.join(ROOT, "src", "fluffos-v2019", "build", "src", "driver")
SAMPLE_LOG = os.path.join(ROOT, "logs", "scenario-driver.log")
SAMPLE_PORT = 4005
PLAYER = "xkxref"
SAMPLE_PROC_PATTERN = "driver " + SAMPLE_CONFIG

SCENARIOS = [
    ("S1_room", ["go up|2", "look|2"], "look|2"),
    ("S2_go_exits", ["go up|2"], "go up|2"),
    ("S3_score", ["score"], "score"),
    ("S4_ask", ["go east|1.5", "go south|1.5", "go south|1.5", "go south|1.5",
                "go west|1.5", "ask yang about 面具|3"],
     "ask yang about 面具|3"),
    ("S5_combat", ["go east|1.5", "go south|1.5", "kill liu mang|30"],
     "kill liu mang|30"),
]

SCENARIO_NOTES = {
    "S1_room": (
        "武庙二楼 look。稳定：房间标题、长描述、出口列表、英雄榜。"
        "不稳定：无（室内无天时/天气；偶发【存盘】等系统行已归一化）。"
    ),
    "S2_go_exits": (
        "武庙 → 武庙二楼 的 go 输出。稳定：目标房间标题、出口 down/up。"
        "不稳定：无（室内）。"
    ),
    "S3_score": (
        "出生点 score 面板。稳定：称号、姓名、性别、性格、家乡、年龄、"
        "杀生/被杀、经验/潜能、正气、江湖阅历/威望。"
        "不稳定：游戏时间（每次登录不同，已归一化）；食物/饮水/<精>/<气> 条随"
        "在线秒数微变。"
    ),
    "S4_ask": (
        "杂货铺向杨永福 ask 面具。稳定：打听提示、悄悄话、固定答复。"
        "不稳定：偶发【风闻】/【存盘】系统行（已归一化）。"
    ),
    "S5_combat": (
        "中央广场 kill liu mang 至死亡。稳定锚点（run*.anchor.txt）：已发起挑战、"
        "「看起来流氓想杀死你！」、眼前一黑、倒地结算。"
        "不稳定：挑战喝句（二选一随机，锚点用占位符）、每回合命中/闪避/部位/"
        "伤害/伤情状态（战斗 RNG）、死亡发生秒数，以及【谣言】死亡通报出现的"
        "时机（可能在倒地结算之前或之后）。归一化会把部位/伤情/Hp 状态替换为"
        "占位符，并在倒地结算处截断。"
    ),
}

AMBIENT_RE = re.compile(r"^(?:> ?)?【(?:江湖传闻|风闻|谣言|传闻|存盘)】")
PLAYTIME_RE = re.compile(r"(你在侠客行一百里的游戏时间是:)\d+m")
BODY_RE = re.compile(
    r"你的(左|右)?(脸|臂|腿|耳|肩|两肋|后心|小腹|胸|背|头|颈|腰|手腕|脚腕|脚|拳|臀|膝)"
)
WOUND_RE = re.compile(r"(瘀青|瘀伤|血痕|伤口|擦伤|挫伤|淤青|淤伤)")
HP_RE = re.compile(r"^\( 你[^)]*\)\s*$", re.M)
DEATH_END = "你倒在地上，挣扎了几下就死了。\n"


def strip_ambient(text: str) -> str:
    """去掉系统行；若系统行接在提示符后，保留提示符本身。"""
    out = []
    for line in text.splitlines(keepends=True):
        if AMBIENT_RE.match(line):
            if line.startswith("> "):
                out.append("> \n")
            continue
        out.append(line)
    return "".join(out)


def normalize(text: str, scenario: str) -> str:
    text = strip_ambient(text)
    text = PLAYTIME_RE.sub(r"\1<PLAY_MINUTES>", text)
    if scenario == "S5_combat":
        if DEATH_END in text:
            text = text.split(DEATH_END)[0] + DEATH_END
        text = BODY_RE.sub("你的<部位>", text)
        text = WOUND_RE.sub("<伤情>", text)
        text = HP_RE.sub("<HP_STATUS>\n", text)
    return text.rstrip() + "\n"


S5_ANCHORS = [
    ("START", re.compile(r"^你对着流氓喝道：.*$", re.M)),
    ("ENGAGE", re.compile(r"^看起来流氓想杀死你！$", re.M)),
    ("BLACKOUT", re.compile(r"^你的眼前一黑，接着什么也不知道了\.\.\.\.$", re.M)),
    ("SETTLE", re.compile(r"^你倒在地上，挣扎了几下就死了。$", re.M)),
]


def combat_anchor(raw: str) -> str:
    """抽取战斗的稳定锚点，按固定顺序输出，便于跨运行 diff。"""
    lines = []
    for label, rx in S5_ANCHORS:
        if label == "START":
            # 挑战台词本身是二选一的随机句，只保留“已发起挑战”这一事实
            present = bool(rx.search(raw))
            lines.append(f"<{label}> "
                         f"{'<RANDOM_CHALLENGE_LINE>' if present else '<MISSING>'}")
            continue
        match = rx.search(raw)
        lines.append(f"<{label}> {match.group(0) if match else '<MISSING>'}")
    return "\n".join(lines) + "\n"


def port_ready(port: int, timeout: float = 25.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.4):
                return True
        except OSError:
            time.sleep(0.4)
    return False


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def prepare_sample_mudlib() -> None:
    marker = os.path.join(SAMPLE_DIR, ".sample-ready")
    if not os.path.exists(marker):
        res = run(["rsync", "-a", "--delete", MUDLIB + "/", SAMPLE_DIR + "/"])
        if res.returncode != 0:
            raise RuntimeError(f"复制采样 mudlib 失败：{res.stderr[-400:]}")
        config = open(os.path.join(SAMPLE_DIR, "config.ini"), encoding="utf-8").read()
        config = config.replace("telnet 4004", f"telnet {SAMPLE_PORT}")
        with open(os.path.join(SAMPLE_DIR, SAMPLE_CONFIG), "w", encoding="utf-8") as fh:
            fh.write(config)
        open(marker, "w").write("ok\n")
    os.makedirs(os.path.join(SAMPLE_DIR, "log", "nosave"), exist_ok=True)
    os.makedirs(os.path.dirname(SAMPLE_LOG), exist_ok=True)


def stop_sample_driver() -> None:
    run(["pkill", "-f", SAMPLE_PROC_PATTERN])
    end = time.time() + 8
    while time.time() < end:
        if not run(["pgrep", "-f", SAMPLE_PROC_PATTERN]).stdout.strip():
            return
        time.sleep(0.3)


def cycle_fresh_sample() -> None:
    stop_sample_driver()
    shutil.copyfile(SNAPSHOT, SAMPLE_SAVE)
    logfh = open(SAMPLE_LOG, "ab")
    try:
        subprocess.Popen([DRIVER, SAMPLE_CONFIG], cwd=SAMPLE_DIR,
                         stdout=logfh, stderr=logfh, start_new_session=True)
    finally:
        logfh.close()
    if not port_ready(SAMPLE_PORT):
        raise RuntimeError(f"{SAMPLE_PORT} 在 25 秒内未就绪")


def capture(scenario: str, commands: list[str], step: str, outdir: str, run_idx: int) -> str:
    tmp = os.path.join(outdir, f".{scenario}.{run_idx}.json")
    res = run([sys.executable, PROBE, "--port", str(SAMPLE_PORT), "--player-id", PLAYER,
               "--commands", *commands, "--record", tmp])
    if not os.path.exists(tmp):
        raise RuntimeError(f"探针失败：{res.stdout[-400:]}{res.stderr[-400:]}")
    data = json.load(open(tmp))
    os.remove(tmp)
    steps = {s["step"]: s["plain"] for s in data["steps"]}
    if step not in steps:
        raise RuntimeError(f"{scenario} 未找到步骤 {step}")
    raw = steps[step]
    scen_dir = os.path.join(outdir, scenario)
    os.makedirs(scen_dir, exist_ok=True)
    with open(os.path.join(scen_dir, f"run{run_idx}.raw.txt"), "w") as fh:
        fh.write(raw)
    norm = normalize(raw, scenario)
    with open(os.path.join(scen_dir, f"run{run_idx}.txt"), "w") as fh:
        fh.write(norm)
    anchor = ""
    if scenario == "S5_combat":
        anchor = combat_anchor(raw)
        with open(os.path.join(scen_dir, f"run{run_idx}.anchor.txt"), "w") as fh:
            fh.write(anchor)
    return norm, anchor


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def write_stability(outdir: str, results: dict[str, tuple[list[str], list[str]]]) -> None:
    lines = ["# 场景样本稳定性报告", "", f"生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
    lines.append("固定条件：角色 `xkxref`（测试侠客，男，天赋 20/20/20/20），"
                 "每次采样前由存档快照重启采样实例（端口 4005，mudlib-sample）。"
                 "4004 参照服务全程未停。")
    lines.append("")
    for scen, (norms, anchors) in results.items():
        hashes = [sha(n) for n in norms]
        same = len(set(norms)) == 1
        lines.append(f"## {scen}")
        lines.append("")
        lines.append(f"- 归一化后一致：**{'是' if same else '否'}**")
        lines.append(f"- 三次哈希：{', '.join(hashes)}")
        lines.append(f"- 字段说明：{SCENARIO_NOTES.get(scen, '')}")
        if any(anchors):
            anchor_same = len(set(anchors)) == 1
            lines.append(f"- 稳定锚点一致：**{'是' if anchor_same else '否'}**"
                         f"（{', '.join(sha(a) for a in anchors)}）")
        if same:
            lines.append("- 判定：稳定，可直接作为 golden 文件。")
        else:
            lines.append("- 判定：存在差异，见下方 diff；差异按字段说明归类。")
            a, b = norms[0], norms[1] if len(norms) > 1 else norms[0]
            diff = list(difflib.unified_diff(
                a.splitlines(), b.splitlines(), fromfile="run1", tofile="run2", lineterm="", n=2
            ))
            if diff:
                lines.append("")
                lines.append("```diff")
                lines.extend(diff[:60])
                lines.append("```")
        lines.append("")
    open(os.path.join(outdir, "stability.md"), "w").write("\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--outdir", default=os.path.join(REPO, "reports", "xkx-ref", "scenarios"))
    ap.add_argument("--only", default="")
    args = ap.parse_args()

    if not os.path.exists(SNAPSHOT):
        print(f"缺少快照 {SNAPSHOT}，先由固定角色生成", file=sys.stderr)
        return 2
    if not os.path.exists(DRIVER):
        print(f"缺少驱动 {DRIVER}，先跑 build_fluffos_v2019.sh", file=sys.stderr)
        return 2
    os.makedirs(args.outdir, exist_ok=True)
    prepare_sample_mudlib()

    results: dict[str, tuple[list[str], list[str]]] = {}
    try:
        for scen, commands, step in SCENARIOS:
            if args.only and scen != args.only:
                continue
            norms: list[str] = []
            anchors: list[str] = []
            for i in range(1, args.runs + 1):
                print(f"[{scen}] run {i}/{args.runs}", flush=True)
                cycle_fresh_sample()
                norm, anchor = capture(scen, commands, step, args.outdir, i)
                norms.append(norm)
                anchors.append(anchor)
            results[scen] = (norms, anchors)
        if results:
            write_stability(args.outdir, results)
    finally:
        stop_sample_driver()

    print("采样结束，采样实例已停止；4004 参照服务未受影响")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
