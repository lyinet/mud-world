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


def _two_players_one_offline(tmp_path: Path) -> tuple[WorldSession, str, str]:
    """u_live 活跃，u_gone 在 36 tick 后离线；两人同处 market。"""
    session = _make(tmp_path)
    assert _enter(session, "u_live", "观察者")["ok"]
    assert _enter(session, "u_gone", "断线者")["ok"]
    session._advance(PRESENCE_TIMEOUT_TICKS - 1)
    assert _talk(session, "u_live")["ok"]
    session._advance(1)
    return session, "player:u_live", "player:u_gone"


def test_offline_player_cannot_be_fought_or_killed(tmp_path: Path) -> None:
    session, live, gone = _two_players_one_offline(tmp_path)
    assert session.state.players[gone].online is False
    hp_before = float(session.state.entities[gone].hp)
    events_before = session.store.count()

    for verb in ("fight", "kill"):
        response = session.perform(
            "u_live", verb, {"account_id": "u_live", "target": gone, "rounds": 5}
        )
        assert not response["ok"], (verb, response)
        assert response["reject_reason"] == "target_offline", (verb, response)
        assert response["reason_text"] == "对方已离线，不能进行该操作。"
    assert float(session.state.entities[gone].hp) == hp_before
    # 拒绝可留审计 reject 事件，但绝不能产生伤害/状态变化。
    for event in session.store.read_all()[events_before:]:
        assert event.outcome != "ok", event.verb
        assert not any(e.get("op") in ("damage", "entity_status") for e in event.effects)

    # 在线玩家仍按原规则拒绝（不是“离线”冒充）。
    assert _enter(session, "u_third", "在线靶子")["ok"]
    online = session.perform(
        "u_live", "fight", {"account_id": "u_live", "target": "player:u_third"}
    )
    assert online["reject_reason"] == "target_is_player"
    session.close()


def test_offline_player_cannot_be_looted(tmp_path: Path) -> None:
    session, _live, gone = _two_players_one_offline(tmp_path)
    ent = session.state.entities[gone]
    ent.inventory["dog_skin"] = 2
    before = dict(ent.inventory)

    response = session.perform(
        "u_live", "loot", {"account_id": "u_live", "target": gone, "item": "dog_skin"}
    )
    assert not response["ok"], response
    assert response["reject_reason"] == "target_offline"
    assert dict(ent.inventory) == before, "不能从离线玩家身上拿走任何东西"

    online = session.perform(
        "u_live", "loot", {"account_id": "u_live", "target": "player:u_live"}
    )
    assert online["reject_reason"] == "target_is_player", "搜身路径一律拒绝"
    session.close()


def test_npc_aggression_rejects_offline_target(tmp_path: Path) -> None:
    from wsim.actions.aggression import NpcAggressionAction

    session, _live, gone = _two_players_one_offline(tmp_path)
    npc_id = next(
        eid for eid in sorted(session.state.entities) if not eid.startswith("player:")
    )
    npc = session.state.entities[npc_id]
    player = session.state.entities[gone]
    npc.location = player.location  # 同房，排除 not_together 干扰
    # 该内容根没有主动攻击规则；这里只验证前置判定，规则表用最小替身。
    action = NpcAggressionAction(rules={str(npc.template or npc.id): {"aggressive": True}})
    tick = int(session.state.tick)
    trigger = Intent(
        actor=npc_id,
        verb="npc_aggression",
        args={"target": gone, "mode": "trigger"},
        tick=tick,
        source="system",
    )
    assert action.preconditions(session.state, trigger) == ["target_offline"]
    session.close()


def test_combat_timeout_eighteen_ticks_then_disengage(tmp_path: Path) -> None:
    session = _make(tmp_path)
    assert _enter(session, "u_fight", "鏖战者")["ok"]
    pid = "player:u_fight"
    npc_id = next(
        eid
        for eid, ent in sorted(session.state.entities.items())
        if not eid.startswith("player:") and ent.status == "alive"
    )
    npc = session.state.entities[npc_id]
    player = session.state.entities[pid]
    npc.location = player.location
    npc.combat_target = pid
    npc.combat_enemies = [pid]
    player.combat_target = npc_id
    player.combat_enemies = [npc_id]
    hp_before = float(player.hp)

    session._advance(COMBAT_PRESENCE_TIMEOUT_TICKS - 1)
    assert session.state.players[pid].online is True
    session._advance(1)

    # 每次 submit 都会用 clone 替换 state，断言必须重新取实体。
    npc = session.state.entities[npc_id]
    player = session.state.entities[pid]
    assert session.state.players[pid].online is False
    assert npc.combat_target == ""
    assert npc.combat_enemies == []
    assert player.combat_target == ""
    assert player.combat_enemies == []
    assert float(player.hp) == hp_before

    # 脱战后 NPC 的 continue 不得对离线目标结算伤害。
    tick = int(session.state.tick)
    follow = session.writer.submit(
        Intent(
            actor=npc_id,
            verb="npc_aggression",
            args={"target": pid, "mode": "continue"},
            tick=tick,
            source="system",
        )
    )
    assert follow.outcome != "ok" or all(e.get("op") != "damage" for e in follow.effects)
    assert float(session.state.entities[pid].hp) == hp_before
    session.close()


def test_offline_player_not_in_status_nearby(tmp_path: Path) -> None:
    session, live, gone = _two_players_one_offline(tmp_path)
    nearby = session.status("u_live")["nearby"]
    assert gone not in {row["id"] for row in nearby}
    assert live not in {row["id"] for row in nearby}, "自己不出现在 nearby"
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
