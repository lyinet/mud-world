"""同模板多实例 schema：派生数量、id 唯一性、旧实体序列化不变。"""
from __future__ import annotations

from pathlib import Path

import pytest

from wsim.api.xkx_content import load_xkx_content
from wsim.world.state import Entity

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def content():
    return load_xkx_content(ROOT / "content" / "xkx")


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
