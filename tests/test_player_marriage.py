"""玩家婚姻闭环回归：裁决边界、事件链、守恒、重婚/和离、日常行为、确定性。"""
from __future__ import annotations

from pathlib import Path

import pytest

from wsim.actions.marriage import (
    DECISION_ACCEPT,
    DECISION_COURTSHIP,
    DECISION_REJECT,
    decide,
)
from wsim.audit.conservation import conservation
from wsim.api import WorldSession
from wsim.core.clock import TICKS_PER_DAY
from wsim.core.content import load_content
from wsim.core.models import Effect, Intent
from wsim.world.effects import apply_effects
from wsim.world.state import Relation, WorldState

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = "u_marriage"


@pytest.fixture()
def session(tmp_path):
    s = WorldSession(ROOT, run_id="marriage-test", seed=42, npc_limit=30, run_dir=tmp_path / "run")
    yield s
    s.close()


def _enter(session, request_id="e1"):
    return session.perform(
        ACCOUNT,
        "enter",
        {"account_id": ACCOUNT, "display_name": "婚试甲", "archetype": "捕快"},
        request_id,
    )


def _set_bond(session, pid: str, npc: str, affection: float, trust: float = 40.0) -> None:
    for a, b in ((pid, npc), (npc, pid)):
        session.state.relations[f"{a}|{b}|friend"] = Relation(
            a, b, "friend", affection=affection, trust=trust
        )


def _same_location_npc(session, pid: str, exclude_spouse: bool = True) -> str:
    loc = session.state.entities[pid].location
    for ent in session.state.entities.values():
        if ent.id.startswith("player:"):
            continue
        if ent.location != loc or ent.status != "alive":
            continue
        if exclude_spouse and ent.spouse is not None:
            continue
        return ent.id
    raise AssertionError("同地点没有可用 NPC")


def _verbs(session) -> list[str]:
    return [e.verb for e in session.writer.store.read_all()]


def test_decision_boundaries_are_deterministic(content):
    state = WorldState(locations=content.locations)
    apply_effects(
        state,
        [
            Effect(
                "create_entity",
                {
                    "id": "n1",
                    "name": "甲",
                    "role": "worker",
                    "location": "a",
                    "home": "a",
                    "stamina": 100.0,
                    "skills": {"work": 0.4},
                    "personality": {},
                },
            ),
            Effect(
                "create_entity",
                {
                    "id": "player:t",
                    "name": "乙",
                    "role": "worker",
                    "location": "a",
                    "home": "a",
                    "stamina": 100.0,
                    "skills": {"work": 0.4},
                    "personality": {},
                },
            ),
        ],
    )
    cases = [
        (70.0, 50.0, DECISION_ACCEPT),
        (50.0, 40.0, DECISION_COURTSHIP),
        (60.0, 25.0, DECISION_COURTSHIP),
        (58.0, 40.0, DECISION_REJECT),
        (30.0, 40.0, DECISION_REJECT),
        (70.0, 10.0, DECISION_REJECT),
    ]
    for aff, trust, expected in cases:
        state.relations = {}
        for a, b in (("player:t", "n1"), ("n1", "player:t")):
            state.relations[f"{a}|{b}|friend"] = Relation(a, b, "friend", affection=aff, trust=trust)
        got = decide(state, "player:t", "n1")
        assert got["decision"] == expected, (aff, trust, got)
        assert got["affection"] == aff and got["trust"] == trust
        assert "thresholds" in got


def test_full_flow_events_conservation_and_divorce(session):
    pid = _enter(session)["player"]
    npc = _same_location_npc(session, pid)
    _set_bond(session, pid, npc, 70.0, 50.0)
    before = conservation(session.state)
    wallet_before = session.state.account_balance(f"wallet:{pid}")
    npc_wallet_before = session.state.account_balance(f"wallet:{npc}")

    res = session.perform(ACCOUNT, "marriage_propose", {"target": npc, "gift_money": 50}, "m1")
    assert res["ok"] is True, res
    assert res["details"]["decision"] == "accept"
    assert res["data"]["marriage"]["status"] == "married"
    assert session.state.entities[pid].spouse == npc
    assert session.state.entities[npc].spouse == pid
    assert session.state.account_balance(f"wallet:{pid}") == wallet_before - 50
    assert session.state.account_balance(f"wallet:{npc}") == npc_wallet_before + 50

    after = conservation(session.state)
    assert after["accounts_sum"] == before["accounts_sum"]
    assert after["minted_total"] == before["minted_total"]
    assert after["burned_total"] == before["burned_total"]
    assert after["conservation_error"] == 0

    verbs = _verbs(session)
    marriage_verbs = [
        v for v in verbs if v in {"marriage_propose", "marriage_accept", "marriage", "marriage_end"}
    ]
    assert marriage_verbs == ["marriage_propose", "marriage_accept", "marriage"]

    # 配偶专属 talk：确定性台词，不依赖模型。
    talk = session.perform(ACCOUNT, "talk", {"target": npc, "message": "我回来了。"}, "t1")
    assert talk["ok"] is True
    assert talk["details"]["reply"] in {
        "婚试甲，我温着汤呢，忙完早些回来。",
        "我把婚试甲的换洗衣裳叠在柜头了，出门别又忘了带伞。",
        "婚试甲，米缸见底了，今日该缴租，你回来顺路去一趟。",
    }

    order = session.perform(ACCOUNT, "marriage_end", {}, "d1")
    assert order["ok"] is True, order
    assert order["data"]["marriage"]["status"] == "single"
    assert session.state.entities[pid].spouse is None
    assert session.state.entities[npc].spouse is None
    rel = session.state.relation(pid, npc, "marriage")
    assert rel is not None and rel.status == "ended"
    assert conservation(session.state)["conservation_error"] == 0
    marriage_verbs = [
        v for v in _verbs(session)
        if v in {"marriage_propose", "marriage_accept", "marriage", "marriage_end"}
    ]
    assert marriage_verbs == [
        "marriage_propose", "marriage_accept", "marriage", "marriage_end"
    ]


def test_reject_reasons(session):
    pid = _enter(session)["player"]
    npc = _same_location_npc(session, pid)
    _set_bond(session, pid, npc, 30.0, 40.0)
    res = session.perform(ACCOUNT, "marriage_propose", {"target": npc, "gift_money": 10}, "r1")
    assert res["ok"] is False and res["reject_reason"] == "bond_too_low"
    assert res["verb"] == "marriage_reject"
    assert "marriage_reject" in _verbs(session)

    _set_bond(session, pid, npc, 70.0, 50.0)
    res = session.perform(ACCOUNT, "marriage_propose", {"target": npc, "gift_money": 999999}, "r2")
    assert res["ok"] is False and res["reject_reason"] == "not_enough_money"

    res = session.perform(ACCOUNT, "marriage_propose", {"target": npc, "gift_item": "no_such_item"}, "r3")
    assert res["ok"] is False and res["reject_reason"] == "gift_missing"

    res = session.perform(ACCOUNT, "marriage_propose", {"target": npc, "gift_money": 10}, "r4")
    assert res["ok"] is True
    res = session.perform(ACCOUNT, "marriage_propose", {"target": npc, "gift_money": 10}, "r5")
    assert res["ok"] is False and res["reject_reason"] == "already_married"


def test_courtship_intermediate_state(session):
    pid = _enter(session)["player"]
    npc = _same_location_npc(session, pid)
    _set_bond(session, pid, npc, 50.0, 40.0)
    res = session.perform(ACCOUNT, "marriage_propose", {"target": npc, "gift_money": 10}, "c1")
    assert res["ok"] is True
    assert res["details"]["decision"] == "courtship"
    view = session.player_state(ACCOUNT)["marriage"]
    assert view["status"] == "courtship"
    assert view["spouse_id"] == npc
    assert view["expires_tick"] == res["tick"] + TICKS_PER_DAY * 3
    assert session.state.entities[pid].spouse is None
    assert session.state.account_balance(f"wallet:{pid}") == 300


def test_spouse_daily_decide_without_content_goal(real_content):
    """内容目标耗尽（choice is None）时，婚后日常仍必须每日发生。"""
    from wsim.actions.registry import build_registry
    from wsim.sim.intent import MockReasoner

    npc = "npc_daily"
    state = WorldState(locations=real_content.locations)
    apply_effects(
        state,
        [
            Effect(
                "create_entity",
                {
                    "id": npc,
                    "name": "阿藕",
                    "role": "worker",
                    "location": "market",
                    "home": "market",
                    "stamina": 100.0,
                    "skills": {"work": 0.4},
                    "personality": {},
                },
            ),
            Effect(
                "create_entity",
                {
                    "id": "player:t",
                    "name": "测试玩家",
                    "role": "捕快",
                    "location": "market",
                    "home": "market",
                    "stamina": 100.0,
                    "skills": {"捕快": 0.3},
                    "personality": {},
                    "presence": "online",
                },
            ),
            Effect("create_account", {"account": f"wallet:{npc}", "balance": 0}),
            Effect("mint", {"account": f"wallet:{npc}", "amount": 100}),
            Effect("marry", {"a": npc, "b": "player:t"}),
            Effect("marriage_meta", {"a": npc, "b": "player:t", "status": "married", "since_tick": 0}),
        ],
    )
    key = f"{npc}|player:t" if npc <= "player:t" else f"player:t|{npc}"
    registry = build_registry(real_content, 42)
    reasoner = MockReasoner(seed=42, content=real_content)

    def view(tick: int) -> dict:
        return {
            "tick": tick,
            "state": state,
            "content": real_content,
            "tier": "spotlight",
            "interval": 36,
        }

    # 该 NPC 不在内容包里，没有可选目标：choose 返回 None。
    assert reasoner.selector.choose(state, npc, 0) is None
    first = reasoner.decide({"id": npc}, view(0))
    assert [i.verb for i in first] == ["spouse_time"], first
    # 同一世界日不重复。
    assert reasoner.decide({"id": npc}, view(5)) == []

    move = registry.get("move")
    spouse_time = registry.get("spouse_time")
    assert spouse_time is not None
    assert spouse_time.preconditions(state, first[0]) == []
    apply_effects(state, spouse_time.apply(state, first[0], None))
    assert state.marriages[key]["last_daily_tick"] == 0
    assert state.memories["player:t"][-1]["source"] == "spouse:daily"

    # 次日玩家走到邻处：先按最短路径移动，再到场陪伴，均在同一 tick 内完成。
    neighbor = sorted(real_content.locations["market"]["exits"])[0]
    state.entities["player:t"].location = neighbor
    day1 = reasoner.decide({"id": npc}, view(TICKS_PER_DAY))
    verbs = [i.verb for i in day1]
    assert verbs.count("spouse_time") == 1 and verbs.count("move") >= 1, day1
    for intent in day1:
        before = state.entities[npc].location
        apply_effects(state, (move if intent.verb == "move" else spouse_time).apply(state, intent, None))
        assert state.entities[npc].location != before or intent.verb != "move"
    assert state.entities[npc].location == neighbor
    assert state.marriages[key]["last_daily_tick"] == TICKS_PER_DAY

    # 第三日仍在场：不再产生移动，只问候一次。
    day2 = reasoner.decide({"id": npc}, view(TICKS_PER_DAY * 2))
    assert [i.verb for i in day2] == ["spouse_time"], day2


def test_no_player_state_omits_marriage_metadata(real_state):
    assert "marriages" not in real_state.to_dict()
    assert real_state.marriages == {}


def test_same_seed_marriage_is_byte_identical(tmp_path):
    hashes = []
    for idx in range(2):
        s = WorldSession(ROOT, run_id=f"det-{idx}", seed=42, npc_limit=30, run_dir=tmp_path / f"det-{idx}")
        try:
            pid = _enter(s, "e1")["player"]
            npc = _same_location_npc(s, pid)
            _set_bond(s, pid, npc, 70.0, 50.0)
            res = s.perform(ACCOUNT, "marriage_propose", {"target": npc, "gift_money": 50}, "m1")
            assert res["ok"] is True
            hashes.append((s.state.hash(), s.writer.store.event_hash()))
        finally:
            s.close()
    assert hashes[0] == hashes[1]
