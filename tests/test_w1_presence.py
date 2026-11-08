"""Presence 断线超时的回归测试（全部按世界 tick，不读墙钟）。

旧实现按玩家自己的 wall-clock 静置时间判离线，且只在本人发请求时才懒判定，
历史账号永远 online。新实现由系统动作在每个 tick 扫描 `profile.last_intent_tick`。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from wsim.api.session import WorldSession
from wsim.core.clock import TICKS_PER_DAY

ROOT = Path(__file__).resolve().parents[1]


def _make(tmp_path: Path, **kwargs) -> WorldSession:
    defaults = dict(
        run_id="presence",
        run_dir=tmp_path / "run",
        seed=42,
        endow=False,
        post_quests=True,
        npc_limit=0,
    )
    defaults.update(kwargs)
    return WorldSession(ROOT, **defaults)


def _enter(session: WorldSession, account: str, name: str) -> dict:
    # 测试里不让“进房”推进世界钟，便于精确控制 35/36 tick 边界。
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


def test_fifty_concurrent_players_get_zero_false_player_offline(tmp_path: Path) -> None:
    """50 个并发玩家只发请求、不推进世界钟：0 次 player_offline 误判。

    世界钟推进（advance=True）时，36 tick 是真实经过的 6 游戏小时；
    本用例把“时间流逝”交给显式 `_advance`，避免并发动作把世界日压缩成几秒。
    """
    session = _make(tmp_path)
    accounts = [f"u_load{i:03d}" for i in range(50)]
    for i, account in enumerate(accounts):
        response = _enter(session, account, f"玩家{i:03d}")
        assert response["ok"], response
    npc = _npc_at(session, "market")

    offline_codes: list[str] = []

    def play(account: str) -> None:
        for _ in range(6):
            response = session.perform(
                account,
                "talk",
                {"account_id": account, "target": npc, "message": "今日可好"},
                advance=False,
            )
            if not response["ok"] and response.get("reject_reason"):
                offline_codes.append(str(response["reject_reason"]))

    with ThreadPoolExecutor(max_workers=50) as pool:
        list(pool.map(play, accounts))

    assert offline_codes.count("player_offline") == 0, offline_codes[:10]
    assert offline_codes == [], offline_codes[:10]

    session._advance(35)  # 35 tick 未超时
    for account in accounts:
        assert session.state.players[f"player:{account}"].online is True
    session._advance(1)  # 第 36 tick：全部离线
    for account in accounts:
        assert session.state.players[f"player:{account}"].online is False
    session.close()


def test_day_boundaries_do_not_batch_demote_online_players(tmp_path: Path) -> None:
    """跨多个世界日，只要玩家持续有请求，就不会被日界/扫描批量标离线。"""
    session = _make(tmp_path)
    assert _enter(session, "u_one", "测试甲")["ok"]
    npc = _npc_at(session, "market")
    before = session.store.count()
    for _ in range((TICKS_PER_DAY * 3) // 30):
        session._advance(30)
        talk = session.perform(
            "u_one",
            "talk",
            {"account_id": "u_one", "target": npc, "message": "今日可好"},
            advance=False,
        )
        assert talk["ok"], talk
    appended = session.store.read_all()[before:]
    assert appended
    assert all(event.verb != "system_player_presence" for event in appended)
    profile = session.state.players["player:u_one"]
    assert profile.online is True
    assert session._activity_of("player:u_one")[0] == "active"
    session.close()


def test_three_states_and_offline_only_after_tick_timeout(tmp_path: Path) -> None:
    """active -> idle（仍可写）-> 36 tick 才 offline；重连回原角色原位置。"""
    session = _make(tmp_path)
    assert _enter(session, "u_idle", "测试乙")["ok"]
    assert session._activity_of("player:u_idle")[0] == "active"

    session._advance(12)  # 12 tick = idle，但仍在线
    assert session._activity_of("player:u_idle")[0] == "idle"
    assert session.player_state("u_idle")["player"]["online"] is True

    session._advance(23)  # 距上次动作 35 tick，仍在宽限内
    assert session.state.players["player:u_idle"].online is True
    moved = session.perform(
        "u_idle", "move", {"account_id": "u_idle", "to": "academy"}, advance=False
    )
    assert moved["ok"], moved
    assert session._activity_of("player:u_idle")[0] == "active"

    session._advance(36)  # 第 36 tick：系统动作置 offline
    state = session.player_state("u_idle")
    assert state["player"]["activity"] == "offline"
    assert state["player"]["online"] is False
    rejected = session.perform("u_idle", "move", {"account_id": "u_idle", "to": "market"})
    assert not rejected["ok"]
    assert rejected["reject_reason"] == "player_offline"

    # 离线只是世界状态；重连接回同一角色，位置/行囊不变。
    resumed = session.resume("u_idle", reconnect_grace=60)
    assert resumed["ok"], resumed
    assert session.state.players["player:u_idle"].online is True
    assert session.state.entities["player:u_idle"].location == "academy"
    session.close()


def test_presence_timeout_thresholds() -> None:
    from wsim.actions.player import (
        COMBAT_PRESENCE_TIMEOUT_TICKS,
        PRESENCE_TIMEOUT_TICKS,
    )

    assert PRESENCE_TIMEOUT_TICKS == 36
    assert COMBAT_PRESENCE_TIMEOUT_TICKS == 18
