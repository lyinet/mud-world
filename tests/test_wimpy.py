"""wimpy（原版自动逃跑）测试：命令、数据落库、心跳成败、随机流锁定。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import yaml  # noqa: E402

from wsim.actions.aggression import NpcAggressionAction  # noqa: E402
from wsim.actions.registry import ActionRegistry  # noqa: E402
from wsim.actions.wimpy import WimpyAction  # noqa: E402
from wsim.api import WorldSession  # noqa: E402
from wsim.core.models import Effect, Intent, Reject  # noqa: E402
from wsim.world.state import WorldState, Entity  # noqa: E402
from wsim.world.store import EventStore  # noqa: E402
from wsim.world.writer import Writer  # noqa: E402

NPC_ID = "/d/hangzhou/npc/laohu"
PLAYER_ID = "player:hero"
XKX_CONTENT = ROOT / "content" / "xkx"
XKX_WORLD = ROOT / "config" / "worlds" / "xkx.yaml"


def _locations(inn_exits=("road",)):
    return {
        "inn": {"id": "inn", "name": "客栈", "exits": list(inn_exits)},
        "road": {"id": "road", "name": "土路", "exits": ["inn"]},
    }


def _world(*, player_dex=20, npc_dex=0, wimpy=80, hp=100, max_hp=100, inn_exits=("road",)):
    state = WorldState(locations=_locations(inn_exits))
    store = EventStore(":memory:")
    reg = ActionRegistry()
    reg.register(NpcAggressionAction({NPC_ID: {"kind": "always", "name": "老虎"}}))
    reg.register(WimpyAction())
    writer = Writer(state, store, reg, 42)
    writer.bootstrap(
        [
            Effect("create_entity", {
                "id": NPC_ID, "name": "老虎", "role": "npc", "location": "inn",
                "max_hp": 500, "hp": 500, "attack": 60, "defense": 10,
                "attrs": {"str": 20, "dex": npc_dex}, "combat_exp": 10,
                "skill_levels": {"unarmed": 30},
            }),
            Effect("create_entity", {
                "id": PLAYER_ID, "name": "英雄", "role": "wanderer", "location": "inn",
                "max_hp": max_hp, "hp": hp, "wimpy": wimpy, "status": "alive",
                "attack": 15, "defense": 5,
                "attrs": {"str": 20, "con": 20, "dex": player_dex, "kar": 20, "per": 20},
                "combat_exp": 10, "skill_levels": {"unarmed": 40},
            }),
        ],
        tick=0, verb="genesis",
    )
    return writer, store


def _trigger(writer, tick=0):
    return writer.submit(Intent(NPC_ID, "npc_aggression", {"mode": "trigger", "target": PLAYER_ID}, tick, "npc"))


def _continue(writer, tick):
    return writer.submit(Intent(NPC_ID, "npc_aggression", {"mode": "continue", "target": PLAYER_ID}, tick, "npc"))


def _wlog(event):
    for eff in event.effects:
        if eff.get("op") == "wimpy_log":
            return dict(eff.get("data") or {})
    return {}


def test_entity_omits_wimpy_zero_and_keeps_it_when_set():
    zero = Entity(id="e", name="n", role="npc", location="inn")
    assert "wimpy" not in zero.to_dict()
    ent = Entity(id="e", name="n", role="npc", location="inn", wimpy=80)
    assert ent.to_dict()["wimpy"] == 80


def test_wimpy_action_accepts_range_and_rejects_outside():
    state = WorldState(locations=_locations())
    state.players[PLAYER_ID] = object()
    state.entities[PLAYER_ID] = Entity(id=PLAYER_ID, name="英雄", role="wanderer", location="inn")
    action = WimpyAction()
    assert action.preconditions(state, Intent(PLAYER_ID, "wimpy", {"value": 80}, 0, "player")) == []
    assert action.preconditions(state, Intent(PLAYER_ID, "wimpy", {"value": 81}, 0, "player")) == ["bad_wimpy"]
    assert action.preconditions(state, Intent(PLAYER_ID, "wimpy", {"value": -1}, 0, "player")) == ["bad_wimpy"]
    effects = action.apply(state, Intent(PLAYER_ID, "wimpy", {"value": 80}, 0, "player"), None)
    assert effects[0].data["field"] == "wimpy" and effects[0].data["value"] == 80
    assert effects[1].data == {"action": "set", "value": 80, "previous": 0}


def test_wimpy_success_moves_and_clears_all_aggressors():
    writer, _ = _world(player_dex=20, npc_dex=0, hp=100, max_hp=500)
    _trigger(writer, 0)
    event = _continue(writer, 1)
    assert event.outcome == "ok"
    log = _wlog(event)
    assert log["result"] == "success"
    assert log["from_location"] == "inn" and log["to_location"] == "road"
    # 原版 do_flee 先选方向、再掷 dex；两个随机值在日志里按消费顺序标注。
    assert log["roll_order"] == ["exit_index", "roll_target", "roll_npc"]
    assert log["exit_index"] == 0 and log["exit_count"] == 1
    player = writer.state.entities[PLAYER_ID]
    npc = writer.state.entities[NPC_ID]
    assert player.location == "road"
    assert npc.combat_target == "" and npc.combat_round == 0
    # 成功逃跑的回合不再结算伤害。
    assert player.hp == 100


def test_wimpy_failure_stays_and_takes_the_round():
    writer, _ = _world(player_dex=1, npc_dex=20, hp=100, max_hp=500)
    _trigger(writer, 0)
    event = _continue(writer, 1)
    log = _wlog(event)
    assert log["result"] == "failed" and log["round"] == 1
    # 失败也先消费方向随机数：exit_index 已记录，但人不移动。
    assert log["roll_order"] == ["exit_index", "roll_target", "roll_npc"]
    assert log["exit_index"] == 0 and log["attempted_exit"] == "road"
    player = writer.state.entities[PLAYER_ID]
    npc = writer.state.entities[NPC_ID]
    assert player.location == "inn"
    assert npc.combat_target == PLAYER_ID and npc.combat_round == 1
    # 失败后照常挨打（本 seed 下该回合未命中，但血量不会因为逃跑成功而“免疫”）。
    assert player.hp <= 100


def test_wimpy_zero_never_consumes_the_escape_rolls():
    writer, _ = _world(wimpy=0)
    _trigger(writer, 0)
    event = _continue(writer, 1)
    assert _wlog(event) == {}
    assert writer.state.entities[NPC_ID].combat_round == 1


def test_wimpy_no_exit_stays_and_takes_the_round():
    writer, _ = _world(player_dex=20, npc_dex=0, hp=100, max_hp=500, inn_exits=())
    _trigger(writer, 0)
    event = _continue(writer, 1)
    log = _wlog(event)
    assert log["result"] == "no_exit" and log["round"] == 1
    assert writer.state.entities[PLAYER_ID].location == "inn"
    assert writer.state.entities[NPC_ID].combat_target == PLAYER_ID


def test_same_seed_replays_identical_wimpy_logs():
    runs = []
    for _ in range(2):
        writer, _ = _world(player_dex=20, npc_dex=0, hp=100, max_hp=500)
        _trigger(writer, 0)
        event = _continue(writer, 1)
        runs.append((_wlog(event), writer.state.entities[PLAYER_ID].location))
    assert runs[0] == runs[1]


def _session(tmp_path):
    spawn = str((yaml.safe_load(XKX_WORLD.read_text(encoding="utf-8")) or {}).get("spawn"))
    return WorldSession(
        ROOT, run_id="xkx-wimpy-test", seed=42, npc_limit=0,
        run_dir=tmp_path / "run", content_root=XKX_CONTENT,
        world_config=XKX_WORLD, spawn=spawn,
    )


def test_wimpy_command_sets_and_displays(tmp_path):
    session = _session(tmp_path)
    try:
        account = "xkx_wimpy"
        before = session.xkx_command(account, {"account_id": account, "command": "wimpy"})
        assert before["ok"] is True
        assert "低于 0%" in before["lines"][0]["text"]
        set_result = session.xkx_command(account, {"account_id": account, "command": "wimpy 80"})
        assert set_result["ok"] is True
        assert int(session.state.entities[f"player:{account}"].wimpy) == 80
        bad = session.xkx_command(account, {"account_id": account, "command": "wimpy 81"})
        assert bad["ok"] is False
        assert "0-80" in bad["lines"][0]["text"]
    finally:
        session.close()
