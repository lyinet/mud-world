"""P3-C 玩家最小切片：注册、在场、离开与完整链路回放。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from wsim.actions.common import wallet
from wsim.actions.registry import build_registry
from wsim.core.models import Effect, Intent
from wsim.world.effects import apply_effects
from wsim.world.state import WorldState
from wsim.world.store import EventStore
from wsim.world.writer import Writer

ROOT = Path(__file__).resolve().parents[1]
PLAYER = "player:u_9001"


def test_player_enter_registers_entity_wallet_presence(content, state, tmp_path):
    store = EventStore(tmp_path / "events.sqlite")
    writer = Writer(state.clone(), store, build_registry(content, 42), 42)
    event = writer.submit(Intent(
        actor=PLAYER, verb="player_enter",
        args={"player_id": PLAYER, "name": "阿青", "at": "a"},
        tick=0, source="player",
    ))
    assert event.outcome == "ok", event.reason
    ent = writer.state.entities[PLAYER]
    assert ent.name == "阿青"
    assert ent.role == "visitor"
    assert ent.location == "a"
    assert ent.presence == "online"
    assert ent.last_seen_tick == 0
    assert ent.inventory == {}
    assert wallet(PLAYER) in writer.state.accounts
    assert writer.state.account_balance(wallet(PLAYER)) == 0
    store.close()


def test_player_enter_twice_is_rejected(content, state, tmp_path):
    store = EventStore(tmp_path / "events.sqlite")
    writer = Writer(state.clone(), store, build_registry(content, 42), 42)
    args = {"player_id": PLAYER, "at": "a"}
    first = writer.submit(Intent(actor=PLAYER, verb="player_enter", args=args, tick=0, source="player"))
    assert first.outcome == "ok"
    # 同参同 tick 的重复 intent 会被 Writer 幂等去重；换参数才会走到 already_online。
    second = writer.submit(Intent(
        actor=PLAYER, verb="player_enter",
        args={**args, "name": "阿青"}, tick=0, source="player",
    ))
    assert second.outcome == "rejected"
    assert second.reason == "rejected:already_online"
    store.close()


def test_player_presence_updates_location(content, state, tmp_path):
    store = EventStore(tmp_path / "events.sqlite")
    writer = Writer(state.clone(), store, build_registry(content, 42), 42)
    writer.submit(Intent(
        actor=PLAYER, verb="player_enter",
        args={"player_id": PLAYER, "at": "a"}, tick=0, source="player",
    ))
    event = writer.submit(Intent(
        actor=PLAYER, verb="player_presence",
        args={"player_id": PLAYER, "at": "b"}, tick=0, source="player",
    ))
    assert event.outcome == "ok", event.reason
    ent = writer.state.entities[PLAYER]
    assert ent.location == "b"
    assert ent.presence == "online"
    store.close()


def test_player_leave_sets_offline(content, state, tmp_path):
    store = EventStore(tmp_path / "events.sqlite")
    writer = Writer(state.clone(), store, build_registry(content, 42), 42)
    writer.submit(Intent(
        actor=PLAYER, verb="player_enter",
        args={"player_id": PLAYER, "at": "a"}, tick=0, source="player",
    ))
    event = writer.submit(Intent(
        actor=PLAYER, verb="player_leave", args={"player_id": PLAYER},
        tick=0, source="player",
    ))
    assert event.outcome == "ok", event.reason
    assert writer.state.entities[PLAYER].presence == "offline"
    store.close()


def test_player_minimal_chain_replay_match(real_content, tmp_path):
    """完整链路：进入 → 说话（服务端算关系）→ 在场换地点 → 离开 → 回放 MATCH。"""
    state = WorldState(locations=real_content.locations)
    store = EventStore(tmp_path / "events.sqlite")
    writer = Writer(state, store, build_registry(real_content, 42), 42)
    writer.bootstrap(
        [
            Effect("create_entity", {
                "id": "test_target",
                "name": "测试甲",
                "role": "worker",
                "location": "market",
                "home": "market",
                "stamina": 100.0,
                "max_stamina": 100.0,
            }),
            Effect("create_account", {"account": "wallet:test_target"}),
        ],
        tick=0,
    )

    at = "market"
    away = sorted(real_content.locations[at]["exits"])[0]

    events = [
        writer.submit(Intent(
            actor=PLAYER, verb="player_enter",
            args={"player_id": PLAYER, "name": "阿青", "at": at},
            tick=0, source="player",
        )),
        writer.submit(Intent(
            actor=PLAYER, verb="talk",
            args={"player_id": PLAYER, "target": "test_target", "message": "多谢照应。"},
            tick=0, source="player",
        )),
        writer.submit(Intent(
            actor=PLAYER, verb="player_presence",
            args={"player_id": PLAYER, "at": away},
            tick=0, source="player",
        )),
        writer.submit(Intent(
            actor=PLAYER, verb="player_leave",
            args={"player_id": PLAYER},
            tick=0, source="player",
        )),
    ]
    assert [e.outcome for e in events] == ["ok"] * 4, [e.reason for e in events]

    rel = writer.state.relation(PLAYER, "test_target", "acquaintance")
    assert rel is not None
    assert rel.affection > 0
    assert writer.state.entities[PLAYER].location == away
    assert writer.state.entities[PLAYER].presence == "offline"
    assert writer.state.entities[PLAYER].last_seen_tick == 0

    seqs = [e.seq for e in events]
    assert seqs == [2, 3, 4, 5], seqs
    final_state_hash = writer.state.hash()
    event_hash = store.event_hash()
    store.set_meta("content_hash", real_content.content_hash)
    store.close()

    (tmp_path / "world_hash.txt").write_text(final_state_hash + "\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "replay.py"), "--run", str(tmp_path)],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "MATCH" in proc.stdout, proc.stdout
    assert event_hash

    # 终端/UI 的 status 依赖同一实体：离线后仍在，不再“未登记”。
    assert PLAYER in writer.state.entities
    assert writer.state.entities[PLAYER].role == "visitor"
    assert writer.state.account_balance(wallet(PLAYER)) == 0
