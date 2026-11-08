"""xkx 文字壳回归：真实内容包、走/看/问/买卖、不可用出口、人话失败原因。"""
from __future__ import annotations

import re
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml

from wsim.api import WorldSession

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = "xkx_tester"
WORLD_CONFIG = ROOT / "config" / "worlds" / "xkx.yaml"
XKX_CONTENT = ROOT / "content" / "xkx"


def _session(tmp_path, world_config: Path | None = WORLD_CONFIG, content_root: Path = XKX_CONTENT):
    spawn = "market"
    if world_config is not None:
        cfg = yaml.safe_load(world_config.read_text(encoding="utf-8")) or {}
        spawn = str(cfg.get("spawn") or "market")
    s = WorldSession(
        ROOT,
        run_id="xkx-api-test",
        seed=42,
        npc_limit=0,
        run_dir=tmp_path / "run",
        content_root=content_root,
        world_config=world_config,
        spawn=spawn,
    )
    yield s
    s.close()


@pytest.fixture()
def session(tmp_path):
    yield from _session(tmp_path)


def _cmd(session, command: str, **extra):
    return session.xkx_command(ACCOUNT, {"account_id": ACCOUNT, "command": command, **extra})


def _texts(result):
    return [line["text"] for line in result["lines"]]


def test_help_lists_all_required_commands(session):
    result = _cmd(session, "help")
    text = "\n".join(_texts(result))
    for verb in ("look", "go", "ask", "buy", "fight", "loot", "score", "inventory"):
        assert verb in text
    assert "195 间房" in text
    assert "105 位 NPC" in text


def test_content_counts_match_pack_exactly(session):
    stats = session.content.world_stats
    assert len(session.content.locations) == 195
    assert len(session.content.npcs) == 105
    assert stats["rooms_total"] == 201
    assert stats["rooms_in_slice"] == 195
    assert stats["rooms_boundary"] == 6
    assert stats["npcs_in_slice"] == 105
    assert stats["npcs_total"] == 105
    assert stats["masters"] == 24
    assert stats["shops"] == 3
    assert stats["exits_total"] == 427
    assert stats["exits_from_slice"] == 421
    assert stats["exits_walkable"] == 424
    assert stats["exits_walkable_targets"] == 408
    assert stats["exits_derived"] == 9
    assert stats["exits_blocked"] == 6


def test_look_shows_real_description_exits_directions_and_npcs(session):
    result = _cmd(session, "look")
    text = "\n".join(_texts(result))
    assert result["ok"] is True
    assert "【楼外楼】" in text
    assert "各式名菜是杭州一绝" in text
    assert "西→青石大道" in text
    assert "上→相约轩" in text
    assert "在场人物：店小二" in text
    assert "货架：百果油包 70文" in text
    exits = {row["direction"]: row for row in result["view"]["exits"]}
    assert exits["west"]["name"] == "青石大道"
    assert exits["west"]["available"] is True
    assert exits["up"]["name"] == "相约轩"


def test_move_by_keyboard_direction(session):
    result = _cmd(session, "go 西")
    assert result["ok"] is True
    assert result["view"]["player"]["location"] == "/d/hangzhou/road10"
    text = "\n".join(_texts(result))
    assert text.startswith("你往西走")
    assert "【青石大道】" in text
    # 到了青石大道，东边就是回楼外楼的路。
    exits = {row["direction"]: row for row in result["view"]["exits"]}
    assert exits["east"]["id"] == "/d/hangzhou/jiulou"


def test_ground_items_are_visible(session):
    # 楼外楼 → 西、北、西、北上 → 宝石山，地上有大石头与鹅卵石。
    for direction in ("西", "北", "西", "北上"):
        moved = _cmd(session, "go " + direction)
        assert moved["ok"] is True, direction
    result = _cmd(session, "look")
    text = "\n".join(_texts(result))
    assert "【宝石山】" in text
    assert "地上散落：" in text
    assert "大石头" in text and "鹅卵石" in text


def test_ask_present_npc_and_reply(session):
    result = _cmd(session, "ask 店小二 最近有什么好菜？")
    texts = _texts(result)
    assert result["ok"] is True
    assert any(text.startswith("你对店小二说：") for text in texts)
    assert any(text.startswith("店小二答：") for text in texts)


def test_ask_missing_npc_says_who_is_here(session):
    result = _cmd(session, "ask 张三 你好")
    assert result["ok"] is False
    text = "\n".join(_texts(result))
    assert "这里没有「张三」" in text
    assert "在场的有" in text
    assert "店小二" in text


def _state(session):
    # player_state 要求账号已进入世界；先发一条 look，和真实客户端一致。
    _cmd(session, "look")
    return session.player_state(ACCOUNT)


def test_player_state_exposes_vitals_attrs_exp_weight(session):
    state = _state(session)
    player = state["player"]
    ent = session.state.entities[f"player:{ACCOUNT}"]

    vitals = player["vitals"]
    assert set(vitals) >= {"hp", "max_hp", "hp_pct", "neili", "neili_reason"}
    if ent.max_hp:
        assert vitals["hp"] == ent.hp
        assert vitals["max_hp"] == ent.max_hp
        assert vitals["hp_pct"] is not None
    else:
        assert vitals["hp"] is None and vitals["max_hp"] is None
        assert vitals["hp_reason"]
    # 引擎没有内力字段：必须 null + 原因，不能编造数值。
    assert vitals["neili"] is None
    assert vitals["neili_reason"]

    assert player["combat_exp"] == ent.combat_exp
    labels = {row["label"] for row in player["attrs_detail"]}
    assert {"膂力", "根骨", "身法", "悟性", "精神", "耐力"} <= labels
    assert player["attrs_reason"] == ("" if ent.attrs else player["attrs_reason"])

    weight = player["weight"]
    from wsim.actions.common import max_encumbrance

    assert weight["max"] == max_encumbrance(ent)
    assert weight["max"] > 0
    assert weight["reason"] == ""
    expected = 0
    for row in player["wallet"]["items"]:
        meta = session.content.items.get(row["item"])
        if meta and isinstance(meta.get("weight"), (int, float)):
            expected += int(meta["weight"]) * int(row["qty"])
    for slot in player["equipment"]["slots"]:
        if slot.get("empty"):
            continue
        meta = session.content.items.get(slot.get("item"))
        if meta and isinstance(meta.get("weight"), (int, float)):
            expected += int(meta["weight"])
    assert weight["current"] == expected


def test_player_state_reports_skills_or_reason(session):
    player = _state(session)["player"]
    status = player["skills_status"]
    assert isinstance(player["skills_engine"], list)
    assert isinstance(player["skills_detail"], list)
    if player["skills_engine"] or player["skills_detail"]:
        assert status["available"] is True
    else:
        assert status["available"] is False
        assert status["reason"]


def test_player_state_has_quests_shape(session):
    state = _state(session)
    assert isinstance(state.get("active_quests_detail"), list)
    player = state["player"]
    assert isinstance(player.get("active_quests"), list)


def test_equipment_slots_and_actions_are_explicit(session):
    player = _state(session)["player"]
    equipment = player["equipment"]
    assert isinstance(equipment["slots"], list) and equipment["slots"]
    actions = equipment["actions"]
    assert actions["equip"] is True and actions["unequip"] is True
    assert actions["wear"] is True and actions["wield"] is True
    assert actions["reason"] == ""
    # 内容包 equip_slot 已接入：槽位可见且可用。
    assert equipment["xkx_slots"]
    assert equipment["xkx_slots_available"] is True
    assert equipment["xkx_slots_reason"] == ""


def test_exploration_counts_grow_after_moves(session):
    before = _state(session)["player"]["exploration"]
    assert before["rooms_total"] == 195
    assert before["rooms_explored"] >= 1
    if before["area"] is not None:
        assert before["area_total"] > 0
        assert before["area_explored"] >= 1
    else:
        assert before["area_reason"]

    moved = _cmd(session, "go 西")
    assert moved["ok"] is True
    after = _state(session)["player"]["exploration"]
    assert after["rooms_explored"] >= before["rooms_explored"] + 1
    if after["area"] is not None:
        assert after["area_explored"] >= before["area_explored"]


def test_help_catalog_has_153_commands_and_32_topics(session):
    catalog = session.xkx_help_catalog()
    counts = catalog["counts"]
    assert counts["commands"] == 153
    assert counts["topics"] == 32
    assert counts["status"] == {"已实现": 41, "待实现": 51, "不做": 61}
    assert len(catalog["commands"]) == 153
    assert len(catalog["topics"]) == 32
    look = next(row for row in catalog["commands"] if row["name"] == "look")
    baitan = next(row for row in catalog["commands"] if row["name"] == "baitan")
    assert look["shell"] is True
    # baitan 在帮助里是“已实现”，但本文字壳没接入口，绝不能因此说可用。
    assert baitan["shell"] is False


def test_help_command_status_and_topic_body(session):
    catalog = session.xkx_help_catalog()
    pending = next(row for row in catalog["commands"] if row["status"] == "待实现")
    text = "\n".join(_texts(_cmd(session, "help 命令 战斗")))
    assert "待实现" in text
    assert "本壳未接" in text

    topic = next(
        row for row in catalog["topics"] if row["status"] in ("开放", "部分开放")
    )
    doc = yaml.safe_load((ROOT / "content" / "xkx_help" / "topics.yaml").read_text(encoding="utf-8"))
    body = next(
        str(item.get("body") or "")
        for item in doc["topics"]
        if str(item.get("id")) == topic["id"]
    )
    topic_text = "\n".join(_texts(_cmd(session, f"help {topic['id']}")))
    assert topic["title"] in topic_text
    if body.strip():
        assert body.strip().splitlines()[0].strip() in topic_text

    admin = next((row for row in catalog["topics"] if row["status"] == "管理"), None)
    if admin:
        admin_text = "\n".join(_texts(_cmd(session, f"help {admin['id']}")))
        assert "不向玩家开放" in admin_text


def test_unknown_command_says_dont_understand(session):
    text = "\n".join(_texts(_cmd(session, "zzz")))
    assert "你没听懂" in text
    assert "help" in text

    baitan = "\n".join(_texts(_cmd(session, "baitan")))
    assert "还没接上" in baitan or "未接入口" in baitan
    assert "待实现" in baitan or "已实现" in baitan
    # 不能把未接入口的命令写成可以执行。
    assert "本文字壳未接入口" in baitan


def test_buy_and_sell_roundtrip_changes_wallet(session):
    look = _cmd(session, "look")
    assert look["ok"] is True
    before_money = look["view"]["player"]["money"]

    bought = _cmd(session, "buy 东坡肉 2")
    assert bought["ok"] is True
    assert "你买下东坡肉×2" in "\n".join(_texts(bought))
    qty = {row["item"]: row["qty"] for row in bought["view"]["inventory"]}["/d/hangzhou/npc/obj/pork"]
    assert qty == 2
    assert bought["view"]["player"]["money"] == before_money - 240

    sold = _cmd(session, "sell 东坡肉 1")
    assert sold["ok"] is True
    assert "你卖出东坡肉×1" in "\n".join(_texts(sold))
    assert sold["view"]["player"]["money"] > bought["view"]["player"]["money"]


def test_buy_unknown_item_is_human_readable(session):
    result = _cmd(session, "buy 玄铁令 2")
    assert result["ok"] is False
    text = "\n".join(_texts(result))
    assert "这里不卖「玄铁令」" in text
    assert "货架上有" in text
    assert "unknown_item" not in text


def test_boundary_exit_stays_visible_but_blocked(session):
    # 楼外楼走到孤山：西、北、西南、西、西南、西（真实出口路线）。
    for direction in ("西", "北", "西南", "西", "西南", "西"):
        moved = _cmd(session, "go " + direction)
        assert moved["ok"] is True, direction
    result = _cmd(session, "look")
    blocked = [row for row in result["view"]["exits"] if not row["available"]]
    assert blocked
    assert blocked[0]["direction"] == "westup"
    assert blocked[0]["name"] == "孤山石阶"
    assert "内容边界" in blocked[0]["reason"]
    assert "未开放出口" in "\n".join(_texts(result))

    moved = _cmd(session, "go westup")
    assert moved["ok"] is False
    text = "\n".join(_texts(moved))
    assert "走不过去" in text
    assert "内容边界" in text
    # 被拒绝后玩家没有被挪走，也没有被困死。
    assert moved["view"]["player"]["location"] == result["view"]["player"]["location"]
    assert [row for row in moved["view"]["exits"] if row["available"]]


def test_closed_exit_stays_visible_with_reason(tmp_path):
    config = {
        "exit_directions": {
            "market": {
                "north": "academy",
                "east": "smithy",
                "south": "pharmacy",
                "west": "inn",
                "northeast": "teahouse",
            }
        },
        "closed_exits": {
            "market": [
                {
                    "direction": "northwest",
                    "target": "broken_bridge",
                    "name": "断桥",
                    "reason": "栈道还没修好。",
                }
            ]
        },
    }
    config_path = tmp_path / "world.yaml"
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")

    for session in _session(tmp_path, config_path, content_root=ROOT / "content"):
        result = _cmd(session, "look")
        assert result["ok"] is True
        blocked = [row for row in result["view"]["exits"] if not row["available"]]
        assert blocked
        assert blocked[0]["name"] == "断桥"
        assert blocked[0]["direction"] == "northwest"
        assert "栈道还没修好" in blocked[0]["reason"]
        assert "未开放出口" in "\n".join(_texts(result))

        moved = _cmd(session, "go 断桥")
        assert moved["ok"] is False
        assert "栈道还没修好" in "\n".join(_texts(moved))


def test_unknown_command_does_not_leak_codes(session):
    result = _cmd(session, "跳舞")
    assert result["ok"] is False
    text = "\n".join(_texts(result))
    assert "你没听懂「跳舞」" in text
    assert "help" in text


def _first_damages(text: str, count: int = 2) -> list[int]:
    return [int(v) for v in re.findall(r"造成(\d+)点伤害", text)[:count]]


@contextmanager
def _isolated_session(run_dir: Path, world_config: Path):
    gen = _session(run_dir, world_config=world_config)
    session = next(gen)
    try:
        yield session
    finally:
        session.close()


def test_fight_reports_real_rounds_and_death(tmp_path):
    # 楼外楼只有店小二。世界开启 `combat.lethal_fight` 后 `fight` 是切磋（半血即止），
    # 要拿“死亡/超额伤害”战报必须用 `kill`；seed=42 下玩家取胜。
    #
    # 两态都要断言：开启 `eff_qi` 后每场战斗开头两方各抽一次 heal_cd
    # （`combat_rules.py:433`），骰流前移 2，首击由关闭态 15 变 19。
    # 只断言开启态会变成“实现恒定输出 19 也绿”的假通过；这里用同 seed 的
    # 开/关两态交叉，任一态的实现回退都会让另一条断言红。
    cfg = yaml.safe_load(WORLD_CONFIG.read_text(encoding="utf-8")) or {}
    cfg["combat"]["eff_qi"] = False
    off_config = tmp_path / "xkx-eff-off.yaml"
    off_config.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")

    with _isolated_session(tmp_path / "on", WORLD_CONFIG) as on_session:
        on_result = _cmd(on_session, "kill 店小二")
    with _isolated_session(tmp_path / "off", off_config) as off_session:
        off_result = _cmd(off_session, "kill 店小二")

    assert on_result["ok"] is True, on_result.get("reject_reason")
    assert off_result["ok"] is True, off_result.get("reject_reason")
    on_text = "\n".join(_texts(on_result))
    off_text = "\n".join(_texts(off_result))
    for text in (on_text, off_text):
        assert "第1回合" in text
        # 招式接入后，人类默认招式以 `combat_action` 文案展示，不再固定“击中X，伤害N”。
        assert "挥拳攻击店小二" in text
        assert "你赢了，店小二倒下了" in text
        # “超额伤害”战报行已在主线 e3f17a9 删除（原版 damage.lpc:37 返回请求伤害、
        # combatd.lpc:569 只显示本次伤害，这行是自造的），故不再断言。
    # 必须按“首击/首两击序列”判，不能按全文出现与否：关闭态后续也会掷出
    # 19（本 seed 第 4 刀），全文判“出现/不出现”两态都会假绿。
    assert _first_damages(on_text) == [19, 21]
    assert _first_damages(off_text) == [15, 14]
    assert _first_damages(on_text)[0] != _first_damages(off_text)[0]

    on_combat = on_result["engine_details"]["combat"]
    off_combat = off_result["engine_details"]["combat"]
    assert on_combat["winner"] == "attacker"
    assert int(on_combat["rounds_used"]) == len(on_combat["rounds"])
    # 两态确实跑了不同的骰流，不是把同一份结果断言两遍。
    assert int(on_combat["rounds_used"]) != int(off_combat["rounds_used"])
    assert on_result["view"]["combat_ready"]["fight"] is True


def test_loot_and_get_move_ground_items_into_wallet(session):
    # 楼外楼 → 西、北、西、北上 → 宝石山，创世已把内容摆放物落成地面。
    for direction in ("西", "北", "西", "北上"):
        moved = _cmd(session, "go " + direction)
        assert moved["ok"] is True, direction
    look = _cmd(session, "look")
    assert "地上散落：" in "\n".join(_texts(look))

    first = _cmd(session, "get 大石头")
    assert first["ok"] is True
    text = "\n".join(_texts(first))
    assert "捡起：大石头" in text
    assert "地上还剩：鹅卵石" in text
    qty = {row["item"]: row["qty"] for row in first["view"]["inventory"]}
    assert qty["/d/hangzhou/obj/shitou"] >= 1

    second = _cmd(session, "get 鹅卵石")
    assert second["ok"] is True, second.get("reject_reason")
    assert "捡起：鹅卵石" in "\n".join(_texts(second))
    assert "地上已经空了" in "\n".join(_texts(second))
    # 捡完地面必须真消失，不能被内容包静态摆放重新长回地上。
    after = _cmd(session, "look")
    assert "地上散落" not in "\n".join(_texts(after))
    qty_after = {row["item"]: row["qty"] for row in after["view"]["inventory"]}
    assert qty_after["/d/hangzhou/obj/eluanshi"] >= 1
    assert qty_after["/d/hangzhou/obj/shitou"] >= 1

    # 没有击杀来源时，loot 必须明确提示，而不是静默成功。
    empty = _cmd(session, "loot")
    assert empty["ok"] is False
    assert "这里没有刚死的东西可搜刮" in "\n".join(_texts(empty))


def test_get_all_reports_total_count(session):
    for direction in ("西", "北", "西", "北上"):
        assert _cmd(session, "go " + direction)["ok"] is True
    taken = _cmd(session, "get all")
    assert taken["ok"] is True, taken.get("reject_reason")
    text = "\n".join(_texts(taken))
    assert "捡起 2 件" in text
    assert "大石头" in text and "鹅卵石" in text
    assert "地上已经空了" in text


def test_loot_named_item_missing_lists_what_is_here(session):
    for direction in ("西", "北", "西", "北上"):
        _cmd(session, "go " + direction)
    result = _cmd(session, "loot 金锁")
    assert result["ok"] is False
    text = "\n".join(_texts(result))
    # 没有击杀来源时按 loot 语义提示，不会误碰地面物品。
    assert "这里没有刚死的东西可搜刮" in text


def test_item_name_matching_is_substring(session):
    """`loot/drop/wear/buy/sell` 共用一套匹配：精确优先，其次子串。"""
    for direction in ("西", "北", "西", "北上"):
        _cmd(session, "go " + direction)
    # 「石头」不是「大石头」的前缀，只能靠子串命中。
    hit = _cmd(session, "get 石头")
    assert hit["ok"] is True, hit.get("reject_reason")
    assert "捡起：大石头" in "\n".join(_texts(hit))

    # 多件同时命中时按行序取第一件（原版 present() 语义），不报歧义：
    # 此时地上只剩鹅卵石，「石」在 鹅卵石/大石头 里都命中过。
    ambiguous = _cmd(session, "get 石")
    assert ambiguous["ok"] is True, ambiguous.get("reject_reason")
    assert "捡起：鹅卵石" in "\n".join(_texts(ambiguous))

