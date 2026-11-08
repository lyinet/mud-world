"""P5：房间 reset / NPC 重生 / 商店补货。

覆盖：
- 默认关闭，只有 xkx 世界配置显式打开；
- 原版 room.lpc::reset 的三条可观察语义：死亡 NPC 重生、被拿走的物品不复制、
  被消耗的物品按槽位补回；
- 重生是恢复原实体（不新增实体），死亡时间/地点留审计；
- NPC 货架补回 `business_stock`，玩家自营店铺不自动补；
- 真实内容包闭环：杀郭庄大狼狗 → 掉狗皮 → 捡走 → reset 重生 → 再杀再掉。
"""
from __future__ import annotations

import pytest
import yaml

from conftest import ROOT

from wsim.actions.registry import build_registry
from wsim.api.xkx_content import load_xkx_content
from wsim.core.content import Content
from wsim.core.models import Effect, Intent
from wsim.sim.genesis import build_genesis
from wsim.sim.headless import RunConfig, run_headless
from wsim.sim.room_reset import (
    RESET_PERIOD_TICKS,
    RETURN_HOME_IDLE_TICKS,
    RoomResetPolicy,
)
from wsim.world.effects import apply_effects
from wsim.world.state import Business, Entity, WorldState
from wsim.world.store import EventStore
from wsim.world.writer import Writer

DOG = "/npc/dog"
SKIN = "skin"
BREAD = "flatbread"


def _content() -> Content:
    content = Content()
    content.locations = {
        "room": {
            "id": "room",
            "name": "甲",
            "exits": [],
            "services": [BREAD],
            "objects": [
                {"kind": "npc", "ref": DOG, "count": 1},
                {"kind": "item", "ref": SKIN, "count": 1},
            ],
        }
    }
    content.npcs = [
        {
            "id": DOG,
            "name": "狗",
            "role": "wanderer",
            "vitals": {"combat_exp": 10},
            "assets": {"money": 0, "items": [{"id": "meat", "qty": 2}]},
        }
    ]
    content.items = {
        SKIN: {"id": SKIN, "name": "皮"},
        BREAD: {"id": BREAD, "name": "饼"},
        "meat": {"id": "meat", "name": "肉"},
    }
    content.economy = {"business_stock": 30}
    return content


def _world(content: Content, *, dead: bool = False) -> WorldState:
    state = WorldState(locations=content.locations)
    ent = Entity(
        id=DOG,
        name="狗",
        role="wanderer",
        location="room",
        home="room",
        max_hp=200,
        hp=0 if dead else 200,
        status="dead" if dead else "alive",
        inventory={"meat": 2},
        death_tick=5 if dead else 0,
        death_location="room" if dead else "",
    )
    state.entities[DOG] = ent
    state.businesses[f"biz:room:{BREAD}"] = Business(
        id=f"biz:room:{BREAD}",
        name="房·饼",
        location="room",
        kind=BREAD,
        cash_account="treasury:region",
        stock={BREAD: 27},
    )
    return state


def _due_tick(policy: RoomResetPolicy, room: str) -> int:
    for tick in range(1, RESET_PERIOD_TICKS * 2):
        if policy.due(room, tick):
            return tick
    raise AssertionError("no due tick")


# ---- 开关 ---------------------------------------------------------------


def test_room_reset_is_off_by_default_and_only_xkx_config_enables_it():
    assert RoomResetPolicy().enabled is False
    assert RunConfig().room_reset is False

    xkx = yaml.safe_load((ROOT / "config" / "worlds" / "xkx.yaml").read_text(encoding="utf-8"))
    legacy = yaml.safe_load((ROOT / "config" / "worlds" / "default.yaml").read_text(encoding="utf-8"))
    assert xkx["room_reset"]["enabled"] is True
    assert "room_reset" not in legacy


def test_world_session_reads_room_reset_only_when_config_opens_it(tmp_path):
    from wsim.api.session import WorldSession

    assert WorldSession._load_room_reset(None) is False
    empty = tmp_path / "empty.yaml"
    empty.write_text("id: legacy\n", encoding="utf-8")
    assert WorldSession._load_room_reset(empty) is False
    assert WorldSession._load_room_reset(ROOT / "config" / "worlds" / "xkx.yaml") is True


def test_due_is_tick_periodic_and_deterministic():
    content = _content()
    policy = RoomResetPolicy(content=content, seed=7, enabled=True)
    pattern = [policy.due("room", tick) for tick in range(6)]
    # 10/20 分钟交替：每 3 个 tick 里到期 2 次。
    assert pattern.count(True) == 4
    assert pattern == [policy.due("room", tick) for tick in range(6)]
    assert pattern == [RoomResetPolicy(content=content, seed=7, enabled=True).due("room", t) for t in range(6)]


# ---- 原版 reset 语义 ----------------------------------------------------


def test_dead_npc_respawns_full_hp_and_keeps_death_audit():
    content = _content()
    state = _world(content, dead=True)
    policy = RoomResetPolicy(content=content, seed=7, enabled=True)
    effects = policy.effects(state, _due_tick(policy, "room"))
    respawns = [e for e in effects if e.op == "entity_respawn"]
    assert len(respawns) == 1
    data = respawns[0].data
    assert data["death_tick"] == 5 and data["death_location"] == "room"
    assert data["hp"] == data["max_hp"] == 200

    before = len(state.entities)
    apply_effects(state, effects)
    assert len(state.entities) == before  # 恢复原实体，不新建
    dog = state.entities[DOG]
    assert dog.status == "alive" and dog.hp == dog.max_hp == 200
    assert dog.death_tick == 0 and dog.death_location == ""
    assert dog.last_death_tick == 5 and dog.last_death_location == "room"
    assert dog.inventory == {"meat": 2}


def test_taken_item_is_not_duplicated_but_missing_item_is_replenished():
    content = _content()
    policy = RoomResetPolicy(content=content, seed=7, enabled=True)
    tick = _due_tick(policy, "room")

    state = _world(content)
    state.entities["/npc/holder"] = Entity(
        id="/npc/holder", name="持有者", role="wanderer", location="room", inventory={SKIN: 1}
    )
    ops = [e for e in policy.effects(state, tick) if e.op == "loot_create" and e.data["item"] == SKIN]
    assert ops == []  # 对象还在背包里，原版 present() 仍能找到

    del state.entities["/npc/holder"]
    ops = [e for e in policy.effects(state, tick) if e.op == "loot_create" and e.data["item"] == SKIN]
    assert len(ops) == 1 and ops[0].data["qty"] == 1 and ops[0].data["reason"] == "room_reset"


def test_vendor_restock_only_for_npc_businesses():
    content = _content()
    state = _world(content)
    policy = RoomResetPolicy(content=content, seed=7, enabled=True)
    tick = _due_tick(policy, "room")

    restock = [e for e in policy.effects(state, tick) if e.op == "item_create"]
    assert [(e.data["owner"], e.data["item"], e.data["qty"]) for e in restock] == [
        (f"biz:room:{BREAD}", BREAD, 3)
    ]

    state.businesses[f"biz:room:{BREAD}"].owner = "player:boss"
    restock = [e for e in policy.effects(state, tick) if e.op == "item_create"]
    assert restock == []


def test_reset_is_noop_when_everything_is_in_place():
    content = _content()
    state = _world(content)
    state.ground["room"] = {SKIN: 1}
    state.businesses[f"biz:room:{BREAD}"].stock = {BREAD: 30}
    policy = RoomResetPolicy(content=content, seed=7, enabled=True)
    assert policy.effects(state, _due_tick(policy, "room")) == []


def test_respawn_clears_stale_combat_state():
    """原版 destruct+new：重生对象没有 enemy；不清会让 trigger 被 stale 目标挡住。"""
    content = _content()
    state = _world(content, dead=True)
    dog = state.entities[DOG]
    dog.combat_target = "player:old"
    dog.combat_tick = 3
    dog.combat_round = 4
    policy = RoomResetPolicy(content=content, seed=7, enabled=True)
    apply_effects(state, policy.effects(state, _due_tick(policy, "room")))
    assert dog.combat_target == ""
    assert dog.combat_tick == 0
    assert dog.combat_round == 0


def test_return_home_waits_for_idle_ticks_and_then_returns():
    """离家 NPC：连续 RETURN_HOME_IDLE_TICKS 无动作才拉回；有目标不拉。"""
    content = _content()
    content.locations["away"] = {"id": "away", "name": "乙", "exits": ["room"]}
    content.locations["room"]["exits"] = ["away"]
    state = _world(content)
    dog = state.entities[DOG]
    dog.location = "away"
    policy = RoomResetPolicy(content=content, seed=7, enabled=True)
    tick = _due_tick(policy, "room")

    dog.last_action_tick = tick - 1
    assert [e for e in policy.effects(state, tick) if e.op == "set_location"] == []

    dog.last_action_tick = tick - RETURN_HOME_IDLE_TICKS
    back = [e for e in policy.effects(state, tick) if e.op == "set_location"]
    assert [(e.data["entity"], e.data["to"]) for e in back] == [(DOG, "room")]

    dog.goal_id = "goal:dog"
    assert [e for e in policy.effects(state, tick) if e.op == "set_location"] == []


# ---- 真实内容包闭环 -----------------------------------------------------


def _writer(tmp_path, content, seed: int = 7):
    state = WorldState(locations=content.locations)
    store = EventStore(tmp_path / "events.sqlite")
    policy = RoomResetPolicy(content=content, seed=seed, enabled=True)
    registry = build_registry(content, seed, room_reset=policy)
    writer = Writer(state, store, registry, seed)
    genesis = build_genesis(content, content.npcs, root=ROOT, seed=seed)
    genesis.effects.append(
        Effect(
            "create_entity",
            {
                "id": "player:accept",
                "name": "验收玩家",
                "role": "wanderer",
                "location": "/d/hangzhou/guozhuang",
                "max_hp": 5000,
                "hp": 5000,
                "attrs": {"str": 200, "con": 200, "dex": 200},
                "combat_exp": 1_000_000,
                "skill_levels": {"unarmed": 800, "force": 800, "dodge": 800, "parry": 800},
                "race": "人类",
            },
        )
    )
    writer.bootstrap(genesis.effects, tick=0, verb="genesis")
    return writer, policy, store


def test_wolfdog_kill_drop_loot_reset_kill_again(tmp_path):
    content = load_xkx_content(ROOT / "content" / "xkx")
    writer, policy, store = _writer(tmp_path, content)
    dog = "/d/hangzhou/npc/wolfdog"
    goupi = "/d/hangzhou/npc/obj/goupi"
    room = "/d/hangzhou/guozhuang"

    assert writer.state.entities[dog].drops == {goupi: 1}
    first = writer.submit(Intent(actor="player:accept", verb="fight", args={"target": dog}, tick=0, source="player"))
    assert first.outcome == "ok"
    assert writer.state.ground[room][goupi] == 1
    assert writer.state.entities[dog].status == "dead"

    looted = writer.submit(
        Intent(actor="player:accept", verb="loot", args={"item": goupi}, tick=0, source="player")
    )
    assert looted.outcome == "ok"
    assert writer.state.entities["player:accept"].inventory[goupi] == 1
    assert writer.state.ground.get(room, {}).get(goupi, 0) == 0

    reset_tick = _due_tick(policy, room)
    reset = writer.submit(
        Intent(actor="system", verb="system_room_reset", args={"tick": reset_tick}, tick=reset_tick, source="system")
    )
    assert reset.outcome == "ok"
    revived = writer.state.entities[dog]
    assert revived.status == "alive" and revived.hp == revived.max_hp
    assert revived.last_death_tick == 0 and revived.last_death_location == room
    # 原版：狗皮对象还在玩家背包里，reset 不会凭空复制第二张。
    assert writer.state.ground.get(room, {}).get(goupi, 0) == 0

    second = writer.submit(
        Intent(actor="player:accept", verb="fight", args={"target": dog}, tick=reset_tick, source="player")
    )
    assert second.outcome == "ok"
    assert writer.state.ground[room][goupi] == 1
    assert writer.state.entities["player:accept"].inventory[goupi] == 1
    store.close()


def test_enabled_reset_is_reproducible_and_noop_on_healthy_world(tmp_path):
    content = load_xkx_content(ROOT / "content" / "xkx")
    runs = {}
    for rid, flag in (("reset-off", False), ("reset-on-a", True), ("reset-on-b", True)):
        runs[rid] = run_headless(
            ROOT,
            RunConfig(seed=20261014, days=1, out_root=tmp_path, run_id=rid, room_reset=flag),
            content=content,
        )
    # 打开 reset 但世界没有死亡/缺失时：零事件、哈希与关闭态完全一致。
    assert runs["reset-on-a"]["hashes"] == runs["reset-on-b"]["hashes"]
    assert runs["reset-on-a"]["hashes"] == runs["reset-off"]["hashes"]
    assert runs["reset-on-a"]["events"]["total"] == runs["reset-off"]["events"]["total"]
    for rid in runs:
        store = EventStore(tmp_path / rid / "events.sqlite")
        assert not [e for e in store.read_all() if e.verb == "system_room_reset"]
        store.close()
