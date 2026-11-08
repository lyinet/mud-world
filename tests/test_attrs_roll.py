"""NPC 六维掷点：SHA-256 确定性、非法区间诊断与 legacy 兼容。"""
from __future__ import annotations

from conftest import ROOT

from wsim.api.xkx_content import load_xkx_content
from wsim.sim.genesis import build_genesis
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


HUMAN_DERIVE = {
    "int": {
        "kind": "difference",
        "total": 80,
        "minus": ["str", "con", "dex"],
    }
}


def test_resolve_npc_vitals_derives_human_int_from_rolled_attrs():
    npc = {
        "id": "npc.human",
        "attrs_roll": {"str": [10, 30], "con": [10, 30], "dex": [10, 30]},
        "attrs_derive": HUMAN_DERIVE,
    }
    resolved = resolve_npc_vitals(npc, seed=7)
    assert resolved is not None
    attrs = resolved["attrs"]
    assert attrs["int"] == 80 - attrs["str"] - attrs["con"] - attrs["dex"]
    again = resolve_npc_vitals(npc, seed=7)
    assert again["attrs"] == attrs
    other = resolve_npc_vitals(npc, seed=8)
    assert other["attrs"] != attrs


def test_resolve_npc_vitals_keeps_explicit_int_over_derive():
    npc = {
        "id": "npc.human",
        "attrs": {"str": 20, "con": 20, "dex": 20, "int": 28},
        "attrs_derive": HUMAN_DERIVE,
    }
    resolved = resolve_npc_vitals(npc, seed=7)
    assert resolved["attrs"]["int"] == 28


def test_resolve_npc_vitals_skips_derive_when_minus_key_missing():
    errors: list[str] = []
    npc = {
        "id": "npc.human",
        "attrs": {"str": 20, "int": 28},
        "attrs_roll": {"con": [10, 30]},
        "attrs_derive": HUMAN_DERIVE,
    }
    resolved = resolve_npc_vitals(npc, seed=7, errors=errors)
    assert resolved["attrs"]["int"] == 28
    # 显式 int 优先；缺 dex 的诊断在不覆盖显式值时不会触发
    assert errors == []

    npc2 = {
        "id": "npc.human2",
        "attrs": {"str": 20},
        "attrs_derive": HUMAN_DERIVE,
    }
    errors2: list[str] = []
    resolved2 = resolve_npc_vitals(npc2, seed=7, errors=errors2)
    assert "int" not in resolved2["attrs"]
    assert errors2 == ["incomplete_attrs_derive:npc.human2:int"]


def test_resolve_npc_vitals_rejects_bad_derive_rule():
    errors: list[str] = []
    npc = {
        "id": "npc.human",
        "attrs": {"str": 20, "con": 20, "dex": 20},
        "attrs_derive": {"int": {"kind": "expr", "minus": ["str"]}},
    }
    resolved = resolve_npc_vitals(npc, seed=7, errors=errors)
    assert "int" not in resolved["attrs"]
    assert errors == ["bad_attrs_derive:npc.human:int"]


def test_roll_key_makes_each_instance_roll_independently():
    """原版每个 clone 在 new() 现掷：同一模板的两个实例不能共享一套六维。"""
    npc = {"id": "npc.tpl", "attrs_roll": {"str": [10, 30], "con": [10, 30]}}
    first = resolve_npc_vitals(npc, seed=20261014, roll_key="npc.tpl@room#1")
    second = resolve_npc_vitals(npc, seed=20261014, roll_key="npc.tpl@room#2")
    assert first["attrs"] != second["attrs"]
    # 同一身份重复解析逐字节可复现。
    assert resolve_npc_vitals(npc, seed=20261014, roll_key="npc.tpl@room#1")["attrs"] == first["attrs"]


def _clone_attrs(seed: int = 20261014) -> dict[str, dict[str, int]]:
    content = load_xkx_content(ROOT / "content" / "xkx")
    genesis = build_genesis(content, content.npcs, root=ROOT, seed=seed)
    return {
        e.data["id"]: e.data["attrs"]
        for e in genesis.effects
        if e.op == "create_entity" and e.data.get("template") and "attrs" in e.data
    }


def test_genesis_clones_do_not_share_template_attrs():
    attrs = _clone_attrs()
    assert attrs, "真实内容包应包含 clone"
    cor = {row["cor"] for row in attrs.values() if "cor" in row}
    assert len(cor) > 1, "clone 仍共享模板六维（模板键化未修掉）"
    # 同一 seed 双跑完全一致。
    assert _clone_attrs() == attrs


def test_xkx_human_cor_distribution_matches_original_range():
    content = load_xkx_content(ROOT / "content" / "xkx")
    values: list[int] = []
    for npc in content.npcs:
        span = (npc.get("attrs_roll") or {}).get("cor")
        if not span:
            continue
        resolved = resolve_npc_vitals(npc, seed=20261014)
        values.append(resolved["attrs"]["cor"])
    assert values
    assert min(values) >= 10 and max(values) <= 30
    assert 19.0 <= sum(values) / len(values) <= 21.0
