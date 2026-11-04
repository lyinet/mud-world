"""在线三态与并发离线误判的回归测试。

旧实现按全局 tick 差值判离线：50 人并发时一个世界日 ≈1 秒，
日界会把仍在线的玩家批量标成 offline（W5 压测 4,727 次 player_offline）。
修复后只看玩家自己的 wall-clock 活跃时间。
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
    return session.perform(
        account, "enter", {"account_id": account, "display_name": name, "archetype": "游侠"}
    )


def _npc_at(session: WorldSession, location: str) -> str:
    for eid, ent in sorted(session.state.entities.items()):
        if eid.startswith("player:"):
            continue
        if getattr(ent, "status", "alive") == "alive" and ent.location == location:
            return eid
    raise AssertionError(f"no npc at {location}")


def test_fifty_concurrent_players_get_zero_false_player_offline(tmp_path: Path) -> None:
    """50 个并发玩家连续动作：0 次 player_offline 误判，全部仍在线。"""
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
                account, "talk", {"account_id": account, "target": npc, "message": "今日可好"}
            )
            if not response["ok"] and response.get("reject_reason"):
                offline_codes.append(str(response["reject_reason"]))

    with ThreadPoolExecutor(max_workers=50) as pool:
        list(pool.map(play, accounts))

    assert offline_codes.count("player_offline") == 0, offline_codes[:10]
    assert offline_codes == [], offline_codes[:10]
    for account in accounts:
        assert session.state.players[f"player:{account}"].online is True
    session.close()


def test_day_boundaries_do_not_batch_demote_online_players(tmp_path: Path) -> None:
    """全局 tick 推进多个世界日，也不能把在线玩家标离线（日界 presence 结算已停用）。"""
    session = _make(tmp_path)
    assert _enter(session, "u_one", "测试甲")["ok"]
    before = session.store.count()
    session._advance()  # 每个动作推进 6 tick；跨 3 个世界日
    for _ in range((TICKS_PER_DAY * 3) // 6):
        session._advance()
    appended = session.store.read_all()[before:]
    assert appended
    assert all(event.verb != "system_player_presence" for event in appended)
    profile = session.state.players["player:u_one"]
    assert profile.online is True
    assert session._activity_of("player:u_one") == "active"
    session.close()


def test_three_states_and_offline_only_after_grace(tmp_path: Path) -> None:
    """active -> idle（仍可写）-> 超过宽限期才 offline；周 tick 不参与判定。"""
    clock = [1000.0]
    session = _make(
        tmp_path,
        presence_clock=lambda: clock[0],
        presence_active_seconds=5.0,
        presence_offline_seconds=10.0,
    )
    assert _enter(session, "u_idle", "测试乙")["ok"]
    state = session.player_state("u_idle")
    assert state["player"]["activity"] == "active"

    clock[0] = 1006.0  # 静置 6 秒：idle，但仍在线
    state = session.player_state("u_idle")
    assert state["player"]["activity"] == "idle"
    assert state["player"]["online"] is True
    moved = session.perform("u_idle", "move", {"account_id": "u_idle", "to": "academy"})
    assert moved["ok"], moved
    assert session.player_state("u_idle")["player"]["activity"] == "active"

    clock[0] = 1017.0  # 距最后一次动作 11 秒 > 10 秒宽限：真正离线
    rejected = session.perform("u_idle", "move", {"account_id": "u_idle", "to": "market"})
    assert not rejected["ok"]
    assert rejected["reject_reason"] == "player_offline"
    state = session.player_state("u_idle")
    assert state["player"]["activity"] == "offline"
    assert state["player"]["online"] is False

    # 离线只是世界状态；重连 resume 会重新上线，存档与位置保留。
    resumed = session.resume("u_idle", reconnect_grace=60)
    assert resumed["ok"], resumed
    assert session.state.players["player:u_idle"].online is True
    assert session.state.entities["player:u_idle"].location == "academy"
    session.close()


def test_activity_stage_thresholds() -> None:
    from wsim.actions.player import activity_stage

    assert activity_stage(0) == "active"
    assert activity_stage(5, active_seconds=5, offline_seconds=10) == "active"
    assert activity_stage(6, active_seconds=5, offline_seconds=10) == "idle"
    assert activity_stage(11, active_seconds=5, offline_seconds=10) == "offline"
