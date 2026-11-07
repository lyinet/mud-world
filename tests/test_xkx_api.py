"""xkx 文字壳回归：走/看/问/买卖、不可用出口、人话失败原因。"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from wsim.api import WorldSession

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = "xkx_tester"
WORLD_CONFIG = ROOT / "config" / "worlds" / "xkx.yaml"


def _session(tmp_path, world_config: Path | None = WORLD_CONFIG):
    s = WorldSession(
        ROOT,
        run_id="xkx-api-test",
        seed=42,
        npc_limit=30,
        run_dir=tmp_path / "run",
        content_root=ROOT / "content",
        world_config=world_config,
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


def test_look_shows_description_exits_directions_and_npcs(session):
    result = _cmd(session, "look")
    text = "\n".join(_texts(result))
    assert result["ok"] is True
    assert "【集市】" in text
    assert "集市上人声嘈杂" in text
    assert "北→书院" in text
    assert "未开放" not in text
    exits = {row["id"]: row for row in result["view"]["exits"]}
    assert exits["academy"]["direction"] == "north"
    assert exits["academy"]["direction_label"] == "北"
    assert exits["academy"]["available"] is True


def test_move_by_keyboard_direction(session):
    result = _cmd(session, "go 北")
    assert result["ok"] is True
    assert result["view"]["player"]["location"] == "academy"
    text = "\n".join(_texts(result))
    assert text.startswith("你往北走")
    assert "【书院】" in text
    # 到了书院，南边就是回集市的路。
    exits = {row["direction"]: row for row in result["view"]["exits"]}
    assert exits["south"]["id"] == "market"


def test_ask_present_npc_and_reply(session):
    result = _cmd(session, "ask 金满仓 最近有什么好货？")
    texts = _texts(result)
    assert result["ok"] is True
    assert any(text.startswith("你对金满仓说：") for text in texts)
    assert any(text.startswith("金满仓答：") for text in texts)


def test_ask_missing_npc_says_who_is_here(session):
    result = _cmd(session, "ask 张三 你好")
    assert result["ok"] is False
    text = "\n".join(_texts(result))
    assert "这里没有「张三」" in text
    assert "在场的有" in text
    assert "金满仓" in text


def test_buy_and_sell_roundtrip_changes_wallet(session):
    look = _cmd(session, "look")
    assert look["ok"] is True
    before_money = look["view"]["player"]["money"]
    before_qty = {row["item"]: row["qty"] for row in look["view"]["inventory"]}["flatbread"]

    bought = _cmd(session, "buy 炊饼 2")
    assert bought["ok"] is True
    bought_text = "\n".join(_texts(bought))
    assert "你买下炊饼×2" in bought_text
    qty = {row["item"]: row["qty"] for row in bought["view"]["inventory"]}["flatbread"]
    assert qty == before_qty + 2
    assert bought["view"]["player"]["money"] == before_money - 6

    sold = _cmd(session, "sell 炊饼 1")
    assert sold["ok"] is True
    assert "你卖出炊饼×1" in "\n".join(_texts(sold))
    assert sold["view"]["player"]["money"] == before_money - 4


def test_buy_unknown_item_is_human_readable(session):
    result = _cmd(session, "buy 玄铁令 2")
    assert result["ok"] is False
    text = "\n".join(_texts(result))
    assert "这里不卖「玄铁令」" in text
    assert "货架上有" in text
    assert "unknown_item" not in text


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

    for session in _session(tmp_path, config_path):
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
