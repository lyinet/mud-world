"""P3-B 三槽位：意图功能表、中文参数映射、缺字段/否定/改口/破坏性确认。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from fake_intent_server import respond  # noqa: E402
from wsim.agent.intent_model import (  # noqa: E402
    load_intent_catalog,
    parse_intent_response,
)
from wsim.agent.prompt import NPC_VERBS  # noqa: E402

CATALOG = None


def _catalog(content):
    global CATALOG
    if CATALOG is None:
        CATALOG = load_intent_catalog(ROOT, content.verbs)
    return CATALOG


def _resolve(payload, content, state, npc="shen_yanzhou"):
    return parse_intent_response(
        json.dumps(payload, ensure_ascii=False), _catalog(content), content, state, npc
    )


def test_catalog_matches_whitelist_and_verbs(real_content):
    catalog = _catalog(real_content)
    catalog.validate_against_verbs(real_content.verbs)
    assert set(catalog.by_verb) == set(NPC_VERBS)
    assert len(catalog.templates) == 17
    for template in catalog.templates:
        assert template.required, f"{template.verb} required 不能为空"


def test_catalog_detects_verbs_mismatch(real_content):
    catalog = _catalog(real_content)
    broken = dict(real_content.verbs)
    broken["move"] = dict(broken["move"], args={"to": "string", "speed": "int"})
    try:
        catalog.validate_against_verbs(broken)
    except ValueError as exc:
        assert "speed" in str(exc)
    else:
        raise AssertionError("功能表与 verbs.yaml 脱节时必须测试失败")


def test_full_arguments_land(real_content, real_state):
    loc = real_content.locations["inn"]
    res = _resolve(
        {"category": "去某地", "args": [{"key": "地点", "value": loc["name"]}]},
        real_content, real_state,
    )
    assert res.status == "ok"
    assert res.verb == "move"
    assert res.args == {"to": "inn"}
    assert res.intent("shen_yanzhou", 10).source == "intent"


def test_missing_field_does_not_guess(real_content, real_state):
    res = _resolve({"category": "去某地", "args": []}, real_content, real_state)
    assert res.status == "needs_clarification"
    assert res.missing == ["destination"]
    assert res.intent("shen_yanzhou", 10) is None


def test_negation_does_not_execute(real_content, real_state):
    res = _resolve({"category": "none", "args": {}}, real_content, real_state)
    assert res.status == "reject"
    assert res.reason == "unknown_category"
    assert res.intent("shen_yanzhou", 10) is None


def test_unknown_category_rejected(real_content, real_state):
    res = _resolve({"category": "去天上", "args": {"地点": "南桥"}}, real_content, real_state)
    assert res.status == "reject"
    assert res.reason == "unknown_category"


def test_chinese_quantity_maps(real_content, real_state):
    item = real_content.items["wine"]
    res = _resolve(
        {"category": "吃饭", "args": [{"key": "物品", "value": item["name"]}, {"key": "数量", "value": "两瓶"}]},
        real_content, real_state,
    )
    assert res.status == "ok", res.to_dict()
    assert res.args == {"item": "wine", "qty": 2}


def test_destructive_requires_confirmation(real_content, real_state):
    target = real_content.npcs[1]["id"]
    res = _resolve(
        {"category": "婚配", "args": [{"key": "对象", "value": target}]},
        real_content, real_state,
    )
    assert res.status == "needs_confirmation"
    assert res.destructive is True
    assert res.intent("shen_yanzhou", 10) is None


def test_retraction_multiturn_changes_only_named_field(real_content, real_state):
    prior = json.dumps({"category": "去某地", "args": {"地点": "集市"}}, ensure_ascii=False)
    prompt = (
        "【前几轮对话】\n- " + prior + "\n\n"
        "【用户/上层说的话】\n改口，改成铁匠铺\n\n"
        "【系统支持哪些意图】\n去某地、上工\n\n"
        "【每个意图要填哪些字段】\n- 去某地（move）：必填 destination；选填 无\n"
        "【当前上下文】\n" + json.dumps({"npc": "shen_yanzhou", "location": "market"}, ensure_ascii=False)
    )
    payload = respond(prompt)
    res = parse_intent_response(
        json.dumps(payload, ensure_ascii=False), _catalog(real_content), real_content, real_state,
        "shen_yanzhou",
    )
    assert res.status == "ok"
    assert res.args == {"to": "smithy"}
    assert payload["category"] == "去某地", "改口不改变意图类别"


def test_fake_server_cases(real_content, real_state):
    base = "【系统支持哪些意图】\n去某地、吃饭、对话\n\n"
    fields = (
        "【每个意图要填哪些字段】\n"
        "- 去某地（move）：必填 destination；选填 无\n"
        "- 吃饭（consume）：必填 item；选填 qty\n"
        "- 对话（talk）：必填 target、message；选填 reply\n"
    )
    ctx = "【当前上下文】\n" + json.dumps(
        {"npc": "shen_yanzhou", "location": "market",
         "nearby_npcs": [{"id": "liu_sanniang", "name": "柳三娘"}]},
        ensure_ascii=False,
    )
    full = respond(f"{base}{fields}{ctx}\n【用户/上层说的话】\n去客栈\n【系统支持哪些意图】")
    assert full["category"] == "去某地" and full["args"]["地点"] == "客栈"
    missing = respond(f"{base}{fields}{ctx}\n【用户/上层说的话】\n去一趟\n【系统支持哪些意图】")
    assert missing.get("needs_clarification") == ["destination"]
    neg = respond(f"{base}{fields}{ctx}\n【用户/上层说的话】\n今天不去客栈\n【系统支持哪些意图】")
    assert neg["category"] == "none"
    qty = respond(f"{base}{fields}{ctx}\n【用户/上层说的话】\n喝两瓶浊酒\n【系统支持哪些意图】")
    assert qty["args"]["数量"] == "两瓶"
    talk = respond(f"{base}{fields}{ctx}\n【用户/上层说的话】\n找柳三娘闲聊\n【系统支持哪些意图】")
    assert talk["args"]["对象"] == "liu_sanniang"
