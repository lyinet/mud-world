"""标准库 HTTP 层：只做 JSON 进出，所有语义在 WorldSession。

鉴权边界只有一个闸门：`auth_required` 为真时，所有写端点必须先通过
Bearer 令牌校验；不存在“配置存在但仍可匿名写”的旁路。
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .auth import AccountStore, error_payload
from .session import WorldSession

_GET_ROUTES = {
    "/api/player/state": "state",
    "/api/player/status": "status",
    "/api/player/quests": "quests",
}
_POST_ROUTES = {
    "/api/player/action": "action",
    "/api/player/enter": "enter",
    "/api/player/heartbeat": "heartbeat",
    "/api/player/leave": "leave",
    "/api/player/move": "move",
    "/api/player/talk": "talk",
    "/api/player/trade": "trade",
    "/api/player/quest/accept": "quest/accept",
    "/api/player/quest/deliver": "quest/deliver",
}
_AUTH_ROUTES = {
    "/api/auth/register": "register",
    "/api/auth/login": "login",
    "/api/auth/logout": "logout",
    "/api/auth/refresh": "refresh",
}
_AUTH_ME = "/api/auth/me"
_RECONNECT_ROUTE = "/api/player/reconnect"
_SERVICE_INFO = "/api/service/info"
_INTENTS_PREFIX = "/api/player/intents/"

# 席位/额度类业务拒绝：用 429 明确表达“不是服务端 500”。
_CAPACITY_CODES = frozenset({"player_cap_reached", "endowment_cap_reached"})

_AUTH_STATUS = {
    "auth_missing_token": 401,
    "auth_invalid_token": 401,
    "auth_expired_token": 401,
    "auth_account_mismatch": 403,
    "auth_bad_username": 400,
    "auth_bad_password": 400,
    "auth_username_taken": 409,
    "auth_invalid_credentials": 401,
    "auth_disabled": 503,
}


def resolve_auth_required(root: str | Path, mode: str = "auto") -> bool:
    """auto：有 deploy/service.yaml 才强制鉴权；on/off 为显式覆盖。"""
    if mode == "on":
        return True
    if mode == "off":
        return False
    return (Path(root) / "deploy" / "service.yaml").exists()


class _Handler(BaseHTTPRequestHandler):
    server_version = "wsim-player/1.0"

    def log_message(self, fmt: str, *args) -> None:  # 静默：不污染 playthrough 叙事
        return

    # ------------------------------------------------------------- primitives
    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        data = json.loads(raw.decode("utf-8") or "{}")
        if not isinstance(data, dict):
            raise ValueError("body_must_be_object")
        return data

    def _query(self) -> dict[str, str]:
        parsed = urlparse(self.path)
        return {k: v[-1] for k, v in parse_qs(parsed.query).items()}

    def _path(self) -> str:
        return urlparse(self.path).path.rstrip("/") or "/"

    def _session(self) -> WorldSession:
        return self.server.session  # type: ignore[attr-defined]

    def _accounts(self) -> AccountStore | None:
        return getattr(self.server, "accounts", None)  # type: ignore[attr-defined]

    def _auth_required(self) -> bool:
        return bool(getattr(self.server, "auth_required", False))  # type: ignore[attr-defined]

    def _bearer(self) -> str:
        header = str(self.headers.get("Authorization") or "")
        if header.lower().startswith("bearer "):
            return header[len("bearer "):].strip()
        return ""

    def _send_auth_error(self, code: str, status: int | None = None) -> None:
        self._send(status or _AUTH_STATUS.get(code, 401), error_payload(code, self._session().state.tick))

    def _resolve_token(self) -> tuple[dict[str, Any] | None, str | None]:
        accounts = self._accounts()
        if accounts is None:
            return None, "auth_disabled"
        return accounts.resolve_token(self._bearer())

    def _read_body_or_400(self) -> dict[str, Any] | None:
        try:
            return self._body()
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            self._send(
                400,
                {
                    "ok": False,
                    "code": "bad_json",
                    "reject_reason": "bad_json",
                    "event_seq": 0,
                    "tick": self._session().state.tick,
                    "reason_text": "请求体必须是合法 JSON 对象。",
                },
            )
            return None

    # ------------------------------------------------------------------- GET
    def do_GET(self) -> None:  # noqa: N802
        session = self._session()
        path = self._path()
        query = self._query()
        account = query.get("account_id", "")
        if path == _SERVICE_INFO:
            self._send(
                200,
                {
                    "ok": True,
                    "auth_required": self._auth_required(),
                    "tick": int(session.state.tick),
                    "run": session.run_id,
                    "profile_version": 1,
                },
            )
            return
        if path == _AUTH_ME:
            row, err = self._resolve_token()
            if err or row is None:
                self._send_auth_error(err or "auth_invalid_token")
                return
            self._send(
                200,
                {
                    "ok": True,
                    "account_id": str(row["account_id"]),
                    "username": str(row["account_id"]),
                    "session_id": str(row["id"]),
                    "expires_at": int(row["expires_at"]),
                    "status": str(row["status"]),
                    "disconnected": row["disconnected_at"] is not None,
                },
            )
            return
        if path.startswith(_INTENTS_PREFIX):
            intent_id = unquote(path[len(_INTENTS_PREFIX):])
            if not intent_id:
                self._send(400, {"ok": False, "reject_reason": "bad_intent_id",
                                 "reason_text": "intent_id 不能为空。", "tick": session.state.tick})
                return
            self._send(200, session.intent(account, intent_id))
            return
        if path == "/api/player/messages":
            self._send(200, session.messages(account, query.get("since_tick", "0")))
            return
        route = _GET_ROUTES.get(path)
        if route is None:
            self._send(404, {"ok": False, "reject_reason": "unknown_action",
                             "reason_text": "未知接口；可用接口见 docs/player-api.md。"})
            return
        if route == "state":
            payload = session.player_state(account)
        elif route == "status":
            payload = session.status(account)
        else:
            payload = session.quests(account)
        payload.setdefault("profile_version", 1)
        self._send(200, payload)

    # ------------------------------------------------------------------ POST
    def do_POST(self) -> None:  # noqa: N802
        path = self._path()
        auth_route = _AUTH_ROUTES.get(path)
        if auth_route is not None:
            self._handle_auth(auth_route)
            return
        if path == _RECONNECT_ROUTE:
            self._handle_reconnect()
            return
        route = _POST_ROUTES.get(path)
        if route is None:
            self._send(404, {"ok": False, "reject_reason": "unknown_action",
                             "reason_text": "未知接口；可用接口见 docs/player-api.md。"})
            return
        self._handle_write(route)

    def _handle_write(self, route: str) -> None:
        session = self._session()
        row: dict[str, Any] | None = None
        if self._auth_required():
            # 安全边界：配置存在时先鉴权；连账号库缺失也失败关闭，而不是匿名放行。
            accounts = self._accounts()
            if accounts is None:
                self._send_auth_error("auth_disabled", 503)
                return
            row, err = accounts.resolve_token(self._bearer())
            if err or row is None:
                self._send_auth_error(err or "auth_invalid_token")
                return
        body = self._read_body_or_400()
        if body is None:
            return
        body_account = str(body.get("account_id") or "").strip().lower()
        if row is not None:
            token_account = str(row["account_id"])
            if body_account and body_account != token_account:
                self._send(403, error_payload("auth_account_mismatch", session.state.tick))
                return
            # 令牌账号是唯一事实源；客户端未带 account_id 时自动补齐。
            body["account_id"] = token_account
            account = token_account
        else:
            account = body_account
        request_id = str(body.get("request_id") or "")
        if route == "action":
            verb = str(body.get("verb") or body.get("action") or "")
            raw_args = body.get("args")
            args = raw_args if isinstance(raw_args, dict) else body
            result = session.action(
                account,
                verb,
                args,
                body.get("client_tick"),
                request_id,
                bool(body.get("confirm")),
            )
        else:
            result = session.perform(account, route, body, request_id)
        status = 429 if str(result.get("reject_reason") or "") in _CAPACITY_CODES else 200
        if status == 429:
            # 原因码双写：老客户端读 reject_reason，新客户端读 code。
            result.setdefault("code", str(result.get("reject_reason") or ""))
        # leave 成功即记录断线 tick，60 秒重连据此判断同一会话/新会话。
        if route == "leave" and row is not None and bool(result.get("ok")):
            accounts = self._accounts()
            if accounts is not None:
                accounts.disconnect(str(row["id"]), int(session.state.tick))
                result["session_id"] = str(row["id"])
                result["session_disconnected"] = True
        self._send(status, result)

    def _handle_auth(self, route: str) -> None:
        accounts = self._accounts()
        if accounts is None:
            self._send_auth_error("auth_disabled", 503)
            return
        if route in ("register", "login"):
            body = self._read_body_or_400()
            if body is None:
                return
            if route == "register":
                res = accounts.register(body.get("username"), body.get("password"))
                if not res.get("ok"):
                    self._send_auth_error(str(res.get("code") or "auth_bad_username"))
                    return
                self._send(
                    200,
                    {
                        "ok": True,
                        "account_id": res["account_id"],
                        "username": res["username"],
                        "created": True,
                    },
                )
                return
            res = accounts.login(body.get("username"), body.get("password"))
            if not res.get("ok"):
                self._send_auth_error(str(res.get("code") or "auth_invalid_credentials"))
                return
            self._send(200, res)
            return

        token = self._bearer()
        if route == "refresh":
            res = accounts.refresh(token)
            if not res.get("ok"):
                self._send_auth_error(str(res.get("code") or "auth_invalid_token"))
                return
            self._send(200, res)
            return
        res = accounts.logout(token)
        if not res.get("ok"):
            self._send_auth_error(str(res.get("code") or "auth_invalid_token"))
            return
        self._send(200, res)

    def _handle_reconnect(self) -> None:
        session = self._session()
        accounts = self._accounts()
        if accounts is None:
            self._send_auth_error("auth_disabled", 503)
            return
        # 不 touch last_seen_at：要用原始空闲时长判断“静默断线”是否超过宽限。
        row, err = accounts.resolve_token(self._bearer(), touch=False)
        if err or row is None:
            self._send_auth_error(err or "auth_invalid_token")
            return
        body = self._read_body_or_400()
        if body is None:
            return
        body_account = str(body.get("account_id") or "").strip().lower()
        token_account = str(row["account_id"])
        if body_account and body_account != token_account:
            self._send(403, error_payload("auth_account_mismatch", session.state.tick))
            return
        rec = accounts.reconnect(str(row["id"]))
        if not rec.get("ok"):
            self._send_auth_error(str(rec.get("code") or "auth_invalid_token"))
            return
        payload = session.resume(
            token_account,
            session_id=str(rec["session_id"]),
            token=str(rec.get("token") or self._bearer()),
            expires_at=int(rec.get("expires_at") or row["expires_at"]),
            new_session=bool(rec.get("new_session")),
            tick_at_disconnect=rec.get("tick_at_disconnect"),
            reconnect_grace=accounts.reconnect_grace,
        )
        status = 429 if str(payload.get("reject_reason") or "") in _CAPACITY_CODES else 200
        self._send(status, payload)


def create_server(
    session: WorldSession,
    host: str = "127.0.0.1",
    port: int = 0,
    accounts: AccountStore | None = None,
    auth_required: bool = False,
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, int(port)), _Handler)
    server.session = session  # type: ignore[attr-defined]
    server.accounts = accounts  # type: ignore[attr-defined]
    server.auth_required = bool(auth_required)  # type: ignore[attr-defined]
    server.daemon_threads = True
    return server


def serve(
    session: WorldSession,
    host: str = "127.0.0.1",
    port: int = 0,
    accounts: AccountStore | None = None,
    auth_required: bool = False,
) -> tuple[ThreadingHTTPServer, int]:
    """启动后台服务线程，返回 (server, 实际端口)。调用方负责 `server.shutdown()`。"""
    server = create_server(session, host, port, accounts=accounts, auth_required=auth_required)
    thread = threading.Thread(target=server.serve_forever, name="wsim-player-api", daemon=True)
    thread.start()
    return server, int(server.server_address[1])
