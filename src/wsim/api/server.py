"""标准库 HTTP 层：只做 JSON 进出，所有语义在 WorldSession。"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

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
_INTENTS_PREFIX = "/api/player/intents/"


class _Handler(BaseHTTPRequestHandler):
    server_version = "wsim-player/1.0"

    def log_message(self, fmt: str, *args) -> None:  # 静默：不污染 playthrough 叙事
        return

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

    def do_GET(self) -> None:  # noqa: N802
        session: WorldSession = self.server.session  # type: ignore[attr-defined]
        path = self._path()
        query = self._query()
        account = query.get("account_id", "")
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

    def do_POST(self) -> None:  # noqa: N802
        session: WorldSession = self.server.session  # type: ignore[attr-defined]
        path = self._path()
        route = _POST_ROUTES.get(path)
        if route is None:
            self._send(404, {"ok": False, "reject_reason": "unknown_action",
                             "reason_text": "未知接口；可用接口见 docs/player-api.md。"})
            return
        try:
            body = self._body()
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            self._send(400, {"ok": False, "reject_reason": "bad_json", "event_seq": 0,
                             "tick": session.state.tick,
                             "reason_text": "请求体必须是合法 JSON 对象。"})
            return
        account = str(body.get("account_id") or "").strip().lower()
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
        self._send(200, result)


def create_server(session: WorldSession, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, int(port)), _Handler)
    server.session = session  # type: ignore[attr-defined]
    server.daemon_threads = True
    return server


def serve(session: WorldSession, host: str = "127.0.0.1", port: int = 0) -> tuple[ThreadingHTTPServer, int]:
    """启动后台服务线程，返回 (server, 实际端口)。调用方负责 `server.shutdown()`。"""
    server = create_server(session, host, port)
    thread = threading.Thread(target=server.serve_forever, name="wsim-player-api", daemon=True)
    thread.start()
    return server, int(server.server_address[1])
