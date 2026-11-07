"""xkx 支线：进度入世界状态、交付原因码、动态奖励种子化取数顺序。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.actions.registry import ActionRegistry  # noqa: E402
from wsim.actions.xkx_quests import XkxQuestAcceptAction, XkxQuestDeliverAction  # noqa: E402
from wsim.core.models import Effect, Intent  # noqa: E402
from wsim.core.rng import PRNG  # noqa: E402
from wsim.world.effects import apply_effects  # noqa: E402
from wsim.world.hash import state_hash  # noqa: E402
from wsim.world.state import WorldState  # noqa: E402
from wsim.world.store import EventStore  # noqa: E402
from wsim.world.writer import Writer  # noqa: E402

LOC = "/d/shiliang/street"

QUESTS = {
    "q_dynamic_jinshe_jian": {
        "id": "q_dynamic_jinshe_jian",
        "name": "金蛇剑",
        "dynamic": True,
        "giver": None,
        "target": {"id": "/d/shiliang/npc/jinshe", "name": "夏雪宜"},
        "required_item": {"id": "/questobj/jinshe-jian", "name": "金蛇剑"},
        "reward": {
            "exp": {"base": 100, "random": 400},
            "pot": {"formula": "exp/(random(n)+1)", "random": 10, "plus": 1},
            "score": {"base": 50, "random": 50},
        },
    },
    "q_atu_afeng": {
        "id": "q_atu_afeng",
        "name": "阿土 → 阿凤",
        "dynamic": False,
        "giver": {"id": "/d/shiliang/npc/atu", "name": "阿土", "topic": "阿凤"},
        "target": {"id": "/d/shiliang/npc/afeng", "name": "阿凤"},
        "required_item": {"id": "/d/shiliang/npc/obj/qingshu", "name": "情书"},
        "reward": {"exp": 0, "items": [], "money": 0},
    },
    "q_wenyi_jinshe": {
        "id": "q_wenyi_jinshe",
        "name": "温仪 → 夏雪宜",
        "dynamic": False,
        "giver": {"id": "/d/shiliang/npc/wenyi", "name": "温仪", "topic": "手帕"},
        "target": {"id": "/d/shiliang/npc/jinshe", "name": "夏雪宜"},
        "required_item": {"id": "/d/shiliang/npc/obj/shoupa", "name": "手帕"},
        "reward": {"exp": 0, "items": ["/clone/book/jinshe2", "/clone/book/jinshe3"], "money": 0},
    },
}


class _Content:
    xkx_quests = QUESTS


def _writer(seed: int = 42, inventory: dict[str, int] | None = None):
    state = WorldState(locations={LOC: {"id": LOC, "name": "街", "exits": [], "services": []}})
    store = EventStore(":memory:")
    reg = ActionRegistry()
    reg.register(XkxQuestAcceptAction(_Content()))
    reg.register(XkxQuestDeliverAction(_Content()))
    writer = Writer(state, store, reg, seed=seed)
    effects = [
        Effect(
            "create_entity",
            {"id": "player:hero", "name": "英雄", "role": "visitor", "location": LOC},
        )
    ]
    for npc in ("/d/shiliang/npc/jinshe", "/d/shiliang/npc/wenyi", "/d/shiliang/npc/atu",
                "/d/shiliang/npc/afeng"):
        effects.append(
            Effect("create_entity", {"id": npc, "name": npc, "role": "npc", "location": LOC})
        )
    for item, qty in sorted((inventory or {}).items()):
        effects.append(Effect("item_create", {"owner": "player:hero", "item": item, "qty": qty}))
    writer.bootstrap(effects, tick=10)
    return writer, store


def _intent(verb: str, args: dict, tick: int = 10) -> Intent:
    return Intent("player:hero", verb, {"player_id": "player:hero", **args}, tick, "player")


def test_dynamic_reward_is_seeded_in_fixed_order():
    writer, store = _writer(inventory={"/questobj/jinshe-jian": 1})
    seq = store.count() + 1
    event = writer.submit(
        _intent("xkx_quest_deliver", {"npc": "/d/shiliang/npc/jinshe", "item": "/questobj/jinshe-jian"})
    )
    assert event.outcome == "ok"
    # 取数顺序由测试锁死：exp → pot 除数 → score，且与写入口的 salt 一致。
    rng = PRNG(42, seq, "action:xkx_quest_deliver")
    exp = 100 + rng.randint(0, 399)
    divisor = rng.randint(0, 9) + 1
    potential = exp // divisor
    score = 50 + rng.randint(0, 49)
    ent = writer.state.entities["player:hero"]
    assert ent.combat_exp == exp
    assert ent.potential == potential
    assert ent.score == score
    record = writer.state.xkx_quests["player:hero|q_dynamic_jinshe_jian"]
    assert record["status"] == "completed" and record["step"] == "delivered"
    assert record["reward"] == {"exp": exp, "potential": potential, "score": score}
    assert ent.inventory.get("/questobj/jinshe-jian") is None
    # 事件日志回放：不跑动作，只按落盘 Effect 重建，世界哈希一致。
    replayed = WorldState(locations={LOC: {"id": LOC, "name": "街", "exits": [], "services": []}})
    for recorded in store.read_all():
        apply_effects(replayed, [Effect.from_dict(d) for d in recorded.effects])
        replayed.tick = recorded.tick
    assert replayed.hash() == writer.state.hash()
    assert replayed.to_dict()["xkx_quests"] == writer.state.to_dict()["xkx_quests"]


def test_talk_quest_requires_accept_then_completes():
    writer, _ = _writer()
    rejected = writer.submit(
        _intent("xkx_quest_deliver", {
            "npc": "/d/shiliang/npc/afeng", "item": "/d/shiliang/npc/obj/qingshu",
        })
    )
    assert rejected.outcome == "rejected"
    assert rejected.reason == "rejected:quest_not_accepted"

    accepted = writer.submit(
        _intent("xkx_quest_accept", {"npc": "/d/shiliang/npc/atu", "topic": "阿凤在哪里"})
    )
    assert accepted.outcome == "ok"
    assert writer.state.entities["player:hero"].inventory["/d/shiliang/npc/obj/qingshu"] == 1
    record = writer.state.xkx_quests["player:hero|q_atu_afeng"]
    assert record["status"] == "accepted" and record["step"] == "asked"

    writer.state.tick = 20  # 模拟世界时间推进
    done = writer.submit(
        _intent("xkx_quest_deliver", {
            "npc": "/d/shiliang/npc/afeng", "item": "/d/shiliang/npc/obj/qingshu",
        }, tick=20)
    )
    assert done.outcome == "ok"
    record = writer.state.xkx_quests["player:hero|q_atu_afeng"]
    assert record["status"] == "completed"
    writer.state.tick = 21
    again = writer.submit(
        _intent("xkx_quest_deliver", {
            "npc": "/d/shiliang/npc/afeng", "item": "/d/shiliang/npc/obj/qingshu",
        }, tick=21)
    )
    assert again.outcome == "rejected"
    assert again.reason == "rejected:quest_already_completed"


def test_deliver_reason_codes_are_explicit():
    writer, _ = _writer()
    # 目标不在同房
    event = writer.submit(
        _intent("xkx_quest_deliver", {
            "npc": "/d/shiliang/npc/jinshe", "item": "/questobj/jinshe-jian",
        })
    )
    assert event.reason == "rejected:quest_item_missing"
    # 物品给了错误的人
    writer.state.entities["player:hero"].inventory["/questobj/jinshe-jian"] = 1
    writer.state.tick = 11
    event = writer.submit(
        _intent("xkx_quest_deliver", {
            "npc": "/d/shiliang/npc/afeng", "item": "/questobj/jinshe-jian",
        }, tick=11)
    )
    assert event.reason == "rejected:quest_not_found"
    # 接取话题对不上
    writer.state.tick = 12
    event = writer.submit(
        _intent("xkx_quest_accept", {"npc": "/d/shiliang/npc/atu", "topic": "天气"}, tick=12)
    )
    assert event.reason == "rejected:quest_giver_none"


def test_world_hash_unchanged_without_xkx_quests():
    state = WorldState(locations={LOC: {"id": LOC, "name": "街", "exits": [], "services": []}})
    assert "xkx_quests" not in state.to_dict()
    assert state_hash(state) == state.hash()
