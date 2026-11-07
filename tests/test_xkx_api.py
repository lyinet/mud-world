"""xkx 文字壳回归：真实内容包、走/看/问/买卖、不可用出口、人话失败原因。"""
from __future__ import annotations

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
    assert "92 位 NPC" in text


def test_content_counts_match_pack_exactly(session):
    stats = session.content.world_stats
    assert len(session.content.locations) == 195
    assert len(session.content.npcs) == 92
    assert stats["rooms_total"] == 201
    assert stats["rooms_in_slice"] == 195
    assert stats["rooms_boundary"] == 6
    assert stats["npcs_in_slice"] == 92
    assert stats["npcs_total"] == 105
    assert stats["masters"] == 22
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
    assert "地上散落：大石头、鹅卵石" in text


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
    assert "不明白「跳舞」" in text
    assert "help" in text
