"""玩家/NPC 创世气血：来源区分 + 缺省规则 + 0 血防线。

- 玩家：max_hp=100 是原版数值（logind/human/user），六维属性是我们的默认。
- NPC：优先 vitals.max_qi（原版）；缺失按技能三档（500/300/200，我们的默认）。
- 旧 legacy 内容没有 vitals/attrs/skills 键 → 解析返回 None，事件不变。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.actions.registry import ActionRegistry  # noqa: E402
from wsim.actions.player import (  # noqa: E402
    ARCHETYPE_ATTRS,
    PLAYER_MAX_HP,
    PlayerEnterAction,
    _player_vitals,
    player_genesis_effects,
)
from wsim.api.xkx_content import load_xkx_content  # noqa: E402
from wsim.core.models import Effect, Intent  # noqa: E402
from wsim.sim.genesis import build_genesis  # noqa: E402
from wsim.sim.vitals import DEFAULT_MAX_HP_TIERS, resolve_npc_vitals  # noqa: E402
from wsim.world.effects import apply_effects  # noqa: E402
from wsim.world.state import WorldState  # noqa: E402
from wsim.world.store import EventStore  # noqa: E402
from wsim.world.writer import Writer  # noqa: E402


def _xkx_npcs() -> list[dict]:
    raw = json.loads((ROOT / "content" / "xkx" / "npcs.json").read_text(encoding="utf-8"))
    return raw["npcs"] if isinstance(raw, dict) else raw


def test_npc_missing_max_qi_uses_documented_tiers():
    by_id = {str(n["id"]): n for n in _xkx_npcs()}
    expected = {
        "/d/hangzhou/npc/laohu": 200,  # 无技能 → 200
        "/d/shiliang/npc/li": 500,  # 技能 100 → 500
        "/d/shiliang/npc/qingming": 500,  # 技能 100 → 500
        "/d/shiliang/npc/tufei2": 300,  # 技能 25 → 300
    }
    for npc_id, max_hp in expected.items():
        assert "max_qi" not in (by_id[npc_id].get("vitals") or {})
        assert resolve_npc_vitals(by_id[npc_id])["max_hp"] == max_hp


def test_every_xkx_npc_has_positive_hp():
    npcs = _xkx_npcs()
    assert len(npcs) == 105  # 92 个 slice + 13 个 external
    for npc in npcs:
        resolved = resolve_npc_vitals(npc)
        assert resolved is not None and resolved["max_hp"] > 0, npc["id"]
    # 三档默认值本身也不允许出现 0。
    assert all(value > 0 for _, value in DEFAULT_MAX_HP_TIERS)


def test_content_max_qi_is_carried_verbatim():
    npc = next(n for n in _xkx_npcs() if (n.get("vitals") or {}).get("max_qi"))
    resolved = resolve_npc_vitals(npc)
    assert resolved["max_hp"] == int(npc["vitals"]["max_qi"])
    assert resolved["hp"] == resolved["max_hp"]
    assert resolved["combat_exp"] == int((npc.get("vitals") or {}).get("combat_exp") or 0)


def test_legacy_npc_without_combat_keys_is_untouched():
    legacy = {"id": "npc.legacy", "name": "旧村民", "role": "farmer"}
    assert resolve_npc_vitals(legacy) is None


def test_player_genesis_has_hp_and_attrs():
    effects = player_genesis_effects("player:p1", "张三", "inn")
    create = next(e for e in effects if e.op == "create_entity")
    assert create.data["max_hp"] == PLAYER_MAX_HP == 100
    assert create.data["hp"] == 100
    assert set(create.data["attrs"]) == {"str", "con", "dex", "int", "spi", "sta"}
    # 我们的默认：unarmed 40 + 250 经验保证打得动；dodge/parry 10 避免玩家无敌。
    assert create.data["combat_exp"] == 250
    assert create.data["skill_levels"] == {"unarmed": 40, "dodge": 10, "parry": 10}

    state = WorldState(locations={"inn": {"id": "inn", "name": "客栈", "exits": [], "services": []}})
    apply_effects(state, effects)
    ent = state.entities["player:p1"]
    assert (ent.max_hp, ent.hp) == (100, 100)
    assert ent.attrs == ARCHETYPE_ATTRS["游侠"]


def test_player_enter_assigns_archetype_vitals_and_backfills_old_entity():
    locs = {"inn": {"id": "inn", "name": "客栈", "exits": [], "services": []}}
    state = WorldState(locations=locs)
    store = EventStore(":memory:")
    reg = ActionRegistry()
    reg.register(PlayerEnterAction())
    writer = Writer(state, store, reg, seed=42)
    intent = Intent(
        "player:p2",
        "player_enter",
        {"player_id": "player:p2", "account_id": "acct_p2", "at": "inn", "archetype": "捕快", "display_name": "李四"},
        0,
        "player",
    )
    event = writer.submit(intent)
    assert event.outcome == "ok"
    ent = writer.state.entities["player:p2"]
    assert (ent.max_hp, ent.hp) == (100, 100)
    assert ent.attrs == ARCHETYPE_ATTRS["捕快"]
    assert ent.combat_exp == 250
    assert ent.skill_levels == {"unarmed": 40, "dodge": 10, "parry": 10}
    assert _player_vitals("商人")["attrs"] != _player_vitals("捕快")["attrs"]

    # 旧实体（创世早于本次改动）重进时补一次 vitals_set。
    old = WorldState(locations=locs)
    apply_effects(
        old,
        [
            Effect("create_entity", {"id": "player:p3", "name": "旧号", "role": "visitor", "location": "inn"}),
            Effect(
                "player_upsert",
                {"id": "player:p3", "name": "旧号", "archetype": "游侠", "role": "镖师", "created_tick": 0},
            ),
        ],
    )
    store2 = EventStore(":memory:")
    reg2 = ActionRegistry()
    reg2.register(PlayerEnterAction())
    writer2 = Writer(old, store2, reg2, seed=42)
    ev2 = writer2.submit(
        Intent(
            "player:p3",
            "player_enter",
            {"player_id": "player:p3", "account_id": "acct_p3", "at": "inn"},
            0,
            "player",
        )
    )
    assert ev2.outcome == "ok"
    assert any(e["op"] == "vitals_set" for e in ev2.effects)
    assert (writer2.state.entities["player:p3"].max_hp, writer2.state.entities["player:p3"].hp) == (100, 100)


def test_xkx_genesis_lands_hp_on_every_npc_entity():
    content = load_xkx_content(ROOT / "content" / "xkx")
    state = WorldState(locations=content.locations)
    result = build_genesis(content, content.npcs, root=ROOT, state=state)
    apply_effects(state, result.effects)
    assert len(content.npcs) == 92
    assert all(state.entities[str(n["id"])].max_hp > 0 for n in content.npcs)
    assert state.entities["/d/hangzhou/npc/laohu"].max_hp == 200
    assert state.entities["/d/shiliang/npc/li"].max_hp == 500
    assert state.entities["/d/shiliang/npc/qingming"].max_hp == 500
    assert state.entities["/d/shiliang/npc/tufei2"].max_hp == 300
