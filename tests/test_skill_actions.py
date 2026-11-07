"""招式/钩子内容索引的计数、缺键与选招语义。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.sim import skill_actions as sa  # noqa: E402

CONTENT = ROOT / "content" / "xkx"


class FakePRNG:
    def __init__(self, values=None):
        self.calls: list[int] = []
        self.values = list(values or [])

    def below(self, n: int) -> int:
        self.calls.append(int(n))
        if n <= 0:
            return 0
        return self.values.pop(0) if self.values else 0


def test_action_table_has_22_skills_and_247_actions():
    table = sa.load_action_table(CONTENT)
    assert len(table) == 22
    assert sum(len(actions) for actions in table.values()) == 247


def test_default_coefficients_are_126_and_all_zero():
    raw = json.loads((CONTENT / "skills_actions.json").read_text(encoding="utf-8"))
    total = 0
    for row in raw["skills_actions"]:
        for action in row.get("actions") or []:
            coeffs = action.get("coefficients") or {}
            for key, source in (action.get("coefficient_source") or {}).items():
                if source == "default":
                    total += 1
                    assert int(coeffs.get(key) or 0) == 0
    assert total == 126


def test_missing_key_is_zero():
    assert sa.coefficient({"coeff": {"dodge": 3}}, "force") == 0
    assert sa.coefficient({"coeff": {"dodge": 3}}, "damage") == 0
    assert sa.coefficient(None, "parry") == 0
    assert sa.coefficient({"coeff": {"force": None}}, "force") == 0


def test_select_action_uses_last_eligible_index_and_prng():
    actions = [
        {"index": 0, "level": 0},
        {"index": 1, "level": 10},
        {"index": 2, "level": 20},
    ]
    prng = FakePRNG([1])
    picked = sa.select_action(actions, 15, prng)
    assert prng.calls == [2]
    assert picked["index"] == 1

    prng0 = FakePRNG()
    picked0 = sa.select_action(actions, 0, prng0)
    assert prng0.calls == [0]  # random(0) 记录一笔 (0, 0)
    assert picked0["index"] == 0

    prng_empty = FakePRNG()
    assert sa.select_action([], 99, prng_empty) is None
    assert prng_empty.calls == []  # 空表不掷点


def test_default_action_is_random_of_race_table():
    human = FakePRNG([3])
    action = sa.select_default_action("人类", human)
    assert human.calls == [5]
    assert action["text"].startswith("$N")
    assert all(action["coeff"][k] == 0 for k in sa.COEFF_KEYS)

    beast = FakePRNG([2])
    action = sa.select_default_action("走兽", beast)
    assert beast.calls == [3]
    assert action["coeff"]["damage"] == 30

    unknown = FakePRNG()
    assert sa.select_default_action("飞禽", unknown) is None
    assert unknown.calls == []


def test_hook_code_skills_and_unmodeled_three():
    hooks = sa.load_hook_table(CONTENT)
    force = {k for k, v in hooks.items() if sa.has_code_hook(v, "force_hit")}
    martial = {k for k, v in hooks.items() if sa.has_code_hook(v, "martial_hit")}
    assert force == {
        "bibo-shengong", "force", "huntian-qigong",
        "hunyuan-yiqi", "taiji-shengong", "yunlong-shengong",
    }
    assert martial == {"mantian-xing", "yunlong-zhua"}
    for name in ("victim_force_hit", "victim_parry_hit", "weapon_hit"):
        assert not any(sa.has_code_hook(v, name) for v in hooks.values())


def test_mapped_skill_reads_entity_mapping():
    class Ent:
        map_skills = {"unarmed": "jingang-quan"}

    assert sa.mapped_skill(Ent(), "unarmed") == "jingang-quan"
    assert sa.mapped_skill(Ent(), "sword") == ""
