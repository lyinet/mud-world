"""xkx 内容抽取器的单测与切片集成测试。

集成测试依赖本地源码（`tools/xkx_extract/_src/xkx100`）；源码未 clone 时自动 skip。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from xkx_extract import emit, extract, lpc, validate  # noqa: E402

SRC = REPO_ROOT / "tools" / "xkx_extract" / "_src" / "xkx100"


def _ctx(rel: str) -> lpc.ParseContext:
    return lpc.ParseContext(lpc.mudlib_id(rel), lpc.mudlib_dir(rel), {})


def test_heredoc_and_macro_expansion():
    text = """
inherit ROOM;
void create() {
  set("short", "大道");
  set("long", @LONG
一条大道。
LONG);
  set("exits", ([
    "north": __DIR__ "road5",
    "up": SHOP_DIR "hangzhou_shop",
  ]));
}
"""
    calls, errors = lpc.scan_calls(text, _ctx("d/hangzhou/dadao1.lpc"))
    assert not errors
    values = {args[0]: args[1] for name, args, _line, _m in calls if name == "set"}
    assert values["short"] == "大道"
    assert values["long"] == "一条大道。"
    assert values["exits"]["north"] == "/d/hangzhou/road5"
    assert values["exits"]["up"] == "/clone/shop/hangzhou_shop"


def test_color_macros_are_stripped():
    text = """
inherit NPC;
void create() {
  set_name(HIY "百果油包" NOR, ({ "baozi" }));
  set("title", HIR "红花会" HIG "总舵主" NOR);
}
"""
    calls, _ = lpc.scan_calls(text, _ctx("d/hangzhou/npc/obj/baozi.lpc"))
    assert calls[0][1][0] == "百果油包"
    assert calls[1][1][1] == "红花会总舵主"


def test_function_pointer_and_chained_call_do_not_crash():
    text = """
inherit NPC;
void create() {
  set("inquiry", ([ "自宫": (: ask_for_zigong :) ]));
  set("order_owner", me->query("id"));
  set("combat_exp", this_player()->query("combat_exp") + 1000);
}
"""
    calls, errors = lpc.scan_calls(text, _ctx("d/jiaxing/npc/hai.lpc"))
    assert not errors
    inquiry = {a[0]: a[1] for name, a, _l, _m in calls if name == "set"}["inquiry"]
    assert isinstance(inquiry["自宫"], lpc.Expr)
    assert "ask_for_zigong" in inquiry["自宫"].text


def test_clean_text_strips_ansi_and_trailing_space():
    assert lpc.clean_text("\x1b[1;31m红\x1b[2;37;0m\n  ") == "红"


def test_validate_flags_dangling_and_duplicate():
    rooms = [
        {"id": "/a", "name": "A", "desc": "d", "origin": "slice",
         "source": {"path": "a.lpc", "line": 1}},
        {"id": "/a", "name": "A2", "desc": "d", "origin": "slice",
         "source": {"path": "a2.lpc", "line": 1}},
        {"id": "/b", "name": "B", "desc": None, "origin": "slice",
         "source": {"path": "b.lpc", "line": 1}},
    ]
    exits = [
        {"from": "/a", "dir": "north", "to": "/missing", "line": 2},
    ]
    diags = validate.validate({
        "rooms": rooms, "exits": exits, "items": [], "npcs": [],
        "shops": [], "skills": [], "dialogue": [], "sects": [],
    })
    rules = {d["rule"] for d in diags}
    assert "E_DANGLING_EXIT" in rules
    assert "E_DUP_ROOM_ID" in rules


def test_build_outputs_is_bytewise_deterministic():
    pack = {
        "rooms": [{"id": "/a", "name": "A", "desc": "d", "origin": "slice",
                   "source": {"path": "a.lpc", "line": 1}, "objects": [], "flags": {}, "coord": {}, "kind": "room"}],
        "exits": [], "items": [], "npcs": [], "dialogue": [],
        "shops": [], "sects": [], "skills": [],
    }
    diags = [{"rule": "W_NO_EXIT", "severity": "warning", "path": "a.lpc",
              "line": 1, "message": "x"}]
    counts = {"rooms_in_slice": 1}
    coverage = {"rooms_desc": {"hit": 1, "total": 1, "ratio": 1.0, "target": 0.961}}
    src = {"repo": "fluffos/xkx100", "commit": "c1b87db", "areas": ["hangzhou"]}
    first = emit.build_outputs(pack, diags, counts, coverage, src)
    second = emit.build_outputs(pack, diags, counts, coverage, src)
    assert first == second
    manifest = json.loads(first["manifest.json"])
    assert manifest["content_hash"]


@pytest.mark.skipif(not SRC.is_dir(), reason="未 clone xkx100 源码")
def test_slice_pack_counts_and_zero_errors():
    ex = extract.Extractor(str(SRC), extract.AREA_DIRS)
    pack = ex.run()
    diags = list(ex.diagnostics) + validate.validate(pack)
    counts = ex.counts()
    assert counts["rooms_in_slice"] == 195
    assert counts["npcs_in_slice"] == 92
    assert counts["masters"] == 22
    assert counts["aggressive_npcs"] == 7
    assert counts["shops"] == 3
    assert counts["sects"] == 1
    assert counts["skills"] == 48
    assert counts["dialogue_topics_in_slice"] == 40
    errors = [d for d in emit.dedupe(diags) if d["severity"] == "error"]
    assert not errors
    # 房间/出口目标不允许悬空
    room_ids = {r["id"] for r in pack["rooms"]}
    assert all(e["to"] in room_ids for e in pack["exits"] if e.get("from_kind") != "boundary_stub")
    assert all(e["dir"] for e in pack["exits"])


@pytest.mark.skipif(not SRC.is_dir(), reason="未 clone xkx100 源码")
def test_slice_pack_is_idempotent(tmp_path):
    ex = extract.Extractor(str(SRC), extract.AREA_DIRS)
    pack = ex.run()
    diags = list(ex.diagnostics) + validate.validate(pack)
    counts = ex.counts()
    src = {"repo": "fluffos/xkx100", "commit": "c1b87db", "areas": list(extract.AREA_DIRS)}
    files = emit.build_outputs(pack, diags, counts, ex.coverage, src)
    emit.write_outputs(str(tmp_path), files)
    diffs = emit.verify_outputs(str(tmp_path), files)
    assert diffs == []
    assert os.path.isfile(tmp_path / "manifest.json")
