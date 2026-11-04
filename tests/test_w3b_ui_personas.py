"""W3b：UI adapter 与后端共用同一份内容解析，开启态 29 个新 NPC 不再哑巴。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from ui.adapter import WorldUIAdapter  # noqa: E402
from wsim.core.content import load_content  # noqa: E402
from wsim.sim.headless import RunConfig, run_headless  # noqa: E402

BASE_HASH = "7ec670c1c83c7b99e9bee35f84602bd7a68ef22126b29c9003529809b2c3d53f"
PACK_HASH = "3fd99946b44a7dac4c3ea041d114e28d84ef07e67aa90e40e209970c2c43534c"
PACK = "scaleup_w3"


def _run(tmp_path, content, run_id):
    return run_headless(
        ROOT,
        RunConfig(
            days=1, seed=42, npc_limit=0, out_root=tmp_path, run_id=run_id,
            with_players=0, dump_state=True, enable_llm=False,
        ),
        content=content,
    )


def test_adapter_reads_pack_personas_same_as_engine(monkeypatch, tmp_path):
    monkeypatch.delenv("XKX_CONTENT_PACKS", raising=False)
    base = load_content(ROOT, allow_fixture=False)
    assert base.content_hash == BASE_HASH

    monkeypatch.setenv("XKX_CONTENT_PACKS", PACK)
    enabled = load_content(ROOT, allow_fixture=False)
    assert enabled.content_hash == PACK_HASH

    _run(tmp_path, enabled, "ui-pack")
    adapter = WorldUIAdapter(tmp_path / "ui-pack")
    assert adapter.content is not None
    assert adapter.content.content_hash == PACK_HASH
    assert len(adapter.personas) == 59

    pack_ids = sorted({n["id"] for n in enabled.npcs} - {n["id"] for n in base.npcs})
    assert len(pack_ids) == 29
    for npc_id in pack_ids:
        detail = adapter.npc_detail(npc_id)
        assert detail["persona"]["raw"].strip(), npc_id
        assert detail["persona"]["source"] == "persona_card", npc_id
        assert len(detail["persona"]["voice_lines"]) >= 2, npc_id


def test_adapter_closed_mode_still_has_exactly_base_cards(monkeypatch, tmp_path):
    monkeypatch.delenv("XKX_CONTENT_PACKS", raising=False)
    base = load_content(ROOT, allow_fixture=False)

    _run(tmp_path, base, "ui-off")
    adapter = WorldUIAdapter(tmp_path / "ui-off")
    assert adapter.content is not None
    assert adapter.content.content_hash == BASE_HASH
    assert len(adapter.personas) == 30
    assert set(adapter.personas) == set(base.personas)
    for npc_id, text in adapter.personas.items():
        assert text == base.personas[npc_id], npc_id
