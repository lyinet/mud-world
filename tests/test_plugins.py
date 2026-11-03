"""规则层：插件契约、扫描注册、纯度、未知原语回滚。"""
from __future__ import annotations

import textwrap

import pytest

from conftest import ROOT
from wsim.actions.registry import ActionRegistry, build_registry
from wsim.core.models import Effect, Intent
from wsim.core.rng import PRNG
from wsim.plugins.loader import PluginError, load_plugins
from wsim.world.effects import apply_effects
from wsim.world.store import EventStore
from wsim.world.state import Relation
from wsim.world.writer import Writer

PLUGIN_DIR = ROOT / "plugins" / "actions"


def _add_second_npc(state):
    apply_effects(
        state,
        [
            Effect("create_account", {"account": "wallet:n2"}),
            Effect("mint", {"account": "wallet:n2", "amount": 100, "reason": "test"}),
            Effect("create_entity", {
                "id": "n2", "name": "乙", "role": "worker", "location": "a", "home": "a",
                "stamina": 100.0, "skills": {}, "personality": {},
            }),
        ],
    )


def _write_plugin(root, name: str, manifest: str, code: str) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(textwrap.dedent(manifest), encoding="utf-8")
    (d / "action.py").write_text(textwrap.dedent(code), encoding="utf-8")


def test_example_plugin_registers_via_directory_scan(content):
    reg = build_registry(content, seed=42, plugin_dir=PLUGIN_DIR)
    assert "borrow_money" in reg.verbs()
    action = reg.get("borrow_money")
    assert action.manifest.version == "1.0.0"
    assert set(action.manifest.effects) == {"transfer", "debt_create", "memory_add"}


def test_plugin_apply_is_pure_and_does_not_mutate_state(state):
    _add_second_npc(state)
    action = load_plugins(PLUGIN_DIR)[0]
    intent = Intent(actor="n1", verb="borrow_money", args={"target": "n2", "amount": 10}, tick=1)
    before = state.hash()
    effects = action.apply(state, intent, PRNG(42, 1, "action:borrow_money"))
    assert state.hash() == before
    assert [e.op for e in effects] == ["transfer", "debt_create", "memory_add"]


def test_plugin_verb_works_through_writer_without_engine_changes(state, tmp_path):
    _add_second_npc(state)
    action = load_plugins(PLUGIN_DIR)[0]
    reg = ActionRegistry()
    reg.register(action)
    store = EventStore(tmp_path / "events.sqlite")
    writer = Writer(state, store, reg, seed=42)
    before = writer.state.hash()

    event = writer.submit(
        Intent(actor="n1", verb="borrow_money", args={"target": "n2", "amount": 10}, tick=1, source="test")
    )
    assert event.outcome == "ok"
    assert writer.state.accounts["wallet:n1"] == 110
    assert writer.state.accounts["wallet:n2"] == 90
    assert len(writer.state.debts) == 1
    assert writer.state.minted_total >= 0
    assert writer.state.hash() != before  # 只有 Writer 提交后才变
    store.close()


def test_plugin_precondition_insufficient_balance_rejects_whole_intent(state, tmp_path):
    _add_second_npc(state)
    action = load_plugins(PLUGIN_DIR)[0]
    reg = ActionRegistry()
    reg.register(action)
    store = EventStore(tmp_path / "events.sqlite")
    writer = Writer(state, store, reg, seed=42)
    before = writer.state.hash()
    event = writer.submit(
        Intent(actor="n1", verb="borrow_money", args={"target": "n2", "amount": 999}, tick=1)
    )
    assert event.outcome == "rejected"
    assert "insufficient_balance" in event.reason
    assert writer.state.hash() == before
    store.close()


def test_plugin_that_mutates_state_is_rejected_and_original_state_safe(state, tmp_path):
    state.entities["n1"].inventory = {"food": 3}
    plugins = tmp_path / "plugins"
    _write_plugin(
        plugins,
        "evil",
        """
        id: evil
        verb: evil_touch
        version: "1.0.0"
        entry: action.py
        args: {}
        preconditions: []
        effects: [memory_add]
        """,
        """
        from wsim.core.models import Effect

        def apply(state, args):
            state.entities["n1"].stamina = 0.0
            return [Effect("memory_add", {"npc": "n1", "tick": 0, "text": "x", "importance": 0.1})]
        """,
    )
    action = load_plugins(plugins)[0]
    before = state.hash()
    intent = Intent(actor="n1", verb="evil_touch", args={}, tick=1)
    with pytest.raises(PluginError, match="plugin_mutated_state"):
        action.apply(state, intent, PRNG(42, 1, "action:evil"))
    assert state.hash() == before

    reg = ActionRegistry()
    reg.register(action)
    store = EventStore(tmp_path / "events.sqlite")
    writer = Writer(state, store, reg, seed=42)
    before_writer = writer.state.hash()
    event = writer.submit(intent)
    assert event.outcome == "rejected"
    assert "plugin_mutated_state" in event.reason
    assert writer.state.hash() == before_writer
    store.close()


@pytest.mark.parametrize(
    "mutation,label",
    [
        ("state.tick = 999", "tick"),
        ("state.locations['a']['exits'] = ['a']", "locations"),
        ("state.relations.clear()", "relations"),
    ],
)
def test_plugin_full_state_mutation_is_rejected(state, tmp_path, mutation, label):
    # 关系表在两地点合成 fixture 中为空，先放一条，才能验证 clear() 被隔离层发现。
    state.relations["n1|n2|friend"] = Relation(a="n1", b="n2", kind="friend")
    plugins = tmp_path / f"plugins_{label}"
    _write_plugin(
        plugins,
        "evil_full",
        """
        id: evil_full
        verb: evil_full
        version: "1.0.0"
        entry: action.py
        args: {}
        preconditions: []
        effects: [memory_add]
        """,
        f"""
        from wsim.core.models import Effect

        def apply(state, args):
            {mutation}
            return [Effect("memory_add", {{"npc": "n1", "tick": 0, "text": "x", "importance": 0.1}})]
        """,
    )
    action = load_plugins(plugins)[0]
    intent = Intent(actor="n1", verb="evil_full", args={}, tick=1)
    with pytest.raises(PluginError, match="plugin_mutated_state"):
        action.apply(state, intent, PRNG(42, 1, "action:evil_full"))


def test_unknown_effect_primitive_is_rejected_at_load(tmp_path):
    plugins = tmp_path / "plugins"
    _write_plugin(
        plugins,
        "flying",
        """
        id: flying
        verb: fly
        version: "1.0.0"
        entry: action.py
        args: {}
        preconditions: []
        effects: [flying_effect]
        """,
        """
        def apply(state, args):
            return []
        """,
    )
    with pytest.raises(PluginError, match="未知 Effect 原语"):
        load_plugins(plugins)


def test_mint_is_forbidden_for_plugins(tmp_path):
    plugins = tmp_path / "plugins"
    _write_plugin(
        plugins,
        "minter",
        """
        id: minter
        verb: print_money
        version: "1.0.0"
        entry: action.py
        args: {}
        preconditions: []
        effects: [mint]
        """,
        """
        def apply(state, args):
            return []
        """,
    )
    with pytest.raises(PluginError, match="禁止使用原语"):
        load_plugins(plugins)
