"""玩家消费类动作（buy/sell/consume/escort）的接口契约与不变量测试。

这些用例只走 WorldSession（与 HTTP 层同一入口），断言：
- 四个动作已放行，且复用既有动作的经济语义；
- 玩家可读原因码、NPC 专属动作的未开放码；
- state 价格字段是纯增量，不改变既有键；
- 买/吃/卖与押镖闭环的钱包守恒、seq 连续、无半写。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from wsim.actions.common import wallet
from wsim.api.session import WorldSession
from wsim.audit.conservation import conservation

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = "u_demo"


@pytest.fixture()
def session(tmp_path: Path):
    s = WorldSession(
        ROOT,
        run_id="player_commerce",
        seed=42,
        npc_limit=0,
        post_quests=True,
        run_dir=tmp_path / "run",
    )
    yield s
    s.close()


def _enter(s: WorldSession, account: str = ACCOUNT, archetype: str = "游侠") -> None:
    resp = s.action(account, "enter", {"display_name": "阿镖", "archetype": archetype})
    assert resp["ok"], resp


def _walk(s: WorldSession, account: str, dest: str) -> None:
    """按 content 的相邻关系 BFS 走最短路径（与玩家逐段 move 一致）。"""
    start = s.state.entities[s._pid(account)].location
    if start == dest:
        return
    queue: list[tuple[str, list[str]]] = [(start, [])]
    seen = {start}
    while queue:
        cur, path = queue.pop(0)
        for nxt in s.state.locations[cur].get("exits") or []:
            if nxt in seen:
                continue
            if nxt == dest:
                for step in [*path, nxt]:
                    resp = s.action(account, "move", {"to": step})
                    assert resp["ok"], (step, resp)
                return
            seen.add(nxt)
            queue.append((nxt, [*path, nxt]))
    raise AssertionError(f"no path {start} -> {dest}")


def _assert_seq_contiguous(s: WorldSession) -> None:
    events = s.store.read_all()
    assert [e.seq for e in events] == list(range(1, len(events) + 1))


def test_state_exposes_prices_without_changing_existing_keys(session: WorldSession) -> None:
    _enter(session)
    state = session.player_state(ACCOUNT)
    market_services = sorted(str(x) for x in session.content.locations["market"].get("services") or [])

    # 旧字段原样：仍是 item id 列表。
    assert state["location"]["services"] == market_services
    detail = {row["item"]: row for row in state["location"]["services_detail"]}
    flatbread = detail["flatbread"]
    assert flatbread["price"] == 3
    assert flatbread["sell_price"] == 2
    assert flatbread["buy_limit"] > 0
    assert detail["pearl_earring"]["buy_limit"] == 0  # 300 文买不起 313+税
    assert detail["coarse_cloth"]["sell_limit"] == 1

    # 顶层 shop：可买行带价与上限；可卖行带收购价与上限。
    shop = state["shop"]
    assert shop["location"] == "market"
    buy = {row["item"]: row for row in shop["buy"]}
    assert buy["flatbread"]["price"] == 3
    assert buy["flatbread"]["total"] == buy["flatbread"]["price"] + buy["flatbread"]["tax"]
    assert buy["flatbread"]["buy_limit"] > 0
    sell = {row["item"]: row for row in shop["sell"]}
    assert sell["flatbread"]["sell_price"] == 2
    assert sell["flatbread"]["qty"] == 3

    # 钱包 items 只加 price/sell_price，原 item/name/qty 仍在。
    items = {row["item"]: row for row in state["player"]["wallet"]["items"]}
    assert items["flatbread"]["qty"] == 3
    assert items["flatbread"]["price"] == 3
    assert items["flatbread"]["sell_price"] == 2


def test_buy_consume_sell_flow_and_wallet_conservation(session: WorldSession) -> None:
    _enter(session)
    pid = session._pid(ACCOUNT)
    before = conservation(session.state)
    money0 = session.state.account_balance(wallet(pid))
    treasury0 = session.state.account_balance("treasury:region")

    buy = session.action(ACCOUNT, "buy", {"item": "flatbread", "qty": 1})
    assert buy["ok"], buy
    after_buy = conservation(session.state)
    assert session.state.account_balance(wallet(pid)) == money0 - 3
    assert session.state.entities[pid].inventory["flatbread"] == 4
    assert session.state.account_balance("treasury:region") == treasury0 + 3
    assert after_buy["minted_total"] == before["minted_total"]
    assert after_buy["burned_total"] == before["burned_total"]
    assert after_buy["conservation_error"] == 0
    assert "wallet" in buy["data"] and "inventory" in buy["data"]

    # 吃：先买一份再消耗，净库存不变、钱减少、体力回升。
    session.state.entities[pid].stamina = 40.0
    consume = session.action(ACCOUNT, "consume", {"item": "flatbread", "qty": 1})
    assert consume["ok"], consume
    assert session.state.entities[pid].inventory["flatbread"] == 4
    assert session.state.entities[pid].stamina > 40.0
    assert session.state.account_balance(wallet(pid)) == money0 - 6

    sell = session.action(ACCOUNT, "sell", {"item": "flatbread", "qty": 1})
    assert sell["ok"], sell
    assert session.state.entities[pid].inventory["flatbread"] == 3
    assert session.state.account_balance(wallet(pid)) == money0 - 6 + 2
    assert session.state.account_balance("treasury:region") == treasury0 + 6 - 2
    assert conservation(session.state)["conservation_error"] == 0
    _assert_seq_contiguous(session)

    # 含税商品：60 文货款 + 1 文销毁税，minted/burned 可对账。
    burn_before = conservation(session.state)["burned_total"]
    silk = session.action(ACCOUNT, "buy", {"item": "raw_silk", "qty": 1})
    assert silk["ok"], silk
    burn_after = conservation(session.state)["burned_total"]
    assert burn_after == burn_before + 1
    assert session.state.account_balance(wallet(pid)) == money0 - 6 + 2 - 61
    assert session.state.entities[pid].inventory["raw_silk"] == 1
    final = conservation(session.state)
    assert final["conservation_error"] == 0
    assert final["accounts_sum"] == final["minted_total"] - final["burned_total"]
    _assert_seq_contiguous(session)


def test_player_reason_codes_are_distinct_and_readable(session: WorldSession) -> None:
    _enter(session)
    cases = [
        ("buy", {"item": "pearl_earring", "qty": 1}, "not_enough_money"),
        ("buy", {"item": "no_such_item"}, "item_not_available"),
        ("sell", {"item": "flatbread", "qty": 9}, "item_missing"),
        ("buy", {"item": "flatbread", "qty": 1, "from": "inn"}, "not_here"),
        ("buy", {"item": "good_blade"}, "not_a_merchant"),
    ]
    for action, payload, expected in cases:
        resp = session.action(ACCOUNT, action, payload)
        assert not resp["ok"], (action, payload, resp)
        assert resp["reject_reason"] == expected, (action, payload, resp)
        assert resp["reason_text"], (action, payload, resp)


def test_shop_closed_branch_is_reachable(session: WorldSession) -> None:
    """内容包内每个地点都有商业体；用运行态空地点验证防御分支，而不是删改 content。"""
    _enter(session)
    pid = session._pid(ACCOUNT)
    session.state.locations["empty_market"] = {
        "name": "空集市",
        "description": "",
        "exits": ["market"],
        "services": [],
    }
    session.state.entities[pid].location = "empty_market"
    resp = session.action(ACCOUNT, "buy", {"item": "flatbread", "qty": 1})
    assert not resp["ok"], resp
    assert resp["reject_reason"] == "shop_closed", resp
    assert resp["reason_text"]


def test_npc_only_verbs_return_not_implemented_for_player(session: WorldSession) -> None:
    _enter(session)
    for verb in ("marry", "found_sect", "apprentice"):
        resp = session.action(ACCOUNT, verb, {})
        assert not resp["ok"], resp
        assert resp["reject_reason"] == "not_implemented_for_player", (verb, resp)
        assert resp["reason_text"] == "这个玩法还没对玩家开放。"


def test_escort_closed_loop_records_cargo_route_fee_and_settles(session: WorldSession) -> None:
    _enter(session)
    pid = session._pid(ACCOUNT)
    # 货物是真实物品：先在集市买 6 匹粗布，再押出去（背包不够会被 item_missing 拦下）。
    _walk(session, ACCOUNT, "market")
    buy = session.action(ACCOUNT, "buy", {"item": "coarse_cloth", "qty": 6})
    assert buy["ok"], buy
    assert session.state.entities[pid].inventory["coarse_cloth"] == 7
    _walk(session, ACCOUNT, "escort_agency")
    money0 = session.state.account_balance(wallet(pid))

    resp = session.action(
        ACCOUNT,
        "escort",
        {
            "route": ["escort_agency", "smithy", "market"],
            "cargo": {"item": "coarse_cloth", "qty": 6},
            "fee": 130,
        },
    )
    assert resp["ok"], resp
    order = [o for o in session.player_state(ACCOUNT)["escort_orders"] if o["status"] == "active"][0]
    assert order["origin"] == "escort_agency"
    assert order["destination"] == "market"
    assert order["cargo_value"] == 150  # 粗布 25 文 × 6 件
    assert order["fee"] == 130
    assert order["route_id"] == "route_market_short"
    create = [e for e in session.store.read_all() if e.verb == "escort"][-1]
    assert create.args["route"] == ["escort_agency", "smithy", "market"]
    order_effect = next(
        eff for eff in create.effects
        if eff["op"] == "escort_order_create"
    )["data"]
    assert order_effect["cargo_item"] == "coarse_cloth"
    assert order_effect["cargo_qty"] == 6
    assert order_effect["cargo_value"] == 150
    # 发单即托管：背包立刻扣货，托管桶在账上可见，事件带 reason/order/account。
    assert session.state.entities[pid].inventory["coarse_cloth"] == 1
    state_now = session.player_state(ACCOUNT)
    oid = order["id"]
    assert state_now["escrow_total"] == 6
    assert {r["account"] for r in state_now["escrow"]} == {f"escrow:{oid}"}
    escrow_row = state_now["escrow"][0]
    assert escrow_row["item"] == "coarse_cloth"
    assert escrow_row["qty"] == 6
    escrow_effects = [
        eff for eff in create.effects if eff["data"].get("reason") == "escort_cargo_escrow"
    ]
    assert [eff["op"] for eff in escrow_effects] == ["item_consume", "item_create"]
    assert escrow_effects[0]["data"]["owner"] == pid
    assert escrow_effects[0]["data"]["qty"] == 6
    assert escrow_effects[0]["data"]["movement"] == "player->escrow"
    assert escrow_effects[1]["data"]["item"] == f"escrow:{oid}:coarse_cloth"
    assert escrow_effects[1]["data"]["escrow_account"] == f"escrow:{oid}"
    _assert_seq_contiguous(session)

    # 144 tick 后到达：镖局/委托方出镖银，玩家到账并扣交易税，写入 settle 事件。
    for _ in range(24):
        session._advance()
    state = session.player_state(ACCOUNT)
    assert state["escort_orders"][0]["status"] == "done"
    assert state["escort_orders"][0]["success"] is True
    assert state["player"]["location"] == "market"
    assert session.state.account_balance(wallet(pid)) == money0 + 91 - 2  # 130×0.7，税 1/1 分摊
    # 送达即离场：托管归零，背包不再有这 6 匹（不会出现第二份货）。
    assert session.state.entities[pid].inventory["coarse_cloth"] == 1
    assert state["escrow"] == [] and state["escrow_total"] == 0
    assert state["reconciliation"]["delta"] == 0
    assert state["reconciliation"]["escrow_items"] == 0
    settle = [e for e in session.store.read_all() if e.verb == "system_escort_arrive"][-1]
    assert any(eff["op"] == "escort_settle" for eff in settle.effects)
    deliver = [
        eff for eff in settle.effects
        if eff["data"].get("reason") == "escort_cargo_deliver"
    ]
    assert [eff["data"]["qty"] for eff in deliver] == [6]
    assert any(
        eff["op"] == "transfer"
        and eff["data"]["reason"] == "escort_fee"
        and eff["data"]["src"] == "treasury:region"
        for eff in settle.effects
    )
    cons = conservation(session.state)
    assert cons["conservation_error"] == 0
    assert cons["burned_total"] >= 1
    _assert_seq_contiguous(session)


def test_escort_wrong_origin_and_bad_cargo_are_readable(session: WorldSession) -> None:
    _enter(session)
    resp = session.action(
        ACCOUNT,
        "escort",
        {"route": ["escort_agency", "smithy", "market"], "cargo": {"item": "coarse_cloth", "qty": 6}, "fee": 130},
    )
    assert not resp["ok"]
    assert resp["reject_reason"] == "not_here", resp
    resp = session.action(
        ACCOUNT,
        "escort",
        {"route": ["market", "smithy", "escort_agency"], "cargo": {"item": "no_such_item", "qty": 1}, "fee": 130},
    )
    assert not resp["ok"]
    assert resp["reject_reason"] == "item_not_available", resp
    # 货物是真实物品：背包只有 1 匹粗布却报 6 匹，应给 item_missing 而不是放行。
    resp = session.action(
        ACCOUNT,
        "escort",
        {"route": ["market", "smithy", "escort_agency"], "cargo": {"item": "coarse_cloth", "qty": 6}, "fee": 130},
    )
    assert not resp["ok"]
    assert resp["reject_reason"] == "item_missing", resp


def test_procurement_quest_can_be_completed_after_buy_enabled(session: WorldSession) -> None:
    _enter(session)
    _walk(session, ACCOUNT, "ferry")
    board = session.player_state(ACCOUNT)["quest_board"]["rows"]
    row = next(
        r for r in board
        if r["template"] == "daily_ferry_rope" and r["status"] == "open"
    )
    accept = session.action(ACCOUNT, "quest_accept", {"quest": row["id"]})
    assert accept["ok"], accept

    _walk(session, ACCOUNT, "market")
    buy = session.action(ACCOUNT, "buy", {"item": "hemp_rope", "qty": 2})
    assert buy["ok"], buy
    deliver = session.action(ACCOUNT, "quest_deliver", {"quest": row["id"]})
    assert deliver["ok"], deliver
    pid = session._pid(ACCOUNT)
    # 交付消耗 1 件，剩 1 件；垫付 16 文、报酬 16 文，钱包回到原值。
    assert session.state.entities[pid].inventory["hemp_rope"] == 1
    assert session.state.account_balance(wallet(pid)) == 300
    _assert_seq_contiguous(session)


def _buy_cloth(session: WorldSession, qty: int = 6) -> None:
    _walk(session, ACCOUNT, "market")
    buy = session.action(ACCOUNT, "buy", {"item": "coarse_cloth", "qty": qty})
    assert buy["ok"], buy
    _walk(session, ACCOUNT, "escort_agency")


def _start_escort(session: WorldSession) -> dict:
    resp = session.action(
        ACCOUNT,
        "escort",
        {
            "route": ["escort_agency", "smithy", "market"],
            "cargo": {"item": "coarse_cloth", "qty": 6},
            "fee": 130,
        },
    )
    assert resp["ok"], resp
    return [o for o in session.player_state(ACCOUNT)["escort_orders"] if o["status"] == "active"][0]


def test_escort_failure_returns_full_cargo_and_escrow_goes_to_zero(session: WorldSession) -> None:
    # 把线路风险钉死为必失败，验证“失败/超时 -> 原量退回”而不是货没。
    session.content.escort_routes["route_market_short"]["risk_loss_prob"] = 1.0
    _enter(session)
    pid = session._pid(ACCOUNT)
    _buy_cloth(session)
    assert session.state.entities[pid].inventory["coarse_cloth"] == 7
    money0 = session.state.account_balance(wallet(pid))
    order = _start_escort(session)
    oid = order["id"]

    # 开单后：背包 7 -> 1，托管 6 件在账上。
    assert session.state.entities[pid].inventory["coarse_cloth"] == 1
    assert session.player_state(ACCOUNT)["escrow_total"] == 6
    assert [r["qty"] for r in session.player_state(ACCOUNT)["escrow"]] == [6]

    for _ in range(24):
        session._advance()
    state = session.player_state(ACCOUNT)
    assert state["escort_orders"][0]["status"] == "failed"
    assert state["escort_orders"][0]["success"] is False
    # 原量退回：背包 1 -> 7，托管归零，镖费未发（失败单没有收入）。
    assert session.state.entities[pid].inventory["coarse_cloth"] == 7
    assert state["escrow"] == [] and state["escrow_total"] == 0
    assert session.state.account_balance(wallet(pid)) == money0
    settle = [e for e in session.store.read_all() if e.verb == "system_escort_arrive"][-1]
    returns = [eff for eff in settle.effects if eff["data"].get("reason") == "escort_cargo_return"]
    assert [eff["op"] for eff in returns] == ["item_consume", "item_create"]
    assert returns[0]["data"]["qty"] == 6 and returns[0]["data"]["movement"] == "escrow->player"
    assert returns[1]["data"]["owner"] == pid and returns[1]["data"]["qty"] == 6
    cons = conservation(session.state)
    assert cons["conservation_error"] == 0
    assert state["reconciliation"]["delta"] == 0
    _assert_seq_contiguous(session)


def test_second_escort_on_same_cargo_is_rejected_item_missing(session: WorldSession) -> None:
    _enter(session)
    pid = session._pid(ACCOUNT)
    _buy_cloth(session)
    money0 = session.state.account_balance(wallet(pid))
    order = _start_escort(session)
    oid = order["id"]
    assert session.state.entities[pid].inventory["coarse_cloth"] == 1
    assert session.player_state(ACCOUNT)["escrow_total"] == 6

    second = session.action(
        ACCOUNT,
        "escort",
        {
            "route": ["escort_agency", "smithy", "market"],
            "cargo": {"item": "coarse_cloth", "qty": 6},
            "fee": 130,
        },
    )
    assert not second["ok"]
    assert second["reject_reason"] == "item_missing", second
    # 不可重复：没有第二个镖单、没有第二份托管、钱包不变。
    creates = [
        e for e in session.store.read_all()
        if any(eff["op"] == "escort_order_create" for eff in e.effects)
    ]
    assert len(creates) == 1
    assert session.player_state(ACCOUNT)["escrow_total"] == 6
    assert {r["account"] for r in session.player_state(ACCOUNT)["escrow"]} == {f"escrow:{oid}"}
    assert session.state.entities[pid].inventory["coarse_cloth"] == 1
    assert session.state.account_balance(wallet(pid)) == money0
    _assert_seq_contiguous(session)


def test_escort_cargo_survives_leave_and_timeout_without_duplication(session: WorldSession) -> None:
    session.content.escort_routes["route_market_short"]["risk_loss_prob"] = 1.0
    _enter(session)
    pid = session._pid(ACCOUNT)
    _buy_cloth(session)
    order = _start_escort(session)
    assert session.state.entities[pid].inventory["coarse_cloth"] == 1
    assert session._escrow_view()[1] == 6

    # 押镖中退出：只改在线状态，货仍在托管桶里（不蒸发）。
    leave = session.action(ACCOUNT, "leave")
    assert leave["ok"] is True
    assert session.state.entities[pid].inventory["coarse_cloth"] == 1
    assert session._escrow_view()[1] == 6
    assert session.state.escorts[order["id"]].status == "active"

    # 到期结算失败：托管原量回到（已离线的）玩家背包；不增发第二份。
    for _ in range(24):
        session._advance()
    assert session.state.escorts[order["id"]].status == "failed"
    assert session.state.entities[pid].inventory["coarse_cloth"] == 7
    assert session._escrow_view()[1] == 0
    _assert_seq_contiguous(session)
