"""纯度 / 可回放性守卫。

这些测试不验证数值公式，而是验证约束：
- 公式包不 import 随机、时间、数据库或引擎模块；
- 相同输入 + 相同取数序列 => 相同输出与相同取数调用序列（可回放）；
- `RandomSource` 协议可被测试替身满足。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from xkx_formula import RandomSource, SequenceRandom, resolve_damage  # noqa: E402

PACKAGE = ROOT / "tools" / "xkx_formula"
FORBIDDEN_MODULES = {"random", "time", "os", "sqlite3", "wsim"}


def test_formula_package_has_no_stateful_imports() -> None:
    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    imported.add(node.module.split(".")[0])
        leaked = imported & FORBIDDEN_MODULES
        assert not leaked, f"{path.name} 引入了禁止模块: {sorted(leaked)}"


def test_sequence_random_satisfies_protocol() -> None:
    assert isinstance(SequenceRandom([1]), RandomSource)


def test_sequence_random_zero_bound_is_recorded_not_consumed() -> None:
    prng = SequenceRandom([7])
    assert prng.below(0) == 0
    assert prng.calls == [0]
    assert prng.remaining == 1


def test_same_sequence_replays_same_result_and_draw_log() -> None:
    draws = [50, 10, 15, 500]
    first = SequenceRandom(draws)
    result_a = resolve_damage(
        raw_damage=100,
        armor=20,
        str_effective=30,
        attacker_exp=500,
        defender_exp=1000,
        prng=first,
    )

    second = SequenceRandom(draws)
    result_b = resolve_damage(
        raw_damage=100,
        armor=20,
        str_effective=30,
        attacker_exp=500,
        defender_exp=1000,
        prng=second,
    )

    assert result_a == result_b
    assert first.calls == second.calls == [100, 20, 30, 1000]


def test_sequence_random_raises_when_exhausted() -> None:
    prng = SequenceRandom([])
    with pytest.raises(AssertionError):
        prng.below(10)
