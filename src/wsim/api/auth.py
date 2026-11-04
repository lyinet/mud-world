"""账号与令牌存储（W1 服务化基座）。

- 口令只以 PBKDF2-HMAC-SHA256 盐化摘要落盘，数据库里没有明文。
- 令牌只存 SHA-256 摘要；客户端持有原文。
- 30 天/24 小时等策略由调用方注入；时间统一走 `now_fn`，便于测试假时钟。
- 单进程、sqlite、RLock；与事件日志（events.sqlite）分库，互不影响。
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

DEFAULT_ITERATIONS = 200_000
DEFAULT_TOKEN_TTL = 86_400
DEFAULT_RECONNECT_GRACE = 60
MIN_PASSWORD = 6
MAX_PASSWORD = 128

# 与 player.ACCOUNT_RE 保持同一规则；这里复制一份，避免鉴权层依赖动作层。
USERNAME_RE = re.compile(r"^[a-z0-9_-]{3,32}$")

ERROR_TEXT: dict[str, str] = {
    "auth_bad_username": "用户名需为 3-32 位小写字母、数字、下划线或连字符。",
    "auth_bad_password": "口令长度需为 6-128 位。",
    "auth_username_taken": "该用户名已被注册。",
    "auth_invalid_credentials": "用户名或口令不正确。",
    "auth_missing_token": "缺少 Bearer 令牌。",
    "auth_invalid_token": "令牌无效或已注销。",
    "auth_expired_token": "令牌已过期；请重新登录。",
    "auth_account_mismatch": "请求账号与令牌账号不一致。",
    "auth_disabled": "当前为 legacy 匿名模式，鉴权接口不可用。",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    salt TEXT NOT NULL,
    hash_hex TEXT NOT NULL,
    iterations INTEGER NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    last_seen_at REAL NOT NULL,
    disconnected_at REAL,
    tick_at_disconnect INTEGER,
    status TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_account ON sessions(account_id);
"""


def error_payload(code: str, tick: int = 0) -> dict[str, Any]:
    """统一的鉴权错误体；`code` 与 `reject_reason` 同值，前端可任选。"""
    return {
        "ok": False,
        "code": code,
        "reject_reason": code,
        "reason_text": ERROR_TEXT.get(code, code),
        "tick": int(tick),
    }


def normalize_username(raw: Any) -> str:
    return str(raw or "").strip().lower()


class AccountStore:
    """用户名/口令账号表 + 会话令牌表。"""

    def __init__(
        self,
        path: str | Path,
        iterations: int = DEFAULT_ITERATIONS,
        token_ttl: int = DEFAULT_TOKEN_TTL,
        reconnect_grace: int = DEFAULT_RECONNECT_GRACE,
        now_fn: Callable[[], float] | None = None,
        check_same_thread: bool = False,
    ):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.iterations = max(1, int(iterations))
        self.token_ttl = max(1, int(token_ttl))
        self.reconnect_grace = max(0, int(reconnect_grace))
        self.now_fn = now_fn or time.time
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=check_same_thread)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    # ---------------------------------------------------------------- helpers
    def _now(self, now: float | None) -> float:
        return float(self.now_fn() if now is None else now)

    @staticmethod
    def _hash_password(password: str, salt_hex: str, iterations: int) -> str:
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
        return digest.hex()

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return None if row is None else {k: row[k] for k in row.keys()}

    def _user_row(self, username: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()

    # --------------------------------------------------------------- accounts
    def register(self, username: Any, password: Any) -> dict[str, Any]:
        """注册账号。成功返回 account_id；失败返回稳定错误码。"""
        name = normalize_username(username)
        pwd = str(password or "")
        if not USERNAME_RE.match(name):
            return {"ok": False, "code": "auth_bad_username"}
        if not (MIN_PASSWORD <= len(pwd) <= MAX_PASSWORD):
            return {"ok": False, "code": "auth_bad_password"}
        salt_hex = secrets.token_bytes(16).hex()
        hash_hex = self._hash_password(pwd, salt_hex, self.iterations)
        with self.lock:
            try:
                self.conn.execute(
                    "INSERT INTO users(username,salt,hash_hex,iterations,created_at) VALUES(?,?,?,?,?)",
                    (name, salt_hex, hash_hex, self.iterations, self._now(None)),
                )
                self.conn.commit()
            except sqlite3.IntegrityError:
                self.conn.rollback()
                return {"ok": False, "code": "auth_username_taken"}
        return {"ok": True, "account_id": name, "username": name, "created": True}

    def authenticate(self, username: Any, password: Any) -> str | None:
        """校验口令；成功返回 account_id，失败返回 None（不区分用户不存在/口令错）。"""
        name = normalize_username(username)
        pwd = str(password or "")
        if not USERNAME_RE.match(name) or not pwd:
            return None
        with self.lock:
            row = self._user_row(name)
        if row is None:
            # 固定做一次等价开销的比较，避免明显的用户枚举时序差。
            hmac.compare_digest(secrets.token_bytes(32), secrets.token_bytes(32))
            return None
        expected = self._hash_password(pwd, str(row["salt"]), int(row["iterations"]))
        return name if hmac.compare_digest(expected, str(row["hash_hex"])) else None

    def login(self, username: Any, password: Any, now: float | None = None) -> dict[str, Any]:
        account = self.authenticate(username, password)
        if account is None:
            return {"ok": False, "code": "auth_invalid_credentials"}
        with self.lock:
            return self._create_session(account, self._now(now))

    # --------------------------------------------------------------- sessions
    def _create_session(self, account: str, now: float) -> dict[str, Any]:
        token = secrets.token_urlsafe(32)
        session_id = "s:" + secrets.token_hex(16)
        expires_at = now + self.token_ttl
        self.conn.execute(
            "INSERT INTO sessions(id,account_id,token_hash,created_at,expires_at,"
            "last_seen_at,disconnected_at,tick_at_disconnect,status)"
            " VALUES(?,?,?,?,?,?,NULL,NULL,'active')",
            (session_id, account, self._hash_token(token), now, expires_at, now),
        )
        self.conn.commit()
        return {
            "ok": True,
            "account_id": account,
            "username": account,
            "session_id": session_id,
            "token": token,
            "token_type": "Bearer",
            "expires_at": int(expires_at),
            "expires_in": int(self.token_ttl),
        }

    def resolve_token(self, token: Any, now: float | None = None) -> tuple[dict[str, Any] | None, str | None]:
        """解析 Bearer 令牌。返回 (session_row, error_code)。"""
        raw = str(token or "").strip()
        if not raw:
            return None, "auth_missing_token"
        now_f = self._now(now)
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM sessions WHERE token_hash=?", (self._hash_token(raw),)
            ).fetchone()
            if row is None:
                return None, "auth_invalid_token"
            if str(row["status"]) == "expired" or float(row["expires_at"]) <= now_f:
                return None, "auth_expired_token"
            self.conn.execute(
                "UPDATE sessions SET last_seen_at=? WHERE id=?", (now_f, str(row["id"]))
            )
            self.conn.commit()
            return self._row(row), None

    def session_info(self, session_id: str) -> dict[str, Any] | None:
        with self.lock:
            return self._row(
                self.conn.execute("SELECT * FROM sessions WHERE id=?", (str(session_id),)).fetchone()
            )

    def refresh(self, token: Any, now: float | None = None) -> dict[str, Any]:
        """轮换令牌：同一会话换新 token，旧 token 立即失效。"""
        now_f = self._now(now)
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM sessions WHERE token_hash=?", (self._hash_token(str(token or "").strip()),)
            ).fetchone()
            if row is None:
                return {"ok": False, "code": "auth_invalid_token"}
            if str(row["status"]) == "expired" or float(row["expires_at"]) <= now_f:
                return {"ok": False, "code": "auth_expired_token"}
            new_token = secrets.token_urlsafe(32)
            expires_at = now_f + self.token_ttl
            self.conn.execute(
                "UPDATE sessions SET token_hash=?, expires_at=?, last_seen_at=? WHERE id=?",
                (self._hash_token(new_token), expires_at, now_f, str(row["id"])),
            )
            self.conn.commit()
            return {
                "ok": True,
                "account_id": str(row["account_id"]),
                "username": str(row["account_id"]),
                "session_id": str(row["id"]),
                "token": new_token,
                "token_type": "Bearer",
                "expires_at": int(expires_at),
                "expires_in": int(self.token_ttl),
                "rotated": True,
            }

    def logout(self, token: Any, now: float | None = None) -> dict[str, Any]:
        """注销令牌：会话立即 expired，不允许再走 60 秒重连。"""
        now_f = self._now(now)
        raw = str(token or "").strip()
        if not raw:
            return {"ok": False, "code": "auth_missing_token"}
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM sessions WHERE token_hash=?", (self._hash_token(raw),)
            ).fetchone()
            if row is None:
                return {"ok": False, "code": "auth_invalid_token"}
            self.conn.execute(
                "UPDATE sessions SET status='expired', last_seen_at=? WHERE id=?",
                (now_f, str(row["id"])),
            )
            self.conn.commit()
            return {"ok": True, "logged_out": True, "session_id": str(row["id"])}

    def disconnect(self, session_id: str, tick: int, now: float | None = None) -> bool:
        """标记断线；记录断线 tick，供 60 秒重连恢复。"""
        now_f = self._now(now)
        with self.lock:
            row = self.conn.execute("SELECT * FROM sessions WHERE id=?", (str(session_id),)).fetchone()
            if row is None or str(row["status"]) == "expired":
                return False
            self.conn.execute(
                "UPDATE sessions SET status='disconnected', disconnected_at=?, tick_at_disconnect=?"
                " WHERE id=?",
                (now_f, int(tick), str(session_id)),
            )
            self.conn.commit()
            return True

    def reconnect(self, session_id: str, now: float | None = None) -> dict[str, Any]:
        """60 秒内沿用原会话；超时则作废旧会话并新建（存档在事件日志里，不受影响）。"""
        now_f = self._now(now)
        with self.lock:
            row = self.conn.execute("SELECT * FROM sessions WHERE id=?", (str(session_id),)).fetchone()
            if row is None:
                return {"ok": False, "code": "auth_invalid_token"}
            if str(row["status"]) == "expired" or float(row["expires_at"]) <= now_f:
                return {"ok": False, "code": "auth_expired_token"}
            if row["disconnected_at"] is None:
                self.conn.execute(
                    "UPDATE sessions SET last_seen_at=? WHERE id=?", (now_f, str(session_id))
                )
                self.conn.commit()
                return {
                    "ok": True,
                    "resumed": True,
                    "new_session": False,
                    "session_id": str(row["id"]),
                    "account_id": str(row["account_id"]),
                    "token": None,
                    "expires_at": int(row["expires_at"]),
                    "tick_at_disconnect": row["tick_at_disconnect"],
                }
            elapsed = now_f - float(row["disconnected_at"])
            if elapsed <= float(self.reconnect_grace):
                self.conn.execute(
                    "UPDATE sessions SET status='active', disconnected_at=NULL, last_seen_at=?"
                    " WHERE id=?",
                    (now_f, str(session_id)),
                )
                self.conn.commit()
                return {
                    "ok": True,
                    "resumed": True,
                    "new_session": False,
                    "session_id": str(row["id"]),
                    "account_id": str(row["account_id"]),
                    "token": None,
                    "expires_at": int(row["expires_at"]),
                    "tick_at_disconnect": row["tick_at_disconnect"],
                }
            # 超过宽限：旧会话作废，新会话沿用同一账号；存档不动。
            self.conn.execute("UPDATE sessions SET status='expired' WHERE id=?", (str(session_id),))
            created = self._create_session(str(row["account_id"]), now_f)
            created.update(
                {
                    "resumed": True,
                    "new_session": True,
                    "tick_at_disconnect": row["tick_at_disconnect"],
                }
            )
            created["reconnect"] = {
                "resumed": True,
                "new_session": True,
                "session_id": created["session_id"],
                "account_id": str(row["account_id"]),
                "token": created["token"],
                "expires_at": created["expires_at"],
                "tick_at_disconnect": row["tick_at_disconnect"],
            }
            return created

    def purge_zombies(self, now: float | None = None) -> int:
        """清理僵尸会话：断线超过宽限期，或令牌已过期。只动 service.sqlite。"""
        now_f = self._now(now)
        cutoff = now_f - float(self.reconnect_grace)
        with self.lock:
            cur = self.conn.execute(
                "UPDATE sessions SET status='expired'"
                " WHERE status='disconnected' AND disconnected_at IS NOT NULL AND disconnected_at < ?",
                (cutoff,),
            )
            count = int(cur.rowcount or 0)
            cur = self.conn.execute(
                "UPDATE sessions SET status='expired' WHERE status='active' AND expires_at <= ?",
                (now_f,),
            )
            count += int(cur.rowcount or 0)
            self.conn.commit()
            return count

    def list_sessions(self, account: str | None = None) -> list[dict[str, Any]]:
        with self.lock:
            if account:
                rows = self.conn.execute(
                    "SELECT * FROM sessions WHERE account_id=? ORDER BY created_at DESC",
                    (normalize_username(account),),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT * FROM sessions ORDER BY created_at DESC"
                ).fetchall()
            return [self._row(r) or {} for r in rows]
