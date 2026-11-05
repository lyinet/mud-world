"""P3-E 玩家 HTTP API 契约回归：字段名、tick、拒绝原因、幂等与不铸币。"""
from __future__ import annotations

from pathlib import Path

import pytest

from wsim.audit.conservation import conservation
from wsim.api import WorldSession

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = "u_demo"


@pytest.fixture()
def session(tmp_path):
    s = WorldSession(
        ROOT,
        run_id="api-test",
        seed=42,
        npc_limit=30,
        run_dir=tmp_path / "run",
    )
    yield s
    s.close()


def _enter(session, archetype="捕快", request_id="req-enter"):
    return session.perform(
        ACCOUNT,
        "enter",
        {"account_id": ACCOUNT, "display_name": "陈捕快", "archetype": archetype},
        request_id,
    )


def test_enter_contract_and_no_mint(session):
    before = conservation(session.state)
    res = _enter(session)
    after = conservation(session.state)

    assert res["ok"] is True
    assert res["status"] == "executed"
    assert res["profile_version"] == 1
    assert res["action"] == "enter"
    assert res["tick"] == 0
    assert res["next_tick"] == 6
    assert res["player"] == "player:u_demo"
    assert res["details"]["created"] is False
    assert res["details"]["resumed"] is True
    assert res["details"]["role"] == "捕快"
    assert res["details"]["endowment"]["money"] == 300
    assert res["details"]["endowment"]["granted"] is False
    assert res["details"]["endowment"]["already_granted"] is True
    assert res["details"]["endowment"]["money_source"] == "genesis_player_endowment"
    assert res["details"]["endowment"]["granted_at_genesis"] is True
    assert {row["item"]: row["qty"] for row in res["details"]["endowment"]["items"]} == {
        "flatbread": 3,
        "coarse_cloth": 1,
    }
    # player_enter 不铸币：minted_total 不因玩家加入而增加。
    assert after["minted_total"] == before["minted_total"]
    assert after["conservation_error"] == 0


def test_state_contract_fields(session):
    _enter(session)
    state = session.player_state(ACCOUNT)

    assert state["ok"] is True
    assert state["profile_version"] == 1
    assert state["account_id"] == ACCOUNT
    assert state["tick"] == 6
    assert set(state["player"]) >= {
        "entity_id", "account_id", "name", "archetype", "role", "location",
        "stamina", "max_stamina", "presence", "online", "wallet", "relations",
        "active_quests",
    }
    assert state["player"]["wallet"]["money"] == 300
    assert {(row["item"], row["qty"]) for row in state["player"]["wallet"]["items"]} == {
        ("flatbread", 3),
        ("coarse_cloth", 1),
    }
    assert {"id", "name", "description", "exits", "services"} <= set(state["location"])
    for row in state["location"]["exits"]:
        assert {"id", "name", "exists"} == set(row)
    assert all(
        set(row) == {
            "id", "name", "role", "location", "presence", "doing",
            "skills", "employed_by", "bond", "trust",
        }
        for row in state["present_npcs"]
    )
    assert set(state["quest_board"]) == {"counts", "rows", "active"}
    assert set(state["quest_board"]["counts"]) == {"open", "acceptable_here", "accepted_by_me"}
    assert all(set(row) >= {"id", "template", "name", "category", "giver", "reward_money",
                            "deadline_tick", "status", "acceptable", "can_accept_here",
                            "accepted_by_me", "expired"}
               for row in state["quest_board"]["rows"])
    assert state["online_players"][0]["id"] == "player:u_demo"
    assert state["online_players"][0]["self"] is True


def test_action_executed_and_unknown_location_rejected(session):
    _enter(session)
    bad = session.action(ACCOUNT, "move", {"to": "moon"}, client_tick=99, request_id="req-bad")
    assert bad["ok"] is False
    assert bad["status"] == "rejected"
    assert bad["reject_reason"] == "unknown_location"
    assert bad["reason_text"] == "地点不存在；请从当前地点可用的出口中选择。"
    assert bad["client_tick"] == 99
    assert bad["client_tick_ignored"] is True
    assert bad["event_seq"] >= 1
    assert bad["next_tick"] == bad["tick"] + 6

    ok = session.action(ACCOUNT, "move", {"to": "inn"}, request_id="req-move")
    assert ok["ok"] is True
    assert ok["status"] == "executed"
    assert ok["tick"] == bad["next_tick"]
    assert ok["next_tick"] == ok["tick"] + 6

    not_adjacent = session.action(ACCOUNT, "move", {"to": "smithy"})
    assert not_adjacent["reject_reason"] == "not_adjacent"
    assert not_adjacent["reason_text"]


def test_talk_reply_messages_and_intents(session):
    _enter(session)
    talk = session.action(
        ACCOUNT,
        "talk",
        {"target": "liu_sanniang", "message": "三娘，最近集市可有什么新鲜事？"},
        request_id="req-talk",
    )
    assert talk["ok"] is True
    assert talk["details"]["reply"]
    assert talk["details"]["relation"]["target"] == "liu_sanniang"

    messages = session.messages(ACCOUNT, 0)
    assert messages["ok"] is True
    assert messages["next_since_tick"] == session.state.tick
    assert len(messages["messages"]) == 1
    msg = messages["messages"][0]
    assert msg["kind"] == "talk_reply"
    assert msg["seq"] == talk["event_seq"]
    assert msg["tick"] == talk["tick"]
    assert msg["from"] == "liu_sanniang"
    assert msg["text"] == talk["details"]["reply"]

    intent = session.intent(ACCOUNT, "req-talk")
    assert intent["found"] is True
    assert intent["status"] == "executed"
    assert intent["event_seq"] == talk["event_seq"]

    missing = session.intent(ACCOUNT, "req-not-exist")
    assert missing["found"] is False
    assert missing["reject_reason"] == "intent_not_found"


def test_leave_and_reenter_keep_state(session):
    _enter(session)
    session.action(ACCOUNT, "move", {"to": "inn"})
    leave = session.action(ACCOUNT, "leave")
    assert leave["ok"] is True
    assert session.player_state(ACCOUNT)["player"]["presence"] == "offline"

    again = _enter(session, request_id="req-enter-2")
    assert again["ok"] is True
    assert again["details"]["created"] is False
    assert again["details"]["resumed"] is True
    state = session.player_state(ACCOUNT)
    assert state["player"]["location"] == "inn"
    assert state["player"]["wallet"]["money"] == 300
    assert state["player"]["last_intent_tick"] == again["tick"]
