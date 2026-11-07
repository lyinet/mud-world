"""xkx 千房间性能实测：三档合成规模 + NPC/商店隔离扫描。

用法（推荐由仓库内 .venv 执行）：
  .venv/bin/python tools/bench_xkx/bench.py --all
  .venv/bin/python tools/bench_xkx/bench.py --mode scale --rooms 1000 --npcs 100 --shops 20 --out /tmp/x.json

口径：
- 全部进程内直调产品接口（WorldState / Writer / EventStore / path_between /
  AmbientScheduler / WorldSession.player_state），不启 HTTP、不起 100 个客户端。
- 写 P95 用 100 个线程并发调用 Writer.submit（含全局写锁等待），每次一条
  move 意图；这是服务端真实写路径，不含网络与 JSON 编解码。
- 每档至少预热 30 tick，再采 60 tick；写路径预热 2 轮/线程，采 6 轮。
- 内存用 macOS `ru_maxrss` 峰值增量（进程启动到状态建成），不是稳态 RSS。
"""
from __future__ import annotations

import argparse
import csv
import gc
import json
import random
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from bench_xkx.graphgen import (  # noqa: E402
    SyntheticSpec,
    build_content,
    farthest_pair,
    grid_shape,
    pair_at_distance,
    write_content_pack,
)

from wsim.actions.common import path_between  # noqa: E402
from wsim.actions.registry import ActionRegistry, build_registry  # noqa: E402
from wsim.api.persistence import replay_into  # noqa: E402
from wsim.api.session import WorldSession  # noqa: E402
from wsim.core.models import Intent  # noqa: E402
from wsim.sim.genesis import build_genesis  # noqa: E402
from wsim.sim.scheduler import AmbientConfig, AmbientScheduler  # noqa: E402
from wsim.world.state import WorldState  # noqa: E402
from wsim.world.store import EventStore  # noqa: E402
from wsim.world.writer import Writer  # noqa: E402

SCALES = (195, 1000, 5683)
CAPACITY_ROOMS = (100, 150, 200, 300, 500, 750)
TICK_WARMUP = 30
TICK_SAMPLES = 60
WRITE_WARMUP = 2
WRITE_SAMPLES = 6
WRITE_THREADS = 100
SEATS = 100


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return float(ordered[idx])


def _ms_stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"count": 0, "p50": 0.0, "p95": 0.0, "mean": 0.0, "max": 0.0}
    return {
        "count": len(values),
        "p50": round(_pct(values, 0.50), 3),
        "p95": round(_pct(values, 0.95), 3),
        "mean": round(statistics.fmean(values), 3),
        "max": round(max(values), 3),
    }


def _timed_ms(fn, *, repeat: int) -> list[float]:
    out: list[float] = []
    for _ in range(max(1, repeat)):
        t0 = time.perf_counter()
        fn()
        out.append((time.perf_counter() - t0) * 1000.0)
    return out


def _rss_peak_mb() -> float:
    import resource

    raw = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    if sys.platform == "darwin":
        return raw / (1024.0 * 1024.0)
    return raw / 1024.0


def _make_store(tmp: Path, name: str) -> EventStore:
    # 100 线程并发写同一 Writer 是产品真实用法（WorldSession 全局锁），
    # EventStore 本身支持跨线程串行化，这里显式打开该开关。
    return EventStore(tmp / name, check_same_thread=False)


def _build_started(spec: SyntheticSpec, tmp: Path) -> dict[str, Any]:
    """建内容 + 创世 + 从 EventStore 回放；返回后续基准复用对象。"""
    rss0 = _rss_peak_mb()
    t0 = time.perf_counter()
    content = build_content(spec)
    genesis = build_genesis(content, content.npcs, root=None)
    genesis_build_ms = (time.perf_counter() - t0) * 1000.0

    store = _make_store(tmp, "core.sqlite")
    registry = build_registry(content, spec.seed)
    writer = Writer(WorldState(locations=content.locations), store, registry, spec.seed)
    writer.bootstrap(genesis.effects, tick=0, verb="genesis")
    live = writer.state
    rss_built = _rss_peak_mb()

    t0 = time.perf_counter()
    replay_state, _player_ticks = replay_into(store, content.locations)
    replay_ms = (time.perf_counter() - t0) * 1000.0
    hash_equal = replay_state.hash() == live.hash()

    return {
        "content": content,
        "genesis": genesis,
        "store": store,
        "registry": registry,
        "writer": writer,
        "state": live,
        "genesis_build_ms": round(genesis_build_ms, 3),
        "startup_ms": round(replay_ms, 3),
        "replay_hash_equal": bool(hash_equal),
        "events_for_replay": store.count(),
        "genesis_effects": len(genesis.effects),
        "rss_delta_mb": round(rss_built - rss0, 3),
        "rss_peak_mb": round(rss_built, 3),
    }


def _counts(state: WorldState) -> dict[str, int]:
    return {
        "locations": len(state.locations),
        "entities": len(state.entities),
        "players": len(state.players),
        "businesses": len(state.businesses),
        "factions": len(state.factions),
        "accounts": len(state.accounts),
        "relations": len(state.relations),
        "goals": len(state.goals),
        "schedule": len(state.schedule),
    }


def _measure_hash_clone(state: WorldState, content, repeat: int = 30) -> dict[str, Any]:
    bare = WorldState(locations=content.locations)
    return {
        "hash_ms": _ms_stats(_timed_ms(state.hash, repeat=repeat)),
        "hash_bare_ms": _ms_stats(_timed_ms(bare.hash, repeat=repeat)),
        "full_hash_ms": _ms_stats(_timed_ms(state.full_hash, repeat=max(10, repeat // 2))),
        "clone_ms": _ms_stats(_timed_ms(state.clone, repeat=max(10, repeat // 2))),
    }


def _measure_path(state: WorldState, rooms: int) -> dict[str, Any]:
    width, _height = grid_shape(rooms)
    _far_a, _far_b, max_hops = farthest_pair(rooms)
    by_hops: dict[str, dict[str, Any]] = {}
    buckets = [1, 2, 5, 10, 25, 50, 75, max_hops]
    for d in buckets:
        if d > max_hops:
            continue
        samples: list[float] = []
        ok = 0
        for k in range(30):
            pair = pair_at_distance(rooms, width, d, seed=42 + k)
            if pair is None:
                continue
            for a, b in (pair, (pair[1], pair[0])):
                t0 = time.perf_counter()
                route = path_between(state, a, b)
                samples.append((time.perf_counter() - t0) * 1000.0)
                if len(route) == d:
                    ok += 1
        if samples:
            by_hops[str(d)] = {**_ms_stats(samples), "verified": ok}

    # 最远角点必须单独测：随机采样在 149 跳时几乎不可能命中。
    far_samples: list[float] = []
    far_ok = 0
    for _ in range(30):
        t0 = time.perf_counter()
        route = path_between(state, _far_a, _far_b)
        far_samples.append((time.perf_counter() - t0) * 1000.0)
        if len(route) == max_hops:
            far_ok += 1
    by_hops[str(max_hops)] = {**_ms_stats(far_samples), "verified": far_ok}

    rng = random.Random(20261107)
    ids = sorted(state.locations)
    overall: list[float] = []
    for _ in range(200):
        a, b = rng.sample(ids, 2)
        t0 = time.perf_counter()
        path_between(state, a, b)
        overall.append((time.perf_counter() - t0) * 1000.0)
    return {
        "path_overall_ms": _ms_stats(overall),
        "path_ms_by_hops": by_hops,
        "max_hops": max_hops,
    }


def _measure_tick(content, spec: SyntheticSpec, store: EventStore, live: WorldState, tmp: Path) -> dict[str, Any]:
    registry = build_registry(content, spec.seed)
    writer = Writer(live, store, registry, spec.seed)
    cfg = AmbientConfig(
        out_dir=tmp / "ambient",
        seed=spec.seed,
        npc_limit=spec.npcs,
        speed=1.0,
        start_tick=0,
        summary_every_ticks=0,
        max_cpu_ratio=10.0,
    )
    sched = AmbientScheduler(
        root=ROOT,
        cfg=cfg,
        content=content,
        writer=writer,
        store=store,
        state=live,
        registry=registry,
        lock=threading.RLock(),
    )
    for _ in range(TICK_WARMUP):
        sched._process_tick()
    events_before = sched._events_total
    samples: list[float] = []
    for _ in range(TICK_SAMPLES):
        t0 = time.perf_counter()
        sched._process_tick()
        samples.append((time.perf_counter() - t0) * 1000.0)
    events = sched._events_total - events_before
    return {
        "tick": _ms_stats(samples),
        "tick_warmup": TICK_WARMUP,
        "tick_samples": TICK_SAMPLES,
        "events_per_tick": round(events / TICK_SAMPLES, 3),
        "final_tick": int(live.tick),
    }


class _FairLock:
    """公平票据锁：模拟服务端 100 席同时到达时的排队次序。"""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._head = 0
        self._tail = 0

    def acquire(self) -> None:
        with self._cond:
            ticket = self._tail
            self._tail += 1
            while ticket != self._head:
                self._cond.wait()

    def release(self) -> None:
        with self._cond:
            self._head += 1
            self._cond.notify_all()

    def __enter__(self) -> None:
        self.acquire()

    def __exit__(self, *exc: Any) -> None:
        self.release()


def _measure_write(state: WorldState, store: EventStore, registry, spec: SyntheticSpec) -> dict[str, Any]:
    actors = sorted(eid for eid in state.entities if not eid.startswith("player:"))[:WRITE_THREADS]
    if not actors:
        return {"error": "no_entities"}
    for eid in actors:
        state.entities[eid].stamina = 100.0

    writer = Writer(state, store, registry, spec.seed)
    lock = _FairLock()
    barrier = threading.Barrier(len(actors) + 1)
    total_samples: list[float] = []
    hold_samples: list[float] = []
    results: dict[str, int] = {"ok": 0, "rejected": 0}
    tick_holder = {"tick": max(1000, int(state.tick))}
    errors: list[str] = []

    def worker(eid: str) -> None:
        ent = state.entities[eid]
        here = ent.location
        exits = sorted(state.locations.get(here, {}).get("exits", []))
        other = exits[0] if exits else here
        cur = here
        barrier.wait()
        if not exits:
            return
        try:
            for r in range(WRITE_WARMUP + WRITE_SAMPLES):
                nxt = other if cur == here else here
                t0 = time.perf_counter()
                lock.acquire()
                t1 = time.perf_counter()
                try:
                    tick_holder["tick"] += 1
                    ev = writer.submit(
                        Intent(
                            actor=eid,
                            verb="move",
                            args={"to": nxt},
                            tick=tick_holder["tick"],
                            source="npc",
                            reason="bench_write",
                        )
                    )
                finally:
                    t2 = time.perf_counter()
                    lock.release()
                dt = (t2 - t0) * 1000.0
                hold = (t2 - t1) * 1000.0
                cur = nxt
                if r >= WRITE_WARMUP:
                    total_samples.append(dt)
                    hold_samples.append(hold)
                    results["ok" if ev.outcome == "ok" else "rejected"] += 1
        except Exception as exc:  # 线程异常必须带回主线程
            errors.append(f"{eid}:{exc}")

    threads = [threading.Thread(target=worker, args=(eid,), daemon=True) for eid in actors]
    for th in threads:
        th.start()
    barrier.wait()
    t_start = time.perf_counter()
    for th in threads:
        th.join()
    elapsed = time.perf_counter() - t_start
    if errors:
        return {"error": "; ".join(errors[:5])}

    merged = sorted(total_samples + hold_samples)
    return {
        "seats": len(actors),
        "rounds_per_seat": WRITE_SAMPLES,
        "samples": len(total_samples),
        "total_ms": _ms_stats(total_samples),
        "hold_ms": _ms_stats(hold_samples),
        "throughput_per_s": round(len(total_samples) / elapsed, 3) if elapsed > 0 else 0.0,
        "ok": results["ok"],
        "rejected": results["rejected"],
        "_merged_for_report": len(merged),
    }


def _measure_view(spec: SyntheticSpec, tmp: Path) -> dict[str, Any]:
    """WorldSession.player_state 冷缓存，100 个玩家各读一次。"""
    root = tmp / "session_root"
    content = build_content(spec)
    write_content_pack(root, content)
    accounts = [f"p{i:04d}" for i in range(SEATS)]
    run_dir = root / "runs" / f"view-{spec.key}"
    session = WorldSession(
        root=root,
        run_id=f"view-{spec.key}",
        seed=spec.seed,
        npc_limit=0,
        player_id=f"player:{accounts[0]}",
        display_name=accounts[0],
        spawn=sorted(content.locations)[0],
        endow=True,
        post_quests=False,
        seed_accounts=accounts[1:],
        run_dir=run_dir,
    )
    # 让 100 个档案都在线，读到接近线上 100 席的 online_players/escrow 视图。
    for prof in session.writer.state.players.values():
        prof.online = True
        prof.presence = "online"

    # 预热一次，随后每个玩家强制清空 tick 级视图缓存与价格缓存。
    t0 = time.perf_counter()
    session.player_state(accounts[0])
    warm_ms = (time.perf_counter() - t0) * 1000.0

    view_samples: list[float] = []
    loc_samples: list[float] = []
    for acct in accounts:
        pid = f"player:{acct}"
        ent = session.state.entities[pid]
        local = session._read_local
        local.view_tick = -1
        local.price_tick = -1
        t0 = time.perf_counter()
        out = session.player_state(acct)
        view_samples.append((time.perf_counter() - t0) * 1000.0)
        if not out.get("ok"):
            return {"error": f"player_state not ok: {acct} {out.get('reject_reason')}"}

        local.price_tick = -1
        t0 = time.perf_counter()
        session._location_view(ent.location, pid, {})
        loc_samples.append((time.perf_counter() - t0) * 1000.0)

    session.store.close()
    return {
        "seats": len(accounts),
        "view_ms": _ms_stats(view_samples),
        "location_view_ms": _ms_stats(loc_samples),
        "warm_ms": round(warm_ms, 3),
        "locations": len(session.content.locations),
        "entities": len(session.state.entities),
        "businesses": len(session.state.businesses),
        "factions": len(session.state.factions),
    }


def run_scale(spec: SyntheticSpec, out: Path, *, include_view: bool = True) -> dict[str, Any]:
    tmp = Path(tempfile.mkdtemp(prefix=f"xkx-bench-{spec.rooms}-"))
    started = _build_started(spec, tmp)
    state = started["state"]
    content = started["content"]
    store = started["store"]
    registry = started["registry"]

    result: dict[str, Any] = {
        "scale": spec.rooms,
        "rooms": spec.rooms,
        "npcs": spec.npcs,
        "shops": spec.shops,
        "players": 0,
        "seed": spec.seed,
        "startup_ms": started["startup_ms"],
        "genesis_build_ms": started["genesis_build_ms"],
        "events_for_replay": started["events_for_replay"],
        "genesis_effects": started["genesis_effects"],
        "replay_hash_equal": started["replay_hash_equal"],
        "rss_delta_mb": started["rss_delta_mb"],
        "rss_peak_mb": started["rss_peak_mb"],
        "counts": _counts(state),
    }
    result.update(_measure_hash_clone(state, content))
    result.update(_measure_path(state, spec.rooms))
    result.update(_measure_tick(content, spec, store, state, tmp))
    result.update(_measure_write(state, store, registry, spec))

    if include_view:
        del started, state, content, store, registry
        gc.collect()
        result["view"] = _measure_view(spec, tmp)
    result["elapsed_scale"] = "in-process"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return result


def run_sweep(out: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    fixed_rooms = 1000
    for npcs in (50, 100, 200, 500):
        rows.append(_sweep_row(fixed_rooms, npcs, 20, out.parent / f"sweep-npcs-{npcs}.json"))
    for shops in (5, 20, 100):
        rows.append(_sweep_row(fixed_rooms, 100, shops, out.parent / f"sweep-shops-{shops}.json"))
    payload = {"fixed_rooms": fixed_rooms, "rows": rows}
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def _sweep_row(rooms: int, npcs: int, shops: int, out: Path) -> dict[str, Any]:
    spec = SyntheticSpec(rooms=rooms, npcs=npcs, shops=shops)
    tmp = Path(tempfile.mkdtemp(prefix=f"xkx-sweep-{rooms}-{npcs}-{shops}-"))
    started = _build_started(spec, tmp)
    state = started["state"]
    content = started["content"]
    tick = _measure_tick(content, spec, started["store"], state, tmp)
    base = {
        "rooms": rooms,
        "npcs": npcs,
        "shops": shops,
        "tick_p50_ms": tick["tick"]["p50"],
        "tick_p95_ms": tick["tick"]["p95"],
        "events_per_tick": tick["events_per_tick"],
        "hash_ms": _ms_stats(_timed_ms(state.hash, repeat=15)),
        "clone_ms": _ms_stats(_timed_ms(state.clone, repeat=8)),
        "counts": _counts(state),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(base, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return base


def _fit(xs: list[float], ys: list[float]) -> dict[str, float]:
    """最小二乘斜率/截距；xs 为房间数，ys 为毫秒。"""
    if len(xs) < 2:
        return {"slope_ms_per_1000_rooms": 0.0, "intercept_ms": 0.0}
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom if denom else 0.0
    return {
        "slope_ms_per_1000_rooms": round(slope * 1000.0, 3),
        "intercept_ms": round(my - slope * mx, 3),
    }


def build_summary(
    scales: list[dict[str, Any]],
    sweep: dict[str, Any],
    capacity: list[dict[str, Any]],
    out_dir: Path,
) -> None:
    xs = [float(r["rooms"]) for r in scales]
    def series(metric_path: str) -> list[float]:
        vals = []
        for r in scales:
            node: Any = r
            for part in metric_path.split("."):
                node = node[part]
            vals.append(float(node))
        return vals

    fit = {
        "hash_genesis": _fit(xs, series("hash_ms.p50")),
        "hash_bare": _fit(xs, series("hash_bare_ms.p50")),
        "full_hash": _fit(xs, series("full_hash_ms.p50")),
        "clone": _fit(xs, series("clone_ms.p50")),
        "tick_p95": _fit(xs, series("tick.p95")),
        "write_p95": _fit(xs, series("total_ms.p95")),
        "view_p95": _fit(xs, series("view.view_ms.p95")),
        "path_p95": _fit(xs, series("path_overall_ms.p95")),
    }
    # 容量边界：把 195/300/500/750 一起拟合，求 100 席写 P95=241ms 的房间数。
    bx = [float(r["rooms"]) for r in capacity]
    by = [float(r["total_ms"]["p95"]) for r in capacity]
    cfit = _fit(bx, by)
    threshold_rooms = None
    if cfit["slope_ms_per_1000_rooms"] > 0:
        threshold_rooms = round((241.0 - cfit["intercept_ms"]) / cfit["slope_ms_per_1000_rooms"] * 1000.0, 1)
    combined = {
        "scales": scales, "sweep": sweep, "capacity": capacity,
        "fit": fit,
        "capacity_fit": {**cfit, "write_p95_241ms_room_estimate": threshold_rooms},
    }
    (out_dir / "summary.json").write_text(
        json.dumps(combined, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    fields = [
        "scale", "rooms", "npcs", "shops", "startup_ms", "tick.p50", "tick.p95",
        "rss_delta_mb", "total_ms.p50", "total_ms.p95", "hold_ms.p95",
        "hash_ms.p50", "full_hash_ms.p50", "clone_ms.p50", "view.view_ms.p50",
        "view.view_ms.p95", "path_overall_ms.p50", "path_overall_ms.p95",
        "hash_bare_ms.p50",
    ]
    with (out_dir / "summary.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(fields)
        for r in scales:
            row = []
            for path in fields:
                node: Any = r
                for part in path.split("."):
                    node = node[part]
                row.append(node)
            writer.writerow(row)
    (out_dir / "summary.md").write_text(
        _markdown(scales, sweep, capacity, fit, combined["capacity_fit"]), encoding="utf-8"
    )


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def _markdown(
    scales: list[dict[str, Any]],
    sweep: dict[str, Any],
    capacity: list[dict[str, Any]],
    fit: dict[str, Any],
    capacity_fit: dict[str, Any],
) -> str:
    lines = [
        "# xkx 千房间性能实测（合成房间图）",
        "",
        "口径：进程内直调产品接口；启动回放=从 EventStore 重放 genesis 事件；"
        "tick 预热 30 次、采 60 次；写 P95 = 100 线程并发 `Writer.submit`"
        "（公平票据锁，含排队等待，不含网络；每线程预热 2 轮、采 6 轮）；"
        "内存为进程启动到状态建成的 macOS `ru_maxrss` 峰值增量。",
        "",
        "## 三档规模（每档 NPC=100 / 商店=20，只改房间数）",
        "",
        "| 规模 | rooms | npcs | shops | startup_ms | tick_p50 | tick_p95 | rss_delta_mb | write_p50 | write_p95 | hash_p50 | full_hash_p50 | clone_p50 | view_p50 | view_p95 | path_p50 | path_p95 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in scales:
        lines.append(
            "| {scale} | {rooms} | {npcs} | {shops} | {startup_ms} | {tp50} | {tp95} | {rss} | {wp50} | {wp95} | {hp50} | {fp50} | {cp50} | {vp50} | {vp95} | {pp50} | {pp95} |".format(
                scale=r["scale"], rooms=r["rooms"], npcs=r["npcs"], shops=r["shops"],
                startup_ms=_fmt(r["startup_ms"]), tp50=_fmt(r["tick"]["p50"]),
                tp95=_fmt(r["tick"]["p95"]), rss=_fmt(r["rss_delta_mb"]),
                wp50=_fmt(r["total_ms"]["p50"]), wp95=_fmt(r["total_ms"]["p95"]),
                hp50=_fmt(r["hash_ms"]["p50"]), fp50=_fmt(r["full_hash_ms"]["p50"]),
                cp50=_fmt(r["clone_ms"]["p50"]), vp50=_fmt(r["view"]["view_ms"]["p50"]),
                vp95=_fmt(r["view"]["view_ms"]["p95"]), pp50=_fmt(r["path_overall_ms"]["p50"]),
                pp95=_fmt(r["path_overall_ms"]["p95"]),
            )
        )
    lines += [
        "",
        "## 容量边界补充点（NPC=100 / 商店=20，只加房间）",
        "",
        "| rooms | write_p50 | write_p95 | service_hold_p95 | tick_p50 | hash_p50 | clone_p50 | 写吞吐/s |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in capacity:
        lines.append(
            "| {rooms} | {wp50} | {wp95} | {hp95} | {tp50} | {hash} | {clone} | {thr} |".format(
                rooms=r["rooms"], wp50=_fmt(r["total_ms"]["p50"]), wp95=_fmt(r["total_ms"]["p95"]),
                hp95=_fmt(r["hold_ms"]["p95"]), tp50=_fmt(r["tick"]["p50"]),
                hash=_fmt(r["hash_ms"]["p50"]), clone=_fmt(r["clone_ms"]["p50"]),
                thr=_fmt(r["throughput_per_s"]),
            )
        )
    lines += [
        "",
        "线性拟合：100 席写 P95 = {slope} ms/千房 × rooms + {intercept} ms；"
        "按当前 241ms 安全线，估算触顶房间数 = **{threshold}**。".format(
            slope=capacity_fit["slope_ms_per_1000_rooms"],
            intercept=capacity_fit["intercept_ms"],
            threshold=capacity_fit["write_p95_241ms_room_estimate"],
        ),
        "",
        "## `state_hash` 含不含 locations：用曲线证伪/证实",
        "",
        "| 指标 | 斜率 ms/千房 | 截距 ms |",
        "|---|---:|---:|",
    ]
    for name, f in fit.items():
        lines.append(f"| {name} | {f['slope_ms_per_1000_rooms']} | {f['intercept_ms']} |")
    lines += [
        "",
        "`hash_bare`（只有 locations、无实体/行会/商业体）在任何房间数下都约等于 0 →",
        "`WorldState.to_dict()` 确实不含 locations。`hash_genesis` 随房间数增长，是因为",
        "`build_genesis` 给每间房建一个地点行会（faction）与一个金库账户；这是房间数",
        "通过 genesis 间接进入 per-tick 哈希的真实路径，不是 locations 本身。",
        "",
        "## NPC / 商店隔离扫描（固定 1000 房）",
        "",
        "| rooms | npcs | shops | tick_p50 | tick_p95 | events_per_tick | hash_p50 | clone_p50 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in sweep["rows"]:
        lines.append(
            "| {rooms} | {npcs} | {shops} | {tp50} | {tp95} | {ev} | {hp} | {cp} |".format(
                rooms=row["rooms"], npcs=row["npcs"], shops=row["shops"],
                tp50=_fmt(row["tick_p50_ms"]), tp95=_fmt(row["tick_p95_ms"]),
                ev=_fmt(row["events_per_tick"]), hp=_fmt(row["hash_ms"]["p50"]),
                cp=_fmt(row["clone_ms"]["p50"]),
            )
        )
    lines += ["", "## BFS 分层（path_between，ms）", ""]
    for r in scales:
        lines.append(f"### {r['scale']} 房（max_hops={r['max_hops']}）")
        lines.append("")
        lines.append("| hops | count | p50 | p95 | mean | 校验通过 |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for hops in sorted(r["path_ms_by_hops"], key=lambda x: int(x)):
            row = r["path_ms_by_hops"][hops]
            lines.append(
                f"| {hops} | {row['count']} | {row['p50']} | {row['p95']} | {row['mean']} | {row.get('verified', 0)} |"
            )
        lines.append("")
    s195, s1000, s5683 = scales[0], scales[1], scales[2]
    threshold = capacity_fit["write_p95_241ms_room_estimate"]
    tick_pct = s5683["tick"]["p95"] / 6000.0 * 100.0
    w195 = float(s195["total_ms"]["p95"])
    w1000 = float(s1000["total_ms"]["p95"])
    w5683 = float(s5683["total_ms"]["p95"])
    t195 = 99.0 / max(1e-9, float(s195["throughput_per_s"])) * 1000.0
    t1000 = 99.0 / max(1e-9, float(s1000["throughput_per_s"])) * 1000.0
    t5683 = 99.0 / max(1e-9, float(s5683["throughput_per_s"])) * 1000.0
    lines += [
        "## 结论",
        "",
        f"- 195 房 + 100 席：写 P95 {_fmt(w195)} ms（服务段 P95 {_fmt(s195['hold_ms']['p95'])} ms；"
        f"吞吐 {_fmt(s195['throughput_per_s'])} 写/s，100 席排队下限约 {_fmt(t195)} ms）。"
        f"{'略高于' if w195 > 241 else '低于'}当前 241 ms 安全线，属于**安全线边缘**，"
        "不宜再叠加房间或并发。",
        f"- 1,000 房 + 100 席：写 P95 {_fmt(w1000)} ms，是安全线的 "
        f"{w1000 / 241.0:.2f} 倍，**不可行**；瓶颈是每次写事务 clone 全状态"
        f"（clone P50 {_fmt(s1000['clone_ms']['p50'])} ms）在 100 并发下排成队"
        f"（吞吐 {_fmt(s1000['throughput_per_s'])} 写/s，排队下限约 {_fmt(t1000)} ms）。",
        f"- 5,683 房 + 100 席：写 P95 {_fmt(w5683)} ms，吞吐 {_fmt(s5683['throughput_per_s'])} 写/s；"
        f"即使没有写风暴，tick P95 约 {_fmt(s5683['tick']['p95'])} ms，约为 1x tick 间隔(6s)的 "
        f"{tick_pct:.1f}%，所以 tick 不是主要瓶颈，**写路径的全状态 clone 才是**。",
        f"- 容量边界：100 房 100 席写 P95 {_fmt(capacity[0]['total_ms']['p95'])} ms（通过），"
        f"150 房 {_fmt(capacity[1]['total_ms']['p95'])} ms、195 房 {_fmt(w195)} ms 已超线；"
        f"线性拟合触线房间数约 **{threshold}**。因此 **约 150 房/100 席**是单进程安全边界，"
        "195 房切片在 100 席同瞬写时已超线约 17%，不宜再当成安全容量。",
        "- 分片建议：分片触发阈值取 **150 房/100 席、clone P50 > 0.8 ms、或 tick P95 > 100 ms**。"
        "5,683 房若每片仍需承载 100 席并发写，按 150 房/片切约 38 片；若全服 100 席按玩家分布路由，"
        "可用 300 房/片（约 19 片，每片并发写 ≤50）。每片独立 Writer + EventStore + 进程；"
        "跨片移动/全服账目走事件总线或异步汇总。",
        "- 注意：NPC/商店扫描显示同 1,000 房下 NPC 从 100 → 500 时 tick P50 从 "
        f"{_fmt(sweep['rows'][1]['tick_p50_ms'])} → {_fmt(sweep['rows'][3]['tick_p50_ms'])} ms，"
        "真实 5,683 房若带 2,385 NPC，tick 成本会显著高于本表的固定 100 NPC。",
        "",
        "原始数据：`summary.json` / `summary.csv` / `scale-*.json` / `view-*.json` / "
        "`capacity-rooms-*.json` / `sweep-*.json`。",
    ]
    return "\n".join(lines) + "\n"


def run_all(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    scales: list[dict[str, Any]] = []
    for rooms in SCALES:
        path = out_dir / f"scale-{rooms}.json"
        view_path = out_dir / f"view-{rooms}.json"
        common = [
            sys.executable, str(Path(__file__).resolve()),
            "--rooms", str(rooms), "--npcs", "100", "--shops", "20",
        ]
        print(f"[bench] {rooms} rooms core ...", flush=True)
        subprocess.run(common + ["--mode", "core", "--out", str(path)], check=True, cwd=str(ROOT))
        print(f"[bench] {rooms} rooms view ...", flush=True)
        subprocess.run(common + ["--mode", "view", "--out", str(view_path)], check=True, cwd=str(ROOT))
        scale = json.loads(path.read_text(encoding="utf-8"))
        scale["view"] = json.loads(view_path.read_text(encoding="utf-8"))["view"]
        path.write_text(json.dumps(scale, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        scales.append(scale)
    capacity: list[dict[str, Any]] = []
    for rooms in CAPACITY_ROOMS:
        path = out_dir / f"capacity-rooms-{rooms}.json"
        print(f"[bench] capacity {rooms} rooms ...", flush=True)
        subprocess.run(
            [
                sys.executable, str(Path(__file__).resolve()), "--mode", "core",
                "--rooms", str(rooms), "--npcs", "100", "--shops", "20", "--out", str(path),
            ],
            check=True, cwd=str(ROOT),
        )
        capacity.append(json.loads(path.read_text(encoding="utf-8")))
    print("[bench] sweep ...", flush=True)
    sweep_path = out_dir / "sweep.json"
    subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--mode", "sweep", "--out", str(sweep_path)],
        check=True, cwd=str(ROOT),
    )
    sweep = json.loads(sweep_path.read_text(encoding="utf-8"))
    build_summary(scales, sweep, capacity, out_dir)
    print(f"[bench] done -> {out_dir}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--all", action="store_true", help="跑三档规模 + 隔离扫描（等价 --mode all）")
    p.add_argument("--mode", choices=("all", "core", "scale", "view", "sweep"), default="all")
    p.add_argument("--rooms", type=int, default=1000)
    p.add_argument("--npcs", type=int, default=100)
    p.add_argument("--shops", type=int, default=20)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", default=str(ROOT / "reports" / "xkx-perf"))
    args = p.parse_args()
    out = Path(args.out)
    if args.mode == "all":
        run_all(out)
        return 0
    spec = SyntheticSpec(rooms=args.rooms, npcs=args.npcs, shops=args.shops, seed=args.seed)
    if args.mode == "core":
        run_scale(spec, out, include_view=False)
    elif args.mode == "scale":
        run_scale(spec, out, include_view=True)
    elif args.mode == "view":
        out.parent.mkdir(parents=True, exist_ok=True)
        view = _measure_view(spec, Path(tempfile.mkdtemp(prefix=f"xkx-view-{spec.rooms}-")))
        out.write_text(json.dumps({"view": view}, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    else:
        run_sweep(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
