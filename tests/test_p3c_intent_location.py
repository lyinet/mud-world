"""P3-C 缺 location 追问：prompt 相邻地点/上一句位置与假意图端点解析。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from wsim.agent.intent_model import (
    IntentCatalog,
    IntentTemplate,
    build_intent_prompt,
    parse_intent_response,
)
from wsim.agent.prompt import build_state_summary
from wsim.core.models import Effect
from wsim.world.effects import apply_effects
from wsim.world.state import WorldState

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import fake_intent_server  # noqa: E402


def _catalog() -> IntentCatalog:
    return IntentCatalog(templates=[
        IntentTemplate(category="去某地", verb="move", required=("destination",)),
    ])


def _summary(content, state, prev: dict | None = None) -> dict:
    summary = build_state_summary(content, state, "n1", 10, "L2")
    if prev is not None:
        summary["previous_location"] = prev
    return summary


def test_state_summary_lists_only_reachable_locations(content, state):
    summary = _summary(content, state)
    assert [r["id"] for r in summary["reachable_locations"]] == ["b"]
    assert summary["reachable_locations"][0]["name"] == "乙地"
    # 当前地点 a 与全量地点都不能混进“可到达清单”。
    assert "a" not in [r["id"] for r in summary["reachable_locations"]]
    assert summary["previous_location"] == {"id": "a", "name": "甲地"}


def test_intent_prompt_lists_reachable_locations(content, state):
    summary = _summary(
        content, state,
        prev={"id": "b", "name": "乙地"},
    )
    _, user = build_intent_prompt(_catalog(), "去乙地看看", summary)
    assert "【你刚才在哪】" in user
    assert "上一句时你在 乙地（b）" in user
    assert "【当前可到达的相邻地点（只可从这些里选 destination）】" in user
    assert "- 乙地（b）" in user
    assert "destination" in user


def test_intent_prompt_without_neighbors_tells_model_to_clarify(content, state):
    lonely = WorldState(locations={"z": {"id": "z", "name": "孤地", "exits": [], "services": []}})
    apply_effects(lonely, [Effect("create_entity", {
        "id": "n1", "name": "甲", "role": "worker", "location": "z", "home": "z",
        "stamina": 100.0,
    })])
    summary = build_state_summary(content, lonely, "n1", 10, "L2")
    _, user = build_intent_prompt(_catalog(), "去那边看看", summary)
    assert "无相邻地点" in user
    assert "只缺 destination" in user


def test_fake_intent_resolves_reachable_name(content, state):
    summary = _summary(content, state, prev={"id": "a", "name": "甲地"})
    _, user = build_intent_prompt(_catalog(), "去乙地看看", summary)
    reply = fake_intent_server.respond(user)
    assert reply["category"] == "去某地"
    assert reply["args"]["地点"] == "乙地"

    resolution = parse_intent_response(
        json.dumps(reply, ensure_ascii=False), _catalog(), content, state, "n1",
    )
    assert resolution.status == "ok"
    intent = resolution.intent("n1", 10)
    assert intent is not None
    assert intent.args["to"] == "b"


def test_fake_intent_clarifies_when_no_destination(content, state):
    _, user = build_intent_prompt(_catalog(), "去那边看看", _summary(content, state))
    reply = fake_intent_server.respond(user)
    assert reply == {"needs_clarification": ["destination"]}
