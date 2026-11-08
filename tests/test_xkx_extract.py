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


HUMAN_RACE_LPC = """
void setup_human(object ob) {
  mapping my;
  my = ob->query_entire_dbase();
  if (undefinedp(my["str"])) my["str"] = 10 + random(21);
  if (undefinedp(my["con"])) my["con"] = 10 + random(21);
  if (undefinedp(my["dex"])) my["dex"] = 10 + random(21);
  if (undefinedp(my["int"])) my["int"] = 80 - my["str"] - my["con"] - my["dex"];
  if (undefinedp(my["per"])) my["per"] = 10 + random(21);
  if (undefinedp(my["kar"])) my["kar"] = 10 + random(21);
  if (undefinedp(my["sta"]) || my["sta"] == 0) my["sta"] = 10 + random(21);
  if (undefinedp(my["cps"]) || my["cps"] == 0) my["cps"] = 10 + random(21);
  if (undefinedp(my["cor"]) || my["cor"] == 0) my["cor"] = 10 + random(21);
}
"""


def _parse_test_npc(tmp_path, create_body: str) -> dict:
    race_dir = tmp_path / "adm" / "daemons" / "race"
    race_dir.mkdir(parents=True)
    (race_dir / "human.lpc").write_text(HUMAN_RACE_LPC, encoding="utf-8")
    ex = extract.Extractor(str(tmp_path), ["d"])
    text = "inherit NPC;\nvoid create()\n{\n" + create_body + "\n}\n"
    return ex._parse_npc("d/npc/test.lpc", "/d/npc/test", text, {extract.NPC_BASE})


def test_human_npc_defaults_to_human_race_and_rolls_eight_attrs(tmp_path):
    rec = _parse_test_npc(
        tmp_path,
        '  set_name("测试人", ({"tester"}));\n  set("gender", "男性");',
    )
    assert rec["race"] == "人类"
    assert rec["race_source"] == {"kind": "lpc", "path": "adm/daemons/chard.lpc", "line": 25}
    expected = {"str", "con", "dex", "per", "kar", "sta", "cps", "cor"}
    assert set(rec["attrs_roll"]) == expected
    assert all(span == [10, 30] for span in rec["attrs_roll"].values())
    assert rec["attrs"] == {}
    # int 不是掷骰，而是 human.lpc:63 的差集派生
    assert rec["attrs_roll"].get("int") is None
    assert rec["attrs_derive"]["int"]["kind"] == "difference"
    assert rec["attrs_derive"]["int"]["total"] == 80
    assert rec["attrs_derive"]["int"]["minus"] == ["str", "con", "dex"]
    assert rec["attrs_derive"]["int"]["source"]["line"] == 63


def test_human_npc_explicit_attr_is_not_rolled(tmp_path):
    rec = _parse_test_npc(
        tmp_path,
        '  set_name("测试人", ({"tester"}));\n  set("str", 30);\n  set("cor", 5);',
    )
    assert rec["attrs"]["str"] == 30
    assert rec["attrs"]["cor"] == 5
    assert "str" not in rec["attrs_roll"]
    assert "cor" not in rec["attrs_roll"]
    # str 显式后 int 仍派生（差集里用显式 str）
    assert "int" in rec["attrs_derive"]


def test_human_npc_explicit_int_disables_derive(tmp_path):
    rec = _parse_test_npc(
        tmp_path,
        '  set_name("测试人", ({"tester"}));\n  set("int", 28);',
    )
    assert rec["attrs"]["int"] == 28
    assert rec["attrs_derive"] == {}


def test_explicit_race_is_kept_and_uses_its_own_race_file(tmp_path):
    rec = _parse_test_npc(
        tmp_path,
        '  set_name("测试兽", ({"tester"}));\n  set("race", "走兽");',
    )
    assert rec["race"] == "走兽"
    assert rec["race_source"]["path"] == "d/npc/test.lpc"
    assert rec["attrs_derive"] == {}


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
        "skills_actions": [], "skills_stats": [], "quests": [],
    }
    diags = [{"rule": "W_NO_EXIT", "severity": "warning", "path": "a.lpc",
              "line": 1, "message": "x"}]
    counts = {"rooms_in_slice": 1}
    coverage = {
        "source": {"rooms_desc": {"hit": 1, "total": 1, "ratio": 1.0, "threshold": 0.96}},
        "total": {"rooms_desc": {"hit": 1, "total": 1, "ratio": 1.0, "threshold": 1.0}},
    }
    src = {"repo": "fluffos/xkx100", "commit": "c1b87db", "areas": ["hangzhou"]}
    first = emit.build_outputs(pack, diags, counts, coverage, src)
    second = emit.build_outputs(pack, diags, counts, coverage, src)
    assert first == second
    manifest = json.loads(first["manifest.json"])
    assert manifest["content_hash"]
    assert manifest["field_semantics"]["desc_source"]["authored"]
    assert manifest["coverage_semantics"]["total"]


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
    assert counts["desc_authored_items"] == 7
    assert counts["desc_authored_npcs"] == 4
    assert counts["skill_action_skills"] == 22
    assert counts["skill_action_entries"] == 247
    assert counts["skill_special_actions"] == 4
    assert counts["skill_action_gaps"] == 11
    assert counts["skill_action_unresolved"] == 0
    assert counts["skill_stat_rows"] == 48
    assert counts["npcs_with_drops"] == 3
    assert counts["drop_entries"] == 3
    assert counts["drops_guaranteed"] == 3
    assert counts["drops_random"] == 0
    assert counts["drops_unresolved_chance"] == 0
    assert counts["npcs_no_drops"] == 102
    errors = [d for d in emit.dedupe(diags) if d["severity"] == "error"]
    assert not errors
    assert counts["equipment_items_added"] == 32
    assert counts["equipment_items_unresolved"] == 0
    assert counts["equipment_refs_normalized"] == 3
    item_ids = {i["id"] for i in pack["items"]}
    for npc in pack["npcs"]:
        for slot in npc.get("equipment", []):
            assert slot["ref"] in item_ids, (npc["id"], slot)
            assert slot["ref"].startswith("/") and not slot["ref"].endswith(".lpc")
            item = next(i for i in pack["items"] if i["id"] == slot["ref"])
            assert item["name"]
            assert item["weight"] is not None and item["weight"] > 0
    # NPC 临时属性：只取 create()，值/来源可溯源，未知键不丢。
    # 这些计数随内容包的 apply 抽取口径变化（改内容/抽取规则需同步改这里）：
    # 45 = 34 个 set_temp(lpc) + 11 个永久 set("apply/…")(lpc_set)；
    # 102 条 = 78 条 lpc + 24 条 lpc_set；apply/attack 36 = 25 + 11，
    # apply/defense 34 = 23 + 11（切片内 39 = 28 + 11）。
    assert counts["temp_apply_npcs"] == 45
    assert counts["temp_apply_npcs_in_slice"] == 39
    assert counts["temp_apply_entries"] == 102
    assert counts["temp_apply_keys"]["apply/attack"] == 36
    assert counts["temp_apply_keys"]["apply/defense"] == 34
    assert counts["temp_apply_unknown_keys"] == [
        "armor", "is_rided_by", "is_riding", "qingshu", "shoupa",
    ]
    laohu = next(n for n in pack["npcs"] if n["id"] == "/d/hangzhou/npc/laohu")
    temps = {e["key"]: e for e in laohu["temp_apply"]}
    assert temps["apply/attack"]["value"] == 20000
    assert temps["apply/defense"]["value"] == 30000
    assert temps["apply/armor"]["value"] == 1000
    for npc in pack["npcs"]:
        for entry in npc["temp_apply"]:
            # lpc = create() 内 set_temp；lpc_set = create() 内永久 set("apply/…")。
            assert entry["source_kind"] in {"lpc", "lpc_set"}
            assert entry["source"]["path"] and entry["source"]["line"]
            assert entry["value"] is not None or entry["value_expr"] is not None
    # 掉落：空表必须带原因；掉落物必须在 items 表；概率不让引擎猜
    item_ids = {i["id"] for i in pack["items"]}
    holders = {n["id"] for n in pack["npcs"] if n["drops"]}
    assert holders == {
        "/d/hangzhou/npc/laohu", "/d/hangzhou/npc/wolfdog", "/d/shiliang/npc/fish",
    }
    for npc in pack["npcs"]:
        assert npc["drops_source"] in ("lpc", "none")
        if not npc["drops"]:
            assert npc["drops_note"]
        for drop in npc["drops"]:
            assert drop["item"] in item_ids
            assert drop["qty"] >= 1
            assert drop["chance"] == 1.0 and drop["chance_expr"] is None
            assert drop["landing"] == "ground"
            assert drop["source_landing"] in ("corpse", "ground")
            assert drop["source_kind"] == "lpc"
    for rid in ("/clone/medicine/vegetable/hugu", "/d/npc/m_weapon/tan/item/zhenzhu"):
        item = next(i for i in pack["items"] if i["id"] == rid)
        assert item["origin"] == "external"
        assert item["desc"] and item["desc_source"] == "lpc"
    # 招式表：来源标记三分，缺键必须显式 default + 0
    for skill in pack["skills_actions"]:
        for act in skill["actions"]:
            assert set(act["coefficients"]) == set(act["coefficient_source"])
            assert all(v in ("lpc", "derived", "default") for v in act["coefficient_source"].values())
            for key, src in act["coefficient_source"].items():
                if src == "default":
                    assert act["coefficients"][key] == 0
                    assert key in skill["coefficient_gaps"]
            assert act["name_source"] in ("lpc", "missing")
            assert act["text_source"] in ("lpc", "derived")
    bixie = next(s for s in pack["skills_actions"] if s["id"] == "bixie-jian")
    assert {e["flag"] for e in bixie["effects"]} == {"bixie/cizu", "bixie/ciwan"}
    for effect in bixie["effects"]:
        assert effect["requirements"]["parsed"]
        assert effect["requirements"]["skills"]
        assert all(s["min_level"] > 0 for s in effect["requirements"]["skills"])
        assert effect["requirements"]["weapon_skill_type"] == "sword"
        assert effect["duration"]["base"] > 0 and effect["duration"]["random"] > 0
        assert effect["requirements_source"] == "lpc"
    # 属性表：每行钩子五项齐全；未实现的显式 0，实现的带源码表达式
    for row in pack["skills_stats"]:
        assert row["category"] in ("force", "dodge", "weapon", "unarmed", "action", "knowledge")
        assert set(row["hooks"]) == {
            "force_hit", "victim_force_hit", "martial_hit", "victim_parry_hit", "weapon_hit",
        }
        for hook in row["hooks"].values():
            if hook["implemented"]:
                assert hook["value_kind"] == "code" and hook["expr"]
            else:
                assert hook["value"] == 0 and hook["value_kind"] == "default" and hook["reason"]
        assert row["max_level"]["value"] == 800 and row["max_level"]["reason"]
        assert row["curve"]["learn_bonus"]["source_kind"] in ("lpc", "default")
        assert row["curve"]["power_point"]["kind"] in ("constant", "rules", "expression")
    # 任务：只入库切片内可完成的 4 条；静态清单与切片外动态任务只留计数/原因
    assert counts["quests_total"] == 4
    assert counts["quests_dynamic"] == 2
    assert counts["quests_talk_gated"] == 2
    assert counts["quests_static_entries"] == 2039
    assert counts["quests_static_unique"] == 1397
    assert counts["quests_dynamic_total"] == 71
    assert counts["quests_dynamic_outside_slice"] == 69
    assert counts["quests_non_task"] == 5
    assert {q["id"] for q in pack["quests"]} == {
        "q_dynamic_jinshe_jian", "q_dynamic_shoupa", "q_atu_afeng", "q_wenyi_jinshe",
    }
    for quest in pack["quests"]:
        assert quest["source"]["path"] and quest["source"]["line"]
        assert quest["target"]["id"] and quest["required_item"]["id"]
        assert quest["required_item"]["name"]
    for qid in ("q_dynamic_jinshe_jian", "q_dynamic_shoupa"):
        quest = next(q for q in pack["quests"] if q["id"] == qid)
        assert quest["dynamic"] is True and quest["giver"] is None
        assert quest["reward"]["exp"] == {"base": 100, "random": 400}
        assert quest["reward"]["pot"] == {"formula": "exp/(random(n)+1)", "random": 10, "plus": 1}
        assert quest["reward"]["score"] == {"base": 50, "random": 50}
        assert quest["reward"]["formula_source"]["path"] == "adm/daemons/questd.lpc"
        assert quest["required_item"]["source"] == "dynamic_spawn"
    jinshe = next(q for q in pack["quests"] if q["id"] == "q_wenyi_jinshe")
    assert jinshe["reward"]["items"] == ["/clone/book/jinshe2", "/clone/book/jinshe3"]
    assert jinshe["reward"]["unlock_exits"] == ["/d/shiliang/jinshedong:exits/east"]
    atu = next(q for q in pack["quests"] if q["id"] == "q_atu_afeng")
    assert atu["giver"]["topic"] == "阿凤" and atu["giver"]["id"] == "/d/shiliang/npc/atu"
    assert atu["reward"]["note"] == "原版只给口述秘密，无经验/钱/物品"
    assert any(d["rule"] == "I_QUEST_STATIC_SKIPPED" for d in diags)
    assert any(d["rule"] == "I_QUEST_DYNAMIC_OUTSIDE_SLICE" for d in diags)
    # 房间/出口目标不允许悬空
    room_ids = {r["id"] for r in pack["rooms"]}
    assert all(e["to"] in room_ids for e in pack["exits"] if e.get("from_kind") != "boundary_stub")
    assert all(e["dir"] for e in pack["exits"])
    # 两条口径：source 只认 lpc；total 含 authored 必须 100%
    assert set(ex.coverage) == {"source", "total"}
    assert ex.coverage["source"]["items_desc"]["ratio"] >= 0.82
    assert ex.coverage["source"]["npcs_desc"]["ratio"] >= 0.90
    assert ex.coverage["source"]["rooms_desc"]["ratio"] >= 0.96
    assert ex.coverage["source"]["room_exits"]["ratio"] >= 0.97
    assert ex.coverage["source"]["dialogue_text"]["ratio"] >= 0.63
    for data in ex.coverage["total"].values():
        assert data["ratio"] == 1.0
    # 自撰条目必须逐条标 authored，且不混进 source 计数
    authored = [
        r for r in pack["items"] + pack["npcs"]
        if r.get("origin") == "slice" and r.get("desc_source") == "authored"
    ]
    assert len(authored) == 11
    assert all(r["desc"] for r in authored)
    assert all(r.get("desc_source") in ("lpc", "authored") for r in pack["items"] + pack["npcs"] if r.get("origin") == "slice")


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
