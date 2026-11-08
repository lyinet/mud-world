"""W1 并发隔离：同一区域双客户端 20 轮交错动作，互见、0 串档、同 NPC 关系不脏写。"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

from wsim.api.auth import AccountStore
from wsim.api.server import create_server
from wsim.api.session import WorldSession

ROOT = Path(__file__).resolve().parents[1]


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


def _setup(tmp_path):
    session = WorldSession(ROOT, run_id="w1-concurrent", run_dir=tmp_path / "run", seed=42)
    accounts = AccountStore(tmp_path / "service.sqlite", iterations=1_000)
    server = create_server(session, "127.0.0.1", 0, accounts=accounts, auth_required=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    return session, accounts, server, thread, base


def _login(base, username):
    assert _request(base, "/api/auth/register", body={"username": username, "password": "secret123"})[0] in (200, 409)
    status, payload = _request(base, "/api/auth/login", body={"username": username, "password": "secret123"})
    assert status == 200 and payload["ok"]
    return payload["token"]


def _state(base, account, token):
    # 服务模式匿名读只给公共摘要，读完整状态必须带本人令牌。
    return _request(base, f"/api/player/state?account_id={account}", method="GET", token=token)[1]


def test_two_clients_twenty_interleaved_rounds_are_isolated(tmp_path):
    session, accounts, server, thread, base = _setup(tmp_path)
    try:
        token_a = _login(base, "u_a")
        token_b = _login(base, "u_b")
        for user_token, name in ((token_a, "测试甲"), (token_b, "测试乙")):
            status, payload = _request(
                base, "/api/player/enter",
                body={"display_name": name, "archetype": "游侠"}, token=user_token,
            )
            assert status == 200 and payload["ok"], payload

        state_a = _state(base, "u_a", token_a)
        state_b = _state(base, "u_b", token_b)
        assert {p["id"] for p in state_a["online_players"]} >= {"player:u_a", "player:u_b"}
        assert {p["id"] for p in state_b["online_players"]} >= {"player:u_a", "player:u_b"}

        acceptable = [row["id"] for row in state_a["quest_board"]["rows"] if row["acceptable"]]
        assert len(acceptable) >= 2
        assert _request(base, "/api/player/quest/accept", body={"quest": acceptable[0]}, token=token_a)[1]["ok"]
        assert _request(base, "/api/player/quest/accept", body={"quest": acceptable[1]}, token=token_b)[1]["ok"]

        # 任务隔离在接单后立即断言：上工/修炼按声明小时真实推进世界钟（d169196），
        # 截止较短的单会在后续动作中自然过期，那是任务生命周期行为，与并发隔离无关。
        _sa0 = _state(base, "u_a", token_a)
        _sb0 = _state(base, "u_b", token_b)
        assert set(_sa0["player"]["active_quests"]) == {acceptable[0]}
        assert set(_sb0["player"]["active_quests"]) == {acceptable[1]}

        # 20 轮：两个客户端同时向同一个 NPC 说话；单写者串行落事件，但结果各自独立。
        errors: list[str] = []
        barrier = threading.Barrier(2)

        def _talk_loop(token, prefix):
            try:
                barrier.wait(timeout=10)
                for i in range(20):
                    status, res = _request(
                        base, "/api/player/talk",
                        body={"target": "duan_wudou", "message": f"{prefix}-{i}"},
                        token=token,
                    )
                    assert status == 200 and res["ok"], (status, res)
            except AssertionError as exc:  # pragma: no cover - 失败时收集
                errors.append(str(exc))

        threads = [
            threading.Thread(target=_talk_loop, args=(token_a, "A")),
            threading.Thread(target=_talk_loop, args=(token_b, "B")),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not errors

        # 钱包隔离：A 工作挣钱，B 不变。
        money_b_before = _state(base, "u_b", token_b)["player"]["wallet"]
        status, worked = _request(
            base, "/api/player/action", body={"verb": "work", "args": {"hours": 8}}, token=token_a
        )
        assert status == 200 and worked["ok"]
        final_a = _state(base, "u_a", token_a)
        final_b = _state(base, "u_b", token_b)
        assert final_a["player"]["wallet"]["money"] > 300
        assert final_b["player"]["wallet"]["money"] == 300
        assert final_b["player"]["wallet"] == money_b_before

        # 末尾只校验“不串档”：截止较短的单可能已自然过期（见接单后断言），但对方
        # 接的单绝不应出现在自己的 active_quests 里。
        assert acceptable[1] not in final_a["player"]["active_quests"]
        assert acceptable[0] not in final_b["player"]["active_quests"]

        # 物品隔离：两边初始物品一致且都未被对方动作污染。
        assert final_a["player"]["wallet"]["items"] == final_b["player"]["wallet"]["items"]

        # 同一 NPC 的关系：两名玩家各有一条独立记录，数值与各自快照一致（无脏覆盖）。
        key_a = "player:u_a|duan_wudou|acquaintance"
        key_b = "player:u_b|duan_wudou|acquaintance"
        assert key_a in session.state.relations and key_b in session.state.relations
        rel_a = next(r for r in final_a["player"]["relations"] if r["target"] == "duan_wudou")
        rel_b = next(r for r in final_b["player"]["relations"] if r["target"] == "duan_wudou")
        assert rel_a["affection"] == session.state.relations[key_a].affection
        assert rel_b["affection"] == session.state.relations[key_b].affection
        # 两轮交错写入没有把彼此的关系合并/覆盖成同一条：每条 relation_upsert 的
        # 归属玩家都必须等于发起对话的 actor，且都指向同一个 NPC。
        # 注：不能拿“两边数值不等”当证据——对话增量有每 tick / 每日上限，两人同时
        # 打满上限就会撞值，这是合法巧合，与串档无关（该断言曾因此在全量跑中偶发失败）。
        rel_writes = 0
        for event in session.store.read_all():
            if event.verb != "talk" or event.outcome != "ok":
                continue
            for eff in event.effects:
                if eff.get("op") != "relation_upsert":
                    continue
                data = eff["data"]
                assert data["a"] == event.actor
                assert data["b"] == "duan_wudou"
                rel_writes += 1
        assert rel_writes >= 40

        # 0 未授权写入：事件日志里没有任何非预期玩家 actor。
        player_actors = {
            event.actor for event in session.store.read_all()
            if event.outcome == "ok" and str(event.actor).startswith("player:")
        }
        assert player_actors <= {"player:u_a", "player:u_b"}
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        accounts.close()
        session.close()
