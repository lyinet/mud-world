"""玩家对话落地：人设消费、记忆、关系上限、守恒、审批链。"""
from __future__ import annotations

from wsim.actions.common import wallet
from wsim.actions.registry import build_registry
from wsim.actions.social import TalkAction
from wsim.agent.dialogue import PLAYER_ID, compose_talk, extract_persona_voice
from wsim.audit.conservation import conservation
from wsim.core.models import Intent
from wsim.mcp.audit import AuditLog
from wsim.mcp.gateway import MCPGateway, PERMISSION_ADMIN, PERMISSION_PROPOSE
from wsim.world.effects import apply_effects
from wsim.world.store import EventStore
from wsim.world.writer import Writer


def _talk(message: str, reply: str, tick: int, aff: float = 2.0, trust: float = 2.0) -> Intent:
    return Intent(
        actor=PLAYER_ID,
        verb="talk",
        args={
            "target": "n1",
            "message": message,
            "reply": reply,
            "affection_delta": aff,
            "trust_delta": trust,
            "from": "visitor",
        },
        tick=tick,
        source="test",
    )


def test_compose_talk_uses_persona_line(real_content):
    persona = real_content.personas["liu_sanniang"]
    voice = extract_persona_voice(persona)
    assert voice["quotes"], "人设卡里应有台词"
    out = compose_talk(persona, "liu_sanniang", "柳三娘", "绣娘", "三娘，多谢你照应。", 10)
    assert any(q in out["reply"] for q in voice["quotes"])
    assert out["affection_delta"] > 0


def test_talk_writes_memory_with_source(content, state):
    action = TalkAction(content)
    intent = _talk("三娘，多谢你照应。", "阿是这话，你也听得的？", tick=0)
    effects = action.apply(state, intent, None)
    apply_effects(state, effects)

    assert PLAYER_ID in state.entities
    mem = state.memories["n1"]
    assert any("访客说：三娘，多谢你照应。" == m["text"] and m["source"] == "talk:player" for m in mem)
    assert any(m["source"] == "talk:npc" for m in mem)
    rel = state.relation(PLAYER_ID, "n1", "acquaintance")
    assert rel is not None
    assert abs(rel.affection) <= 2.0
    assert rel.trust == 42.0


def test_talk_relation_delta_is_capped_per_tick_and_day(content, state):
    action = TalkAction(content)
    # 同一 tick 连续 5 次 +2：单 tick 上限 2。
    for _ in range(5):
        apply_effects(state, action.apply(state, _talk("多谢", "嗯", tick=0), None))
    rel = state.relation(PLAYER_ID, "n1", "acquaintance")
    assert rel.affection <= 2.0
    ledger = state.talk_ledger["n1"]
    assert ledger["tick_affection"] <= 2.0

    # 同一日内换 tick 继续 +2：日累计上限 8。
    for tick in (1, 2, 3, 4, 5):
        apply_effects(state, action.apply(state, _talk("多谢", "嗯", tick=tick), None))
    rel = state.relation(PLAYER_ID, "n1", "acquaintance")
    assert rel.affection <= 8.0
    assert state.talk_ledger["n1"]["day_affection"] <= 8.0


def test_compose_talk_rotates_reply_over_three_turns(real_content):
    persona = real_content.personas["liu_sanniang"]
    messages = ["三娘，今日可好？", "今日气色不错，辛苦了。", "绣坊近来生意如何？"]
    replies: list[str] = []
    for turn, message in enumerate(messages):
        out = compose_talk(
            persona,
            "liu_sanniang",
            "柳三娘",
            "绣娘",
            message,
            10,
            turn=turn,
            last_message=messages[turn - 1] if turn else "",
            last_reply=replies[-1] if replies else "",
        )
        replies.append(out["reply"])
    assert len(set(replies)) == 3, "同一 NPC 同 tick 连续 3 轮回应必须不同"


def test_talk_cross_day_accumulates_at_most_24(content, state):
    action = TalkAction(content)
    # 每天 8 个 tick，每 tick 请求 +2；日上限 8，三日累计上限 24。
    for day in range(3):
        for offset in range(8):
            tick = day * 144 + offset
            apply_effects(state, action.apply(state, _talk("多谢", "嗯", tick=tick), None))
    rel = state.relation(PLAYER_ID, "n1", "acquaintance")
    assert rel.affection == 24.0
    assert rel.trust == 64.0
    assert rel.affection <= 24.0


def test_talk_keeps_conservation_zero(content, state):
    before_minted = state.minted_total
    before_burned = state.burned_total
    action = TalkAction(content)
    apply_effects(state, action.apply(state, _talk("多谢", "嗯", tick=0), None))

    cons = conservation(state)
    assert cons["conservation_error"] == 0
    assert state.minted_total == before_minted
    assert state.burned_total == before_burned
    assert state.account_balance(wallet(PLAYER_ID)) == 0
    assert not any(e.op in ("mint", "burn", "transfer") for e in action.apply(state, _talk("多谢", "嗯", tick=1), None))


def test_gateway_propose_approve_apply_lands_talk(content, state, tmp_path):
    store = EventStore(tmp_path / "events.sqlite")
    writer = Writer(state.clone(), store, build_registry(content, 42), 42)
    gateway = MCPGateway(
        state_provider=lambda: writer.state,
        writer=writer,
        audit=AuditLog(tmp_path / "audit.jsonl"),
        tick_provider=lambda: int(writer.state.tick),
        identities={
            "ui": PERMISSION_PROPOSE,
            "ui:requester": PERMISSION_ADMIN,
            "ui:reviewer": PERMISSION_ADMIN,
        },
    )
    raw = _talk("多谢", "阿是这话，你也听得的？", tick=0).to_dict()
    proposed = gateway.call("ui", "world.propose_intent", {"intent": raw})
    assert proposed["accepted"] is True

    request = gateway.call("ui:requester", "admin.request", {"intent": raw, "reason": "ui dialogue"})
    gateway.call("ui:reviewer", "admin.approve", {"token": request["token"]})
    applied = gateway.call("ui:reviewer", "admin.apply", {"token": request["token"]})
    assert applied["seq"] >= 1
    assert applied["outcome"] == "ok"
    assert applied["verb"] == "talk"

    events = store.read_all()
    store.close()
    talk_event = [e for e in events if e.verb == "talk"][-1]
    assert talk_event.effects and any(e["op"] == "memory_add" for e in talk_event.effects)
    assert any("访客说" in m["text"] for m in writer.state.memories["n1"])

    # 关掉 store 再重开：事件必须仍在（证明 effect 写进了持久 store，不是内存）。
    reopened = EventStore(tmp_path / "events.sqlite")
    durable = [e for e in reopened.read_all() if e.verb == "talk"]
    reopened.close()
    assert len(durable) == 1
    assert durable[0].seq == applied["seq"]
