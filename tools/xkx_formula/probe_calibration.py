#!/usr/bin/env python3
"""实测校准探针：通过 telnet 驱动本机参照版 xkx100（127.0.0.1:4004）。

本脚本只放在 tools/xkx_formula/ 下，不读写 tools/xkx_ref/ 的任何文件。
用途：建立可复现场景（固定角色/固定对手/固定命令序列），采集原版真实输出，
把公式库里的“推导值”升级为“推导值 + 实测校准”。

用法：
  # 登录已有角色并执行命令（每次命令后可跟 |秒数 指定等待）
  python3 tools/xkx_formula/probe_calibration.py --player-id xkxref \
      --commands "score" "hp" "look"

  # 新建固定属性角色（region 3，20/20/20，男性）
  python3 tools/xkx_formula/probe_calibration.py --player-id xkxcal \
      --create --commands "score" "hp" "look"

  # 连续攻击场景：走到目标房间后 fight 对手，持续采样
  python3 tools/xkx_formula/probe_calibration.py --player-id xkxref \
      --fight-scene kongxiaotian --duration 60 --record out.json

所有原始/去 ANSI 输出都记录到 --record 指定的 JSON；不写任何参照版文件。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import time

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
TELNET_NEG = re.compile(rb"\xff[\xfb-\xfe].", re.S)

# ---------------------------------------------------------------- telnet


class TelnetSession:
    def __init__(self, host: str, port: int, name: str = ""):
        self.name = name
        self.sock = socket.create_connection((host, port), timeout=5)
        self.sock.settimeout(0.2)
        self.raw_log: list[bytes] = []
        self.out_log: list[str] = []

    @staticmethod
    def _filter(buf: bytes) -> str:
        # 去掉 telnet IAC 协商，保留正文
        buf = TELNET_NEG.sub(b"", buf)
        buf = buf.replace(b"\xff\xff", b"\xff")
        return buf.decode("utf-8", errors="replace").replace("\r\n", "\n")

    def recv(self, idle: float = 0.5, max_wait: float = 8.0) -> str:
        buf = b""
        start = time.time()
        last = time.time()
        while True:
            try:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
                last = time.time()
            except socket.timeout:
                if time.time() - last >= idle:
                    break
            if time.time() - start > max_wait:
                break
        self.raw_log.append(buf)
        text = self._filter(buf)
        self.out_log.append(text)
        return text

    def recv_for(self, seconds: float) -> str:
        buf = b""
        end = time.time() + seconds
        while time.time() < end:
            try:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
            except socket.timeout:
                pass
        self.raw_log.append(buf)
        text = self._filter(buf)
        self.out_log.append(text)
        return text

    def send(self, text: str) -> None:
        self.sock.sendall(text.encode("utf-8") + b"\r\n")
        self.out_log.append(f"<<SEND>> {text}")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def plain(text: str) -> str:
    return ANSI_RE.sub("", text)


# ---------------------------------------------------------------- login

CREATE_RULES = [
    (r"赶出去吗.*\(y/n\)", "y"),
    (r"您确定吗\(y/n\)", "y"),
    (r"您的中文名字", "实测侠客"),
    (r"请设定您的密码", ""),
    (r"请再输入一次您的密码", ""),
    (r"选择你想出生的地区范围", "3"),
    (r"请输入您的膂力点数", "20"),
    (r"请输入您的悟性点数", "20"),
    (r"请输入您的根骨点数", "20"),
    (r"您同意这一组天赋吗", "y"),
    (r"0、普通", "0"),
    (r"您的电子邮件地址", "xkx-cal@example.com"),
    (r"男性\(m\)的角色或女性\(f\)", "m"),
    (r"请输入密码", ""),
    (r"你叫什么名字", "实测侠客"),
]

IN_WORLD_MARKS = ("这里明显的出口是", "这里明显的出路是", "你看不清明显的出路")


def drive_login(sess: TelnetSession, player_id: str, password: str,
                cn_name: str, create: bool) -> str:
    """处理登录/建号，直到角色进入世界。

    与 probe_mud.py 不同的是：这里按“最近一段输出里是否出现某个问题”逐条应答，
    并且对同一问题只应答一次，避免把命令输入当成新的中文名字。
    """
    transcript = ""
    answered: dict[str, int] = {}
    for _ in range(120):
        text = sess.recv(idle=0.5, max_wait=8)
        transcript += text
        shown = plain(text)
        if not shown.strip():
            continue
        if any(m in shown for m in IN_WORLD_MARKS):
            # 可能同一批输出里还有提示，再读一轮确认
            extra = sess.recv(idle=0.5, max_wait=4)
            transcript += extra
            if any(m in plain(extra) for m in IN_WORLD_MARKS):
                break
        # 首要：登录横幅可能把“英文名字”和“密码”拼在同一段里。
        # 永远先回答英文名字；若名字非法被重问，也在这里重发。
        if "的英文名字" in shown:
            sess.send(player_id)
            continue
        answer = None
        key = None
        for pattern, resp in CREATE_RULES:
            if re.search(pattern, shown):
                key = pattern
                # 密码相关提示统一用调用方传入的 password，避免建号密码写死
                if pattern in ("请设定您的密码", "请再输入一次您的密码", "请输入密码"):
                    resp = password
                answer = resp
                # 同一 prompt 在同一段里只答一次
                if answered.get(pattern, 0) >= 2:
                    answer = None
                break
        if answer is not None:
            answered[key] = answered.get(key, 0) + 1
            sess.send(answer)
        elif create and "你叫什么名字" in shown:
            sess.send(cn_name)
    return transcript


# ---------------------------------------------------------------- commands


def run_command(sess: TelnetSession, cmd: str, wait: float | None) -> str:
    sess.send(cmd)
    if wait:
        return sess.recv_for(wait)
    return sess.recv(idle=0.6, max_wait=8)


FIGHT_END = ("承让了", "哈哈大笑", "罢手", "住手", "不打了")
FIGHT_REFUSE = ("少惹为妙", "禁止战斗", "你想攻击谁", "并不是生物")


def collect_fight(sess: TelnetSession, max_seconds: float) -> str:
    """收集一次战斗输出，出现“结束/拒绝”标记即提前返回。"""
    text = ""
    end = time.time() + max_seconds
    while time.time() < end:
        chunk = sess.recv(idle=0.6, max_wait=2.0)
        text += chunk
        shown = plain(chunk)
        if any(m in shown for m in FIGHT_END + FIGHT_REFUSE):
            break
    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=4004)
    ap.add_argument("--player-id", default="xkxref")
    ap.add_argument("--password", default="xkxref2026")
    ap.add_argument("--cn-name", default="实测侠客")
    ap.add_argument("--create", action="store_true", help="建号（固定 20/20/20、region 3）")
    ap.add_argument("--commands", nargs="*", default=["score", "hp", "look"])
    ap.add_argument("--walk", nargs="*", default=[], help="战斗前逐个执行的移动方向")
    ap.add_argument("--fight", default="", help="循环挑战的目标 id/中文名")
    ap.add_argument("--rounds", type=int, default=0, help="战斗轮数")
    ap.add_argument("--round-seconds", type=float, default=60.0)
    ap.add_argument("--regen-wait", type=float, default=12.0,
                    help="两轮之间等待对手回气的秒数")
    ap.add_argument("--record", default="")
    args = ap.parse_args()

    sess = TelnetSession(args.host, args.port, args.player_id)
    steps: list[dict] = []
    try:
        login_out = drive_login(sess, args.player_id, args.password,
                                args.cn_name, args.create)
        steps.append({"step": "login", "plain": plain(login_out)})
        # 进入世界后先稳定一下
        settle = sess.recv_for(1.0)
        steps.append({"step": "settle", "plain": plain(settle)})
        for spec in args.commands:
            cmd, _, secs = spec.partition("|")
            try:
                out = run_command(sess, cmd, float(secs) if secs else None)
            except (BrokenPipeError, OSError) as exc:
                steps.append({"step": spec, "error": repr(exc)})
                print(f"\n=== {spec} ===\n[connection lost] {exc}",
                      file=sys.stderr)
                break
            steps.append({"step": spec, "plain": plain(out)})
            print(f"\n=== {spec} ===")
            print(plain(out))

        for direction in args.walk:
            out = run_command(sess, direction, 1.0)
            steps.append({"step": f"walk:{direction}", "plain": plain(out)})

        for r in range(args.rounds):
            tries = 0
            while True:
                sess.send(f"fight {args.fight}")
                out = collect_fight(sess, args.round_seconds)
                shown = plain(out)
                refused = any(m in shown for m in FIGHT_REFUSE)
                steps.append({"step": f"fight#{r + 1}", "plain": shown,
                              "refused": refused})
                if not refused or tries >= 3:
                    break
                tries += 1
                time.sleep(args.regen_wait)
            hp = run_command(sess, "hp", 0.6)
            steps.append({"step": f"hp#{r + 1}", "plain": plain(hp)})
            print(f"\n=== fight#{r + 1} (refused={refused}) ===")
            print(shown)
            if r < args.rounds - 1:
                time.sleep(args.regen_wait)
    finally:
        # 尽量正常 quit，避免留下 linkdead 会话占住角色
        try:
            sess.send("quit")
            sess.recv_for(0.5)
        except OSError:
            pass
        sess.close()
        if args.record:
            parent = os.path.dirname(os.path.abspath(args.record))
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(args.record, "w", encoding="utf-8") as fh:
                json.dump({"player_id": args.player_id, "create": args.create,
                           "steps": steps, "trace": sess.out_log}, fh,
                          ensure_ascii=False, indent=2)
            print(f"\n[recorded] {args.record}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
