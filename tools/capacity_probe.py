#!/usr/bin/env python3
"""单进程容量探针：把锁等待、队列深度、快照拷贝、读写服务时间量出来。

不启动 HTTP：直接在进程内调用 WorldSession，用 TimedLock 代理替换
session.lock，统计（不含客户端与网络开销的）服务端真实成本。

用法：
  PYTHONPATH=src .venv/bin/python tools/capacity_probe.py --players 150 --duration 45
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from load_test import Locations, percentiles  # noqa: E402
from wsim.api.session import WorldSession  # noqa: E402
from wsim.world.state import WorldState  # noqa: E402


def _ms(rows: list[float]) -> dict:
    if not rows:
        return {}
    summary = percentiles(rows)
    return {k: (int(v) if k == "count" else round(float(v) * 1000.0, 3)) for k, v in summary.items()}


class LockStats:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.wait_times: list[float] = []
        self.hold_times: list[float] = []
        self.waiters_at_acquire: list[int] = []
        self.max_waiters = 0

    def add_wait(self, seconds: float) -> None:
        with self._lock:
            self.wait_times.append(seconds)

    def add_hold(self, seconds: float) -> None:
        with self._lock:
            self.hold_times.append(seconds)

    def waiting(self, delta: int) -> None:
        with self._lock:
            self._waiting = max(0, getattr(self, "_waiting", 0) + delta)
            self.max_waiters = max(self.max_waiters, self._waiting)

    def snapshot_waiters(self) -> int:
        with self._lock:
            self.waiters_at_acquire.append(getattr(self, "_waiting", 0))
            return self._waiting

    def summary(self) -> dict:
        with self._lock:
            return {
                "acquires": len(self.wait_times),
                "wait_ms": _ms(self.wait_times),
                "hold_ms": _ms(self.hold_times),
                "max_waiters": self.max_waiters,
                "waiters_p50": (sorted(self.waiters_at_acquire)[len(self.waiters_at_acquire) // 2]
                                if self.waiters_at_acquire else 0),
                "waiters_p95": (sorted(self.waiters_at_acquire)[
                    min(len(self.waiters_at_acquire) - 1,
                        int(len(self.waiters_at_acquire) * 0.95))] if self.waiters_at_acquire else 0),
            }


class TimedLock:
    """RLock 代理：统计外层等待、持有时长和等待线程数。"""

    def __init__(self, inner, stats: LockStats) -> None:
        self._inner = inner
        self._stats = stats
        self._local = threading.local()

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        depth = getattr(self._local, "depth", 0)
        if depth:
            ok = self._inner.acquire(blocking, timeout)
            if ok:
                self._local.depth = depth + 1
            return ok
        started = time.perf_counter()
        self._stats.waiting(1)
        ok = self._inner.acquire(blocking, timeout)
        if ok:
            self._stats.waiting(-1)
            self._stats.add_wait(time.perf_counter() - started)
            self._stats.snapshot_waiters()
            self._local.depth = 1
            self._local.hold_started = time.perf_counter()
        else:
            self._stats.waiting(-1)
        return ok

    def release(self) -> None:
        depth = getattr(self._local, "depth", 0)
        if depth <= 1:
            started = getattr(self._local, "hold_started", None)
            if started is not None:
                self._stats.add_hold(time.perf_counter() - started)
            self._local.depth = 0
            self._inner.release()
            return
        self._local.depth = depth - 1
        self._inner.release()

    def __enter__(self) -> "TimedLock":
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()

    def _is_owned(self) -> bool:
        return bool(getattr(self._local, "depth", 0))


class Timers:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.clone_s: list[float] = []
        self.apply_s: list[float] = []
        self.append_s: list[float] = []
        self.read_s: list[float] = []
        self.write_s: list[float] = []

    def add(self, bucket: list[float], seconds: float) -> None:
        with self.lock:
            bucket.append(seconds)

    def summary(self) -> dict:
        with self.lock:
            return {
                "clone_ms": _ms(self.clone_s),
                "apply_ms": _ms(self.apply_s),
                "append_ms": _ms(self.append_s),
                "read_ms": _ms(self.read_s),
                "write_ms": _ms(self.write_s),
            }


def _instrument_clone(timers: Timers) -> None:
    original = WorldState.clone

    def timed_clone(self):  # type: ignore[no-untyped-def]
        started = time.perf_counter()
        result = original(self)
        timers.add(timers.clone_s, time.perf_counter() - started)
        return result

    WorldState.clone = timed_clone  # type: ignore[method-assign]


def _instrument_append(session: WorldSession, timers: Timers) -> None:
    original = session.store.append

    def timed_append(event):  # type: ignore[no-untyped-def]
        started = time.perf_counter()
        result = original(event)
        timers.add(timers.append_s, time.perf_counter() - started)
        return result

    session.store.append = timed_append  # type: ignore[method-assign]


def _instrument_apply(timers: Timers) -> None:
    from wsim.world import writer as writer_module

    original = writer_module.apply_effects

    def timed_apply(state, effects):  # type: ignore[no-untyped-def]
        started = time.perf_counter()
        result = original(state, effects)
        timers.add(timers.apply_s, time.perf_counter() - started)
        return result

    writer_module.apply_effects = timed_apply  # type: ignore[assignment]


def _account(i: int) -> str:
    return f"bench{i:04d}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--players", type=int, default=150)
    parser.add_argument("--duration", type=float, default=45)
    parser.add_argument("--think-time", type=float, default=2.0)
    parser.add_argument("--reps", type=int, default=60)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    run_id = f"probe-{args.players}-{int(time.time())}"
    run_dir = ROOT / "runs" / run_id
    if run_dir.exists():
        shutil.rmtree(run_dir)
    session = WorldSession(ROOT, run_id=run_id, seed=42, run_dir=run_dir)
    timers = Timers()
    stats = LockStats()
    session.lock = TimedLock(threading.RLock(), stats)  # type: ignore[assignment]
    _instrument_clone(timers)
    _instrument_append(session, timers)
    _instrument_apply(timers)
    locations = Locations(ROOT)

    accounts = [_account(i) for i in range(args.players)]
    entered = 0
    for i, account in enumerate(accounts):
        response = session.perform(
            account,
            "enter",
            {
                "account_id": account,
                "display_name": f"容量探针{i:03d}",
                "archetype": ("游侠", "商人", "捕快")[i % 3],
            },
            advance=False,
        )
        if response.get("ok"):
            entered += 1
    state = session.state

    def location_of(account: str) -> str:
        return session.state.entities[session._pid(account)].location

    # ---------------- 单线程微基准（归因用）
    micro: dict[str, dict] = {}
    for label, fn, reps in (
        ("clone", lambda: session.state.clone(), args.reps),
        ("read_player_state", lambda: session.player_state(accounts[0]), args.reps),
        ("write_move", None, args.reps),
    ):
        durations: list[float] = []
        for _ in range(reps):
            if label == "write_move":
                loc = location_of(accounts[0])
                dest = locations.random_exit(loc)
                started = time.perf_counter()
                session.perform(accounts[0], "move", {"to": dest})
                durations.append(time.perf_counter() - started)
            else:
                started = time.perf_counter()
                fn()
                durations.append(time.perf_counter() - started)
        micro[label] = _ms(durations)

    # ---------------- 并发基准
    stop = threading.Event()
    deadline = time.monotonic() + args.duration
    think = max(0.0, args.think_time)
    started = time.monotonic()

    def worker(index: int) -> None:
        account = accounts[index]
        i = 0
        while not stop.is_set() and time.monotonic() < deadline:
            t0 = time.perf_counter()
            session.player_state(account)
            timers.add(timers.read_s, time.perf_counter() - t0)
            t0 = time.perf_counter()
            loc = location_of(account)
            dest = locations.random_exit(loc)
            session.perform(account, "move", {"to": dest})
            timers.add(timers.write_s, time.perf_counter() - t0)
            i += 1
            if think:
                stop.wait(think)

    threads = [threading.Thread(target=worker, args=(i,), daemon=True, name=f"probe-{i}")
               for i in range(args.players)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    stop.set()
    elapsed = time.monotonic() - started

    lock_summary = stats.summary()
    timer_summary = timers.summary()
    result = {
        "schema": "capacity-probe/v1",
        "players": args.players,
        "entered": entered,
        "duration_s": round(elapsed, 2),
        "think_time_s": think,
        "world_size": {
            "entities": len(session.state.entities),
            "players": len(session.state.players),
            "accounts": len(session.state.accounts),
            "businesses": len(session.state.businesses),
            "quests": len(getattr(session.state, "quests", {})),
            "escorts": len(getattr(session.state, "escorts", {})),
            "total_tick": int(session.state.tick),
        },
        "lock": lock_summary,
        "timers": timer_summary,
        "microbench_ms": micro,
        "throughput": {
            "reads_per_second": round(len(timers.read_s) / max(elapsed, 1e-9), 2),
            "writes_per_second": round(len(timers.write_s) / max(elapsed, 1e-9), 2),
            "clone_ms_per_write": round(
                (statistics.fmean(timers.clone_s) * 1000.0) if timers.clone_s else 0.0, 3),
            "append_ms_per_write": round(
                (statistics.fmean(timers.append_s) * 1000.0) if timers.append_s else 0.0, 3),
        },
        "cpu_seconds": round(time.process_time(), 2),
    }
    out = args.out or (ROOT / "reports" / "capacity" / f"probe-{args.players}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: result[k] for k in ("players", "entered", "duration_s", "lock",
                                            "throughput", "microbench_ms", "world_size")},
                     ensure_ascii=False, indent=2))
    session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
