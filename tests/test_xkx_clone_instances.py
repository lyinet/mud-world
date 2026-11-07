"""同模板多实例：内容层派生、创世创建、旧实体序列化不变。"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from wsim.api import WorldSession
from wsim.api.xkx_content import load_xkx_content
from wsim.world.state import Entity

ROOT = Path(__file__).resolve().parents[1]
XKX_CONTENT = ROOT / "content" / "xkx"
WORLD_CONFIG = ROOT / "config" / "worlds" / "xkx.yaml"


@pytest.fixture(scope="module")
def content():
    return load_xkx_content(XKX_CONTENT)


@pytest.fixture()
def session(tmp_path):
    cfg = yaml.safe_load(WORLD_CONFIG.read_text(encoding="utf-8")) or {}
    s = WorldSession(
        ROOT,
        run_id="xkx-clone-test",
        seed=42,
        npc_limit=0,
        run_dir=tmp_path / "run",
        content_root=XKX_CONTENT,
        world_config=WORLD_CONFIG,
        spawn=str(cfg.get("spawn") or "market"),
    )
    yield s
    s.close()


def test_instances_are_room_scoped_and_unique(content):
    rows = content.npc_instances
    assert len(rows) == 44
    ids = [row["id"] for row in rows]
    assert len(set(ids)) == len(ids)
    templates = {str(npc["id"]) for npc in content.npcs}
    assert all(row["template"] in templates for row in rows)
    assert all(row["id"] == f"{row['template']}@{row['room']}#{row['instance']}" for row in rows)
    assert not (set(ids) & templates)
    assert content.world_stats["npc_instances"] == 44


def test_road12_has_four_qingbing_slots(content):
    rows = [
        row
        for row in content.npc_instances
        if row["template"] == "/d/hangzhou/npc/qingbing" and row["room"] == "/d/hangzhou/road12"
    ]
    assert [row["instance"] for row in rows] == [2, 3, 4]


def test_relation_master_shop_and_quest_npcs_are_not_cloned(content):
    cloned = {row["template"] for row in content.npc_instances}
    for npc in content.npcs:
        if npc.get("relations") or npc.get("is_master"):
            assert str(npc["id"]) not in cloned
    assert "/d/hangzhou/npc/smith" not in cloned
    assert "/d/shiliang/npc/atu" not in cloned
    assert "/d/hangzhou/npc/qingbing" in cloned


def test_canonical_entity_serialization_has_no_clone_keys():
    ent = Entity(id="npc:p", name="P", role="npc", location="loc")
    data = ent.to_dict()
    assert "template" not in data
    assert "instance" not in data


def test_extra_instance_serialization_marks_template():
    ent = Entity(
        id="/d/hangzhou/npc/qingbing@/d/hangzhou/road12#3",
        name="绿营清兵",
        role="npc",
        location="/d/hangzhou/road12",
        template="/d/hangzhou/npc/qingbing",
        instance=3,
    )
    data = ent.to_dict()
    assert data["template"] == "/d/hangzhou/npc/qingbing"
    assert data["instance"] == 3


def test_genesis_creates_all_instances_with_template_stats(session, content):
    state = session.state
    clones = [ent for ent in state.entities.values() if ent.template]
    assert len(clones) == 44
    assert {ent.id for ent in clones} == {row["id"] for row in content.npc_instances}
    meta = {str(npc["id"]): npc for npc in content.npcs}
    for ent in clones:
        base = state.entities[ent.template]
        assert ent.status == "alive"
        assert ent.location == next(row["room"] for row in content.npc_instances if row["id"] == ent.id)
        assert ent.name == base.name
        assert ent.hp == base.hp == ent.max_hp
        assert dict(ent.skills) == dict(base.skills)
        assert list(ent.equipment) == list(base.equipment)
        assert meta[ent.template]["display_name"] == base.name
    # 105 个规范 NPC + 44 个别实例；玩家不在这个集合里。
    npc_like = [ent for ent in state.entities.values() if not str(ent.id).startswith("player:")]
    assert len(npc_like) == 149


def test_road12_has_four_alive_qingbing(session):
    alive = sorted(
        ent.name for ent in session.state.entities.values() if ent.location == "/d/hangzhou/road12" and ent.status == "alive"
    )
    assert alive.count("绿营清兵") == 4
    assert "李可秀" in alive


def test_present_view_resolves_clone_display_names(session):
    rows = session._present_view("player:none", "/d/hangzhou/road12")
    qingbing = [row for row in rows if row["name"] == "绿营清兵"]
    assert len(qingbing) == 4
    assert all(row["id"].startswith("/d/hangzhou/npc/qingbing") for row in qingbing)
