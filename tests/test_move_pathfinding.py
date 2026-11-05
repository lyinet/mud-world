"""多跳寻路与移动/动作同 tick 次序（路径确定性、漂移自愈、不再空转）。"""
from pathlib import Path

from wsim.actions.basic import MoveAction
from wsim.actions.common import path_between
from wsim.core.models import Effect, Intent
from wsim.sim.intent import Planner
from wsim.world.effects import apply_effects
from wsim.world.state import WorldState

ROOT = Path(__file__).resolve().parents[1]


def test_route_is_deterministic_and_adjacent(real_content):
    state = WorldState(locations=real_content.locations)
    first = path_between(state, "smithy", "temple")
    second = path_between(state, "smithy", "temple")
    assert first == second
    assert first == ["market", "academy", "temple"]
    route = ["smithy", *first]
    for src, dst in zip(route, route[1:]):
        assert dst in state.locations[src]["exits"]


def _actor_at(state, location: str) -> str:
    apply_effects(state, [Effect("create_entity", {
        "id": "npc:walker", "name": "行者", "role": "庙祝", "location": location,
        "stamina": 100.0, "max_stamina": 100.0,
    })])
    return "npc:walker"


def test_move_recovers_full_route_when_actual_location_drifted(real_content):
    """跨 tick 丢意图导致实际位置偏离计划：按 dest 从实际位置补足，绝不 not_adjacent。"""
    state = WorldState(locations=real_content.locations)
    eid = _actor_at(state, "pharmacy")
    action = MoveAction(real_content)
    intent = Intent(actor=eid, verb="move", args={"to": "academy", "dest": "smithy"}, tick=5)
    assert action.preconditions(state, intent) == []
    effects = action.apply(state, intent, rng=None)
    moved = [e for e in effects if e.op == "set_location"][0]
    assert moved.data["to"] == "smithy"
    stamina = [e for e in effects if e.op == "stamina"][0]
    assert stamina.data["delta"] == -5.0 * 2


def test_move_without_dest_still_rejects_non_adjacent(real_content):
    state = WorldState(locations=real_content.locations)
    eid = _actor_at(state, "smithy")
    action = MoveAction(real_content)
    intent = Intent(actor=eid, verb="move", args={"to": "temple"}, tick=5)
    assert action.preconditions(state, intent) == ["not_adjacent"]


def test_hop_to_current_location_is_noop_not_blocked(real_content):
    """前面的跳按 dest 自愈提前到达后，最后一跳落在原地：0 步成功，不算 not_adjacent。"""
    state = WorldState(locations=real_content.locations)
    eid = _actor_at(state, "academy")
    action = MoveAction(real_content)
    intent = Intent(actor=eid, verb="move", args={"to": "academy", "dest": "academy"}, tick=5)
    assert action.preconditions(state, intent) == []
    assert action.apply(state, intent, rng=None) == []


def test_stale_hop_while_already_at_dest_is_noop(real_content):
    """已站在最终目的地时，剩下的陈旧一跳（to 在别处）作废：0 步成功，不报 not_adjacent。"""
    state = WorldState(locations=real_content.locations)
    eid = _actor_at(state, "smithy")
    action = MoveAction(real_content)
    intent = Intent(actor=eid, verb="move", args={"to": "academy", "dest": "smithy"}, tick=5)
    assert action.preconditions(state, intent) == []
    assert action.apply(state, intent, rng=None) == []


def test_last_hop_shares_tick_with_followup_work(real_content):
    """最后一个移动与后续上工共用同一 tick，且列表顺序保证先移动后动作。"""
    state = WorldState(locations=real_content.locations)
    eid = _actor_at(state, "market")
    planner = Planner(real_content, 42)
    planner._stamina = 100.0
    intents: list[Intent] = []
    loc, cursor = planner._move(
        state, intents, eid, "market", "temple", cursor=10, end=200, reason="去上工"
    )
    assert loc == "temple"
    assert [i.args["to"] for i in intents] == ["academy", "temple"]
    assert all(i.args["dest"] == "temple" for i in intents)
    assert [i.tick for i in intents] == [10, 11]
    assert cursor == 11
    cursor = planner._add(
        intents, eid, "work", {"at": "temple", "hours": 8}, cursor, 200, "上工"
    )
    assert intents[-1].verb == "work"
    assert intents[-1].tick == intents[-2].tick == 11
    assert cursor == 12
