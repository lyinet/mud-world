"""Presence 超时：判定、系统动作、可见性、活跃刷新与确定性。

规格：`specs/player-presence-timeout/spec.md`
"""
from __future__ import annotations

from pathlib import Path

from wsim.actions.player import (
    COMBAT_PRESENCE_TIMEOUT_TICKS,
    PRESENCE_TIMEOUT_TICKS,
    presence_timeout_pids,
)
from wsim.api.session import WorldSession
from wsim.core.models import Intent
from wsim.world.hash import event_hash, state_hash

ROOT = Path(__file__).resolve().parents[1]


def _make(tmp_path: Path, run_id: str = "presence-timeout", **kwargs) -> WorldSession:
    defaults = dict(
        run_id=run_id,
        run_dir=tmp_path / run_id,
        seed=42,
        endow=False,
        post_quests=True,
        npc_limit=0,
    )
    defaults.update(kwargs)
    return WorldSession(ROOT, **defaults)


def _enter(session: WorldSession, account: str, name: str) -> dict:
    return session.perform(
        account,
        "enter",
        {"account_id": account, "display_name": name, "archetype": "游侠"},
        advance=False,
    )


def _npc_at(session: WorldSession, location: str) -> str:
    for eid, ent in sorted(session.state.entities.items()):
        if eid.startswith("player:"):
            continue
        if getattr(ent, "status", "alive") == "alive" and ent.location == location:
            return eid
    raise AssertionError(f"no npc at {location}")


def _talk(session: WorldSession, account: str) -> dict:
    return session.perform(
        account,
        "talk",
        {"account_id": account, "target": _npc_at(session, "market"), "message": "今日可好"},
        advance=False,
    )


def test_timeout_boundary_and_system_action(tmp_path: Path) -> None:
    session = _make(tmp_path)
    assert _enter(session, "u_one", "测试甲")["ok"]
    assert _enter(session, "u_two", "测试乙")["ok"]

    session._advance(PRESENCE_TIMEOUT_TICKS - 1)
    assert presence_timeout_pids(session.state, session.state.tick) == []
    session._advance(1)

    assert presence_timeout_pids(session.state, session.state.tick) == []
    for account in ("u_one", "u_two"):
        pid = f"player:{account}"
        assert session.state.players[pid].online is False
        assert session.state.entities[pid].presence == "offline"

    # 系统动作幂等：同一 tick 再跑一次不产生离线副作用。
    tick = int(session.state.tick)
    event = session.writer.submit(
        Intent(
            actor="system",
            verb="system_player_presence",
            args={"tick": tick, "pids": []},
            tick=tick,
            source="system",
        )
    )
    assert event.outcome == "ok"
    assert event.effects == []
    session.close()


def test_offline_hidden_from_views(tmp_path: Path) -> None:
    session = _make(tmp_path)
    assert _enter(session, "u_seen", "观察者")["ok"]
    assert _enter(session, "u_gone", "断线者")["ok"]
    session._advance(PRESENCE_TIMEOUT_TICKS - 1)
    assert _talk(session, "u_seen")["ok"]  # 观察者保持活跃
    session._advance(1)  # u_gone 满 36 tick

    gone = "player:u_gone"
    assert session.state.players[gone].online is False
    assert gone not in {row["id"] for row in session._online_players_view("player:u_seen")}
    assert session.public_state("u_gone")["online"] is False
    assert session.player_state("u_gone")["player"]["online"] is False
    # 观察者仍在（只要每 36 tick 内有请求）。
    assert session.player_state("u_seen")["player"]["online"] is True
    session.close()


def test_touch_tick_heartbeat_cooldown(tmp_path: Path) -> None:
    session = _make(tmp_path)
    assert _enter(session, "u_touch", "测试丙")["ok"]

    def heartbeats() -> int:
        return sum(1 for e in session.store.read_all() if e.verb == "player_heartbeat")

    before = heartbeats()
    for _ in range(10):
        session.player_state("u_touch")
    assert heartbeats() == before, "同一 tick 的多次读不应刷心跳"

    session._advance(12)
    session.player_state("u_touch")
    assert heartbeats() == before + 1
    for _ in range(10):
        session.player_state("u_touch")
    assert heartbeats() == before + 1
    session.close()


def _run_script(tmp_path: Path) -> tuple[str, str, list[tuple[int, str, str]]]:
    session = _make(tmp_path, run_id="script")
    assert _enter(session, "u_a", "玩家甲")["ok"]
    assert _enter(session, "u_b", "玩家乙")["ok"]
    session._advance(30)
    assert _talk(session, "u_a")["ok"]
    session._advance(6)  # u_b 满 36 tick 离线，u_a 仍在线
    events = session.store.read_all()
    rows = [(int(e.tick), e.verb, e.actor) for e in events]
    result = (event_hash(events), state_hash(session.state), rows)
    session.close()
    return result


def test_same_seed_double_run_is_identical(tmp_path: Path) -> None:
    first = _run_script(tmp_path / "a")
    second = _run_script(tmp_path / "b")
    assert first[0] == second[0], "event_hash 必须逐字节一致"
    assert first[1] == second[1], "state_hash 必须逐字节一致"
    assert first[2] == second[2]
    assert any(verb == "system_player_presence" for _, verb, _ in first[2])
    assert COMBAT_PRESENCE_TIMEOUT_TICKS == 18
