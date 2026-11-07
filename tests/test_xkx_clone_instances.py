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
    assert {row["template"] for row in qingbing} == {"/d/hangzhou/npc/qingbing"}
    assert sorted(row["instance"] for row in qingbing) == [0, 2, 3, 4]


def _road12_session(tmp_path, tag: str, room: str = "/d/hangzhou/road12"):
    cfg = yaml.safe_load(WORLD_CONFIG.read_text(encoding="utf-8")) or {}
    return WorldSession(
        ROOT,
        run_id=f"xkx-clone-{tag}",
        seed=42,
        npc_limit=0,
        run_dir=tmp_path / f"run-{tag}",
        content_root=XKX_CONTENT,
        world_config=WORLD_CONFIG,
        spawn=room,
    )


@pytest.fixture()
def road12_session(tmp_path):
    """玩家直接落在 road12：验证真实 look / fight 的多实例表现。"""
    s = _road12_session(tmp_path, "road12")
    yield s
    s.close()


def _cmd(session, command):
    account = "clone_probe"
    return session.xkx_command(account, {"account_id": account, "command": command})


def test_look_groups_clones_with_count_and_unit(road12_session):
    text = "\n".join(line["text"] for line in _cmd(road12_session, "look")["lines"])
    assert "在场人物：" in text
    assert "四位绿营清兵" in text
    # 单只的人不加数量词，保持改造前逐字一致。
    assert "杭州提督 李可秀" in text
    assert "一位杭州提督 李可秀" not in text


def test_look_uses_bird_unit_for_crows(session):
    shell = session._xkx_shell_instance()
    rows = [
        {"id": f"/d/jiaxing/npc/wuya@{i}", "name": "乌鸦", "template": "/d/jiaxing/npc/wuya", "instance": i}
        for i in range(1, 5)
    ]
    assert shell._npc_present_text(rows) == ["四只乌鸦"]


def test_fight_without_index_hits_last_created_clone(road12_session):
    before = road12_session.state.entities
    reply = _cmd(road12_session, "fight qing bing")
    assert reply["ok"], reply
    assert reply["target"] == "/d/hangzhou/npc/qingbing@/d/hangzhou/road12#4"
    assert reply["target_index"] == 1
    after = road12_session.state.entities
    assert after[reply["target"]].hp < before[reply["target"]].hp or after[reply["target"]].status == "dead"


def test_fight_index_two_hits_second_in_lifo_order(tmp_path):
    # 一次 fight 会打到死亡/倒下，两条命令各起一个干净世界，避免受战斗状态影响。
    first_session = _road12_session(tmp_path, "fight1")
    second_session = _road12_session(tmp_path, "fight2")
    try:
        first = _cmd(first_session, "fight qing bing")
        second = _cmd(second_session, "fight qing bing 2")
    finally:
        first_session.close()
        second_session.close()
    assert first["ok"] and second["ok"], (first, second)
    assert first["target"] == "/d/hangzhou/npc/qingbing@/d/hangzhou/road12#4"
    assert first["target_instance"] == 4
    assert second["target"] == "/d/hangzhou/npc/qingbing@/d/hangzhou/road12#3"
    assert second["target_instance"] == 3
    assert second["target_index"] == 2


def test_fight_index_out_of_range_has_no_target(road12_session):
    reply = _cmd(road12_session, "fight qing bing 5")
    assert not reply["ok"]
    text = "\n".join(line["text"] for line in reply["lines"])
    assert "没有第5个" in text


def test_unconcious_state_stays_visible_and_gets_look_tag(tmp_path):
    # 引擎能力（不是内容初始态）：运行期把 NPC 置为昏迷，look 仍可见并追加状态后缀。
    s = _road12_session(tmp_path, "unconcious", "/d/hangzhou/huanglongdong")
    try:
        snake = s.state.entities["/d/hangzhou/npc/dushe"]
        assert snake.status == "alive"
        snake.status = "unconcious"
        rows = s._present_view("player:none", "/d/hangzhou/huanglongdong")
        assert any(
            row["id"] == "/d/hangzhou/npc/dushe" and row["status"] == "unconcious"
            for row in rows
        )
        text = "\n".join(line["text"] for line in _cmd(s, "look")["lines"])
        assert "毒蛇 <昏迷不醒>" in text
    finally:
        s.close()
