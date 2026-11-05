"""意图层：玩家店铺雇工的日程（workplace 候选、资金门槛、欠薪仍出工）。

锁定行为缺口：签约后 `employed_at=shop:player:<account>` 的 NPC 必须真的走到
玩家店铺并 `work`。资金门槛按雇主类型区分：
- NPC 雇主沿用原规则（现金 ≥ 日薪 + reserve），防止商业体被抽干；
- 玩家雇主按玩家钱包余额 + 店铺当日营收判断，但**不因此罢工**：
  契约仍然成立，钱不够由日结 `system_shop_settle` 记欠薪债。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from wsim.actions.common import wallet
from wsim.actions.social import TalkAction
from wsim.core.clock import TICKS_PER_DAY
from wsim.core.models import Effect, Intent
from wsim.sim.genesis import build_genesis
from wsim.sim.intent import GoalChoice, Planner
from wsim.sim.scheduler import AmbientConfig, AmbientScheduler
from wsim.world.effects import apply_effects
from wsim.world.state import WorldState

ROOT = Path(__file__).resolve().parents[1]
SHOP_ID = "shop:player:u1"
SHOP_CASH = f"cash:{SHOP_ID}"
EMPLOYEE = "yan_shouyi"


@pytest.fixture()
def world(real_content):
    state = WorldState(locations=real_content.locations)
    apply_effects(state, build_genesis(real_content, real_content.npcs, root=ROOT).effects)
    return state


def _seed_player_shop(state, money: int = 500, kind: str = "stall") -> None:
    effects = [
        Effect("create_entity", {
            "id": "player:u1", "name": "测试掌柜", "role": "商人",
            "location": "market", "stamina": 100.0, "max_stamina": 100.0,
        }),
        Effect("create_account", {"account": wallet("player:u1")}),
        Effect("create_account", {"account": SHOP_CASH}),
        Effect("create_business", {
            "id": SHOP_ID, "name": "测试小店", "location": "market",
            "kind": kind, "owner": "player:u1", "cash_account": SHOP_CASH,
        }),
    ]
    if money > 0:
        effects.append(Effect("mint", {
            "account": wallet("player:u1"), "amount": money, "reason": "test",
        }))
    apply_effects(state, effects)


def _hire(state, wage: int) -> None:
    apply_effects(state, [Effect("shop_hire", {
        "business": SHOP_ID, "entity": EMPLOYEE, "position": "庙祝", "wage": wage,
    })])


def _plan(state, content):
    choice = GoalChoice(
        npc=EMPLOYEE, goal_id="g:test", content_goal_id="",
        content_type="earn_money", kind="earn_money", target=0.0,
        source="self", reason="攒钱",
    )
    planner = Planner(content, 42)
    return planner, planner.plan(state, EMPLOYEE, choice, 0, TICKS_PER_DAY, "active", 0)


def _work_intents(intents, hours: int = 8):
    return [
        i for i in intents
        if i.verb == "work" and int(i.args.get("hours", 0)) == hours
    ]


def test_player_employer_schedule_commutes_to_shop_and_works(real_content, world):
    _seed_player_shop(world)
    _hire(world, wage=18)
    planner, intents = _plan(world, real_content)

    ent = world.entities[EMPLOYEE]
    assert ent.employed_at == SHOP_ID
    moves = [i.args.get("to") for i in intents if i.verb == "move"]
    assert "market" in moves, "雇工必须先走到玩家店铺所在地"
    work = _work_intents(intents)
    assert work and work[0].args.get("at") == "market", "雇工必须在玩家店铺上工"
    # 地点与 market 里的其他摊位同名，必须靠 employer 归因到玩家店铺。
    assert work[0].args.get("employer") == SHOP_ID
    # 契约日薪 18 < NPC 雇主的 2000 文储备要求；玩家雇主不套用该规则。
    assert planner._wage_for_employment(world.businesses[SHOP_ID], ent) == 18


def test_npc_employer_work_args_stay_unchanged(real_content, world):
    """NPC 雇主的 work 参数保持 {at, hours}，无玩家雇佣的世界事件不变。"""
    _, intents = _plan(world, real_content)
    work = _work_intents(intents)
    assert work, "原雇主现金充足时应照常排整班"
    assert set(work[0].args) == {"at", "hours"}


def test_player_employer_works_even_when_shop_cash_is_zero(real_content, world):
    """店现 0、玩家钱包 500（低于 18+2000 储备）也要出工。"""
    _seed_player_shop(world, money=500)
    _hire(world, wage=18)
    assert world.account_balance(SHOP_CASH) == 0
    _, intents = _plan(world, real_content)
    work = _work_intents(intents)
    assert work, "店现为 0 不是罢工理由：工资由日结走欠薪"
    assert "欠薪" not in work[0].reason


def test_player_employer_insufficient_balance_still_works_with_arrears_note(real_content, world):
    _seed_player_shop(world, money=0)
    _hire(world, wage=18)
    _, intents = _plan(world, real_content)
    work = _work_intents(intents)
    assert work, "玩家钱包不足时契约仍成立，雇工照常到店"
    assert "欠薪" in work[0].reason


def test_player_contract_wage_and_day_revenue_count_toward_solvency(real_content, world):
    """玩家雇主：契约日薪优先于岗位市场价，且当日营收计入偿付能力。"""
    _seed_player_shop(world, money=30)
    _hire(world, wage=40)
    planner, intents = _plan(world, real_content)
    biz = world.businesses[SHOP_ID]
    assert planner._wage_for_employment(biz, world.entities[EMPLOYEE]) == 40
    work = _work_intents(intents)
    assert work and "欠薪" in work[0].reason

    biz.day_revenue = 10  # 钱包 30 + 当日营收 10 >= 日薪 40
    assert planner._player_employer_can_pay(world, biz, 40) is True


def test_npc_employer_reserve_rule_is_unchanged(real_content, world):
    """NPC 雇主现金不足时仍走 cultivate，不产生 8 小时整班。"""
    ent = world.entities[EMPLOYEE]
    employer = world.businesses[ent.employed_at]
    reserve = int((real_content.economy.get("work") or {}).get("employer_reserve", 300))
    gross = int(round(real_content.wage_for(ent.role)))
    balance = world.account_balance(employer.cash_account)
    assert balance > gross + reserve

    apply_effects(world, [Effect("burn", {
        "account": employer.cash_account,
        "amount": balance - 1,
        "reason": "test_drain",
    })])
    _, intents = _plan(world, real_content)
    assert not _work_intents(intents)
    cultivate = [i for i in intents if i.verb == "cultivate"]
    assert cultivate, "NPC 雇主付不出整班工资时仍按原规则留在原地练手艺"


@pytest.mark.parametrize(
    ("employee_location", "expected"),
    [("market", []), ("temple", ["not_together"])],
)
def test_talk_availability_tracks_workplace_presence(
    real_content, world, employee_location, expected
):
    """玩家在店里时，雇工是否同地点决定 talk 的可用性。"""
    _seed_player_shop(world)
    world.entities[EMPLOYEE].location = employee_location
    intent = Intent(
        actor="player:u1", verb="talk",
        args={"target": EMPLOYEE, "message": "今日辛苦了"},
        tick=0, source="test",
    )
    assert TalkAction(real_content).preconditions(world, intent) == expected


def _run_hired_world(real_content, tmp_path, name: str, days: int = 3) -> AmbientScheduler:
    cfg = AmbientConfig(
        out_dir=tmp_path / name, seed=42, max_ticks=days * TICKS_PER_DAY, start_tick=0,
        max_cpu_ratio=100.0, cpu_pause_after_s=9999.0,
    )
    sched = AmbientScheduler(ROOT, cfg, content=real_content)
    _seed_player_shop(sched.writer.state, money=500, kind="branch")
    apply_effects(sched.writer.state, [Effect("transfer", {
        "src": wallet("player:u1"), "dst": SHOP_CASH, "amount": 200,
        "reason": "test_float",
    })])
    _hire(sched.writer.state, wage=18)
    sched.run_ticks(days * TICKS_PER_DAY)
    return sched


def test_three_day_hired_world_pays_wages_and_is_deterministic(real_content, tmp_path):
    """自走 3 个世界日：每天到店上工、日结发工资，同 seed 两次哈希一致。"""
    first = _run_hired_world(real_content, tmp_path, "hire-a")
    second = _run_hired_world(real_content, tmp_path, "hire-b")

    assert first.writer.state.hash() == second.writer.state.hash()
    assert first.store.event_hash() == second.store.event_hash()

    events = first.store.read_all()
    for day in range(3):
        work = [
            e for e in events
            if e.actor == EMPLOYEE and e.verb == "work"
            and e.args.get("at") == "market"
            and e.args.get("employer") == SHOP_ID
            and e.tick // TICKS_PER_DAY == day
        ]
        assert work, f"day{day} 雇工没有到玩家店铺上工（需带 employer={SHOP_ID}）"
    settles = [e for e in events if e.verb == "system_shop_settle"]
    assert len(settles) == 3
    for event in settles:
        wage = sum(
            item["data"]["amount"] for item in event.effects
            if item["op"] == "transfer" and item["data"].get("reason") == "wage"
        )
        tax = sum(
            item["data"]["amount"] for item in event.effects
            if item["op"] == "burn" and item["data"].get("reason") == "shop_daily_tax"
        )
        output = sum(item["data"]["qty"] for item in event.effects if item["op"] == "item_create")
        # 产出随技能：新手 1 件，干满几天技能≥0.5 后 2 件；工资与税不变。
        assert (wage, tax) == (18, 10) and output >= 1
