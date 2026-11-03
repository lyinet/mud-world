"""测试共用。

- `content` / `state`：2 地点、1 NPC、4 动词的最小合成世界，供单元测试锁定
  纯函数与 Effect 语义，不随内容包改版漂移；
- `real_content` / `real_state`：加载真实 `content/`（31 NPC / 9 地点 / 全动词），
  供集成测试与回归测试覆盖真实路径（阻断项要求）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.core.content import Content  # noqa: E402
from wsim.core.models import Effect  # noqa: E402
from wsim.world.effects import apply_effects  # noqa: E402
from wsim.world.state import WorldState  # noqa: E402


@pytest.fixture
def content() -> Content:
    c = Content()
    c.locations = {
        "a": {"id": "a", "name": "甲地", "exits": ["b"], "services": ["food"]},
        "b": {"id": "b", "name": "乙地", "exits": ["a"], "services": []},
    }
    c.items = {"food": {"id": "food", "name": "饼", "category": "food", "base_price": 5}}
    c.verbs = {
        "move": {"name": "move", "args": ["to"], "cooldown": 0, "allowed_roles": []},
        "work": {"name": "work", "args": ["at", "hours"], "cooldown": 0, "allowed_roles": []},
        "consume": {"name": "consume", "args": ["item"], "cooldown": 0, "allowed_roles": []},
        "cultivate": {"name": "cultivate", "args": ["hours", "skill"], "cooldown": 0, "allowed_roles": []},
    }
    c.economy = {
        "wages_daily": {"worker": 20},
        "income_tax_rate": 0.1,
        "transaction_tax_rate": 0.03,
        "living_costs": {"by_role": {"worker": {"housing": 2, "upkeep": 1, "dues": 0}}},
        "stamina_work": 12,
        "stamina_move": 5,
        "stamina_cultivate": 4,
        "stamina_recover": 40,
        "food_stamina": 25,
        "sell_ratio": 0.6,
    }
    return c


def seed_state(content: Content) -> WorldState:
    state = WorldState(locations=content.locations)
    effects = [
        Effect("create_account", {"account": "wallet:n1"}),
        Effect("create_account", {"account": "treasury:region"}),
        Effect("mint", {"account": "wallet:n1", "amount": 100, "reason": "test"}),
        Effect("create_entity", {
            "id": "n1",
            "name": "甲",
            "role": "worker",
            "location": "a",
            "home": "a",
            "stamina": 100.0,
            "skills": {"work": 0.4},
            "personality": {},
        }),
        Effect("create_business", {
            "id": "biz:a:food",
            "name": "甲地食肆",
            "location": "a",
            "kind": "food",
            "cash_account": "treasury:region",
        }),
        Effect("mint", {"account": "treasury:region", "amount": 1000, "reason": "test_float"}),
        Effect("item_create", {"owner": "biz:a:food", "item": "food", "qty": 100, "reason": "test_stock"}),
        Effect("set_employment", {"entity": "n1", "business": "biz:a:food"}),
    ]
    apply_effects(state, effects)
    return state


@pytest.fixture
def state(content) -> WorldState:
    return seed_state(content)


@pytest.fixture(scope="session")
def real_content():
    """真实内容包（31 NPC / 9 地点 / 31 物品 / 全部动词与门派）。"""
    from wsim.core.content import load_content

    return load_content(ROOT, allow_fixture=False, use_content=True)


@pytest.fixture
def real_state(real_content) -> WorldState:
    """用真实内容包走创世流程的状态，供意图层/回归测试使用。"""
    from wsim.sim.genesis import build_genesis

    state = WorldState(locations=real_content.locations)
    genesis = build_genesis(real_content, real_content.npcs, root=ROOT)
    apply_effects(state, genesis.effects)
    return state
