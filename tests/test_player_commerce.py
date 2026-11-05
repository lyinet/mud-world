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

from wsim.actions.business import daily_shop_tax
from wsim.actions.common import transaction_tax, wallet
from wsim.actions.escort import resolve_payer
from wsim.core.clock import TICKS_PER_DAY, TICKS_PER_HOUR
from wsim.core.models import Effect
from wsim.world.effects import apply_effects
from wsim.world.state import Quest
from wsim.api.session import WorldSession
from wsim.audit.conservation import conservation

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = "u_demo"


def _fee_account(oid: str, payer: str) -> str:
    return f"escrow:{oid}:fee:{payer}"


def _escort_payer(s: WorldSession, oid: str) -> str:
    prefix = f"escrow:{oid}:fee:"
    return next((a[len(prefix) :] for a in sorted(s.state.accounts) if a.startswith(prefix)), "")


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
    session.content.escort_routes["route_market_short"]["risk_loss_prob"] = 0.0
    session.content.escort_routes["route_market_short"]["risk_events"] = []
    _enter(session)
    pid = session._pid(ACCOUNT)
    # 货物是真实物品：先在集市买 3 匹粗布（自带 1 匹，4 匹货值 100 文），再押出去。
    _walk(session, ACCOUNT, "market")
    buy = session.action(ACCOUNT, "buy", {"item": "coarse_cloth", "qty": 3})
    assert buy["ok"], buy
    assert session.state.entities[pid].inventory["coarse_cloth"] == 4
    _walk(session, ACCOUNT, "escort_agency")
    money0 = session.state.account_balance(wallet(pid))
    destination = "market"
    payer = resolve_payer(session.state, destination, 130, 0.30)
    assert payer, "集市必须有付得起运费的委托方"
    payer_money0 = session.state.account_balance(wallet(payer))
    treasury0 = session.state.account_balance("treasury:region")

    resp = session.action(
        ACCOUNT,
        "escort",
        {
            "route": ["escort_agency", "smithy", "market"],
            "cargo": {"item": "coarse_cloth", "qty": 4},
            "fee": 130,
        },
    )
    assert resp["ok"], resp
    order = [o for o in session.player_state(ACCOUNT)["escort_orders"] if o["status"] == "active"][0]
    assert order["origin"] == "escort_agency"
    assert order["destination"] == "market"
    assert order["cargo_value"] == 100  # 粗布 25 文 × 4 件（buy 口径）
    assert order["fee"] == 130
    assert order["route_id"] == "route_market_short"
    assert _escort_payer(session, order["id"]) == payer
    create = [e for e in session.store.read_all() if e.verb == "escort"][-1]
    assert create.args["route"] == ["escort_agency", "smithy", "market"]
    order_effect = next(
        eff for eff in create.effects
        if eff["op"] == "escort_order_create"
    )["data"]
    assert order_effect["cargo_item"] == "coarse_cloth"
    assert order_effect["cargo_qty"] == 4
    assert order_effect["cargo_value"] == 100
    # 运费来自委托方真实钱包（130 + 30% 抽成 = 169），进入托管账户，不走 treasury。
    fee_transfer = next(
        eff for eff in create.effects
        if eff["op"] == "transfer" and eff["data"].get("reason") == "escort_fee_escrow"
    )["data"]
    assert fee_transfer["src"] == wallet(payer)
    assert fee_transfer["amount"] == 169
    assert session.state.account_balance(wallet(payer)) == payer_money0 - 169
    assert session.state.account_balance("treasury:region") == treasury0
    # 发单即托管：背包立刻扣货，托管桶在账上可见，事件带 reason/order/account。
    assert session.state.entities[pid].inventory.get("coarse_cloth", 0) == 0
    state_now = session.player_state(ACCOUNT)
    oid = order["id"]
    assert state_now["escrow_total"] == 4
    assert {r["account"] for r in state_now["escrow"]} == {f"escrow:{oid}"}
    escrow_row = state_now["escrow"][0]
    assert escrow_row["item"] == "coarse_cloth"
    assert escrow_row["qty"] == 4
    escrow_effects = [
        eff for eff in create.effects if eff["data"].get("reason") == "escort_cargo_escrow"
    ]
    assert [eff["op"] for eff in escrow_effects] == ["item_consume", "item_create"]
    assert escrow_effects[0]["data"]["owner"] == pid
    assert escrow_effects[0]["data"]["qty"] == 4
    assert escrow_effects[0]["data"]["movement"] == "player->escrow"
    assert escrow_effects[1]["data"]["item"] == f"escrow:{oid}:coarse_cloth"
    assert escrow_effects[1]["data"]["escrow_account"] == f"escrow:{oid}"
    _assert_seq_contiguous(session)

    # 144 tick 后到达：委托方托管运费分给玩家与镖局，玩家到账并扣交易税。
    for _ in range(24):
        session._advance()
    state = session.player_state(ACCOUNT)
    assert state["escort_orders"][0]["status"] == "done"
    assert state["escort_orders"][0]["success"] is True
    assert state["player"]["location"] == "market"
    tax = transaction_tax(session.content, 130)
    assert session.state.account_balance(wallet(pid)) == money0 + 130 - tax
    # 送达即离场：托管归零，背包不再有这 4 匹（不会出现第二份货）。
    assert session.state.entities[pid].inventory.get("coarse_cloth", 0) == 0
    assert state["escrow"] == [] and state["escrow_total"] == 0
    assert state["reconciliation"]["delta"] == 0
    assert state["reconciliation"]["escrow_items"] == 0
    settle = [e for e in session.store.read_all() if e.verb == "system_escort_arrive"][-1]
    settle_payload = next(eff for eff in settle.effects if eff["op"] == "escort_settle")["data"]
    assert settle_payload["payer"] == payer
    assert settle_payload["cargo_value"] == 100
    assert settle_payload["fee"] == 130
    assert settle_payload["tax"] == tax
    assert settle_payload["agency_cut"] == 39
    assert settle_payload["net_profit"] == 130 - 100 - tax  # > 0
    deliver = [
        eff for eff in settle.effects
        if eff["data"].get("reason") == "escort_delivered"
    ]
    assert [eff["op"] for eff in deliver] == ["item_consume", "item_create"]
    assert deliver[0]["data"]["qty"] == 4
    assert deliver[0]["data"]["movement"] == "escrow->consignor"
    assert deliver[1]["data"]["owner"] == payer
    assert deliver[1]["data"]["qty"] == 4
    # 运费付款与镖局抽成：全部来自托管账户，treasury 只进不出。
    pay = next(
        eff for eff in settle.effects
        if eff["op"] == "transfer" and eff["data"].get("reason") == "escort_fee_pay"
    )["data"]
    assert pay["src"] == _fee_account(order["id"], payer) and pay["amount"] == 130
    cut = next(
        eff for eff in settle.effects
        if eff["op"] == "transfer" and eff["data"].get("reason") == "escort_agency_cut"
    )["data"]
    assert cut["amount"] == 39 and cut["dst"] == "treasury:region"
    to_treasury = int(round(tax * float(session.content.economy["taxes"]["transaction_treasury_share"])))
    assert session.state.account_balance("treasury:region") == treasury0 + 39 + to_treasury
    assert session.state.account_balance(wallet(payer)) == payer_money0 - 169
    assert session.state.account_balance(_fee_account(order["id"], payer)) == 0
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


def _buy_cloth(session: WorldSession, qty: int = 3) -> None:
    """自带 1 匹，再买 qty 匹；默认 4 匹（货值 100，运费 130 满足 1.15 倍门槛）。"""
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
            "cargo": {"item": "coarse_cloth", "qty": 4},
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
    assert session.state.entities[pid].inventory["coarse_cloth"] == 4
    money0 = session.state.account_balance(wallet(pid))
    payer = resolve_payer(session.state, "market", 130, 0.30)
    payer_money0 = session.state.account_balance(wallet(payer))
    order = _start_escort(session)
    oid = order["id"]
    assert session.state.account_balance(wallet(payer)) == payer_money0 - 169

    # 开单后：背包 4 -> 0，托管 4 件在账上。
    assert session.state.entities[pid].inventory.get("coarse_cloth", 0) == 0
    assert session.player_state(ACCOUNT)["escrow_total"] == 4
    assert [r["qty"] for r in session.player_state(ACCOUNT)["escrow"]] == [4]

    for _ in range(24):
        session._advance()
    state = session.player_state(ACCOUNT)
    assert state["escort_orders"][0]["status"] == "failed"
    assert state["escort_orders"][0]["success"] is False
    # 原量退回：背包 0 -> 4，托管归零，镖费未发（失败单没有收入）。
    assert session.state.entities[pid].inventory["coarse_cloth"] == 4
    assert state["escrow"] == [] and state["escrow_total"] == 0
    assert session.state.account_balance(wallet(pid)) == money0
    # 失败对称：运费原额退回委托方，委托方钱包净变化为 0。
    assert session.state.account_balance(wallet(payer)) == payer_money0
    settle = [e for e in session.store.read_all() if e.verb == "system_escort_arrive"][-1]
    settle_payload = next(eff for eff in settle.effects if eff["op"] == "escort_settle")["data"]
    assert settle_payload["fee"] == 0 and settle_payload["tax"] == 0
    assert settle_payload["net_profit"] == 0 and settle_payload["cargo_returned"] is True
    refund = next(
        eff for eff in settle.effects
        if eff["op"] == "transfer" and eff["data"].get("reason") == "escort_fee_refund"
    )["data"]
    assert refund["src"] == _fee_account(oid, payer) and refund["amount"] == 169
    assert refund["dst"] == wallet(payer)
    returns = [eff for eff in settle.effects if eff["data"].get("reason") == "escort_cargo_return"]
    assert [eff["op"] for eff in returns] == ["item_consume", "item_create"]
    assert returns[0]["data"]["qty"] == 4 and returns[0]["data"]["movement"] == "escrow->player"
    assert returns[1]["data"]["owner"] == pid and returns[1]["data"]["qty"] == 4
    assert not any(eff["data"].get("reason") == "escort_fee_pay" for eff in settle.effects)
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
    assert session.state.entities[pid].inventory.get("coarse_cloth", 0) == 0
    assert session.player_state(ACCOUNT)["escrow_total"] == 4

    second = session.action(
        ACCOUNT,
        "escort",
        {
            "route": ["escort_agency", "smithy", "market"],
            "cargo": {"item": "coarse_cloth", "qty": 4},
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
    assert session.player_state(ACCOUNT)["escrow_total"] == 4
    assert {r["account"] for r in session.player_state(ACCOUNT)["escrow"]} == {f"escrow:{oid}"}
    assert session.state.entities[pid].inventory.get("coarse_cloth", 0) == 0
    assert session.state.account_balance(wallet(pid)) == money0
    _assert_seq_contiguous(session)


def test_escort_requires_cargo_and_writes_auditable_reject(session: WorldSession) -> None:
    """空货押镖 = 白拿镖银：一律 item_missing，且 treasury 零支出。"""
    _enter(session)
    _walk(session, ACCOUNT, "escort_agency")
    treasury0 = session.state.account_balance("treasury:region")
    resp = session.action(
        ACCOUNT,
        "escort",
        {"route": ["escort_agency", "smithy", "market"], "fee": 130},
    )
    assert not resp["ok"], resp
    assert resp["reject_reason"] == "item_missing", resp
    reject = [e for e in session.store.read_all() if e.verb == "escort"][-1]
    assert reject.reason == "rejected:item_missing"
    assert reject.args.get("reason") == "item_missing"
    assert session.state.account_balance("treasury:region") == treasury0
    assert not any(
        eff["op"] in ("transfer", "mint", "burn", "escort_order_create")
        for eff in reject.effects
    )
    _assert_seq_contiguous(session)


def test_escort_fee_below_cargo_threshold_is_rejected(session: WorldSession) -> None:
    """fee < 货值 × 1.15：拒单，避免带货净亏。"""
    _enter(session)
    _walk(session, ACCOUNT, "market")
    buy = session.action(ACCOUNT, "buy", {"item": "coarse_cloth", "qty": 3})
    assert buy["ok"], buy
    _walk(session, ACCOUNT, "escort_agency")
    treasury0 = session.state.account_balance("treasury:region")
    resp = session.action(
        ACCOUNT,
        "escort",
        {
            "route": ["escort_agency", "smithy", "market"],
            "cargo": {"item": "coarse_cloth", "qty": 4},
            "fee": 114,
        },
    )
    assert not resp["ok"], resp
    assert resp["reject_reason"] == "fee_too_low", resp
    assert session.state.account_balance("treasury:region") == treasury0
    assert session.state.entities[session._pid(ACCOUNT)].inventory["coarse_cloth"] == 4
    _assert_seq_contiguous(session)


def test_escort_without_solvent_consignor_is_rejected(session: WorldSession) -> None:
    """委托方现金不足：no_payer_funds，不垫付、不产生任何转账。"""
    _enter(session)
    _walk(session, ACCOUNT, "market")
    buy = session.action(ACCOUNT, "buy", {"item": "coarse_cloth", "qty": 3})
    assert buy["ok"], buy
    _walk(session, ACCOUNT, "escort_agency")
    dest_npcs = [
        e.id for e in session.state.entities.values()
        if not e.id.startswith("player:") and e.location == "market"
    ]
    assert dest_npcs
    for npc in dest_npcs:
        session.state.accounts[wallet(npc)] = 0
    treasury0 = session.state.account_balance("treasury:region")
    resp = session.action(
        ACCOUNT,
        "escort",
        {
            "route": ["escort_agency", "smithy", "market"],
            "cargo": {"item": "coarse_cloth", "qty": 4},
            "fee": 130,
        },
    )
    assert not resp["ok"], resp
    assert resp["reject_reason"] == "no_payer_funds", resp
    reject = [e for e in session.store.read_all() if e.verb == "escort"][-1]
    assert reject.reason == "rejected:no_payer_funds"
    assert session.state.account_balance("treasury:region") == treasury0
    assert session.state.entities[session._pid(ACCOUNT)].inventory["coarse_cloth"] == 4
    _assert_seq_contiguous(session)


def test_three_consecutive_escorts_keep_treasury_net_inflow(session: WorldSession) -> None:
    """连续 3 单：每单运费来自委托方，treasury 只有抽成/税流入，绝不净流出。"""
    session.content.escort_routes["route_market_short"]["risk_loss_prob"] = 0.0
    session.content.escort_routes["route_market_short"]["risk_events"] = []
    _enter(session)
    pid = session._pid(ACCOUNT)
    session.state.entities[pid].inventory["coarse_cloth"] = 12
    treasury = "treasury:region"
    runs = []
    for _ in range(3):
        _walk(session, ACCOUNT, "escort_agency")
        treasury0 = session.state.account_balance(treasury)
        payer = resolve_payer(session.state, "market", 130, 0.30)
        assert payer
        payer0 = session.state.account_balance(wallet(payer))
        resp = session.action(
            ACCOUNT,
            "escort",
            {
                "route": ["escort_agency", "smithy", "market"],
                "cargo": {"item": "coarse_cloth", "qty": 4},
                "fee": 130,
            },
        )
        assert resp["ok"], resp
        assert session.state.account_balance(wallet(payer)) == payer0 - 169
        assert session.state.account_balance(treasury) == treasury0
        for _ in range(24):
            session._advance()
        order = session.state.escorts[sorted(session.state.escorts)[-1]]
        assert order.status == "done"
        flows = [
            eff["data"]
            for e in session.store.read_all()
            if e.verb == "system_escort_arrive"
            for eff in e.effects
            if eff["op"] == "transfer" and eff["data"].get("order") == order.id
        ]
        assert all(flow["src"] != treasury for flow in flows), flows
        assert any(flow["dst"] == treasury for flow in flows), flows
        assert session.state.account_balance(treasury) > treasury0
        runs.append(
            {
                "order": order.id,
                "payer": payer,
                "payer_delta": session.state.account_balance(wallet(payer)) - payer0,
                "treasury_delta": session.state.account_balance(treasury) - treasury0,
            }
        )
        assert order.success, "三连单默认线路允许风险，测试数据不足以允许失败"
    assert all(run["treasury_delta"] > 0 for run in runs), runs
    assert session.state.entities[pid].inventory.get("coarse_cloth", 0) == 0
    assert session.player_state(ACCOUNT)["reconciliation"]["delta"] == 0
    cons = conservation(session.state)
    assert cons["conservation_error"] == 0
    _assert_seq_contiguous(session)


def test_rest_recovers_stamina_hourly_at_inn(session: WorldSession) -> None:
    """客栈休息：2 小时 = 12 tick × 2.0/tick = +24 体力，并推进 12 tick。"""
    _enter(session)
    pid = session._pid(ACCOUNT)
    _walk(session, ACCOUNT, "inn")
    session.state.entities[pid].stamina = 20.0
    tick0 = int(session.state.tick)
    resp = session.action(ACCOUNT, "rest", {"hours": 2})
    assert resp["ok"], resp
    assert session.state.entities[pid].stamina == 44.0
    assert int(session.state.tick) == tick0 + 2 * TICKS_PER_HOUR
    event = [e for e in session.store.read_all() if e.verb == "rest"][-1]
    assert event.args["hours"] == 2
    stamina_eff = next(eff for eff in event.effects if eff["op"] == "stamina")
    assert stamina_eff["data"]["delta"] == 24.0
    assert stamina_eff["data"]["reason"] == "rest"
    _assert_seq_contiguous(session)


def test_rest_is_slower_in_the_wild(session: WorldSession) -> None:
    """野外（无商业体）恢复 0.25/tick，是客栈的 1/8。"""
    _enter(session)
    pid = session._pid(ACCOUNT)
    # 当前内容包每个地点都有商业体；这里临时清空码头商业体来模拟野外。
    wild = "ferry"
    session.state.businesses = {
        bid: biz for bid, biz in session.state.businesses.items() if biz.location != wild
    }
    session.state.entities[pid].location = wild
    session.state.entities[pid].stamina = 20.0
    tick0 = int(session.state.tick)
    resp = session.action(ACCOUNT, "rest", {"hours": 2})
    assert resp["ok"], resp
    assert session.state.entities[pid].stamina == 23.0  # 12 tick × 0.25
    assert int(session.state.tick) == tick0 + 2 * TICKS_PER_HOUR


def test_rest_rejects_bad_hours_and_active_escort(session: WorldSession) -> None:
    _enter(session)
    pid = session._pid(ACCOUNT)
    _walk(session, ACCOUNT, "inn")
    bad = session.action(ACCOUNT, "rest", {"hours": 9})
    assert not bad["ok"] and bad["reject_reason"] == "bad_arg:hours", bad
    assert not any(e.verb == "rest" and e.outcome == "ok" for e in session.store.read_all())
    # 押镖途中：明确拒绝，体力与 tick 都不变。
    session.content.escort_routes["route_market_short"]["risk_loss_prob"] = 0.0
    session.content.escort_routes["route_market_short"]["risk_events"] = []
    session.state.entities[pid].inventory["coarse_cloth"] = 4
    _walk(session, ACCOUNT, "escort_agency")
    resp = session.action(
        ACCOUNT,
        "escort",
        {"route": ["escort_agency", "smithy", "market"], "cargo": {"item": "coarse_cloth", "qty": 4}, "fee": 130},
    )
    assert resp["ok"], resp
    stamina0 = session.state.entities[pid].stamina
    blocked = session.action(ACCOUNT, "rest", {"hours": 2})
    assert not blocked["ok"], blocked
    assert blocked["reject_reason"] == "cannot_rest_while_escorting", blocked
    reject = [e for e in session.store.read_all() if e.verb == "rest"][-1]
    assert reject.reason == "rejected:cannot_rest_while_escorting"
    assert session.state.entities[pid].stamina == stamina0
    _assert_seq_contiguous(session)


def test_rest_rejects_when_quest_deadline_would_be_missed(session: WorldSession) -> None:
    _enter(session)
    pid = session._pid(ACCOUNT)
    _walk(session, ACCOUNT, "inn")
    tick = int(session.state.tick)
    session.state.quests["quest:test-rest"] = Quest(
        id="quest:test-rest",
        template_id="tpl",
        giver="jin_mancang",
        acceptor=pid,
        target_npc="jin_mancang",
        target_location="market",
        verb="talk",
        args={},
        reward_money=20,
        reward_relation=0.0,
        deadline_tick=tick + 6,  # 1 小时内到期
        status="accepted",
    )
    resp = session.action(ACCOUNT, "rest", {"hours": 2})
    assert not resp["ok"], resp
    assert resp["reject_reason"] == "cannot_rest_now", resp
    reject = [e for e in session.store.read_all() if e.verb == "rest"][-1]
    assert reject.reason == "rejected:cannot_rest_now"
    assert not any(eff["op"] == "stamina" for eff in reject.effects)


def _seed_own_shop(
    s: WorldSession, kind: str = "storefront", location: str = "market"
) -> str:
    """在运行态造一家玩家店铺（视图只读，测试视图用真实 state 形状即可）。"""
    pid = s._pid(ACCOUNT)
    shop_id = f"shop:player:{ACCOUNT}"
    cash = f"cash:{shop_id}"
    apply_effects(
        s.state,
        [
            Effect("create_account", {"account": cash}),
            Effect("create_business", {
                "id": shop_id,
                "name": "试营业小店",
                "location": location,
                "kind": kind,
                "owner": pid,
                "cash_account": cash,
            }),
            Effect("set_field", {"entity": pid, "field": "business", "value": shop_id}),
        ],
    )
    return shop_id


def _seed_employee(
    s: WorldSession,
    shop_id: str,
    eid: str,
    name: str,
    role: str,
    location: str,
    wage: int,
    skills: dict[str, float] | None = None,
) -> None:
    apply_effects(
        s.state,
        [
            Effect("create_entity", {
                "id": eid,
                "name": name,
                "role": role,
                "location": location,
                "stamina": 100.0,
                "max_stamina": 100.0,
                "skills": dict(skills or {}),
            }),
            Effect("create_account", {"account": wallet(eid)}),
            Effect("shop_hire", {
                "business": shop_id,
                "entity": eid,
                "position": role,
                "wage": wage,
            }),
        ],
    )


def test_shop_view_exposes_employees_stock_and_daily_stats(session: WorldSession) -> None:
    _enter(session)
    shop_id = _seed_own_shop(session, kind="storefront")
    cash = f"cash:{shop_id}"
    # 129 文入账：结清 9 文测试欠薪债后，现金余额正好 120。
    apply_effects(session.state, [Effect("mint", {"account": cash, "amount": 129, "reason": "test"})])
    _seed_employee(session, shop_id, "npc:emp1", "陆九斤", "脚夫", "market", 30, {"脚夫": 0.4})
    _seed_employee(session, shop_id, "npc:emp2", "王二", "酒保", "inn", 20, {"酒保": 0.3})
    # 两笔同雇工的 active 欠薪债 + 一笔已结清（不应计入）。
    apply_effects(
        session.state,
        [
            Effect("debt_create", {
                "id": "debt:test:emp1:a",
                "creditor": "npc:emp1",
                "debtor": shop_id,
                "amount": 5,
                "note": "unpaid_wage",
            }),
            Effect("debt_create", {
                "id": "debt:test:emp1:b",
                "creditor": "npc:emp1",
                "debtor": shop_id,
                "amount": 3,
                "note": "unpaid_wage",
            }),
            Effect("debt_create", {
                "id": "debt:test:emp1:settled",
                "creditor": "npc:emp1",
                "debtor": shop_id,
                "amount": 9,
                "note": "unpaid_wage",
            }),
            Effect("debt_settle", {"id": "debt:test:emp1:settled", "amount": 9}),
        ],
    )
    biz = session.state.businesses[shop_id]
    biz.stock = {
        "flatbread": 10,
        "cured_meat": 3,
        "escrow:es1:coarse_cloth": 2,
        "hemp_rope": 0,
    }
    biz.day_revenue = 40
    biz.tax_arrears = 7

    shop = session.player_state(ACCOUNT)["shop"]
    rows = {row["name"]: row for row in shop["employees"]}
    assert set(rows) == {"陆九斤", "王二"}
    expected_keys = {"name", "position", "wage", "present", "unpaid_wage"}
    for row in shop["employees"]:
        assert set(row) == expected_keys
    assert rows["陆九斤"] == {
        "name": "陆九斤", "position": "脚夫", "wage": 30, "present": True, "unpaid_wage": 8,
    }
    assert rows["王二"] == {
        "name": "王二", "position": "酒保", "wage": 20, "present": False, "unpaid_wage": 0,
    }
    assert shop["cash"] == 120
    assert session.state.account_balance(cash) == 120
    # 件数口径：10 + 3；escrow 托管键与 qty<=0 都不计入，也不等于品类数。
    assert shop["stock_count"] == 13
    assert len(shop["stock"]) == 2
    assert shop["stock_count"] != len(shop["stock"])
    stock = {row["item"]: row for row in shop["stock"]}
    assert set(stock) == {"flatbread", "cured_meat"}
    for item, row in stock.items():
        assert set(row) == {"item", "name", "qty", "sell_price"}
        assert row["name"] == session._item_name(item)
        assert row["sell_price"] == session._item_prices(None, item)[1]
    assert stock["flatbread"]["qty"] == 10
    assert stock["cured_meat"]["qty"] == 3
    assert shop["today_revenue"] == 40
    assert shop["today_wage"] == 50
    assert shop["today_tax"] == daily_shop_tax(session.content, "storefront")
    assert shop["tax_arrears"] == 7
    tick = int(session.state.tick)
    assert shop["last_settle_tick"] == tick - tick % TICKS_PER_DAY
    assert shop["last_settle_tick"] % TICKS_PER_DAY == 0


def test_shop_view_empty_shape_is_stable_for_new_player(session: WorldSession) -> None:
    _enter(session)
    shop = session.player_state(ACCOUNT)["shop"]
    assert shop["employees"] == []
    assert shop["stock"] == []
    for key in (
        "cash", "stock_count", "today_revenue", "today_wage",
        "today_tax", "tax_arrears",
    ):
        assert shop[key] == 0
    assert shop["last_settle_tick"] % TICKS_PER_DAY == 0
    assert {"location", "businesses", "buy", "sell"} <= set(shop)


def test_present_view_exposes_skills_employment_and_relations(session: WorldSession) -> None:
    _enter(session)
    pid = session._pid(ACCOUNT)
    npcs = sorted(
        e.id for e in session.state.entities.values()
        if e.id != pid
        and not str(e.id).startswith("player:")
        and e.location == "market"
        and e.status == "alive"
    )
    assert len(npcs) >= 2
    first, second = npcs[0], npcs[1]
    session.state.entities[first].skills = {"捕快": 0.35, "武艺": 0.2}
    session.state.entities[first].employed_at = "shop:player:u1"
    session.state.entities[second].skills = {}
    session.state.entities[second].employed_at = None
    session.state.entities[second].business = "shop:player:u1"
    apply_effects(
        session.state,
        [
            Effect("relation_upsert", {
                "a": pid, "b": first, "kind": "friend", "affection": 12.0, "trust": 0.4,
            }),
            Effect("relation_upsert", {
                "a": first, "b": pid, "kind": "friend", "affection": 3.0, "trust": 0.7,
            }),
        ],
    )

    present = session.player_state(ACCOUNT)["present_npcs"]
    assert present, "market 必须有在场 NPC"
    expected_keys = {"skills", "employed_by", "bond", "trust"}
    for row in present:
        assert expected_keys <= set(row)
    row = next(r for r in present if r["id"] == first)
    assert row["skills"] == {"name": "捕快", "level": 0.35}
    assert row["employed_by"] == "shop:player:u1"
    assert row["bond"] == session.state.affection_between(pid, first) == 12.0
    assert row["trust"] == 0.7
    other = next(r for r in present if r["id"] == second)
    assert other["skills"] is None
    assert other["employed_by"] == "shop:player:u1"
    assert other["bond"] == 0.0
    assert other["trust"] == 0.0


def test_state_view_cache_reused_within_tick(session: WorldSession) -> None:
    _enter(session)
    shop_id = _seed_own_shop(session)
    session.state.businesses[shop_id].day_revenue = 40

    first = session.player_state(ACCOUNT)
    assert first["shop"]["today_revenue"] == 40
    assert len(session._view_cache) == 1

    # 同一 tick 内篡改 state：缓存不得重建，读数保持不变。
    session.state.businesses[shop_id].day_revenue = 999
    second = session.player_state(ACCOUNT)
    assert second["shop"]["today_revenue"] == 40
    assert len(session._view_cache) == 1

    # tick 推进后缓存失效，读到最新值。
    session._advance()
    third = session.player_state(ACCOUNT)
    assert third["shop"]["today_revenue"] == 999
    assert len(session._view_cache) == 1


def test_escort_cargo_survives_leave_and_timeout_without_duplication(session: WorldSession) -> None:
    session.content.escort_routes["route_market_short"]["risk_loss_prob"] = 1.0
    _enter(session)
    pid = session._pid(ACCOUNT)
    _buy_cloth(session)
    order = _start_escort(session)
    assert session.state.entities[pid].inventory.get("coarse_cloth", 0) == 0
    assert session._escrow_view()[1] == 4

    # 押镖中退出：只改在线状态，货仍在托管桶里（不蒸发）。
    leave = session.action(ACCOUNT, "leave")
    assert leave["ok"] is True
    assert session.state.entities[pid].inventory.get("coarse_cloth", 0) == 0
    assert session._escrow_view()[1] == 4
    assert session.state.escorts[order["id"]].status == "active"

    # 到期结算失败：托管原量回到（已离线的）玩家背包；不增发第二份。
    for _ in range(24):
        session._advance()
    assert session.state.escorts[order["id"]].status == "failed"
    assert session.state.entities[pid].inventory["coarse_cloth"] == 4
    assert session._escrow_view()[1] == 0
    _assert_seq_contiguous(session)
