"""W3b：引擎原生读取扩展包（不物化），开关切换零残留且哈希确定。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.core.content import load_content  # noqa: E402
from wsim.sim.headless import RunConfig, run_headless  # noqa: E402

BASE_HASH = "5267a8a84f7c2e0200358c92b8e63cebff54db461de8123484732a66aa696da4"
PACK = "scaleup_w3"
BASE_COUNTS = (31, 9, 41, 27, 30)
PACK_COUNTS = (60, 20, 70, 60, 59)


def _load_enabled(monkeypatch):
    monkeypatch.setenv("XKX_CONTENT_PACKS", PACK)
    return load_content(ROOT, allow_fixture=False)


def test_off_mode_stays_bit_identical(monkeypatch):
    monkeypatch.delenv("XKX_CONTENT_PACKS", raising=False)
    content = load_content(ROOT, allow_fixture=False)
    assert content.content_hash == BASE_HASH
    assert (
        len(content.npcs), len(content.locations), len(content.items),
        len(content.quests), len(content.personas),
    ) == BASE_COUNTS
    assert "scaleup_w3" not in " ".join(content.sources.values())


def test_enabled_native_reads_pack_without_materialize(monkeypatch):
    content = _load_enabled(monkeypatch)
    assert content.content_hash != BASE_HASH
    assert (
        len(content.npcs), len(content.locations), len(content.items),
        len(content.quests), len(content.personas),
    ) == PACK_COUNTS
    assert "content/packs/scaleup_w3/npcs.yaml" in content.sources["npcs"]
    assert "content/packs/scaleup_w3/quests.yaml" in content.sources["quests"]
    assert "content/packs/scaleup_w3/personas/*.md" in content.sources["personas"]
    # 原生路径不得生成物化中间目录。
    assert not (ROOT / "runs" / "_w3" / PACK / "content").exists()


def test_switch_toggle_is_deterministic_and_leaves_no_residue(monkeypatch):
    first = _load_enabled(monkeypatch)
    second = _load_enabled(monkeypatch)
    assert first.content_hash == second.content_hash

    monkeypatch.delenv("XKX_CONTENT_PACKS", raising=False)
    off = load_content(ROOT, allow_fixture=False)
    assert off.content_hash == BASE_HASH
    assert len(off.personas) == 30


def test_enabled_two_day_run_hashes_are_stable(monkeypatch, tmp_path):
    content = _load_enabled(monkeypatch)
    hashes = []
    for run_id in ("w3b-test-a", "w3b-test-b"):
        summary = run_headless(
            ROOT,
            RunConfig(
                days=2, seed=42, npc_limit=0, out_root=tmp_path, run_id=run_id,
                with_players=0, dump_state=True, enable_llm=False,
            ),
            content=content,
        )
        hashes.append((
            summary["hashes"]["state_final"],
            summary["hashes"]["event_final"],
            summary["content_hash"],
            summary["events"]["total"],
        ))
    assert hashes[0] == hashes[1]
    assert hashes[0][3] > 800


def test_validator_passes_in_native_pack_mode():
    env = dict(os.environ, XKX_CONTENT_PACKS=PACK)
    result = subprocess.run(
        [sys.executable, str(ROOT / "content" / "validate_content.py")],
        cwd=str(ROOT), capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "结果: PASS" in result.stdout
