"""`GET /api/xkx/observe`：只读观察房间/角色，零副作用。

事故背景：离线账号跑文字 `look` 会走 `POST /api/xkx/command`，命令入口自动
`enter` 并推进 tick，站在郭庄被主动怪打了 3 回合。这里用事件库 `max(seq)`
前后不变来证明观察路径没有写任何事件，同时覆盖 presence 与主动怪触发。
"""
from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.api import WorldSession  # noqa: E402
from wsim.api.auth import AccountStore  # noqa: E402
from wsim.api.server import create_server  # noqa: E402
from wsim.sim.aggression import aggression_rules  # noqa: E402

XKX_CONTENT = ROOT / "content" / "xkx"
XKX_WORLD = ROOT / "config" / "worlds" / "xkx.yaml"
SEED_ACCOUNT = "u_demo"
SEED_PID = f"player:{SEED_ACCOUNT}"


def _session(tmp_path) -> WorldSession:
    spawn = str((yaml.safe_load(XKX_WORLD.read_text(encoding="utf-8")) or {}).get("spawn"))
    return WorldSession(
        ROOT,
        run_id="xkx-observe-test",
        seed=42,
        npc_limit=0,
        run_dir=tmp_path / "run",
        content_root=XKX_CONTENT,
        world_config=XKX_WORLD,
        spawn=spawn,
    )


def test_observe_offline_account_keeps_presence_and_event_store_untouched(tmp_path):
    """离线账号观察自己的房间：presence 仍 offline，max(seq) 与 tick 都不变。"""
    session = _session(tmp_path)
    try:
        profile = session.state.players[SEED_PID]
        entity = session.state.entities[SEED_PID]
        before_seq = int(session.store.max_seq())
        before = (
            bool(profile.online),
            str(profile.presence),
            int(profile.last_intent_tick),
            int(entity.last_seen_tick),
            int(session.state.tick),
        )
        assert before[1] == "offline"

        view = session.observe(account_id=SEED_ACCOUNT)

        profile = session.state.players[SEED_PID]
        entity = session.state.entities[SEED_PID]
        after = (
            bool(profile.online),
            str(profile.presence),
            int(profile.last_intent_tick),
            int(entity.last_seen_tick),
            int(session.state.tick),
        )
        assert view["ok"] is True
        assert view["readonly"] is True
        assert view["character"]["presence"] == "offline"
        assert view["character"]["online"] is False
        assert view["character"]["entity_id"] == SEED_PID
        assert view["room"]["id"] == view["character"]["location"]
        assert view["room"]["description"]
        assert after == before, (before, after)
        assert int(session.store.max_seq()) == before_seq
    finally:
        session.close()


def test_observe_aggressive_npc_room_does_not_trigger_aggression(tmp_path):
    """观察主动怪所在房间：不产生 npc_aggression，也不把观察者拉进战斗。"""
    session = _session(tmp_path)
    try:
        rules = aggression_rules(session.content)
        npc = next(
            ent
            for ent in session.state.entities.values()
            if str(getattr(ent, "template", "") or ent.id) in rules
        )
        observer = session.state.entities[SEED_PID]
        before_seq = int(session.store.max_seq())
        before_tick = int(session.state.tick)
        before_loc = str(observer.location)

        view = session.observe(room_id=str(npc.location), entity_id=str(npc.id))

        assert view["ok"] is True
        assert view["room"]["id"] == npc.location
        assert str(npc.id) in {row["entity_id"] for row in view["room"]["present"]}
        assert int(session.store.max_seq()) == before_seq
        assert int(session.state.tick) == before_tick
        assert str(observer.location) == before_loc
        assert str(npc.combat_target) == ""
        assert int(getattr(npc, "aggro_tick", 0)) == 0
        verbs = [e.verb for e in session.store.iter_range(1, before_seq + 1)]
        assert not [v for v in verbs if "aggress" in v]
    finally:
        session.close()


def test_observe_unknown_targets_return_explicit_codes(tmp_path):
    """不存在的账号/房间/空查询都返回明确错误码，且依然不写事件。"""
    session = _session(tmp_path)
    try:
        before_seq = int(session.store.max_seq())
        room = str(session.state.entities[SEED_PID].location)

        missing = session.observe()
        unknown_entity = session.observe(account_id="nobody_here")
        unknown_room = session.observe(room_id="/d/does-not-exist")
        bad_account = session.observe(account_id="AB")
        not_in_room = session.observe(
            entity_id=SEED_PID,
            room_id=next(loc for loc in session.state.locations if loc != room),
        )

        assert missing["ok"] is False
        assert missing["reject_reason"] == "observe_missing_target"
        assert unknown_entity["reject_reason"] == "unknown_entity"
        assert unknown_room["reject_reason"] == "unknown_room"
        assert bad_account["reject_reason"] == "bad_account"
        assert not_in_room["reject_reason"] == "entity_not_in_room"
        assert int(session.store.max_seq()) == before_seq
    finally:
        session.close()


def test_observe_exposes_jing_fields_matching_entities(tmp_path):
    """精神组三字段必须出现在 observe 响应里，且与实体真实值一致。"""
    session = _session(tmp_path)
    try:
        # 玩家 100 / 老虎 212 / 大狼狗 25（feat/xkx-jing 的现值）。
        cases = [
            ("player:u_demo", 100),
            ("/d/hangzhou/npc/laohu", 212),
            ("/d/hangzhou/npc/wolfdog", 25),
        ]
        before_seq = int(session.store.max_seq())
        for entity_id, expected in cases:
            ent = session.state.entities[entity_id]
            view = session.observe(entity_id=entity_id)
            assert view["ok"] is True
            ch = view["character"]
            assert {
                "jing", "max_jing", "eff_jing", "eff_jing_effective", "eff_jing_state"
            } <= set(ch)
            assert ch["jing"] == expected == int(ent.jing)
            assert ch["max_jing"] == int(ent.max_jing)
            assert ch["eff_jing"] == int(ent.eff_jing)
            assert ch["eff_jing_effective"] == expected
            assert ch["eff_jing_state"] == "value"
        assert int(session.store.max_seq()) == before_seq
    finally:
        session.close()


# ---------------------------------------------------------------- HTTP 鉴权口径
def _request(base: str, path: str, token: str = "") -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base + path, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _post(base: str, path: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_observe_http_follows_existing_auth_rules(tmp_path):
    """服务模式：无令牌 401；看别人的角色 403；看自己 200 且不写事件。"""
    session = WorldSession(ROOT, run_id="observe-http", run_dir=tmp_path / "run", seed=42)
    accounts = AccountStore(
        tmp_path / "service.sqlite", iterations=1_000, token_ttl=3_600
    )
    server = create_server(session, "127.0.0.1", 0, accounts=accounts, auth_required=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        _post(base, "/api/auth/register", {"username": SEED_ACCOUNT, "password": "secret123"})
        status, login = _post(
            base, "/api/auth/login", {"username": SEED_ACCOUNT, "password": "secret123"}
        )
        assert status == 200
        token = login["token"]
        before_seq = int(session.store.max_seq())

        status, payload = _request(base, f"/api/xkx/observe?account_id={SEED_ACCOUNT}")
        assert status == 401
        assert payload["reject_reason"] == "auth_missing_token"

        status, payload = _request(
            base, "/api/xkx/observe?entity_id=player:someone_else", token=token
        )
        assert status == 403
        assert payload["reject_reason"] == "auth_account_mismatch"

        status, payload = _request(
            base, f"/api/xkx/observe?account_id={SEED_ACCOUNT}", token=token
        )
        assert status == 200
        assert payload["ok"] is True and payload["readonly"] is True
        assert int(session.store.max_seq()) == before_seq

        # 不存在的房间走 404；未知账号会被既有“令牌账号唯一事实源”规则先挡成 403。
        status, payload = _request(base, "/api/xkx/observe?room_id=/d/does-not-exist", token=token)
        assert status == 404
        assert payload["reject_reason"] == "unknown_room"
        status, payload = _request(base, "/api/xkx/observe?account_id=nobody_here", token=token)
        assert status == 403
        assert payload["reject_reason"] == "auth_account_mismatch"
        assert int(session.store.max_seq()) == before_seq
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        accounts.close()
        session.close()
