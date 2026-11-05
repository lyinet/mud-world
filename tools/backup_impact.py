#!/usr/bin/env python3
"""备份对写路径影响的对照压测：N 并发读 + M 并发写，输出读写 P50/P95/P99/max。

用于 docs/BACKUP-RESTORE.md 的硬验收 D：同一口径跑两次——
一次无备份（基线），一次备份工具并发执行（对照）。

用法：
    python3 tools/backup_impact.py --base-url http://127.0.0.1:8099 \
        --duration 30 --readers 20 --writers 10 --tag impact-a --report reports/p0/impact-baseline.json

鉴权：默认 `--auth on`，脚本自己注册/登录；服务端用 `--auth off` 时传 `--auth off`。

写路径取 `POST /api/player/talk`（当前地点无 NPC 时退化为 `POST /api/player/action cultivate`）。
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "bk-probe-pass123"
ARCHETYPES = ("游侠", "商人", "捕快")


def _request(
    base: str,
    path: str,
    *,
    method: str = "GET",
    body: dict | None = None,
    token: str = "",
    timeout: float = 10.0,
) -> tuple[int, dict, float, str]:
    url = base.rstrip("/") + path
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    started = time.perf_counter()
    status = 0
    payload: dict = {}
    error = ""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = int(resp.status)
            raw = resp.read()
            payload = json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:
            payload = {}
    except Exception as exc:  # URLError / timeout / reset / json
        error = f"{type(exc).__name__}: {exc}"
    return status, payload, (time.perf_counter() - started) * 1000.0, error


def _reason(payload: dict) -> str:
    return str(payload.get("code") or payload.get("reject_reason") or "")


def percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {"count": 0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0, "max": 0.0}
    ordered = sorted(values)
    n = len(ordered)

    def _pct(p: float) -> float:
        idx = max(0, min(n - 1, int(math.ceil(p / 100.0 * n)) - 1))
        return round(ordered[idx], 3)

    return {
        "count": n,
        "p50": _pct(50),
        "p95": _pct(95),
        "p99": _pct(99),
        "mean": round(statistics.fmean(ordered), 3),
        "max": round(ordered[-1], 3),
    }


class Account:
    def __init__(self, base: str, account: str, display: str, archetype: str, auth: bool):
        self.base = base
        self.account = account
        self.display = display
        self.archetype = archetype
        self.auth = auth
        self.token = ""
        self.target = ""

    def register_login(self) -> bool:
        if not self.auth:
            return True
        status, payload, _ms, err = _request(
            self.base, "/api/auth/register", method="POST",
            body={"username": self.account, "password": PASSWORD},
        )
        if err or status not in (200, 409):
            return False
        status, payload, _ms, err = _request(
            self.base, "/api/auth/login", method="POST",
            body={"username": self.account, "password": PASSWORD},
        )
        if err or status != 200 or not payload.get("ok"):
            return False
        self.token = str(payload.get("token") or "")
        return bool(self.token)

    def enter(self) -> bool:
        body = {"display_name": self.display, "archetype": self.archetype}
        if not self.auth:
            body["account_id"] = self.account
        status, payload, _ms, err = _request(
            self.base, "/api/player/enter", method="POST", body=body, token=self.token
        )
        if err or status != 200:
            return False
        code = _reason(payload)
        return bool(payload.get("ok")) or code in ("already_online", "player_already_online")

    def state(self) -> dict:
        status, payload, _ms, err = _request(
            self.base, f"/api/player/state?account_id={self.account}", token=self.token
        )
        if err or status != 200:
            return {}
        return payload


def _setup_accounts(base: str, count: int, tag: str, auth: bool) -> list[Account]:
    accounts: list[Account] = []
    for i in range(count):
        acct = Account(
            base,
            f"bk{tag}{i:02d}",
            f"备{tag}{i:02d}"[:12],
            ARCHETYPES[i % len(ARCHETYPES)],
            auth,
        )
        if not acct.register_login() or not acct.enter():
            raise RuntimeError(f"账号准备失败: {acct.account}")
        accounts.append(acct)
    return accounts


def run(args: argparse.Namespace) -> dict:
    base = args.base_url.rstrip("/")
    auth = args.auth == "on"
    tag = args.tag or f"{int(time.time()) % 100000:05d}"
    total_accounts = max(args.readers, args.writers)
    accounts = _setup_accounts(base, total_accounts, tag, auth)

    stop = threading.Event()
    started = time.perf_counter()
    lock = threading.Lock()
    stats: dict[str, object] = {
        "reads": [],
        "writes": [],
        "read_errors": 0,
        "write_errors": 0,
        "write_success": 0,
        "rejects": Counter(),
        "samples": [],
    }

    def _record_error(kind: str, text: str) -> None:
        with lock:
            stats[f"{kind}_errors"] = int(stats[f"{kind}_errors"]) + 1  # type: ignore[index]
            samples = stats["samples"]
            if isinstance(samples, list) and len(samples) < 20:
                samples.append(text)

    def reader(acct: Account) -> None:
        while not stop.is_set():
            status, payload, ms, err = _request(
                base, f"/api/state?account_id={acct.account}", token=acct.token
            )
            with lock:
                reads = stats["reads"]
                if isinstance(reads, list):
                    reads.append(ms)
            if err or status != 200:
                _record_error("read", f"HTTP {status} {_reason(payload)} {err}".strip())

    def writer(acct: Account) -> None:
        while not stop.is_set():
            state = acct.state()
            present = (state.get("present_npcs") or []) if state.get("ok") else []
            if present and random.random() < 0.8:
                target = str(random.choice(present).get("id") or "")
                body = {"target": target, "message": "备份压测：今日生意如何？"}
                path = "/api/player/talk"
            else:
                body = {"verb": "cultivate", "args": {"hours": 2}}
                path = "/api/player/action"
            if not auth:
                body["account_id"] = acct.account
            status, payload, ms, err = _request(
                base, path, method="POST", body=body, token=acct.token
            )
            with lock:
                writes = stats["writes"]
                if isinstance(writes, list):
                    writes.append(ms)
            if err or status != 200:
                _record_error("write", f"HTTP {status} {_reason(payload)} {err}".strip())
            elif not payload.get("ok"):
                code = _reason(payload)
                with lock:
                    stats["rejects"][code or "unknown_business_reject"] += 1  # type: ignore[index]
                if code in ("player_offline", "player_not_entered"):
                    acct.enter()
            else:
                with lock:
                    stats["write_success"] = int(stats["write_success"]) + 1  # type: ignore[index]
            if args.think_ms:
                time.sleep(args.think_ms / 1000.0)

    threads = [
        threading.Thread(target=reader, args=(accounts[i % len(accounts)],), daemon=True)
        for i in range(args.readers)
    ]
    threads += [
        threading.Thread(target=writer, args=(accounts[i % len(accounts)],), daemon=True)
        for i in range(args.writers)
    ]
    for thread in threads:
        thread.start()
    try:
        while time.perf_counter() - started < args.duration:
            time.sleep(0.05)
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=3.0)
    elapsed = time.perf_counter() - started

    result = {
        "ok": int(stats["write_success"]) > 0 and int(stats["write_errors"]) == 0,
        "config": {
            "base_url": base,
            "duration_s": args.duration,
            "readers": args.readers,
            "writers": args.writers,
            "think_ms": args.think_ms,
            "auth": args.auth,
            "tag": tag,
        },
        "elapsed_s": round(elapsed, 3),
        "reads": {
            "latency_ms": percentiles(list(stats["reads"])) if isinstance(stats["reads"], list) else {},
            "errors": stats["read_errors"],
        },
        "writes": {
            "latency_ms": percentiles(list(stats["writes"])) if isinstance(stats["writes"], list) else {},
            "attempted": len(stats["writes"]) if isinstance(stats["writes"], list) else 0,
            "success": stats["write_success"],
            "errors": stats["write_errors"],
            "rejects": dict(sorted(stats["rejects"].items())) if isinstance(stats["rejects"], Counter) else {},
        },
        "error_samples": stats["samples"],
    }
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="备份期间的读写延迟对照")
    parser.add_argument("--base-url", default="http://127.0.0.1:8020")
    parser.add_argument("--duration", type=float, default=30.0, help="压测时长（秒）")
    parser.add_argument("--readers", type=int, default=20)
    parser.add_argument("--writers", type=int, default=10)
    parser.add_argument("--think-ms", type=float, default=50.0, help="写线程每轮间隔（毫秒）")
    parser.add_argument("--auth", choices=("on", "off"), default="on")
    parser.add_argument("--tag", default="", help="账号后缀，默认时间戳")
    parser.add_argument("--report", default="", help="JSON 报告输出路径")
    args = parser.parse_args(argv)
    try:
        result = run(args)
    except Exception as exc:
        print(f"[impact] FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    text = json.dumps(result, ensure_ascii=False, sort_keys=True)
    print(text)
    if args.report:
        path = Path(args.report)
        if not path.is_absolute():
            path = ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
