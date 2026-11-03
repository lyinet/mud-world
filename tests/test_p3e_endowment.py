"""P3-E P0：运行中新账号首次进入的一次性初始资产。

覆盖：首次发放 300 文 + 炊饼×3 + 粗布×1；同账号二次进入 already_online 不重发；
离开后重进不重发；两个不同账号各发一次；minted 增量 == 发放总额；replay 后金额一致。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from wsim.actions.common import wallet
from wsim.audit.conservation import conservation
from wsim.api import WorldSession
from wsim.core.models import Effect
from wsim.world.effects import apply_effects
from wsim.world.state import WorldState

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def session(tmp_path):
    # endow=False：不预置任何账号，模拟“运行中真实新玩家”。
    s = WorldSession(
        ROOT,
        run_id="endow-test",
        seed=42,
        npc_limit=30,
        endow=False,
        run_dir=tmp_path / "run",
    )
    yield s
    s.close()


def _enter(session, account: str, name: str, archetype: str = "游侠", request_id: str = ""):
    return session.perform(
        account,
        "enter",
        {"account_id": account, "display_name": name, "archetype": archetype},
        request_id or f"req-enter-{account}",
    )


def test_first_enter_grants_once_and_mints_matching_amount(session):
    pid = "player:u_new1"
    before = conservation(session.state)
    res = _enter(session, "u_new1", "阿青")

    assert res["ok"] is True
    assert res["details"]["created"] is True
    assert res["details"]["resumed"] is False

    endow = res["details"]["endowment"]
    assert endow["granted"] is True
    assert endow["already_granted"] is False
    assert endow["money"] == 300
    assert {row["item"]: row["qty"] for row in endow["items"]} == {
        "flatbread": 3,
        "coarse_cloth": 1,
    }
    assert endow["money_source"] == "player_endowment"
    assert endow["item_source"] == "player_endowment_carry"
    assert endow["granted_at_genesis"] is False
    assert endow["endowment_total"] == 300
    # 发放是独立事件：紧随 enter 之后。
    assert endow["event_seq"] == res["event_seq"] + 1

    state = session.player_state("u_new1")
    assert state["player"]["wallet"]["money"] == 300
    assert {(row["item"], row["qty"]) for row in state["player"]["wallet"]["items"]} == {
        ("flatbread", 3),
        ("coarse_cloth", 1),
    }
    assert state["player"]["endowed"] is True
    assert state["endowment_total"] == 300

    after = conservation(session.state)
    assert after["minted_total"] - before["minted_total"] == 300
    assert after["accounts_sum"] - before["accounts_sum"] == 300
    assert after["conservation_error"] == 0
    assert int(session.state.account_balance(wallet(pid))) == 300

    # 事件日志里能看到显式 mint 的来源与 reason。
    grant_events = [e for e in session.store.read_all() if e.verb == "player_endowment"]
    assert len(grant_events) == 1
    ev = grant_events[0]
    assert ev.outcome == "ok"
    assert ev.args["reason"] == "first_entry"
    mint = next(eff for eff in ev.effects if eff["op"] == "mint")
    assert mint["data"]["source"] == "player_endowment"
    assert mint["data"]["reason"] == "first_entry"


def test_second_enter_while_online_is_already_online_and_grants_nothing(session):
    _enter(session, "u_new1", "阿青")
    before = conservation(session.state)
    total_before = session.state.endowment_total

    again = _enter(session, "u_new1", "阿青", request_id="req-enter-again")

    assert again["ok"] is False
    assert again["status"] == "rejected"
    assert again["reject_reason"] == "already_online"
    assert again["reason_text"]
    # 拒绝响应不带 details.endowment；钱包与铸币总量都不变。
    assert session.player_state("u_new1")["player"]["wallet"]["money"] == 300
    after = conservation(session.state)
    assert after["minted_total"] == before["minted_total"]
    assert session.state.endowment_total == total_before


def test_leave_and_reenter_does_not_regrant(session):
    _enter(session, "u_new1", "阿青")
    assert session.action("u_new1", "leave")["ok"] is True
    before = conservation(session.state)

    again = _enter(session, "u_new1", "阿青", request_id="req-enter-resume")

    assert again["ok"] is True
    assert again["details"]["created"] is False
    assert again["details"]["resumed"] is True
    endow = again["details"]["endowment"]
    assert endow["granted"] is False
    assert endow["already_granted"] is True
    assert endow["money"] == 300
    assert endow["event_seq"] == 0
    assert endow["endowment_total"] == 300
    assert session.player_state("u_new1")["player"]["wallet"]["money"] == 300

    after = conservation(session.state)
    assert after["minted_total"] == before["minted_total"]
    assert after["conservation_error"] == 0


def test_two_accounts_each_get_one_endowment(session):
    before = conservation(session.state)
    a = _enter(session, "u_alpha", "阿青")
    b = _enter(session, "u_beta", "阿白")

    assert a["ok"] is True and b["ok"] is True
    assert a["details"]["endowment"]["granted"] is True
    assert b["details"]["endowment"]["granted"] is True
    assert session.player_state("u_alpha")["player"]["wallet"]["money"] == 300
    assert session.player_state("u_beta")["player"]["wallet"]["money"] == 300

    after = conservation(session.state)
    assert after["minted_total"] - before["minted_total"] == 600
    assert after["accounts_sum"] - before["accounts_sum"] == 600
    assert session.state.endowment_total == 600
    # minted 增量与发放总额一致。
    assert after["minted_total"] - before["minted_total"] == session.state.endowment_total
    assert after["conservation_error"] == 0


def test_replay_restores_endowment_balance(session):
    _enter(session, "u_new1", "阿青")
    _enter(session, "u_alpha", "阿白")
    session.action("u_new1", "leave")

    live = session.state
    replayed = WorldState(locations=session.content.locations)
    for event in session.store.read_all():
        if event.outcome == "ok":
            apply_effects(replayed, [Effect.from_dict(d) for d in event.effects])
        replayed.tick = event.tick

    for account in ("u_new1", "u_alpha"):
        pid = f"player:{account}"
        assert replayed.account_balance(wallet(pid)) == 300
        assert replayed.players[pid].endowed is True
        assert replayed.players[pid].endowed_source == "player_endowment"
    assert replayed.endowment_total == 600
    assert replayed.hash() == live.hash()
