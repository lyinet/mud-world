"""玩家门派 P1 动作层回归：门槛、拒绝码、钱流守恒、欠饷离派、事件链。"""
from __future__ import annotations

import json

import pytest

from wsim.actions.player_sect import (
    ARREARS_EXPEL,
    DISCIPLE_CAP,
    DUES_DAILY,
    FOUNDING_COST,
    MIN_REPUTATION,
    REGISTRATION_FEE,
    STARTUP_CAPITAL,
    STIPEND_DAILY,
    PlayerExpelDiscipleAction,
    PlayerFoundSectAction,
    PlayerPayDisciplesAction,
    PlayerRecruitDiscipleAction,
    PlayerSectDepositAction,
    PlayerSectWithdrawAction,
)
from wsim.actions.registry import ActionRegistry
from wsim.audit.conservation import conservation
from wsim.core.models import Effect, Intent, Reject
from wsim.world.effects import apply_effects
from wsim.world.state import Relation, WorldState
from wsim.world.store import EventStore
from wsim.world.writer import Writer

ACADEMY = "academy"
MARKET = "market"
PLAYER = "player:u_demo"
SID = "wuxing"
FID = f"sect:{SID}"
TREASURY = f"treasury:sect:{SID}"


def _locations() -> dict:
    return {
        ACADEMY: {"id": ACADEMY, "name": "书院", "exits": [MARKET], "services": []},
        MARKET: {"id": MARKET, "name": "集市", "exits": [ACADEMY], "services": []},
    }


def _bond(state: WorldState, npc: str, aff: float = 40.0, trust: float = 50.0) -> None:
    for a, b in ((PLAYER, npc), (npc, PLAYER)):
        state.relations[f"{a}|{b}|friend"] = Relation(a, b, "friend", affection=aff, trust=trust)


def seed(player_money: int = 1200, bonds: bool = True) -> WorldState:
    state = WorldState(locations=_locations())
    effects: list[Effect] = [
        Effect("create_account", {"account": f"wallet:{PLAYER}"}),
        Effect("mint", {"account": f"wallet:{PLAYER}", "amount": player_money, "reason": "test"}),
        Effect("create_account", {"account": "treasury:region"}),
        Effect(
            "create_entity",
            {
                "id": PLAYER,
                "name": "掌门甲",
                "role": "镖师",
                "location": ACADEMY,
                "home": ACADEMY,
                "stamina": 100.0,
                "skills": {"镖师": 0.5},
                "personality": {},
            },
        ),
        Effect(
            "create_entity",
            {
                "id": "player:u_other",
                "name": "另一名玩家",
                "role": "镖师",
                "location": ACADEMY,
                "home": ACADEMY,
                "stamina": 100.0,
                "skills": {"镖师": 0.5},
                "personality": {},
            },
        ),
    ]
    for i in range(1, 6):
        nid = f"n{i}"
        skills = {"武艺": 0.3}
        if i == 5:
            skills = {"武艺": 0.05}
        effects.extend(
            [
                Effect("create_account", {"account": f"wallet:{nid}"}),
                Effect("mint", {"account": f"wallet:{nid}", "amount": 60, "reason": "test"}),
                Effect(
                    "create_entity",
                    {
                        "id": nid,
                        "name": f"弟子{nid}",
                        "role": "worker",
                        "location": ACADEMY,
                        "home": ACADEMY,
                        "stamina": 100.0,
                        "skills": skills,
                        "personality": {},
                    },
                ),
            ]
        )
    apply_effects(state, effects)
    if bonds:
        for i in range(1, 5):
            _bond(state, f"n{i}")
    return state


def _intent(actor: str, action, args: dict, tick: int = 0) -> Intent:
    return Intent(actor=actor, verb=action.verb, args=args, tick=tick, source="player")


def _pre(action, state: WorldState, intent: Intent) -> list[str]:
    return action.preconditions(state, intent)


def _apply(action, state: WorldState, intent: Intent) -> list[Effect]:
    effects = action.apply(state, intent, None)
    apply_effects(state, effects)
    return effects


def _found(state: WorldState, name: str = "五行道") -> list[Effect]:
    action = PlayerFoundSectAction(None)
    intent = _intent(
        PLAYER,
        action,
        {"name": name, "at": ACADEMY, "sect_id": SID, "doctrine": "守望相济"},
    )
    assert _pre(action, state, intent) == []
    return _apply(action, state, intent)


def _recruit(state: WorldState, npc: str, tick: int = 0) -> list[Effect]:
    action = PlayerRecruitDiscipleAction(None)
    intent = _intent(PLAYER, action, {"sect_id": SID, "target": npc}, tick=tick)
    assert _pre(action, state, intent) == []
    return _apply(action, state, intent)


def _pay(state: WorldState, tick: int) -> list[Effect]:
    action = PlayerPayDisciplesAction(None)
    intent = _intent(PLAYER, action, {"sect_id": SID}, tick=tick)
    assert _pre(action, state, intent) == []
    return _apply(action, state, intent)


def _assert_conserved(before: dict, after: dict) -> None:
    assert after["minted_total"] == before["minted_total"]
    assert after["burned_total"] == before["burned_total"]
    assert after["conservation_error"] == 0
    assert after["negative_accounts"] == []


# ---------- 立派 ----------


def test_found_sect_transfers_and_faction():
    state = seed()
    before = conservation(state)
    effects = _found(state)
    ops = [e.op for e in effects]
    assert "create_account" in ops and "create_faction" in ops and "faction_join" in ops
    transfers = [e.data for e in effects if e.op == "transfer"]
    assert transfers[0]["dst"] == "treasury:region"
    assert transfers[0]["amount"] == REGISTRATION_FEE
    assert transfers[0]["reason"] == "sect_registration_fee"
    assert transfers[1]["dst"] == TREASURY
    assert transfers[1]["amount"] == STARTUP_CAPITAL
    assert transfers[1]["reason"] == "sect_endowment"
    assert state.accounts[f"wallet:{PLAYER}"] == 1200 - FOUNDING_COST
    assert state.accounts["treasury:region"] == REGISTRATION_FEE
    assert state.accounts[TREASURY] == STARTUP_CAPITAL
    fac = state.factions[FID]
    assert fac.name == "五行道" and fac.founder == PLAYER and fac.leader == PLAYER
    assert fac.location == ACADEMY and fac.treasury_account == TREASURY
    assert state.entities[PLAYER].faction == FID
    assert PLAYER in fac.members
    assert any("宗旨" in m["text"] for m in state.memories[PLAYER])
    _assert_conserved(before, conservation(state))


def test_found_sect_insufficient_funds():
    state = seed(player_money=FOUNDING_COST - 1)
    action = PlayerFoundSectAction(None)
    intent = _intent(PLAYER, action, {"name": "五行道", "at": ACADEMY, "sect_id": SID})
    assert _pre(action, state, intent) == ["insufficient_funds"]


def test_found_sect_insufficient_rep():
    state = seed(bonds=False)
    action = PlayerFoundSectAction(None)
    intent = _intent(PLAYER, action, {"name": "五行道", "at": ACADEMY, "sect_id": SID})
    assert _pre(action, state, intent) == ["insufficient_rep"]
    assert MIN_REPUTATION == 20.0


def test_found_sect_already_in_sect():
    state = seed()
    state.entities[PLAYER].faction = "sect:other"
    action = PlayerFoundSectAction(None)
    intent = _intent(PLAYER, action, {"name": "五行道", "at": ACADEMY, "sect_id": SID})
    assert _pre(action, state, intent) == ["already_in_sect"]


@pytest.mark.parametrize(
    "args,expected",
    [
        ({"name": "五", "at": ACADEMY, "sect_id": SID}, "invalid_sect_name"),
        ({"name": "五行道", "at": MARKET, "sect_id": SID}, "wrong_location"),
        ({"name": "五行道", "at": "ghost", "sect_id": SID}, "unknown_location"),
    ],
)
def test_found_sect_name_and_location(args, expected):
    state = seed()
    if expected == "unknown_location":
        state.entities[PLAYER].location = "ghost"
    action = PlayerFoundSectAction(None)
    intent = _intent(PLAYER, action, args)
    assert _pre(action, state, intent) == [expected]


def test_found_sect_name_taken_and_id_exists():
    state = seed()
    apply_effects(
        state,
        [
            Effect(
                "create_faction",
                {
                    "id": "sect:other",
                    "name": "五行道",
                    "location": ACADEMY,
                    "founder": "n1",
                    "treasury_account": "wallet:n1",
                },
            )
        ],
    )
    action = PlayerFoundSectAction(None)
    intent = _intent(PLAYER, action, {"name": "五行道", "at": ACADEMY, "sect_id": SID})
    assert _pre(action, state, intent) == ["sect_name_taken"]

    state.factions["sect:other"].name = "别的门派"
    assert _pre(action, state, intent) == []

    state.entities[PLAYER].faction = None
    apply_effects(
        state,
        [
            Effect(
                "create_faction",
                {
                    "id": FID,
                    "name": "占位",
                    "location": ACADEMY,
                    "founder": "n2",
                    "treasury_account": "wallet:n2",
                },
            )
        ],
    )
    assert _pre(action, state, intent) == ["sect_exists"]


def test_found_sect_npc_actor_delegates_to_npc_action(content):
    state = seed()
    state.entities["n1"].faction = "sect:other"
    action = PlayerFoundSectAction(content)
    intent = Intent(
        actor="n1",
        verb="found_sect",
        args={"name": "五行道", "at": ACADEMY, "sect_id": SID},
        tick=0,
        source="npc",
    )
    # NPC 分支的拒绝码是 already_in_faction，玩家分支是 already_in_sect。
    assert _pre(action, state, intent) == ["already_in_faction"]

    npc_effects = action.apply(state, intent, None)
    assert npc_effects[0].op == "create_account"
    assert any(e.op == "burn" for e in npc_effects)


# ---------- 收徒 ----------


def test_recruit_disciple_success():
    state = seed()
    _found(state)
    _recruit(state, "n1")
    ent = state.entities["n1"]
    assert ent.faction == FID and ent.master == PLAYER
    assert "n1" in state.entities[PLAYER].disciples
    rel = state.relations[f"{PLAYER}|n1|master"]
    assert rel.status == "active" and rel.affection >= 45.0
    assert state.relations[f"n1|{PLAYER}|disciple"].status == "active"
    assert any("会费" in m["text"] for m in state.memories["n1"])


def test_recruit_refusals_basics():
    state = seed()
    action = PlayerRecruitDiscipleAction(None)

    intent = _intent(PLAYER, action, {"sect_id": SID, "target": "n1"})
    assert _pre(action, state, intent) == ["sect_not_found"]

    _found(state)
    # 非掌门：n1 已入派、同地点，但不是 leader/founder。
    _recruit(state, "n1")
    non_master = _intent("n1", action, {"sect_id": SID, "target": "n2"})
    assert _pre(action, state, non_master) == ["not_sect_leader"]

    # 掌门离开驻地
    state.entities[PLAYER].location = MARKET
    assert _pre(action, state, intent) == ["wrong_location"]
    state.entities[PLAYER].location = ACADEMY

    assert _pre(action, state, _intent(PLAYER, action, {"sect_id": SID, "target": "ghost"})) == [
        "target_not_found"
    ]
    state.entities["n1"].status = "dead"
    assert _pre(action, state, _intent(PLAYER, action, {"sect_id": SID, "target": "n1"})) == [
        "target_not_alive"
    ]
    state.entities["n1"].status = "alive"
    assert _pre(action, state, _intent(PLAYER, action, {"sect_id": SID, "target": "n1"})) == [
        "already_disciple"
    ]
    assert _pre(action, state, _intent(PLAYER, action, {"sect_id": SID, "target": PLAYER})) == [
        "self_recruit"
    ]
    assert _pre(
        action, state, _intent(PLAYER, action, {"sect_id": SID, "target": "player:u_other"})
    ) == ["target_is_player"]


def test_recruit_refusals_target_state():
    state = seed()
    _found(state)
    action = PlayerRecruitDiscipleAction(None)

    state.entities["n2"].location = MARKET
    assert _pre(action, state, _intent(PLAYER, action, {"sect_id": SID, "target": "n2"})) == [
        "not_together"
    ]
    state.entities["n2"].location = ACADEMY

    state.entities["n2"].faction = "sect:other"
    assert _pre(action, state, _intent(PLAYER, action, {"sect_id": SID, "target": "n2"})) == [
        "target_in_sect"
    ]
    state.entities["n2"].faction = None

    state.entities["n2"].status = "dead"
    assert _pre(action, state, _intent(PLAYER, action, {"sect_id": SID, "target": "n2"})) == [
        "target_not_alive"
    ]
    state.entities["n2"].status = "alive"

    # 好感/信任不足
    state.relations.pop(f"{PLAYER}|n2|friend")
    state.relations.pop(f"n2|{PLAYER}|friend")
    assert _pre(action, state, _intent(PLAYER, action, {"sect_id": SID, "target": "n2"})) == [
        "favor_too_low"
    ]

    # 技能不足：目标 0.05
    _bond(state, "n5")
    assert _pre(action, state, _intent(PLAYER, action, {"sect_id": SID, "target": "n5"})) == [
        "skill_too_low"
    ]

    # 掌门技能不足：0.39
    state.entities[PLAYER].skills["镖师"] = 0.39
    _bond(state, "n2")
    assert _pre(action, state, _intent(PLAYER, action, {"sect_id": SID, "target": "n2"})) == [
        "skill_too_low"
    ]


def test_recruit_sect_full():
    state = seed()
    _found(state)
    for nid in ("n1", "n2", "n3"):
        _recruit(state, nid)
    action = PlayerRecruitDiscipleAction(None)
    _bond(state, "n4")
    assert _pre(action, state, _intent(PLAYER, action, {"sect_id": SID, "target": "n4"})) == [
        "sect_full"
    ]
    assert len([e for e in state.factions[FID].members if e.startswith("n")]) == DISCIPLE_CAP


# ---------- 逐出 ----------


def test_expel_disciple_refusals_and_success():
    state = seed()
    action = PlayerExpelDiscipleAction(None)
    assert _pre(action, state, _intent(PLAYER, action, {"sect_id": SID, "target": "n1"})) == [
        "sect_not_found"
    ]

    _found(state)
    _recruit(state, "n1")
    assert _pre(action, state, _intent("n1", action, {"sect_id": SID, "target": "n2"})) == [
        "not_sect_leader"
    ]
    state.entities[PLAYER].location = MARKET
    assert _pre(action, state, _intent(PLAYER, action, {"sect_id": SID, "target": "n1"})) == [
        "wrong_location"
    ]
    state.entities[PLAYER].location = ACADEMY
    assert _pre(action, state, _intent(PLAYER, action, {"sect_id": SID, "target": "ghost"})) == [
        "disciple_not_found"
    ]
    assert _pre(action, state, _intent(PLAYER, action, {"sect_id": SID, "target": PLAYER})) == [
        "self_recruit"
    ]
    assert _pre(action, state, _intent(PLAYER, action, {"sect_id": SID, "target": "n4"})) == [
        "target_not_disciple"
    ]

    effects = _apply(action, state, _intent(PLAYER, action, {"sect_id": SID, "target": "n1"}))
    assert state.entities["n1"].faction is None
    assert state.entities["n1"].master is None
    assert state.relations[f"{PLAYER}|n1|master"].status == "ended"
    assert state.relations[f"n1|{PLAYER}|disciple"].status == "ended"
    assert "n1" in state.factions[FID].members  # 无 faction_leave：历史名册保留
    assert any("逐出" in m["text"] for m in state.memories["n1"])
    assert [e.op for e in effects].count("set_field") >= 2


# ---------- 日结 / 会费 / 俸禄 / 欠饷 ----------


def test_pay_disciples_dues_stipend_and_conservation():
    state = seed()
    _found(state)
    _recruit(state, "n1")
    _recruit(state, "n2")
    before = conservation(state)
    treasury_before = state.account_balance(TREASURY)
    w1 = state.account_balance("wallet:n1")
    w2 = state.account_balance("wallet:n2")

    effects = _pay(state, tick=1)
    reasons = sorted(e.data["reason"] for e in effects if e.op == "transfer")
    assert reasons.count("sect_dues") == 2
    assert reasons.count("sect_stipend") == 2
    assert state.account_balance(TREASURY) == treasury_before + 2 * DUES_DAILY - 2 * STIPEND_DAILY
    assert state.account_balance("wallet:n1") == w1 - DUES_DAILY + STIPEND_DAILY
    assert state.account_balance("wallet:n2") == w2 - DUES_DAILY + STIPEND_DAILY
    assert state.entities["n1"].skills["武艺"] > 0.3  # 在驻地修炼
    assert any("门派日结:sect:wuxing:D0" in m["text"] for m in state.memories[PLAYER])
    _assert_conserved(before, conservation(state))


def test_pay_disciples_is_idempotent_per_day():
    state = seed()
    _found(state)
    _recruit(state, "n1")
    _pay(state, tick=1)
    action = PlayerPayDisciplesAction(None)
    intent = _intent(PLAYER, action, {"sect_id": SID}, tick=2)
    assert _pre(action, state, intent) == ["already_settled_today"]


def test_pay_disciples_refusals():
    state = seed()
    action = PlayerPayDisciplesAction(None)
    assert _pre(action, state, _intent(PLAYER, action, {"sect_id": SID}, tick=1)) == [
        "sect_not_found"
    ]
    _found(state)
    assert _pre(action, state, _intent(PLAYER, action, {"sect_id": SID}, tick=1)) == [
        "no_disciples"
    ]
    _recruit(state, "n1")
    state.entities[PLAYER].location = MARKET
    assert _pre(action, state, _intent(PLAYER, action, {"sect_id": SID}, tick=1)) == [
        "wrong_location"
    ]
    state.entities[PLAYER].location = ACADEMY
    assert _pre(action, state, _intent("n1", action, {"sect_id": SID}, tick=1)) == [
        "not_sect_leader"
    ]
    bad = _intent(PLAYER, action, {"sect_id": SID, "day": 9}, tick=500)
    with pytest.raises(Reject):
        _pre(action, state, bad)


def test_pay_disciples_arrears_expels_on_third_day():
    state = seed()
    _found(state)
    _recruit(state, "n1")
    _recruit(state, "n2")
    before = conservation(state)
    # 门派把金库花光（真实转出到官仓，绕开 100 文保底）后连续 3 日日结。
    apply_effects(
        state,
        [
            Effect(
                "transfer",
                {
                    "src": TREASURY,
                    "dst": "treasury:region",
                    "amount": state.account_balance(TREASURY),
                    "reason": "test_drain",
                },
            )
        ],
    )

    _pay(state, tick=1)  # 第 0 日
    assert state.entities["n2"].faction == FID
    n2_debt = state.debts[f"debt:sect_stipend:{FID}:n2"]
    assert n2_debt.amount == STIPEND_DAILY and n2_debt.note == "unpaid_stipend"

    _pay(state, tick=145)  # 第 1 日
    assert state.debts[f"debt:sect_stipend:{FID}:n2"].amount == 2 * STIPEND_DAILY
    assert state.entities["n2"].faction == FID

    _pay(state, tick=289)  # 第 2 日：欠饷 30 文
    assert state.debts[f"debt:sect_stipend:{FID}:n2"].amount == ARREARS_EXPEL
    assert state.entities["n2"].faction is None
    assert state.entities["n2"].master is None
    assert state.relations[f"{PLAYER}|n2|master"].status == "ended"
    assert state.entities["n1"].faction == FID  # 排序靠前者每日足额领到俸禄
    assert state.debts[f"debt:sect_stipend:{FID}:n2"].status == "active"  # 债务保留
    assert any("欠饷" in m["text"] for m in state.memories["n2"])
    _assert_conserved(before, conservation(state))


def test_pay_disciples_duty_quest_and_away():
    state = seed()
    _found(state)
    _recruit(state, "n1")
    _recruit(state, "n2")
    state.entities["n2"].location = MARKET
    _pay(state, tick=1)
    assert any("在驻地修炼" in m["text"] for m in state.memories["n1"])
    assert any("不在驻地" in m["text"] for m in state.memories["n2"])
    assert state.entities["n1"].skills["武艺"] > 0.3
    assert state.entities["n2"].skills["武艺"] == pytest.approx(0.3)


# ---------- 金库存取 ----------


def test_sect_deposit_and_limits():
    state = seed()
    _found(state)
    action = PlayerSectDepositAction(None)
    intent = _intent(PLAYER, action, {"sect_id": SID, "amount": 100})
    assert _pre(action, state, intent) == []
    before = conservation(state)
    _apply(action, state, intent)
    assert state.account_balance(TREASURY) == STARTUP_CAPITAL + 100
    _assert_conserved(before, conservation(state))

    too_much = _intent(PLAYER, action, {"sect_id": SID, "amount": 10000})
    assert _pre(action, state, too_much) == ["insufficient_funds"]
    with pytest.raises(Reject):
        _pre(action, state, _intent(PLAYER, action, {"sect_id": SID, "amount": 0}))


def test_sect_withdraw_caps_and_reserve():
    state = seed()
    _found(state)
    action = PlayerSectWithdrawAction(None)

    assert _pre(action, state, _intent(PLAYER, action, {"sect_id": SID, "amount": 600})) == [
        "withdraw_over_cap"
    ]
    state.accounts[TREASURY] = 300
    assert _pre(action, state, _intent(PLAYER, action, {"sect_id": SID, "amount": 400})) == [
        "insufficient_treasury"
    ]
    state.accounts[TREASURY] = 550
    assert _pre(action, state, _intent(PLAYER, action, {"sect_id": SID, "amount": 500})) == [
        "treasury_reserve"
    ]
    state.accounts[TREASURY] = 600
    intent = _intent(PLAYER, action, {"sect_id": SID, "amount": 500})
    assert _pre(action, state, intent) == []
    before = conservation(state)
    _apply(action, state, intent)
    assert state.account_balance(TREASURY) == 100  # 保底
    assert state.account_balance(f"wallet:{PLAYER}") == 400 + 500
    _assert_conserved(before, conservation(state))


def test_withdraw_refusals_other():
    state = seed()
    action = PlayerSectWithdrawAction(None)
    assert _pre(action, state, _intent(PLAYER, action, {"sect_id": SID, "amount": 100})) == [
        "sect_not_found"
    ]
    _found(state)
    assert _pre(action, state, _intent("n1", action, {"sect_id": SID, "amount": 100})) == [
        "not_sect_leader"
    ]
    state.entities[PLAYER].location = MARKET
    assert _pre(action, state, _intent(PLAYER, action, {"sect_id": SID, "amount": 100})) == [
        "wrong_location"
    ]


# ---------- 纯函数 / 事件链 ----------


def test_apply_is_deterministic():
    state = seed()
    _found(state)
    _recruit(state, "n1")
    action = PlayerPayDisciplesAction(None)
    intent = _intent(PLAYER, action, {"sect_id": SID}, tick=1)
    a = action.apply(state, intent, None)
    b = action.apply(state, intent, None)
    assert [e.to_dict() for e in a] == [e.to_dict() for e in b]


def test_writer_event_chain_transcript(tmp_path):
    state = seed()
    store = EventStore(tmp_path / "sect.sqlite")
    registry = ActionRegistry()
    for action in (
        PlayerFoundSectAction(None),
        PlayerRecruitDiscipleAction(None),
        PlayerExpelDiscipleAction(None),
        PlayerPayDisciplesAction(None),
        PlayerSectDepositAction(None),
        PlayerSectWithdrawAction(None),
    ):
        registry.register(action)
    writer = Writer(state, store, registry, seed=7)

    def submit(tick: int, verb: str, args: dict, actor: str = PLAYER):
        writer.state.tick = tick  # 模拟世界时钟先推进到该 tick
        return writer.submit(Intent(actor=actor, verb=verb, args=args, tick=tick, source="player"))

    submit(0, "found_sect", {"name": "五行道", "at": ACADEMY, "sect_id": SID, "doctrine": "守望相济"})
    submit(5, "recruit_disciple", {"sect_id": SID, "target": "n1"})
    submit(10, "recruit_disciple", {"sect_id": SID, "target": "n2"})
    # 金库花光（真实转出到官仓），验证欠饷链。
    apply_effects(
        writer.state,
        [
            Effect(
                "transfer",
                {
                    "src": TREASURY,
                    "dst": "treasury:region",
                    "amount": writer.state.account_balance(TREASURY),
                    "reason": "test_drain",
                },
            )
        ],
    )
    submit(20, "pay_disciples", {"sect_id": SID, "day": 0})
    submit(145, "pay_disciples", {"sect_id": SID, "day": 1})
    submit(289, "pay_disciples", {"sect_id": SID, "day": 2})

    events = store.read_all()
    transcript = [
        {
            "seq": e.seq,
            "tick": e.tick,
            "day": e.day,
            "actor": e.actor,
            "verb": e.verb,
            "args": e.args,
            "outcome": e.outcome,
            "effects": e.effects,
        }
        for e in events
    ]
    print(json.dumps(transcript, ensure_ascii=False, indent=2))
    store.close()

    assert [e.verb for e in events] == [
        "found_sect",
        "recruit_disciple",
        "recruit_disciple",
        "pay_disciples",
        "pay_disciples",
        "pay_disciples",
    ]
    assert all(e.outcome == "ok" for e in events)
    leaves = [
        ef for e in events for ef in e.effects
        if ef["op"] == "set_field" and ef["data"].get("field") == "faction"
    ]
    assert leaves and leaves[-1]["data"]["value"] is None
    assert writer.state.entities["n2"].faction is None
    assert writer.state.entities["n1"].faction == FID
    ops = [ef["op"] for e in events for ef in e.effects]
    assert "debt_increase" in ops and "debt_settle" not in ops
    texts = [
        str(ef["data"].get("text", ""))
        for e in events
        for ef in e.effects
        if ef["op"] == "memory_add"
    ]
    assert any("欠饷" in t and "离派" in t for t in texts)


# ---------- 拒绝码契约（docs/player-api.md §13） ----------


def test_recruit_favor_and_trust_are_separate_codes():
    state = seed()
    _found(state)
    action = PlayerRecruitDiscipleAction(None)
    for a, b in ((PLAYER, "n2"), ("n2", PLAYER)):
        rel = state.relations[f"{a}|{b}|friend"]
        rel.affection = 45.0
        rel.trust = 0.0
    assert _pre(action, state, _intent(PLAYER, action, {"sect_id": SID, "target": "n2"})) == [
        "trust_too_low"
    ]
    for a, b in ((PLAYER, "n2"), ("n2", PLAYER)):
        rel = state.relations[f"{a}|{b}|friend"]
        rel.affection = 0.0
        rel.trust = 45.0
    assert _pre(action, state, _intent(PLAYER, action, {"sect_id": SID, "target": "n2"})) == [
        "favor_too_low"
    ]


def test_recruit_requires_master_reputation():
    state = seed()
    _found(state)
    for rel_id in list(state.relations):
        if PLAYER in rel_id.split("|"):
            state.relations.pop(rel_id)
    action = PlayerRecruitDiscipleAction(None)
    assert _pre(action, state, _intent(PLAYER, action, {"sect_id": SID, "target": "n2"})) == [
        "insufficient_rep"
    ]


def test_sect_id_defaults_to_actor_and_no_sect():
    state = seed()
    deposit = PlayerSectDepositAction(None)
    withdraw = PlayerSectWithdrawAction(None)
    # 未入派：省略 sect_id → no_sect；显式未知门派 → sect_not_found。
    assert _pre(deposit, state, _intent(PLAYER, deposit, {"amount": 100})) == ["no_sect"]
    assert _pre(withdraw, state, _intent(PLAYER, withdraw, {"amount": 100})) == ["no_sect"]
    assert _pre(deposit, state, _intent(PLAYER, deposit, {"sect_id": "ghost", "amount": 100})) == [
        "sect_not_found"
    ]
    _found(state)
    # 掌门省略 sect_id 时默认本人门派。
    assert _pre(deposit, state, _intent(PLAYER, deposit, {"amount": 100})) == []
    _recruit(state, "n1")
    # 弟子省略 sect_id 时同样解析到本派，但不是掌门。
    assert _pre(deposit, state, _intent("n1", deposit, {"amount": 1})) == ["not_sect_leader"]
