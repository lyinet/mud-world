#!/usr/bin/env python3
"""Minimal telnet probe for the reference xkx100 mudlib.

Purpose: drive a real login session against a running FluffOS driver and
record the exact server output (ANSI stripped and raw) for parity baselines.

Usage:
  python3 tools/xkx_ref/probe_mud.py --port 4004 --record reports/xkx-ref/session.json \
      --commands look score

The script never writes into src/, ui/, content/ or config/.
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import sys
import time

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
FULLWIDTH_SPACE = "\u3000"


class TelnetSession:
    def __init__(self, host: str, port: int, log_path: str | None = None):
        self.sock = socket.create_connection((host, port), timeout=5)
        self.sock.settimeout(0.2)
        self.log_path = log_path
        self.raw_log: list[bytes] = []
        self.events: list[dict] = []

    def _recv(self, idle: float = 0.5, max_wait: float = 8.0) -> str:
        """Read until the socket stays quiet for `idle` seconds."""
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
        text = self._filter_telnet(buf)
        self.raw_log.append(buf)
        return text

    def recv_for(self, seconds: float) -> str:
        """Read continuously for `seconds`, regardless of quiet periods."""
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
        return self._filter_telnet(buf)

    @staticmethod
    def _filter_telnet(buf: bytes) -> str:
        out = bytearray()
        i = 0
        while i < len(buf):
            b = buf[i]
            if b == 0xFF:  # IAC
                if i + 1 < len(buf) and buf[i + 1] == 0xFF:
                    out.append(0xFF)
                    i += 2
                    continue
                i += 3  # skip IAC + command + option
                continue
            out.append(b)
            i += 1
        return out.decode("utf-8", errors="replace").replace("\r\n", "\n")

    def send(self, text: str) -> None:
        self.sock.sendall(text.encode("utf-8") + b"\r\n")
        self.events.append({"dir": "out", "text": text, "t": round(time.time(), 3)})

    def close(self) -> None:
        self.sock.close()
        if self.log_path:
            with open(self.log_path, "wb") as fh:
                for chunk in self.raw_log:
                    fh.write(chunk)


def plain(text: str) -> str:
    return ANSI_RE.sub("", text)


CREATE_RULES = [
    (r"您确定吗\(y/n\)", "y"),
    (r"您的中文名字", "测试侠客"),
    (r"请设定您的密码", "xkxref2026"),
    (r"请再输入一次您的密码", "xkxref2026"),
    (r"选择你想出生的地区范围", "3"),
    (r"请输入您的膂力点数", "20"),
    (r"请输入您的悟性点数", "20"),
    (r"请输入您的根骨点数", "20"),
    (r"您同意这一组天赋吗", "y"),
    (r"0、普通", "0"),
    (r"您的电子邮件地址", "xkx-ref@example.com"),
    (r"男性\(m\)的角色或女性\(f\)", "m"),
    (r"请输入密码", "xkxref2026"),
    (r"你叫什么名字", "测试侠客"),
]


def drive_login(sess: TelnetSession, args) -> str:
    """Handle account creation / login until the character enters the world."""
    transcript = ""
    sent_gender = False
    idle_rounds = 0
    for _ in range(60):
        text = sess._recv(idle=0.6, max_wait=10)
        transcript += text
        if not text.strip():
            idle_rounds += 1
            if sent_gender and idle_rounds >= 2:
                break
            continue
        idle_rounds = 0
        shown = plain(text)
        matched = None
        for pattern, answer in CREATE_RULES:
            if re.search(pattern, shown):
                matched = answer
                break
        if matched is None:
            if "的英文名字" in shown and "重新" not in shown:
                matched = args.player_id
            elif "的英文名字" in shown:
                matched = args.player_id
        if matched is not None:
            if matched == "m":
                sent_gender = True
            sess.send(matched)
        else:
            # A room header looks like "<title> - \n"; once it shows up and no
            # creation question is pending, the character is in the world.
            if "明显的出口" in shown or "你看不清明显的出路" in shown:
                break
            idle_rounds += 1
            if idle_rounds >= 2:
                break
    return transcript


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=4004)
    ap.add_argument("--player-id", default="xkxref")
    ap.add_argument("--commands", nargs="*", default=["look", "score"])
    ap.add_argument("--record", default="")
    ap.add_argument("--raw-log", default="")
    args = ap.parse_args()

    sess = TelnetSession(args.host, args.port, args.raw_log or None)
    steps = []
    try:
        login_out = drive_login(sess, args)
        steps.append({"step": "login", "raw": login_out, "plain": plain(login_out)})
        # settle: some rooms emit follow-up lines after entering
        settle = sess._recv(idle=0.8, max_wait=6)
        steps.append({"step": "enter_world", "raw": settle, "plain": plain(settle)})
        for spec in args.commands:
            cmd, _, secs = spec.partition("|")
            sess.send(cmd)
            if secs:
                out = sess.recv_for(float(secs))
            else:
                out = sess._recv(idle=0.8, max_wait=8)
            steps.append({"step": spec, "raw": out, "plain": plain(out)})
            print(f"\n=== {spec} ===")
            print(plain(out))
    finally:
        sess.close()

    if args.record:
        with open(args.record, "w", encoding="utf-8") as fh:
            json.dump({"player_id": args.player_id, "steps": steps}, fh, ensure_ascii=False, indent=2)
        print(f"\n[recorded] {args.record}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
