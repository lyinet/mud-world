"""NPC 主动攻击（xkx parity）：规则派生、触发/心跳、条件与概率、旧世界隔离。

原版依据见 `specs/xkx-parity/npc-aggression.spec.md`：
- `feature/attack.lpc:245 init()` 在对象移入环境时触发；
- `feature/attack.lpc:269` aggressive -> `COMBAT_D->auto_fight`；
- `feature/attack.lpc:69 kill_ob` 只登记敌人并开心跳，下一心跳才结算伤害。
复刻对应：进入房间的 trigger 事件只写 `combat_target`；ambient 每 tick 结算 1 回合。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.actions.aggression import NpcAggressionAction  # noqa: E402
from wsim.actions.registry import ActionRegistry  # noqa: E402
from wsim.api import WorldSession  # noqa: E402
from wsim.core.content import load_content  # noqa: E402
from wsim.core.models import Effect, Intent  # noqa: E402
from wsim.core.rng import PRNG  # noqa: E402
from wsim.sim.aggression import aggression_rules, decide  # noqa: E402
from wsim.sim.scheduler import AmbientConfig, AmbientScheduler  # noqa: E402
from wsim.world.state import WorldState  # noqa: E402
from wsim.world.store import EventStore  # noqa: E402
from wsim.world.writer import Writer  # noqa: E402

LOCATIONS = {"inn": {"id": "inn", "name": "客栈", "exits": []}}
NPC_ID = "/d/hangzhou/npc/laohu"
PLAYER_ID = "player:hero"
XIONGHUANG = "/clone/medicine/vegetable/xionghuang"
XKX_CONTENT = ROOT / "content" / "xkx"
XKX_WORLD = ROOT / "config" / "worlds" / "xkx.yaml"


def _rules(kind: str, npc_id: str = NPC_ID, **extra) -> dict[str, dict]:
    return {npc_id: {"kind": kind, "name": "测试NPC", **extra}}


def _world(
    rules: dict,
    seed: int = 42,
    items: dict[str, int] | None = None,
    npc_id: str = NPC_ID,
    player_status: str = "alive",
):
    state = WorldState(locations=LOCATIONS)
    store = EventStore(":memory:")
    reg = ActionRegistry()
    reg.register(NpcAggressionAction(rules))
    writer = Writer(state, store, reg, seed)
    effects = [
        Effect(
            "create_entity",
            {
                "id": npc_id,
                "name": "测试NPC",
                "role": "npc",
                "location": "inn",
                "max_hp": 500,
                "hp": 500,
                "attack": 60,
                "defense": 10,
                "attrs": {"str": 20, "dex": 15},
                "combat_exp": 10,
                "skill_levels": {"unarmed": 30},
            },
        ),
        Effect(
            "create_entity",
            {
                "id": PLAYER_ID,
                "name": "英雄",
                "role": "wanderer",
                "location": "inn",
                "status": player_status,
                "max_hp": 100,
                "hp": 100,
                "attack": 15,
                "defense": 5,
                "attrs": {"str": 20, "con": 20, "dex": 18, "kar": 20, "per": 20},
                "combat_exp": 10,
                "skill_levels": {"unarmed": 40},
            },
        ),
    ]
    if items:
        for item, qty in items.items():
            effects.append(Effect("item_create", {"owner": PLAYER_ID, "item": item, "qty": qty}))
    writer.bootstrap(effects, tick=0, verb="genesis")
    return writer, store


def _trigger(writer: Writer, tick: int = 0, npc_id: str = NPC_ID):
    return writer.submit(
        Intent(npc_id, "npc_aggression", {"mode": "trigger", "target": PLAYER_ID}, tick, "npc")
    )


def _continue(writer: Writer, tick: int, npc_id: str = NPC_ID):
    return writer.submit(
        Intent(npc_id, "npc_aggression", {"mode": "continue", "target": PLAYER_ID}, tick, "npc")
    )


def _log(event) -> dict:
    for eff in event.effects:
        if eff.get("op") == "aggression_log":
            return dict(eff.get("data") or {})
    return {}


# ---------------------------------------------------------------- 规则与旧世界隔离
def test_legacy_content_has_no_aggression_rules():
    legacy = load_content(ROOT, min_npcs=0, allow_fixture=True, use_content=False)
    assert aggression_rules(legacy) == {}
    assert not any(n.get("aggressive") for n in legacy.npcs)


def test_xkx_content_rules_match_original_semantics():
    from wsim.api.xkx_content import load_xkx_content

    rules = aggression_rules(load_xkx_content(XKX_CONTENT))
    assert rules["/d/hangzhou/npc/laohu"]["kind"] == "always"
    assert rules["/d/hangzhou/npc/dushe"]["kind"] == "unless_item"
    assert rules["/d/hangzhou/npc/dushe"]["item_key"] == "xionghuang"
    assert rules["/d/hangzhou/npc/wolfdog"]["kind"] == "chance_kar_per"
    assert rules["/d/hangzhou/npc/wolfdog"]["threshold"] == 30
    # 原版 `attitude=aggressive` 的其余 NPC 也走 always（如 chang1/chang2）。
    assert rules["/d/hangzhou/honghua/chang1"]["kind"] == "always"


# ---------------------------------------------------------------- 无条件（老虎）
def test_always_trigger_only_marks_combat_then_rounds_happen_on_heartbeat():
    writer, _ = _world(_rules("always"))
    event = _trigger(writer)
    assert event.outcome == "ok"
    log = _log(event)
    assert log["result"] == "attack" and log["triggered"] is True
    npc = writer.state.entities[NPC_ID]
    target = writer.state.entities[PLAYER_ID]
    # trigger 只登记仇恨（kill_ob），不结算伤害。
    assert npc.combat_target == PLAYER_ID
    assert npc.combat_round == 0
    assert target.hp == 100

    # 心跳开始逐回合结算。NPC 的伤害在命中回合才落到玩家身上：
    # 本 seed 下前 8 回合玩家都闪避，第 9 回合首次被打到 35 点（PRNG 序列锁定）。
    event1 = _continue(writer, 1)
    assert event1.outcome == "ok"
    assert _log(event1)["round"] == 1
    assert writer.state.entities[PLAYER_ID].hp == 100
    assert writer.state.entities[NPC_ID].hp < 500
    assert writer.state.entities[NPC_ID].combat_round == 1
    for tick in range(2, 9):
        ev = _continue(writer, tick)
        assert ev.outcome == "ok"
        assert writer.state.entities[PLAYER_ID].hp == 100
    ev9 = _continue(writer, 9)
    assert ev9.outcome == "ok"
    assert _log(ev9)["round"] == 9
    assert writer.state.entities[PLAYER_ID].hp == 35


def test_continue_until_dead_resets_combat():
    writer, _ = _world(_rules("always"))
    _trigger(writer, 0)
    for tick in range(1, 40):
        ev = _continue(writer, tick)
        if ev.outcome != "ok":
            break
        if writer.state.entities[PLAYER_ID].status == "dead":
            break
        if writer.state.entities[NPC_ID].status == "dead":
            break
    target = writer.state.entities[PLAYER_ID]
    assert target.status == "dead"
    assert target.hp == 0
    # 目标死亡后同一回合即收兵，之后不再判定。
    assert writer.state.entities[NPC_ID].combat_target == ""


def test_trigger_rejects_when_already_fighting():
    writer, _ = _world(_rules("always"))
    assert _trigger(writer, 0).outcome == "ok"
    second = _trigger(writer, 1)
    assert second.outcome == "rejected"
    assert second.reason == "rejected:already_fighting"


def test_dead_target_is_not_triggered_again():
    writer, _ = _world(_rules("always"))
    _trigger(writer, 0)
    for tick in range(1, 40):
        ev = _continue(writer, tick)
        if ev.outcome != "ok" or writer.state.entities[PLAYER_ID].status == "dead":
            break
    assert writer.state.entities[PLAYER_ID].status == "dead"
    again = _trigger(writer, 50)
    assert again.outcome == "rejected"
    assert again.reason == "rejected:target_not_alive"


def test_unconcious_target_is_not_triggered():
    writer, _ = _world(_rules("always"), player_status="unconcious")
    event = _trigger(writer, 0)
    assert event.outcome == "rejected"
    assert event.reason == "rejected:target_not_alive"
    assert writer.state.entities[NPC_ID].combat_target == ""


# ---------------------------------------------------------------- 条件（毒蛇）
def test_unless_item_declines_when_player_carries_xionghuang():
    writer, _ = _world(
        _rules("unless_item", npc_id="/d/hangzhou/npc/dushe", item=XIONGHUANG, item_key="xionghuang"),
        items={"xionghuang": 1},
        npc_id="/d/hangzhou/npc/dushe",
    )
    event = _trigger(writer, 0, "/d/hangzhou/npc/dushe")
    log = _log(event)
    assert log["result"] == "declined_item"
    assert log["triggered"] is False
    assert writer.state.entities["/d/hangzhou/npc/dushe"].combat_target == ""


def test_unless_item_attacks_without_xionghuang():
    writer, _ = _world(
        _rules("unless_item", npc_id="/d/hangzhou/npc/dushe", item=XIONGHUANG, item_key="xionghuang"),
        npc_id="/d/hangzhou/npc/dushe",
    )
    event = _trigger(writer, 0, "/d/hangzhou/npc/dushe")
    log = _log(event)
    assert log["result"] == "attack" and log["triggered"] is True
    assert writer.state.entities["/d/hangzhou/npc/dushe"].combat_target == PLAYER_ID


# ---------------------------------------------------------------- 概率（大狼狗）
def test_wolfdog_roll_is_locked_to_prng_seed_and_event_seq():
    rules = _rules("chance_kar_per", npc_id="/d/hangzhou/npc/wolfdog", threshold=30)
    # 取数顺序锁定：seed=42、trigger 是第 2 条事件（genesis 之后）时 roll=0。
    assert PRNG(42, 2, "action:npc_aggression").randint(0, 39) == 0
    writer, _ = _world(rules, npc_id="/d/hangzhou/npc/wolfdog")
    event = _trigger(writer, 0, "/d/hangzhou/npc/wolfdog")
    log = _log(event)
    assert (log["roll"], log["total"], log["threshold"]) == (0, 40, 30)
    assert log["result"] == "attack"

    # 同一 seed 下 event seq=4 的取数是 36（>=30），判定为不出手。
    assert PRNG(42, 4, "action:npc_aggression").randint(0, 39) == 36
    writer2, _ = _world(rules, npc_id="/d/hangzhou/npc/wolfdog")
    writer2.reject(Intent("system", "noop", {}, 0, "system"), "dummy")
    writer2.reject(Intent("system", "noop2", {}, 0, "system"), "dummy")
    declined = _trigger(writer2, 0, "/d/hangzhou/npc/wolfdog")
    log2 = _log(declined)
    assert (log2["roll"], log2["result"]) == (36, "declined_chance")
    assert writer2.state.entities["/d/hangzhou/npc/wolfdog"].combat_target == ""


def test_decide_uses_kar_plus_per_not_wall_clock():
    npc = SimpleNamespace(id=NPC_ID, name="狼狗")
    player = SimpleNamespace(id=PLAYER_ID, name="英雄", attrs={"kar": 20, "per": 20}, inventory={})
    rule = {"kind": "chance_kar_per", "threshold": 30}
    # 同 seed/seq 反复调用结果一致，且只消费一次 randint。
    for _ in range(3):
        ok, info = decide(rule, npc, player, PRNG(42, 2, "action:npc_aggression"))
        assert ok is True
        assert info["roll"] == 0
    # total 由 kar+per 决定：10+10 -> 20。
    small = SimpleNamespace(id=PLAYER_ID, name="英雄", attrs={"kar": 10, "per": 10}, inventory={})
    ok, info = decide(rule, npc, small, PRNG(42, 2, "action:npc_aggression"))
    assert info["total"] == 20
    assert ok is (info["roll"] < 30)


# ---------------------------------------------------------------- 每 tick 只判定一次
def test_scheduler_processes_at_most_one_aggression_intent_per_npc_per_tick():
    state = WorldState(locations=LOCATIONS)
    npc = SimpleNamespace(
        id=NPC_ID,
        role="npc",
        status="alive",
        combat_target=PLAYER_ID,
        location="inn",
    )
    player = SimpleNamespace(
        id=PLAYER_ID, role="wanderer", status="alive", location="inn", combat_target=""
    )
    state.entities[NPC_ID] = npc
    state.entities[PLAYER_ID] = player
    state.players[PLAYER_ID] = SimpleNamespace(online=True)

    submitted: list[Intent] = []
    fake = SimpleNamespace(
        registry=SimpleNamespace(get=lambda verb: SimpleNamespace(rules={"x": {"kind": "always"}})),
        writer=SimpleNamespace(state=state),
        _aggression_ticks={},
        _submit=lambda intent: submitted.append(intent),
    )
    AmbientScheduler._process_npc_aggression(fake, 10)
    AmbientScheduler._process_npc_aggression(fake, 10)  # 同一 tick 第二次 -> 跳过
    assert len(submitted) == 1
    assert submitted[0].args["mode"] == "continue"
    AmbientScheduler._process_npc_aggression(fake, 11)
    assert len(submitted) == 2


# ---------------------------------------------------------------- 真实 xkx 会话
def _session(tmp_path) -> WorldSession:
    import yaml

    spawn = str((yaml.safe_load(XKX_WORLD.read_text(encoding="utf-8")) or {}).get("spawn"))
    return WorldSession(
        ROOT,
        run_id="xkx-aggression-test",
        seed=42,
        npc_limit=0,
        run_dir=tmp_path / "run",
        content_root=XKX_CONTENT,
        world_config=XKX_WORLD,
        spawn=spawn,
    )


def _walk(session: WorldSession, account: str, route: list[str]) -> None:
    for direction in route:
        result = session.xkx_command(account, {"account_id": account, "command": f"go {direction}"})
        assert result["ok"] is True, (direction, result.get("lines"))


def test_real_session_entering_laohu_room_takes_damage_without_fight(tmp_path):
    session = _session(tmp_path)
    try:
        account = "xkx_agg"
        session.xkx_command(account, {"account_id": account, "command": "go west"})
        assert int(session.state.entities[f"player:{account}"].hp) == 100
        route = ["south", "south", "south", "southwest", "southwest", "southwest", "westup", "westup", "westdown"]
        _walk(session, account, route)
        account_ent = session.state.entities[f"player:{account}"]
        assert account_ent.location == "/d/hangzhou/shanlu6"

        npc = session.state.entities[NPC_ID]
        assert npc.combat_target == f"player:{account}"
        assert npc.combat_round == 0
        assert int(account_ent.hp) == 100  # 只有 kill_ob，没有立即伤害

        scheduler = AmbientScheduler(
            ROOT,
            AmbientConfig(
                out_dir=tmp_path / "amb",
                run_id="agg-test",
                seed=42,
                start_tick=int(session.state.tick),
            ),
            content=session.content,
            writer=session.writer,
            store=session.store,
            registry=session.registry,
            lock=session.lock,
        )
        scheduler.step(1)
        assert int(session.state.entities[NPC_ID].combat_round) == 1
        hp_after = int(session.state.entities[f"player:{account}"].hp)
        assert hp_after < 100
        combat = session._combat_view(f"player:{account}")
        assert combat and combat["opponent"] == NPC_ID
        view = session.player_state(account)
        assert view["combat"]["round"] == 1
    finally:
        session.close()
