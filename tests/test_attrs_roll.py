"""NPC 六维掷点：SHA-256 确定性、非法区间诊断与 legacy 兼容。"""
from __future__ import annotations

from wsim.sim.vitals import resolve_npc_vitals, roll_attrs

ROLL = {"str": [5, 44], "con": [10, 30], "dex": [8, 24]}


def test_roll_attrs_is_deterministic_for_same_seed():
    first = roll_attrs("npc.a", ROLL, seed=42)
    second = roll_attrs("npc.a", ROLL, seed=42)
    assert first == second
    for key, (lo, hi) in ROLL.items():
        assert lo <= first[key] <= hi


def test_roll_attrs_changes_with_seed():
    first = roll_attrs("npc.a", ROLL, seed=42)
    second = roll_attrs("npc.a", ROLL, seed=43)
    assert any(first[key] != second[key] for key in ROLL)


def test_roll_attrs_reports_bad_intervals_without_output():
    errors: list[str] = []
    rolled = roll_attrs(
        "npc.bad",
        {"str": [5], "con": [30, 10], "dex": "8-24", "unknown": [1, 2]},
        seed=42,
        errors=errors,
    )
    assert rolled == {}
    assert errors == [
        "bad_attrs_roll:npc.bad:con",
        "bad_attrs_roll:npc.bad:dex",
        "bad_attrs_roll:npc.bad:str",
    ]


def test_resolve_npc_vitals_skips_legacy_entities():
    assert resolve_npc_vitals({"id": "npc.old", "name": "旧村民"}) is None


def test_resolve_npc_vitals_rolls_over_static_attrs():
    npc = {
        "id": "npc.a",
        "vitals": {"max_qi": 120},
        "attrs": {"str": 1, "con": 2},
        "attrs_roll": {"str": [5, 44]},
    }
    resolved = resolve_npc_vitals(npc, seed=42)
    assert resolved is not None
    assert resolved["max_hp"] == 120
    rolled = roll_attrs("npc.a", {"str": [5, 44]}, seed=42)
    assert resolved["attrs"]["str"] == rolled["str"]
    assert resolved["attrs"]["con"] == 2
