"""P3I 物价接线：供需乘数必须确定性、按天变化、有护栏，且不误伤静态物品。"""
from __future__ import annotations

from wsim.world.state import Business, WorldState


def _state(tick: int, stocks: dict[str, int] | None = None) -> WorldState:
    state = WorldState()
    state.tick = tick
    if stocks is not None:
        state.businesses = {
            f"biz:test:{item}": Business(
                id=f"biz:test:{item}",
                name=f"测试摊·{item}",
                location="market",
                kind="stall",
                stock={item: qty},
            )
            for item, qty in stocks.items()
        }
    return state


def _neutral_stock(real_content) -> dict[str, int]:
    target = int(real_content.economy.get("business_stock", 2000))
    items = {
        item
        for cfg in real_content.economy["price_model"]["categories"].values()
        for item in cfg["items"]
    }
    return {item: target for item in items}


def test_static_price_without_state(real_content):
    base = int(real_content.items["rice"]["base_price"])
    markup = float(real_content.economy.get("price_markup", 1.0))
    assert real_content.item_price("rice") == max(1, int(round(base * markup)))


def test_day_curve_changes_price(real_content):
    stocks = _neutral_stock(real_content)
    day1 = real_content.item_price("rice", _state(0, stocks))
    day2 = real_content.item_price("rice", _state(287, stocks))
    assert day1 != day2


def test_at_least_five_categories_change(real_content):
    stocks = _neutral_stock(real_content)
    d1 = _state(0, stocks)
    d2 = _state(287, stocks)
    changed = set()
    for name, cfg in real_content.economy["price_model"]["categories"].items():
        if any(real_content.item_price(i, d1) != real_content.item_price(i, d2) for i in cfg["items"]):
            changed.add(name)
    assert len(changed) >= 5, f"只有 {len(changed)} 类商品发生变化: {sorted(changed)}"


def test_stock_pressure_moves_price(real_content):
    target = int(real_content.economy.get("business_stock", 2000))
    scarce = real_content.item_price("rice", _state(287, {"rice": 0}))
    glut = real_content.item_price("rice", _state(287, {"rice": target * 10}))
    assert scarce > glut
    assert scarce >= real_content.item_price("rice", _state(287, {"rice": target}))


def test_multiplier_is_clamped(real_content):
    cap = float(real_content.economy["price_model"].get("max_change", 0.20))
    target = int(real_content.economy.get("business_stock", 2000))
    for tick in (0, 143, 287):
        for item in ("rice", "millet", "hemp_rope", "sickle", "wound_powder", "jade_pendant"):
            for stock in (0, target * 50):
                mult = real_content.price_multiplier(item, _state(tick, {item: stock}))
                assert 1.0 - cap <= mult <= 1.0 + cap + 1e-9, (item, tick, stock, mult)


def test_price_is_deterministic(real_content):
    state = _state(287, _neutral_stock(real_content))
    first = {item: real_content.item_price(item, state) for item in sorted(real_content.items)}
    second = {item: real_content.item_price(item, state) for item in sorted(real_content.items)}
    assert first == second


def test_items_outside_model_stay_static(real_content):
    stocks = _neutral_stock(real_content)
    price1 = real_content.item_price("coarse_cloth", _state(0, stocks))
    price2 = real_content.item_price("coarse_cloth", _state(287, stocks))
    assert price1 == price2
