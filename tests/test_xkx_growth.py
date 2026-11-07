"""xkx 成长线与生死：bai / learn / practice / climb / jump / 死亡与复活。

覆盖两类证据：
- 纯动作层：在最小状态上直接调用 Action，锁死拒绝原因与 Effect 语义；
- 真实内容包：WorldSession + xkx 内容包，按玩家命令路径验证可玩性。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from wsim.actions.growth import BaiAction, ClimbAction, JumpAction, LearnAction, PracticeAction
from wsim.api import WorldSession
from wsim.api.xkx_content import load_xkx_content
from wsim.core.models import Intent, Reject
from wsim.core.rng import PRNG
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


# ------------------------------------------------------- 学习 / 练习（learn / practice）


class RecordingPRNG(PRNG):
    """记录每次 randint 的区间，锁死原版取数顺序。"""

    def __init__(self, seed: int, seq: int, salt: str = ""):
        super().__init__(seed, seq, salt)
        self.calls: list[tuple[int, int]] = []

    def randint(self, a: int, b: int) -> int:
        self.calls.append((a, b))
        return super().randint(a, b)


def _study_state(content, *, master_id: str = "/d/hangzhou/honghua/luo", **fields):
    loc = JIULOU
    state = WorldState(locations={loc: {"id": loc, "name": loc, "exits": []}})
    master = Entity(id=master_id, name="骆冰", role="fighter", location=loc, status="alive")
    defaults = {
        "attrs": {"int": 18},
        "skill_levels": {"dodge": 10},
        "skill_xp": {},
        "combat_exp": 250,
        "potential": 99,
        "learned_points": 0,
        "stamina": 100.0,
        "master": master_id,
        "home": loc,
    }
    defaults.update(fields)
    player = Entity(id="player:p", name="游侠儿", role="player", location=loc, **defaults)
    state.entities[player.id] = player
    state.entities[master.id] = master
    return state, player, master


def test_learn_locks_prng_order_and_exact_result(xkx_content):
    state, player, master = _study_state(xkx_content)
    rng = RecordingPRNG(42, 3, "learn")
    intent = _intent(player.id, "learn", teacher=master.name, skill="dodge", times=8)
    action = LearnAction(xkx_content)
    action.preconditions(state, intent)
    effects = action.apply(state, intent, rng)
    apply_effects(state, effects)

    # 原版 learn.lpc：每次先 random(improve_points)，再做 (level+1)^2 进位。
    # improve_points = (int+int)/2 + learn_bonus(30) = 48，所以区间恒为 0..47。
    assert rng.calls == [(0, 47)] * 8
    ent = state.entities[player.id]
    assert ent.skill_levels["dodge"] == 11
    assert ent.skill_xp.get("dodge", 0) == 0
    assert ent.potential == 91
    assert ent.learned_points == 8
    assert ent.stamina == 36.0

    log = next(e.data for e in effects if e.op == "growth_log")
    assert log["action"] == "learn"
    assert log["level_before"] == 10 and log["level_after"] == 11
    assert log["jing_cost"] == 64


def test_learn_rejections_are_explicit(xkx_content):
    action = LearnAction(xkx_content)

    state, player, master = _study_state(xkx_content, master="/d/hangzhou/honghua/wen")
    with pytest.raises(Reject) as err:
        action.preconditions(state, _intent(player.id, "learn", teacher=master.name, skill="dodge", times=1))
    assert err.value.reason == "not_apprentice_of_teacher"

    state, player, master = _study_state(xkx_content)
    with pytest.raises(Reject) as err:
        action.preconditions(state, _intent(player.id, "learn", teacher=master.name, skill="dodge", times=100))
    assert err.value.reason == "insufficient_potential"

    state, player, master = _study_state(xkx_content, skill_levels={"dodge": 20})
    with pytest.raises(Reject) as err:
        action.preconditions(state, _intent(player.id, "learn", teacher=master.name, skill="dodge", times=1))
    assert err.value.reason == "insufficient_combat_exp"

    state, player, master = _study_state(xkx_content, stamina=20.0)
    with pytest.raises(Reject) as err:
        action.preconditions(state, _intent(player.id, "learn", teacher=master.name, skill="dodge", times=5))
    assert err.value.reason == "insufficient_jing"

    state, player, master = _study_state(xkx_content)
    with pytest.raises(Reject) as err:
        action.preconditions(state, _intent(player.id, "learn", teacher=master.name, skill="不会", times=1))
    assert err.value.reason == "unknown_skill"


def test_learn_real_session_persists_and_replays(session):
    _walk_to_road14(session)
    assert _cmd(session, "bai 骆冰")["ok"] is True
    result = _cmd(session, "learn 骆冰 dodge 5")
    assert result["ok"] is True, _texts(result)
    assert "你向骆冰请教" in _texts(result)
    player = session.state.entities[f"player:{ACCOUNT}"]
    assert player.potential == 94
    assert player.learned_points == 5
    assert int(player.skill_levels["dodge"]) >= 11

    from wsim.api.persistence import replay_into

    replayed, _ = replay_into(session.store, session._world_locations())
    ent = replayed.entities[player.id]
    assert ent.potential == 94
    assert ent.learned_points == 5
    assert int(ent.skill_levels["dodge"]) >= 11


def test_practice_locks_prng_order_and_exact_result(xkx_content):
    state, player, master = _study_state(
        xkx_content,
        skill_levels={"dodge": 10, "yunlong-shenfa": 1},
    )
    rng = RecordingPRNG(42, 5, "practice")
    intent = _intent(player.id, "practice", skill="yunlong-shenfa", times=4)
    action = PracticeAction(xkx_content)
    action.preconditions(state, intent)
    effects = action.apply(state, intent, rng)
    apply_effects(state, effects)

    # 原版 practice.lpc：span = (base^2)/(skill+1)/5 = 100/2/5 = 10。
    # 体力 100、每趟 30，实际只练 3 趟。
    assert rng.calls == [(0, 9)] * 3
    ent = state.entities[player.id]
    assert ent.skill_levels["yunlong-shenfa"] == 3
    assert ent.skill_xp["yunlong-shenfa"] == 14
    assert ent.stamina == 100.0 - 30 * 3
    log = next(e.data for e in effects if e.op == "growth_log")
    assert log["action"] == "practice"
    assert log["gains"] == [15, 11, 14]
    assert log["level_before"] == 1 and log["level_after"] == 3
    assert log["times"] == 3 and log["requested"] == 4 and log["partial"] is True


def test_practice_rejections_are_explicit(xkx_content):
    action = PracticeAction(xkx_content)

    state, player, _ = _study_state(xkx_content)
    with pytest.raises(Reject) as err:
        action.preconditions(state, _intent(player.id, "practice", skill="yunlong-shenfa", times=1))
    assert err.value.reason == "skill_not_learned"

    state, player, _ = _study_state(xkx_content, skill_levels={"dodge": 10, "yunlong-shenfa": 1}, stamina=10.0)
    with pytest.raises(Reject) as err:
        action.preconditions(state, _intent(player.id, "practice", skill="yunlong-shenfa", times=1))
    assert err.value.reason == "insufficient_stamina"

    state, player, _ = _study_state(
        xkx_content,
        skill_levels={"dodge": 40, "yunlong-shenfa": 20},
    )
    with pytest.raises(Reject) as err:
        action.preconditions(state, _intent(player.id, "practice", skill="yunlong-shenfa", times=1))
    assert err.value.reason == "insufficient_combat_exp"


# ------------------------------------------------------------- 攀爬 / 跳跃（climb / jump）

GATE = "/d/shiliang/gate"
TINGZI = "/d/shiliang/tingzi"


def _travel_state(loc: str, dodge: int) -> tuple[WorldState, Entity]:
    spots = [loc, GATE, TINGZI, "/d/shiliang/yuanzi", "/d/shiliang/huli", "/d/shiliang/xiaodao", JIULOU]
    locations = {spot: {"id": spot, "name": spot, "exits": []} for spot in spots}
    state = WorldState(locations=locations)
    player = Entity(
        id="player:p",
        name="游侠儿",
        role="player",
        location=loc,
        home=JIULOU,
        status="alive",
        skill_levels={"dodge": dodge},
    )
    state.entities[player.id] = player
    return state, player


def test_climb_requires_tree_target_and_dodge_50(xkx_content):
    action = ClimbAction(xkx_content)

    state, player = _travel_state(GATE, 10)
    with pytest.raises(Reject) as err:
        action.preconditions(state, _intent(player.id, "climb", arg="tree"))
    assert err.value.reason == "dodge_too_low"

    state, player = _travel_state(GATE, 60)
    with pytest.raises(Reject) as err:
        action.preconditions(state, _intent(player.id, "climb", arg="树"))
    assert err.value.reason == "climb_target_unknown"

    state, player = _travel_state(JIULOU, 60)
    with pytest.raises(Reject) as err:
        action.preconditions(state, _intent(player.id, "climb", arg="tree"))
    assert err.value.reason == "climb_wrong_place"

    state, player = _travel_state(GATE, 50)
    intent = _intent(player.id, "climb", arg="tree")
    action.preconditions(state, intent)
    apply_effects(state, action.apply(state, intent, None))
    assert state.entities[player.id].location == "/d/shiliang/yuanzi"

    # 原版 n>=80 是飞身跃过，50-79 是勉强翻墙；两者都到院子。
    state, player = _travel_state(GATE, 80)
    effects = action.apply(state, _intent(player.id, "climb", arg="tree"), None)
    apply_effects(state, effects)
    log = next(e.data for e in effects if e.op == "growth_log")
    assert log["graceful"] is True and log["to"] == "/d/shiliang/yuanzi"


def test_jump_muzhuang_requires_exact_target_and_sends_to_lake_or_island(xkx_content):
    action = JumpAction(xkx_content)

    state, player = _travel_state(TINGZI, 10)
    with pytest.raises(Reject) as err:
        action.preconditions(state, _intent(player.id, "jump", arg=""))
    assert err.value.reason == "jump_target_unknown"

    state, player = _travel_state(JIULOU, 60)
    with pytest.raises(Reject) as err:
        action.preconditions(state, _intent(player.id, "jump", arg="木桩"))
    assert err.value.reason == "jump_wrong_place"

    # 原版 dodge < 100 一律踩断木桩落湖，50-99 的描述在此处不额外加门槛。
    state, player = _travel_state(TINGZI, 60)
    intent = _intent(player.id, "jump", arg="木桩")
    action.preconditions(state, intent)
    effects = action.apply(state, intent, None)
    apply_effects(state, effects)
    log = next(e.data for e in effects if e.op == "growth_log")
    assert state.entities[player.id].location == "/d/shiliang/huli"
    assert log["fell"] is True and log["landing"] == "湖里"

    state, player = _travel_state(TINGZI, 100)
    effects = action.apply(state, _intent(player.id, "jump", arg="木桩"), None)
    apply_effects(state, effects)
    log = next(e.data for e in effects if e.op == "growth_log")
    assert state.entities[player.id].location == "/d/shiliang/xiaodao"
    assert log["fell"] is False and log["landing"] == "小岛"


def test_climb_and_jump_real_session_rejections_and_jump_to_lake(tmp_path):
    gate = WorldSession(
        ROOT, run_id="xkx-gate", seed=42, npc_limit=0, run_dir=tmp_path / "gate",
        content_root=XKX_CONTENT, world_config=WORLD_CONFIG, spawn=GATE,
    )
    try:
        result = _cmd(gate, "climb tree")
        assert result["ok"] is False
        assert "轻功还不到 50" in _texts(result)
        result = _cmd(gate, "climb 树")
        assert result["ok"] is False
        assert "climb tree" in _texts(result)
        result = _cmd(gate, "jump 木桩")
        assert result["ok"] is False
        assert "不是跳木桩的地方" in _texts(result)
    finally:
        gate.close()

    tingzi = WorldSession(
        ROOT, run_id="xkx-tingzi", seed=42, npc_limit=0, run_dir=tmp_path / "tingzi",
        content_root=XKX_CONTENT, world_config=WORLD_CONFIG, spawn=TINGZI,
    )
    try:
        result = _cmd(tingzi, "jump 木桩")
        assert result["ok"] is True, _texts(result)
        assert "跌进了湖里" in _texts(result)
        player = tingzi.state.entities[f"player:{ACCOUNT}"]
        assert player.location == "/d/shiliang/huli"
        fish = [e for e in tingzi.state.entities.values() if e.location == "/d/shiliang/huli" and "鱼" in e.name]
        assert fish, "落湖后应遇到噬人鱼（夜明珠掉落来源）"
    finally:
        tingzi.close()
