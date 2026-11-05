"""门派动作 API 接线：白名单/别名/参数校验、私有快照、403、拒绝码与守恒。"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from wsim.api.auth import AccountStore
from wsim.api.server import create_server
from wsim.api.session import WorldSession
from wsim.core.models import Effect
from wsim.world.effects import apply_effects
from wsim.world.state import Relation

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = "u_demo"
PID = f"player:{ACCOUNT}"
SECT_ID = "u_demo"
DISCIPLES = ("duan_wudou", "cui_tieyi", "fang_cunxin", "han_xinglin")


class Clock:
    def __init__(self, t: float = 1_000.0):
        self.t = float(t)

    def __call__(self) -> float:
        return self.t


def _request(base, path, method="POST", body=None, token=""):
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


@pytest.fixture()
def service(tmp_path):
    session = WorldSession(ROOT, run_id="sect-api", run_dir=tmp_path / "run", seed=42, npc_limit=30)
    clock = Clock()
    accounts = AccountStore(
        tmp_path / "service.sqlite", iterations=1_000, token_ttl=3_600, now_fn=clock
    )
    server = create_server(session, "127.0.0.1", 0, accounts=accounts, auth_required=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield base, session, accounts
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        accounts.close()
        session.close()


def _login(base, username=ACCOUNT, password="secret123"):
    status, payload = _request(
        base, "/api/auth/register", body={"username": username, "password": password}
    )
    assert status in (200, 409), (status, payload)
    status, payload = _request(
        base, "/api/auth/login", body={"username": username, "password": password}
    )
    assert status == 200, (status, payload)
    return payload["token"]


def _fund(session: WorldSession, amount: int = 2_000) -> None:
    """测试侧铸币（经 effects，minted_total 同步），仅为构造可立派的玩家。"""
    apply_effects(
        session.state,
        [Effect("mint", {"account": f"wallet:{PID}", "amount": amount, "reason": "test_fund"})],
    )


def _bond(session: WorldSession, npc: str, affection: float = 40.0, trust: float = 50.0) -> None:
    ent = session.state.entities[npc]
    ent.location = session.state.entities[PID].location  # 同地点才可收徒
    for a, b in ((PID, npc), (npc, PID)):
        session.state.relations[f"{a}|{b}|friend"] = Relation(
            a, b, "friend", affection=affection, trust=trust
        )


def _boost_master_skill(session: WorldSession) -> None:
    """测试侧补足掌门技能到收徒门槛（正常玩法需先 cultivate 到 0.4）。"""
    ent = session.state.entities[PID]
    apply_effects(
        session.state,
        [Effect("skill", {"entity": PID, "skill": k, "delta": 0.2}) for k in ent.skills],
    )


def _enter(session: WorldSession, archetype: str = "游侠") -> None:
    res = session.perform(
        ACCOUNT, "enter", {"archetype": archetype, "name": "测试掌门"}, "enter-1"
    )
    assert res["ok"], res
    _boost_master_skill(session)


def test_sect_write_requires_own_token_and_private_state(service):
    base, session, _ = service
    token = _login(base)
    status, payload = _request(
        base,
        "/api/player/action",
        body={"verb": "found_sect", "account_id": "someone_else", "args": {"name": "越权门"}},
        token=token,
    )
    assert status == 403, payload
    assert payload["reject_reason"] == "auth_account_mismatch"

    # 匿名公共摘要只给 sect_name，不含门主/名册/金库。
    status, public = _request(base, f"/api/player/state?account_id={ACCOUNT}", method="GET")
    assert status == 200
    assert "sect_name" in public
    for private in ("sect", "player", "marriage", "disciples", "treasury"):
        assert private not in public, (private, public)

    # 本人令牌能看到私有门派快照（未入派时为 null）。
    status, priv = _request(
        base,
        f"/api/player/state?account_id={ACCOUNT}",
        method="GET",
        token=token,
    )
    assert status == 200
    assert priv["player"]["sect"] == {"in_sect": False, "sect": None}


def test_sect_reject_codes_and_private_snapshot(service):
    base, session, _ = service
    token = _login(base)
    status, entered = _request(
        base, "/api/player/enter", body={"archetype": "游侠", "name": "测试掌门"}, token=token
    )
    assert status == 200 and entered["ok"], entered
    _boost_master_skill(session)

    # 未入派 / 钱不够 / 声望不够，都是真实拒绝事件（有 seq）。
    cases = [
        ({"verb": "sect_deposit", "args": {"amount": 10}}, "no_sect"),
        ({"verb": "found_sect", "args": {"name": "没钱门"}}, "insufficient_funds"),
    ]
    for body, code in cases:
        status, res = _request(base, "/api/player/action", body=body, token=token)
        assert status == 200 and not res["ok"], res
        assert res["reject_reason"] == code, res
        assert res["reason_text"] and res["event_seq"] > 0

    _fund(session)
    status, res = _request(
        base, "/api/player/action", body={"verb": "found_sect", "args": {"name": "无望门"}}, token=token
    )
    assert res["reject_reason"] == "insufficient_rep", res

    for npc in DISCIPLES:
        _bond(session, npc)
    status, res = _request(
        base,
        "/api/player/action",
        body={"verb": "found_sect", "args": {"name": "测试门", "doctrine": "义"}},
        token=token,
    )
    assert status == 200 and res["ok"], res
    assert res["details"]["sect"]["in_sect"] is True
    assert res["details"]["sect"]["sect"]["role"] == "leader"
    assert res["details"]["sect"]["sect"]["treasury"]["balance"] == 600

    status, dup = _request(
        base, "/api/player/action", body={"verb": "found_sect", "args": {"name": "二门"}}, token=token
    )
    assert dup["reject_reason"] == "already_in_sect", dup

    # 收徒 3 名 → 第 4 名 sect_full；名册与金库只出现在本人响应里。
    for idx, npc in enumerate(DISCIPLES[:3]):
        status, res = _request(
            base,
            "/api/player/action",
            body={"verb": "recruit_disciple", "args": {"target": npc, "sect_id": SECT_ID}},
            token=token,
        )
        assert status == 200 and res["ok"], (npc, res)
        assert len(res["details"]["sect"]["sect"]["disciples"]) == idx + 1

    status, full = _request(
        base,
        "/api/player/action",
        body={"verb": "recruit_disciple", "args": {"target": DISCIPLES[3], "sect_id": SECT_ID}},
        token=token,
    )
    assert full["reject_reason"] == "sect_full", full

    status, unknown = _request(
        base,
        "/api/player/action",
        body={"verb": "expel_disciple", "args": {"target": "ghost", "sect_id": SECT_ID}},
        token=token,
    )
    assert unknown["reject_reason"] == "disciple_not_found", unknown

    # alias 与省略 sect_id（默认本人门派）走同一条链路。
    status, dep = _request(
        base, "/api/player/action", body={"verb": "sect/deposit", "args": {"amount": 100}}, token=token
    )
    assert dep["ok"] and dep["details"]["sect"]["sect"]["treasury"]["balance"] == 700, dep
    status, wd = _request(
        base, "/api/player/action", body={"verb": "sect_withdraw", "args": {"amount": 100}}, token=token
    )
    assert wd["ok"] and wd["details"]["sect"]["sect"]["treasury"]["balance"] == 600, wd


def test_sect_chain_conservation_and_daily_settlement(service):
    base, session, _ = service
    token = _login(base)
    _enter(session)
    _fund(session)
    for npc in DISCIPLES[:3]:
        _bond(session, npc)

    def do(verb, args):
        status, res = _request(
            base, "/api/player/action", body={"verb": verb, "args": args}, token=token
        )
        assert status == 200, res
        return res

    assert do("found_sect", {"name": "测试门"})["ok"]
    for npc in DISCIPLES[:3]:
        assert do("recruit_disciple", {"target": npc, "sect_id": SECT_ID})["ok"]
    assert do("sect_withdraw", {"amount": 500, "sect_id": SECT_ID})["ok"]
    res = do("pay_disciples", {"sect_id": SECT_ID})
    assert res["ok"], res
    assert res["details"]["settlement"]["dues"] == 15
    assert res["details"]["settlement"]["stipend"] == 30
    assert res["details"]["sect"]["sect"]["settled_today"] is True

    res = do("pay_disciples", {"sect_id": SECT_ID})
    assert res["reject_reason"] == "already_settled_today", res

    res = do("expel_disciple", {"target": DISCIPLES[0], "sect_id": SECT_ID})
    assert res["ok"] and len(res["details"]["sect"]["sect"]["disciples"]) == 2, res

    state_view = session.player_state(ACCOUNT)
    assert state_view["reconciliation"]["delta"] == 0
    assert state_view["sect"]["sect"]["member_count"] == 3
