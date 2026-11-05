"""NPC 进玩家店铺消费：同权轮转、专程进店、双向交易、无玩家世界零变化。

锁定“玩家开店营收恒 0”的机制根因修复：
- 同地点同价时卖家不再恒为 id 更小的 `biz:*`，而按 (地点, 买主, 世界日) 稳定散列轮转；
- 玩家入市后采购门槛降为 1 件、食物可作口粮买走；
- 日常活动地附近有玩家店时，NPC 会按稳定散列专程进店；
- NPC 也会把货卖给现金充足的玩家店。
无玩家店的世界返回原顺序/原门槛，10 日事件哈希逐字节不变。
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from wsim.actions.commerce import SellAction
from wsim.actions.common import (
    business_order,
    business_selling,
    stable_slot,
    wallet,
)
from wsim.actions.business import SHOP_INITIAL_STOCK
from wsim.core.clock import TICKS_PER_DAY, day_of
from wsim.core.models import Effect, Intent
from wsim.sim.genesis import build_genesis
from wsim.sim.intent import Planner
from wsim.world.effects import apply_effects
from wsim.world.state import WorldState

ROOT = Path(__file__).resolve().parents[1]
SHOP = "shop:player:u1"
SHOP_CASH = f"cash:{SHOP}"
OWNER = "player:u1"


def _world(real_content) -> WorldState:
    state = WorldState(locations=real_content.locations)
    apply_effects(state, build_genesis(real_content, real_content.npcs, root=ROOT).effects)
    return state


def _add_shop(
    state: WorldState,
    location: str = "market",
    kind: str = "storefront",
    stock: dict[str, int] | None = None,
    cash: int = 0,
) -> None:
    effects = [
        Effect("create_entity", {
            "id": OWNER, "name": "测试掌柜", "role": "商人",
            "location": location, "stamina": 100.0, "max_stamina": 100.0,
        }),
        Effect("create_account", {"account": wallet(OWNER)}),
        Effect("create_account", {"account": SHOP_CASH}),
        Effect("create_business", {
            "id": SHOP, "name": "测试小店", "location": location,
            "kind": kind, "owner": OWNER, "cash_account": SHOP_CASH,
        }),
    ]
    if cash:
        effects.append(Effect("mint", {"account": SHOP_CASH, "amount": cash, "reason": "test"}))
    apply_effects(state, effects)
    stock = stock if stock is not None else dict(SHOP_INITIAL_STOCK[kind])
    for item, qty in sorted(stock.items()):
        apply_effects(state, [Effect("item_create", {
            "owner": SHOP, "item": item, "qty": qty, "reason": "test_stock",
        })])


def _zero_other_cash(state: WorldState, location: str) -> None:
    """把同地点 NPC 店铺的现金账户换成空账户，隔离“谁有现金”的变量。"""
    apply_effects(state, [Effect("create_account", {"account": "cash:test:empty"})])
    for biz in state.business_at(location):
        if biz.id != SHOP:
            biz.cash_account = "cash:test:empty"


def test_business_order_rotates_only_with_player_shop(real_content):
    state = _world(real_content)
    base = [b.id for b in state.business_at("market")]
    # 无玩家店：任何买主/日子都不改变顺序（无玩家世界逐字节不变的前提）。
    for npc in ("cao_banxian", "jiang_tingyu", "yan_shouyi"):
        for day in range(3):
            assert [b.id for b in business_order(state, "market", npc, day)] == base

    _add_shop(state)
    firsts = {
        business_order(state, "market", f"npc{i}", day)[0].id
        for i in range(16)
        for day in range(3)
    }
    assert SHOP in firsts, "玩家店必须在部分买主/日子排到首位（同权）"
    assert len(firsts) >= 2, "NPC 店铺仍应保有首位份额（不被反向饿死）"


def test_business_selling_gives_player_shop_equal_share(real_content):
    state = _world(real_content)
    old = business_selling(state, "market", "flatbread", salt="cao_banxian", day=0)
    assert old is not None and old.id == "biz:market:flatbread"

    _add_shop(state, stock={"flatbread": 5})
    share = Counter(
        business_selling(state, "market", "flatbread", salt=f"npc{i}", day=day).id
        for i in range(16)
        for day in range(3)
    )
    assert share[SHOP] > 0, share
    assert share["biz:market:flatbread"] > 0, share
    assert share[SHOP] + share["biz:market:flatbread"] == 48, share


def test_propensity_buy_buys_last_item_from_player_shop(real_content):
    state = _world(real_content)
    _add_shop(state, location="temple", stock={"cleaver": 2})
    npc = "cao_banxian"
    planner = Planner(real_content, 42)
    planner._stamina = 100.0
    intents: list[Intent] = []
    loc, cursor, cash = planner._propensity_buy(state, intents, npc, "temple", 0, 60, 600, {})
    buy = [i for i in intents if i.verb == "buy"]
    assert buy, "玩家店最后两件 cleaver 应当可买"
    assert buy[0].args == {"from": "temple", "item": "cleaver", "qty": 1}
    assert "口粮" not in buy[0].reason
    assert cash < 600


def test_propensity_buy_can_buy_food_from_player_shop(real_content):
    """食物不再整体排除：玩家摊上的 flatbread 作为口粮被买走（buy ≠ consume）。"""
    state = _world(real_content)
    _add_shop(state, location="temple", kind="stall")
    planner = Planner(real_content, 42)
    planner._stamina = 100.0
    intents: list[Intent] = []
    planner._propensity_buy(state, intents, "cao_banxian", "temple", 0, 60, 600, {})
    buy = [i for i in intents if i.verb == "buy"]
    assert buy and buy[0].args["item"] in {"flatbread", "noodle", "rice"}, intents
    assert "添置口粮" in buy[0].reason, buy[0].reason


def test_player_shop_trip_plans_move_and_buy(real_content):
    state = _world(real_content)
    _add_shop(state, location="market")  # 集市与铁匠铺相邻（1 跳）
    npc = "cao_banxian"
    day = next(d for d in range(200) if stable_slot(f"{npc}|{d}|trip", 1000) < 320)
    tick = day * TICKS_PER_DAY + 40
    planner = Planner(real_content, 42)
    planner._stamina = 100.0
    intents: list[Intent] = []
    loc, cursor, cash = planner._player_shop_trip(
        state, intents, npc, "smithy", tick, tick + 40, 600, {}, lambda s: s
    )
    moves = [i for i in intents if i.verb == "move"]
    buys = [i for i in intents if i.verb == "buy"]
    assert loc == "market" and moves and buys, intents
    assert moves[-1].args["to"] == "market" and moves[-1].args["dest"] == "market"
    assert buys[0].args["from"] == "market"
    assert buys[0].tick == moves[-1].tick, "移动与购买同 tick，先移动后购买"


def test_sell_to_player_shop_when_cash_available(real_content):
    state = _world(real_content)
    _add_shop(state, location="market", stock={}, cash=100)
    _zero_other_cash(state, "market")
    npc = "cao_banxian"
    state.entities[npc].location = "market"
    apply_effects(state, [Effect("item_create", {
        "owner": npc, "item": "flatbread", "qty": 1, "reason": "test",
    })])
    action = SellAction(real_content)
    intent = Intent(actor=npc, verb="sell", args={"item": "flatbread", "qty": 1, "to": "market"},
                    tick=0, source="test")
    money0 = state.account_balance(wallet(npc))
    assert action.preconditions(state, intent) == []
    apply_effects(state, action.apply(state, intent, None))
    biz = state.businesses[SHOP]
    assert biz.stock.get("flatbread", 0) == 1, "NPC 卖出的货进入玩家店库存"
    assert state.account_balance(SHOP_CASH) == 98
    assert state.account_balance(wallet(npc)) == money0 + 2


def test_playerless_propensity_buy_matches_old_selection(real_content):
    """无玩家店：旧口径（排除口粮、qty>=3、首个最便宜）必须原样保留。"""
    state = _world(real_content)
    loc = "market"
    cash = 600
    old_best = None
    for biz in state.business_at(loc):
        for iid, qty in sorted(biz.stock.items()):
            if qty < 3 or iid not in real_content.items:
                continue
            if str(real_content.items[iid].get("category", "")) in ("food", "drink", "service", "meal"):
                continue
            price = real_content.item_price(iid, state)
            if price <= 0 or price * 3 > cash:
                continue
            if old_best is None or price < old_best[0]:
                old_best = (price, iid)
    assert old_best is not None

    planner = Planner(real_content, 42)
    planner._stamina = 100.0
    intents: list[Intent] = []
    planner._propensity_buy(state, intents, "cao_banxian", loc, 0, 60, cash, {})
    buys = [i for i in intents if i.verb == "buy"]
    assert buys and buys[0].args["item"] == old_best[1], (old_best, buys)
    assert "口粮" not in buys[0].reason
