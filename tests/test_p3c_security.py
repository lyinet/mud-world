"""P3-C 安全/正确性回归：tick、legacy、found_sect 角色。"""
from __future__ import annotations

import json
import sqlite3

from wsim.actions.business import FoundSectAction
from wsim.actions.registry import build_registry
from wsim.agent.dialogue import PLAYER_ID
from wsim.core.canonical import hash_obj
from wsim.core.models import Intent
from wsim.world.store import EventStore
from wsim.world.writer import Writer


def test_writer_rejects_stale_player_tick(content, state, tmp_path):
    """缺陷 3：玩家 tick 不得回退（与最近一次提交比较）。"""
    store = EventStore(tmp_path / "events.sqlite")
    writer = Writer(state.clone(), store, build_registry(content, 42), 42)

    enter = writer.submit(Intent(
        actor=PLAYER_ID, verb="player_enter",
        args={"player_id": PLAYER_ID, "at": "a"}, tick=0, source="player",
    ))
    assert enter.outcome == "ok", enter.reason

    # NPC 推进一步（不受玩家 tick 校验影响，同时把 state.tick 推到 1）。
    move = writer.submit(Intent(
        actor="n1", verb="move", args={"to": "b"}, tick=1, source="npc",
    ))
    assert move.outcome == "ok", move.reason

    ok = writer.submit(Intent(
        actor=PLAYER_ID, verb="player_presence",
        args={"player_id": PLAYER_ID}, tick=1, source="player",
    ))
    assert ok.outcome == "ok", ok.reason

    stale = writer.submit(Intent(
        actor=PLAYER_ID, verb="player_presence",
        args={"player_id": PLAYER_ID}, tick=0, source="player",
    ))
    assert stale.outcome == "rejected"
    assert stale.reason == "rejected:stale_tick"
    store.close()


def test_writer_rejects_future_player_tick(content, state, tmp_path):
    """缺陷 3：玩家 tick 不得超过当前世界 tick。"""
    store = EventStore(tmp_path / "events.sqlite")
    writer = Writer(state.clone(), store, build_registry(content, 42), 42)
    future = writer.submit(Intent(
        actor=PLAYER_ID, verb="player_enter",
        args={"player_id": PLAYER_ID, "at": "a"}, tick=5, source="player",
    ))
    assert future.outcome == "rejected"
    assert future.reason == "rejected:stale_tick"
    store.close()


def test_writer_npc_tick_not_checked(content, state, tmp_path):
    """缺陷 3：NPC/系统调度不受玩家 tick 校验影响。"""
    store = EventStore(tmp_path / "events.sqlite")
    writer = Writer(state.clone(), store, build_registry(content, 42), 42)
    later = writer.submit(Intent(
        actor="n1", verb="move", args={"to": "b"}, tick=7, source="npc",
    ))
    assert later.outcome == "ok", later.reason
    assert writer.state.tick == 7
    forward = writer.submit(Intent(
        actor="n1", verb="move", args={"to": "a"}, tick=3, source="npc",
    ))
    assert forward.outcome == "ok", forward.reason
    store.close()


def test_legacy_events_marked_without_hash_change(tmp_path):
    """缺陷 1：旧库缺 legacy 列时不 ALTER；player:visitor 事件读取时标 legacy，
    且 canonical 哈希不变（旧 run 可回放）。"""
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE events (
            seq INTEGER PRIMARY KEY, tick INTEGER NOT NULL, day INTEGER NOT NULL,
            intent_id TEXT NOT NULL, actor TEXT NOT NULL, verb TEXT NOT NULL,
            args TEXT NOT NULL, effects TEXT NOT NULL, outcome TEXT NOT NULL,
            reason TEXT NOT NULL, source TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (1, 0, 0, "i:legacy", "player:visitor", "talk",
         json.dumps({"target": "n1", "message": "多谢"}, sort_keys=True),
         json.dumps([], sort_keys=True), "ok", "", "test"),
    )
    conn.commit()
    conn.close()

    store = EventStore(path)
    events = store.read_all()
    assert len(events) == 1
    assert events[0].legacy is True
    canonical = [e.to_canonical_dict() for e in events]
    assert "legacy" not in canonical[0]
    assert store.event_hash() == hash_obj(canonical)
    store.close()

    # 新库显式写入 legacy=True 时，列持久化且哈希仍走 canonical。
    fresh = EventStore(tmp_path / "fresh.sqlite")
    legacy = events[0]
    fresh.append(legacy)
    again = fresh.read_all()
    assert again[0].legacy is True
    assert fresh.event_hash() == hash_obj(canonical)
    fresh.close()


def test_found_sect_role_not_allowed(real_content, real_state):
    """缺陷 4：越权角色在 Action 层被拦截，并给出明确原因。"""
    action = FoundSectAction(real_content)
    ent = real_state.entities["shen_yanzhou"]  # 铁匠，不在 found_sect.allowed_roles
    assert ent.role not in real_content.allowed_roles("found_sect")
    intent = Intent(
        actor="shen_yanzhou", verb="found_sect",
        args={"at": ent.location, "name": "铁匠铺"}, tick=0, source="npc",
    )
    assert action.preconditions(real_state, intent) == ["role_not_allowed"]


def test_found_sect_allowed_role_passes(real_content, real_state):
    """缺陷 4：内容包授权角色不会被角色检查拦下。"""
    action = FoundSectAction(real_content)
    ent = real_state.entities["su_hexiang"]  # 掌柜，在 allowed_roles 内
    assert ent.role in real_content.allowed_roles("found_sect")
    intent = Intent(
        actor="su_hexiang", verb="found_sect",
        args={"at": ent.location, "name": "合香号"}, tick=0, source="npc",
    )
    reasons = action.preconditions(real_state, intent)
    assert "role_not_allowed" not in reasons
