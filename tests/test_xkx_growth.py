"""xkx 成长线与生死：bai / learn / practice / climb / jump / 死亡与复活。

覆盖两类证据：
- 纯动作层：在最小状态上直接调用 Action，锁死拒绝原因与 Effect 语义；
- 真实内容包：WorldSession + xkx 内容包，按玩家命令路径验证可玩性。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from wsim.actions.growth import BaiAction
from wsim.api import WorldSession
from wsim.api.xkx_content import load_xkx_content
from wsim.core.models import Intent
from wsim.world.effects import apply_effects
from wsim.world.state import Entity, WorldState

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = "xkx_tester"
WORLD_CONFIG = ROOT / "config" / "worlds" / "xkx.yaml"
XKX_CONTENT = ROOT / "content" / "xkx"

JIULOU = "/d/hangzhou/jiulou"
ROAD14 = "/d/hangzhou/road14"


def _session(tmp_path):
    cfg = yaml.safe_load(WORLD_CONFIG.read_text(encoding="utf-8")) or {}
    s = WorldSession(
        ROOT,
        run_id="xkx-growth-test",
        seed=42,
        npc_limit=0,
        run_dir=tmp_path / "run",
        content_root=XKX_CONTENT,
        world_config=WORLD_CONFIG,
        spawn=str(cfg.get("spawn") or JIULOU),
    )
    yield s
    s.close()


@pytest.fixture()
def session(tmp_path):
    yield from _session(tmp_path)


def _cmd(session, command: str):
    return session.xkx_command(ACCOUNT, {"account_id": ACCOUNT, "command": command})


def _texts(result):
    return "\n".join(line["text"] for line in result["lines"])


@pytest.fixture(scope="module")
def xkx_content():
    return load_xkx_content(XKX_CONTENT)


def _walk_to_road14(session) -> None:
    for loc in ("/d/hangzhou/road10", "/d/hangzhou/road11", "/d/hangzhou/road12", "/d/hangzhou/road13", ROAD14):
        moved = _cmd(session, "go " + loc)
        assert moved["ok"] is True, moved
    assert session.state.entities[f"player:{ACCOUNT}"].location == ROAD14


def _master_rows(content):
    return [raw for raw in content.npcs if raw.get("is_master")]


def _intent(actor: str, verb: str, **args) -> Intent:
    return Intent(actor=actor, verb=verb, args=args, tick=1, source="player")


def _bare_state(locations: dict) -> WorldState:
    return WorldState(locations=locations)


# ---------------------------------------------------------------- 拜师（bai）


def test_content_has_22_masters_and_one_sect(xkx_content):
    masters = _master_rows(xkx_content)
    assert len(masters) == 22
    assert xkx_content.world_stats["masters"] == 22
    sects = {str(raw.get("sect") or "") for raw in masters}
    sects.discard("")
    assert sects == {"honghua"}
    assert xkx_content.sects["honghua"]["name"] == "红花会"


def test_every_content_master_can_be_bai(xkx_content):
    """22 位师父逐一可拜：在只含该师父的房间里调用 BaiAction。"""
    for raw in _master_rows(xkx_content):
        loc = JIULOU
        locations = {loc: {"id": loc, "name": loc, "exits": []}}
        state = _bare_state(locations)
        player = Entity(
            id="player:p",
            name="游侠儿",
            role="player",
            location=loc,
            home=loc,
            status="alive",
        )
        master = Entity(
            id=str(raw["id"]),
            name=str(raw.get("name") or ""),
            role="npc",
            location=loc,
            status="alive",
        )
        state.entities[player.id] = player
        state.entities[master.id] = master
        action = BaiAction(xkx_content)
        action.preconditions(state, _intent(player.id, "bai", target=master.name))
        effects = action.apply(state, _intent(player.id, "bai", target=master.name), None)
        apply_effects(state, effects)
        assert state.entities[player.id].master == master.id
        assert player.id in state.entities[master.id].disciples
        if raw.get("sect"):
            assert state.entities[player.id].faction == f"sect:{raw['sect']}"
            faction = state.factions[f"sect:{raw['sect']}"]
            assert faction.name == "红花会"


def test_bai_real_session_sets_master_and_sect(session):
    _walk_to_road14(session)
    result = _cmd(session, "bai 骆冰")
    assert result["ok"] is True, result.get("reject_reason")
    assert "你向骆冰叩拜" in _texts(result)
    assert "红花会" in _texts(result)
    player = session.state.entities[f"player:{ACCOUNT}"]
    assert player.master == "/d/hangzhou/honghua/luo"
    assert player.faction == "sect:honghua"
    assert player.id in session.state.entities["/d/hangzhou/honghua/luo"].disciples
    # 可回放：只靠事件日志重建，师承与门派仍在。
    from wsim.api.persistence import replay_into

    replayed, _ = replay_into(session.store, session._world_locations())
    assert replayed.entities[player.id].master == "/d/hangzhou/honghua/luo"
    assert replayed.entities[player.id].faction == "sect:honghua"


def test_bai_rejections_are_explicit(session):
    # 在场但不是师父
    result = _cmd(session, "bai 店小二")
    assert result["ok"] is False
    assert "对方不是可以拜师的人物" in _texts(result)

    # 不在场
    result = _cmd(session, "bai 郭靖")
    assert result["ok"] is False
    assert "这里没有你说的这位师父" in _texts(result)

    _walk_to_road14(session)
    assert _cmd(session, "bai 骆冰")["ok"] is True

    # 同一位 → already_apprentice；换一位 → already_has_master
    result = _cmd(session, "bai 骆冰")
    assert result["ok"] is False
    assert "你已经是这位师父的弟子了" in _texts(result)
    result = _cmd(session, "bai 文泰来")
    assert result["ok"] is False
    assert "你已经另有师父" in _texts(result)
