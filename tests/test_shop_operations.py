"""开店经营闭环：初始库存、雇佣契约、日结、自雇口径与日界事件序列。

覆盖两条审计病灶：
1. 开店必须带真实进货（钱从店主钱包转给 treasury:region，不凭空造货/造钱）；
2. 店主自雇不发工资、不生成"自己欠自己"的工资债。

以及日界纪律：`system_shop_settle` 是独立事件，只在存在玩家店铺时发出；
无店世界的事件序列与改动前逐字节一致。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from wsim.actions.basic import WorkAction
from wsim.actions.business import (
    SHOP_INITIAL_STOCK,
    OpenShopAction,
    build_shop_settlements,
    daily_shop_tax,
    shop_day_reset_effects,
    shop_settlement_effects,
    shop_stock_cost,
)
from wsim.actions.common import income_tax, wallet
from wsim.actions.registry import build_registry
from wsim.actions.social import RecruitAction
from wsim.audit.conservation import conservation
from wsim.core.clock import TICKS_PER_DAY
from wsim.core.models import Effect, Intent
from wsim.sim.genesis import build_genesis
from wsim.sim.scheduler import AmbientConfig, AmbientScheduler
from wsim.world.effects import apply_effects
from wsim.world.state import WorldState
from wsim.world.store import EventStore
from wsim.world.writer import Writer

ROOT = Path(__file__).resolve().parents[1]
SHOP_ID = "shop:player:u1"
SHOP_CASH = f"cash:{SHOP_ID}"


@pytest.fixture()
def world(real_content):
    state = WorldState(locations=real_content.locations)
    genesis = build_genesis(real_content, real_content.npcs, root=ROOT)
    apply_effects(state, genesis.effects)
    return state


def _seed_player(state, money: int = 5000, location: str = "market") -> None:
    apply_effects(
        state,
        [
            Effect("create_entity", {
                "id": "player:u1", "name": "测试掌柜", "role": "商人",
                "location": location, "stamina": 100.0, "max_stamina": 100.0,
            }),
            Effect("create_account", {"account": wallet("player:u1")}),
            Effect("mint", {"account": wallet("player:u1"), "amount": money, "reason": "test"}),
        ],
    )


def _seed_shop(state, kind: str = "storefront") -> None:
    _seed_player(state)
    apply_effects(
        state,
        [
            Effect("create_account", {"account": SHOP_CASH}),
            Effect("create_business", {
                "id": SHOP_ID, "name": "测试小店", "location": "market",
                "kind": kind, "owner": "player:u1", "cash_account": SHOP_CASH,
            }),
            Effect("set_field", {"entity": "player:u1", "field": "business", "value": SHOP_ID}),
        ],
    )


def _seed_employee(state, eid: str, wage: int) -> None:
    apply_effects(
        state,
        [
            Effect("create_entity", {
                "id": eid, "name": eid, "role": "屠户", "location": "market",
                "stamina": 100.0, "max_stamina": 100.0, "skills": {"屠户": 0.4},
            }),
            Effect("create_account", {"account": wallet(eid)}),
            Effect("shop_hire", {
                "business": SHOP_ID, "entity": eid, "position": "屠户", "wage": wage,
            }),
        ],
    )


def test_open_shop_buys_initial_stock_from_treasury(real_content, world):
    _seed_player(world)
    action = OpenShopAction(real_content)
    intent = Intent(
        actor="player:u1", verb="open_shop",
        args={"at": "market", "shop_type": "stall", "name": "小摊"},
        tick=0, source="test",
    )
    assert action.preconditions(world, intent) == []

    opening = int(real_content.economy["shop_opening"]["stall"])
    stock_cost = shop_stock_cost(real_content, "stall")
    wallet_before = world.account_balance(wallet("player:u1"))
    minted_before = world.minted_total

    effects = action.apply(world, intent, None)
    apply_effects(world, effects)

    # 铺面费 + 进货：钱全部从店主钱包真实转出。
    assert world.account_balance(wallet("player:u1")) == wallet_before - opening - stock_cost
    stock_pay = [e for e in effects if e.op == "transfer" and e.data.get("reason") == "shop_stock"]
    assert len(stock_pay) == 1
    assert stock_pay[0].data["dst"] == "treasury:region"
    assert stock_pay[0].data["amount"] == stock_cost

    # 库存按档位入库，没有赠送、没有 mint。
    biz = world.businesses[SHOP_ID]
    assert biz.stock == SHOP_INITIAL_STOCK["stall"]
    assert world.minted_total == minted_before
    assert conservation(world)["conservation_error"] == 0


def test_recruit_contract_writes_state_and_reject_codes(real_content, world):
    _seed_player(world)
    action = OpenShopAction(real_content)
    intent = Intent(
        actor="player:u1", verb="open_shop",
        args={"at": "market", "shop_type": "stall", "name": "小摊"},
        tick=0, source="test",
    )
    apply_effects(world, action.apply(world, intent, None))

    hire = RecruitAction(real_content)
    market_wage = int(real_content.wage_for("屠户"))

    # 关系不足：拒绝并给原因码。
    apply_effects(world, [
        Effect("create_entity", {
            "id": "npc:hire1", "name": "雇工甲", "role": "屠户", "location": "market",
            "stamina": 100.0, "max_stamina": 100.0, "skills": {"屠户": 0.4},
        }),
    ])
    low = Intent(actor="player:u1", verb="recruit", args={
        "target": "npc:hire1", "shop": SHOP_ID, "position": "屠户", "wage": market_wage,
    }, tick=0, source="test")
    assert hire.preconditions(world, low) == ["relation_too_low"]

    # 压价与技能不足同样有明确原因码。
    apply_effects(world, [Effect("relation_upsert", {
        "a": "player:u1", "b": "npc:hire1", "kind": "acquaintance",
        "affection": 8.0, "trust": 50.0,
    })])
    cheap = Intent(actor="player:u1", verb="recruit", args={
        "target": "npc:hire1", "shop": SHOP_ID, "position": "屠户",
        "wage": int(market_wage * 0.5),
    }, tick=0, source="test")
    assert hire.preconditions(world, cheap) == ["wage_too_low"]

    # 接受：契约写入 Business，双向记忆，雇工 employed_at 指向店铺。
    ok = Intent(actor="player:u1", verb="recruit", args={
        "target": "npc:hire1", "shop": SHOP_ID, "position": "屠户", "wage": market_wage,
    }, tick=0, source="test")
    assert hire.preconditions(world, ok) == []
    apply_effects(world, hire.apply(world, ok, None))
    biz = world.businesses[SHOP_ID]
    assert biz.employees == ["npc:hire1"]
    assert biz.employee_wages == {"npc:hire1": market_wage}
    assert biz.employee_positions == {"npc:hire1": "屠户"}
    assert world.entities["npc:hire1"].employed_at == SHOP_ID


def test_self_employment_pays_zero_and_creates_no_self_debt(real_content, world):
    _seed_shop(world, kind="stall")
    action = WorkAction(real_content)
    intent = Intent(actor="player:u1", verb="work", args={"hours": 8},
                    tick=0, source="test")
    assert action.preconditions(world, intent) == []

    before = world.account_balance(wallet("player:u1"))
    effects = action.apply(world, intent, None)
    apply_effects(world, effects)

    assert world.account_balance(wallet("player:u1")) == before
    self_debts = {
        did: d for did, d in world.debts.items()
        if "player:u1" in (d.creditor, d.debtor) and "wage" in did
    }
    assert not self_debts, "自雇不得生成任何工资债"
    note = [e for e in effects if e.op == "memory_add" and "工资记0文" in e.data.get("text", "")]
    assert note, "店主自雇必须留下可读提示"


def test_settlement_pays_wage_tax_output_and_reports_loss(real_content, world):
    _seed_shop(world, kind="storefront")
    _seed_employee(world, "npc:emp1", 20)
    _seed_employee(world, "npc:emp2", 20)
    apply_effects(world, [Effect("mint", {"account": SHOP_CASH, "amount": 30, "reason": "test_float"})])
    biz = world.businesses[SHOP_ID]
    biz.day_revenue = 50
    biz.day_restock = 10

    rows = build_shop_settlements(real_content, world)
    assert len(rows) == 1
    row = rows[0]
    assert (row["revenue"], row["restock"], row["wage"]) == (50, 10, 40)
    assert row["wage_paid"] == 30 and row["wage_unpaid"] == 10
    tax = daily_shop_tax(real_content, "storefront")
    assert row["tax"] == tax and row["tax_paid"] == 0
    assert row["net"] == 50 - 10 - 40 - tax
    assert row["loss"] is True

    apply_effects(world, shop_settlement_effects(real_content, world, rows, tick=0))
    assert world.account_balance(SHOP_CASH) == 0
    assert world.account_balance(wallet("npc:emp1")) == 20 - income_tax(real_content, 20)
    assert world.account_balance(wallet("npc:emp2")) == 10 - income_tax(real_content, 10)
    wage_debt = world.debts["debt:wage:shop:player:u1:npc:emp2"]
    assert wage_debt.amount == 10
    assert (wage_debt.creditor, wage_debt.debtor) == ("npc:emp2", SHOP_ID)
    output_item = row["output_item"]
    assert world.businesses[SHOP_ID].stock[output_item] == 2
    owner_memory = [
        m for m in world.memories.get("player:u1", [])
        if "日结" in str(m.get("text", ""))
    ]
    assert owner_memory and "亏损，店主需补足" in str(owner_memory[-1]["text"])
    assert conservation(world)["conservation_error"] == 0

    apply_effects(world, shop_day_reset_effects(world, rows))
    assert world.businesses[SHOP_ID].day_revenue == 0
    assert world.businesses[SHOP_ID].day_restock == 0
    # 现金不足的日摊税结转次日，不从账上凭空消失。
    assert world.businesses[SHOP_ID].tax_arrears == tax


def test_wage_debt_increases_when_active_and_reopens_after_settle(real_content, world):
    """欠薪债：未结清时累加；结清后次日再欠必须用带 tick 的新 id，避免冲突。"""
    _seed_shop(world, kind="stall")
    _seed_employee(world, "npc:emp1", 20)
    _seed_employee(world, "npc:emp2", 20)
    apply_effects(world, [Effect("mint", {"account": SHOP_CASH, "amount": 5, "reason": "test_float"})])
    biz = world.businesses[SHOP_ID]
    biz.day_revenue = 0
    first = build_shop_settlements(real_content, world)[0]
    apply_effects(world, shop_settlement_effects(real_content, world, [first], tick=144))
    debt_id = "debt:wage:shop:player:u1:npc:emp2"
    assert world.debts[debt_id].amount == 20

    # 补足现金后把旧债结清。
    apply_effects(world, [Effect("mint", {"account": SHOP_CASH, "amount": 20, "reason": "test_repay"})])
    apply_effects(world, [Effect("debt_settle", {"id": debt_id, "amount": 20})])
    assert world.debts[debt_id].status == "settled"

    # 次日再次欠薪：旧 id 已 settled，必须开新 id（带 tick）而不是重复创建。
    biz.day_revenue = 0
    second = build_shop_settlements(real_content, world)[0]
    apply_effects(world, shop_settlement_effects(real_content, world, [second], tick=288))
    new_id = f"{debt_id}:288"
    assert world.debts[new_id].status == "active"
    assert world.debts[new_id].amount == 20


def _day_end_events(content, state):
    """无店铺对照组：手工发出改动前的四个日界系统动作。"""
    store = EventStore(state["tmp"] / "events.sqlite")
    registry = build_registry(content, 42)
    writer = Writer(state["state"], store, registry, 42)
    writer.bootstrap(state["genesis"], tick=0, verb="genesis")
    writer.state.tick = TICKS_PER_DAY - 1
    for verb, args in (
        ("system_debts", {}),
        ("system_quest_fail", {}),
        ("system_milestone", {"day": 0}),
        ("system_maintenance", {"day": 0}),
    ):
        writer.submit(Intent("system", verb, args, TICKS_PER_DAY - 1, "system"))
    return writer, store


def _new_world(tmp_path: Path, content):
    state = WorldState(locations=content.locations)
    genesis = build_genesis(content, content.npcs, root=ROOT)
    return state, genesis.effects


def test_day_end_sequence_is_byte_identical_without_shop(tmp_path, real_content):
    """无店世界：打过日结守卫的调度器与改动前四个系统动作逐字节一致。"""
    baseline_dir = tmp_path / "baseline"
    guarded_dir = tmp_path / "guarded"
    baseline_dir.mkdir()
    guarded_dir.mkdir()
    baseline_state, baseline_genesis = _new_world(baseline_dir, real_content)
    guarded_state, guarded_genesis = _new_world(guarded_dir, real_content)

    _, baseline_store = _day_end_events(
        real_content, {"tmp": baseline_dir, "state": baseline_state, "genesis": baseline_genesis}
    )
    store = EventStore(guarded_dir / "events.sqlite")
    registry = build_registry(real_content, 42)
    writer = Writer(guarded_state, store, registry, 42)
    writer.bootstrap(guarded_genesis, tick=0, verb="genesis")
    writer.state.tick = TICKS_PER_DAY - 1

    scheduler = AmbientScheduler(
        ROOT, AmbientConfig(out_dir=guarded_dir / "ambient"), content=real_content,
        writer=writer, store=store, state=writer.state, registry=registry,
        lock=threading.RLock(),
    )
    scheduler._day_end(TICKS_PER_DAY - 1, 0)

    baseline = [json.dumps(e.to_dict(), sort_keys=True, ensure_ascii=False)
                for e in baseline_store.read_all()]
    guarded = [json.dumps(e.to_dict(), sort_keys=True, ensure_ascii=False)
               for e in store.read_all()]
    assert guarded == baseline
    assert [e.verb for e in store.read_all()][-4:] == [
        "system_debts", "system_quest_fail", "system_milestone", "system_maintenance",
    ]


def test_day_end_emits_one_settle_after_maintenance_with_shop(tmp_path, real_content):
    state, genesis = _new_world(tmp_path, real_content)
    store = EventStore(tmp_path / "events.sqlite")
    registry = build_registry(real_content, 42)
    writer = Writer(state, store, registry, 42)
    writer.bootstrap(genesis, tick=0, verb="genesis")
    _seed_shop(writer.state, kind="stall")

    scheduler = AmbientScheduler(
        ROOT, AmbientConfig(out_dir=tmp_path / "ambient"), content=real_content,
        writer=writer, store=store, state=writer.state, registry=registry,
        lock=threading.RLock(),
    )
    for day in range(3):
        tick = (day + 1) * TICKS_PER_DAY - 1
        writer.state.tick = tick
        scheduler._day_end(tick, day)

    events = store.read_all()
    settles = [e for e in events if e.verb == "system_shop_settle"]
    assert [e.tick for e in settles] == [
        TICKS_PER_DAY - 1, 2 * TICKS_PER_DAY - 1, 3 * TICKS_PER_DAY - 1,
    ]
    assert all(e.outcome == "ok" for e in settles)
    for day in range(3):
        tick = (day + 1) * TICKS_PER_DAY - 1
        day_events = [e for e in events if e.tick == tick]
        verbs = [e.verb for e in day_events]
        assert verbs.index("system_shop_settle") > verbs.index("system_maintenance")
        assert verbs.count("system_shop_settle") == 1
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
