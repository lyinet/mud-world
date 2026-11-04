"""W1 账号存储：口令摘要、令牌过期/轮换、断线宽限、僵尸清理。"""
from __future__ import annotations

import sqlite3

from wsim.api.auth import AccountStore, DEFAULT_RECONNECT_GRACE, normalize_username


class Clock:
    def __init__(self, t: float = 1_000.0):
        self.t = float(t)

    def __call__(self) -> float:
        return self.t


def _store(tmp_path, **kw) -> AccountStore:
    clock = Clock()
    return AccountStore(
        tmp_path / "service.sqlite",
        iterations=1_000,
        token_ttl=kw.pop("token_ttl", 3_600),
        reconnect_grace=kw.pop("reconnect_grace", 60),
        now_fn=clock,
        **kw,
    )


def test_register_and_password_digest(tmp_path):
    store = _store(tmp_path)
    try:
        res = store.register("u_alice", "secret123")
        assert res["ok"] is True and res["account_id"] == "u_alice"
        assert store.register("U_ALICE", "another")["code"] == "auth_username_taken"
        assert store.register("ab", "secret123")["code"] == "auth_bad_username"
        assert store.register("u_bob", "short")["code"] == "auth_bad_password"
        assert store.authenticate("u_alice", "secret123") == "u_alice"
        assert store.authenticate("u_alice", "wrongpass") is None
        assert store.authenticate("nobody", "secret123") is None
    finally:
        store.close()

    # 口令与盐都不是明文；数据库文件里搜不到原始口令。
    conn = sqlite3.connect(tmp_path / "service.sqlite")
    row = conn.execute("SELECT salt,hash_hex,iterations FROM users WHERE username='u_alice'").fetchone()
    conn.close()
    assert row[0] and row[1] and int(row[2]) == 1_000
    assert "secret123" not in row[1]
    blob = (tmp_path / "service.sqlite").read_bytes()
    assert b"secret123" not in blob


def test_login_token_is_hashed_in_db_and_expires(tmp_path):
    store = _store(tmp_path, token_ttl=3_600)
    try:
        reg = store.register("u_amy", "secret123")
        assert reg["ok"]
        login = store.login("u_amy", "secret123")
        assert login["ok"] and login["token_type"] == "Bearer"
        assert login["expires_at"] == 1_000 + 3_600
        token = login["token"]
        row, err = store.resolve_token(token)
        assert err is None and row["account_id"] == "u_amy"
        # 数据库里只有 token 摘要，没有原文。
        assert token.encode("utf-8") not in (tmp_path / "service.sqlite").read_bytes()
        _, err = store.resolve_token("")
        assert err == "auth_missing_token"
        _, err = store.resolve_token("not-a-token")
        assert err == "auth_invalid_token"
        store.now_fn = Clock(1_000 + 3_601)
        _, err = store.resolve_token(token)
        assert err == "auth_expired_token"
        assert store.login("u_amy", "bad")["code"] == "auth_invalid_credentials"
    finally:
        store.close()


def test_refresh_rotates_token(tmp_path):
    store = _store(tmp_path)
    try:
        store.register("u_ref", "secret123")
        login = store.login("u_ref", "secret123")
        refreshed = store.refresh(login["token"])
        assert refreshed["ok"] and refreshed["rotated"] is True
        assert refreshed["session_id"] == login["session_id"]
        assert store.resolve_token(login["token"])[1] == "auth_invalid_token"
        row, err = store.resolve_token(refreshed["token"])
        assert err is None and row["id"] == login["session_id"]
        assert store.refresh("bogus")["code"] == "auth_invalid_token"
    finally:
        store.close()


def test_disconnect_within_grace_resumes_same_session(tmp_path):
    clock = Clock(10_000)
    store = AccountStore(
        tmp_path / "service.sqlite", iterations=1_000, reconnect_grace=60, now_fn=clock
    )
    try:
        store.register("u_dis", "secret123")
        login = store.login("u_dis", "secret123")
        assert store.disconnect(login["session_id"], tick=42)
        clock.t += 59
        resumed = store.reconnect(login["session_id"])
        assert resumed["ok"] and resumed["new_session"] is False
        assert resumed["session_id"] == login["session_id"]
        assert resumed["tick_at_disconnect"] == 42
        row = store.session_info(login["session_id"])
        assert row["status"] == "active" and row["disconnected_at"] is None
        # 同一 token 继续有效。
        assert store.resolve_token(login["token"])[1] is None
    finally:
        store.close()


def test_disconnect_beyond_grace_creates_new_session(tmp_path):
    clock = Clock(20_000)
    store = AccountStore(
        tmp_path / "service.sqlite", iterations=1_000, reconnect_grace=60, now_fn=clock
    )
    try:
        store.register("u_late", "secret123")
        login = store.login("u_late", "secret123")
        assert store.disconnect(login["session_id"], tick=7)
        clock.t += 61
        renewed = store.reconnect(login["session_id"])
        assert renewed["ok"] and renewed["new_session"] is True
        assert renewed["session_id"] != login["session_id"]
        assert renewed["token"] != login["token"]
        assert store.session_info(login["session_id"])["status"] == "expired"
        assert store.resolve_token(login["token"])[1] == "auth_expired_token"
        assert store.resolve_token(renewed["token"])[1] is None
        assert renewed["reconnect"]["tick_at_disconnect"] == 7
    finally:
        store.close()


def test_purge_zombies_only_touches_service_sessions(tmp_path):
    clock = Clock(30_000)
    store = AccountStore(
        tmp_path / "service.sqlite", iterations=1_000, reconnect_grace=60, now_fn=clock
    )
    try:
        store.register("u_z1", "secret123")
        store.register("u_z2", "secret123")
        a = store.login("u_z1", "secret123")
        b = store.login("u_z2", "secret123")
        store.disconnect(a["session_id"], tick=1)
        clock.t += 10
        assert store.purge_zombies() == 0  # 仍在 60 秒宽限内
        clock.t += 60
        purged = store.purge_zombies()
        assert purged >= 1
        assert store.session_info(a["session_id"])["status"] == "expired"
        assert store.session_info(b["session_id"])["status"] == "active"
    finally:
        store.close()


def test_username_normalization():
    assert normalize_username("  U_Alice ") == "u_alice"
    assert DEFAULT_RECONNECT_GRACE == 60
