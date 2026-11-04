"""写路径性能与常量时间尾部的回归测试。

覆盖 W5 压测暴露的两类问题：
- 每次写操作不再 `EventStore.read_all()`（17k 事件时单次 160ms，吞吐封顶 ~6/s）。
- 日界结算并入 system_tick，不再每个世界日额外写 4 条系统事件。
"""
from __future__ import annotations

import sqlite3
import statistics
import time
from pathlib import Path

import pytest

from wsim.api.session import WorldSession
from wsim.core.clock import TICKS_PER_DAY, day_of
from wsim.world.hash import state_hash

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = "u_perf"


def _make(tmp_path: Path, **kwargs) -> WorldSession:
    defaults = dict(
        run_id="perf",
        run_dir=tmp_path / "run",
        seed=42,
        endow=False,
        post_quests=True,
        npc_limit=0,
    )
    defaults.update(kwargs)
    return WorldSession(ROOT, **defaults)


def _npc_at(session: WorldSession, location: str) -> str:
    for eid, ent in sorted(session.state.entities.items()):
        if eid.startswith("player:"):
            continue
        if getattr(ent, "status", "alive") == "alive" and ent.location == location:
            return eid
    raise AssertionError(f"no npc at {location}")


def _talk(session: WorldSession, account: str, npc: str, message: str = "你好") -> dict:
    return session.perform(
        account, "talk", {"account_id": account, "target": npc, "message": message}
    )


def _enter(session: WorldSession, account: str = ACCOUNT, name: str = "测试甲") -> None:
    response = session.perform(
        account, "enter", {"account_id": account, "display_name": name, "archetype": "游侠"}
    )
    assert response["ok"], response


def test_write_path_never_calls_read_all(tmp_path: Path) -> None:
    """写操作路径上不允许出现 read_all；read_all 只留给审计/回放读路径。"""
    session = _make(tmp_path)
    calls: list[int] = []
    original = session.store.read_all

    def spy():
        calls.append(1)
        return original()

    session.store.read_all = spy  # type: ignore[method-assign]
    _enter(session)
    npc = _npc_at(session, "market")
    for _ in range(3):
        assert _talk(session, ACCOUNT, npc)["ok"]
    # 含一次早期拒绝（unknown action），也会走 _reject_early 写事件。
    rejected = session.perform(ACCOUNT, "no_such_action", {"account_id": ACCOUNT})
    assert not rejected["ok"]
    assert calls == [], f"写路径读取了整条事件日志 {len(calls)} 次"
    session.close()


def test_tail_cache_rebuilds_from_sqlite_after_restart(tmp_path: Path) -> None:
    """缓存必须在事务成功后同步；重启后从 sqlite 尾部重建，而不是只留在内存。"""
    session = _make(tmp_path)
    _enter(session)
    npc = _npc_at(session, "market")
    for _ in range(4):
        assert _talk(session, ACCOUNT, npc)["ok"]
    expected = (session.tail.last_seq, session.tail.last_tick, session.tail.last_hash)
    assert session.tail.last_seq == session.store.count()
    assert session.tail.last_tick == session.state.tick
    assert expected[2]

    last_event = session.tail.last_event()
    assert last_event is not None
    assert int(last_event.seq) == expected[0]

    session.close()

    reopened = _make(tmp_path)
    assert reopened.replayed is True
    assert (
        reopened.tail.last_seq,
        reopened.tail.last_tick,
        reopened.tail.last_hash,
    ) == expected
    # 端到端再写一条：talk 事件 + system_tick 事件，尾部继续单调递增。
    assert _talk(reopened, ACCOUNT, npc)["ok"]
    assert reopened.tail.last_seq > expected[0]
    assert reopened.tail.last_seq == reopened.store.count()
    reopened.close()


def _bulk_append_synthetic(store_path: Path, count: int) -> None:
    """直接补 17k 条空事件，模拟压测时的日志体量（不经过 Writer 的逐条 commit）。"""
    conn = sqlite3.connect(store_path)
    try:
        with conn:
            rows = []
            for i in range(1, count + 1):
                tick = i * 6
                rows.append(
                    (
                        tick, day_of(tick), f"i:synth:{i}", "system", "system_tick",
                        "{}", "[]", "ok", "", "system",
                    )
                )
            base = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]
            rows = [
                (base + idx + 1,) + row
                for idx, row in enumerate(rows)
            ]
            conn.executemany(
                "INSERT INTO events(seq,tick,day,intent_id,actor,verb,args,effects,"
                "outcome,reason,source) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
    finally:
        conn.close()


def test_write_latency_flat_with_large_event_log(tmp_path: Path) -> None:
    """17k 事件时写操作仍应保持 P95 < 300ms（旧实现单次 read_all 中位 160ms）。"""
    session = _make(tmp_path)
    _enter(session)
    session.close()

    _bulk_append_synthetic(tmp_path / "run" / "events.sqlite", 17_000)

    session = _make(tmp_path)
    assert session.replayed is True
    calls: list[int] = []
    original = session.store.read_all

    def spy():
        calls.append(1)
        return original()

    session.store.read_all = spy  # type: ignore[method-assign]

    latencies: list[float] = []
    npc = _npc_at(session, "market")
    for _ in range(200):
        start = time.perf_counter()
        response = _talk(session, ACCOUNT, npc, "今日生意如何")
        latencies.append((time.perf_counter() - start) * 1000.0)
        assert response["ok"], response

    p95 = statistics.quantiles(latencies, n=20)[18]  # 第 95 百分位
    median = statistics.median(latencies)
    assert calls == [], f"大日志写路径读取了整条日志 {len(calls)} 次"
    assert p95 < 300.0, f"写 P95={p95:.1f}ms, median={median:.1f}ms"
    session.close()


def test_day_boundary_is_one_system_event(tmp_path: Path) -> None:
    """改前 5 条/世界日（presence/recover/quest_fail/quest_post/tick）；改后 1 条。"""
    session = _make(tmp_path)
    before = session.store.count()
    advances = TICKS_PER_DAY // 6
    for _ in range(advances):
        session._advance()
    appended = session.store.read_all()[before:]
    assert len(appended) == advances
    assert {event.verb for event in appended} == {"system_tick"}
    assert all(not (event.verb.startswith("system_player_presence")) for event in appended)
    boundary = appended[-1]
    assert boundary.args.get("day_boundary") is True
    assert boundary.effects, "日界事件应携带体力回复/委托结算效果"
    assert any(eff.get("op") == "stamina" for eff in boundary.effects)
    session.close()


def test_two_identical_runs_have_same_state_and_event_hash(tmp_path: Path) -> None:
    """修复后两次重跑确定性一致（wall-clock 不进入事件日志）。"""

    def run(run_dir: Path):
        session = WorldSession(
            ROOT, run_id="det", run_dir=run_dir, seed=42, endow=False,
            post_quests=True, npc_limit=0,
        )
        _enter(session)
        npc = _npc_at(session, "market")
        for _ in range(24):
            assert _talk(session, ACCOUNT, npc, "今日生意如何")["ok"]
        result = (state_hash(session.state), session.store.event_hash())
        session.close()
        return result

    first = run(tmp_path / "a")
    second = run(tmp_path / "b")
    assert first == second
