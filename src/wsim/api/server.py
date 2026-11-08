"""标准库 HTTP 层：只做 JSON 进出，所有语义在 WorldSession。

鉴权边界只有一个闸门：`auth_required` 为真时，所有写端点必须先通过
Bearer 令牌校验；不存在“配置存在但仍可匿名写”的旁路。
"""
from __future__ import annotations

import hmac
import ipaddress
import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .auth import AccountStore, error_payload
from .health import probe_from_session
from .metrics import HttpMetrics
from .session import WorldSession

_GET_ROUTES = {
    "/api/player/state": "state",
    "/api/state": "state",  # 只读别名，与主路径共用同一 handler/鉴权
    "/api/player/status": "status",
    "/api/player/quests": "quests",
    "/api/player/sects": "sects",
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
    # xkx 文字壳：一行命令走完 look/go/ask/buy/score/inventory 等。
    "/api/xkx/command": "xkx/command",
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
_HEALTH = "/api/health"
_WORLD_FEED = "/api/world/feed"
_WORLD_MAP = "/api/world/map"
# xkx 帮助目录：153 命令 / 32 主题，公共只读，不含玩家数据。
_XKX_HELP = "/api/xkx/help"
_INTENTS_PREFIX = "/api/player/intents/"
_METRICS = "/api/metrics"

# 请求体/头部上限与超时：防止超大 JSON 与慢连接耗尽内存/线程。
_MAX_BODY_BYTES = 8 * 1024
_MAX_HEADER_BYTES = 8 * 1024
_REQUEST_TIMEOUT = 30.0


class _PayloadTooLarge(Exception):
    pass


class _HeadersTooLarge(Exception):
    pass

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
    "too_many_attempts": 429,
}


def parse_trusted_proxies(raw: str | None) -> tuple[Any, ...]:
    """把 `XKX_TRUSTED_PROXIES`（逗号分隔 CIDR/IP）解析为网段元组。

    非法项直接忽略并打印告警；空值返回空元组 = 不信任任何转发头。
    """
    nets: list[Any] = []
    for part in str(raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            print(f"[auth] 忽略非法可信代理网段: {part!r}", file=sys.stderr, flush=True)
    return tuple(nets)


def _in_net(addr: Any, net: Any) -> bool:
    try:
        return addr in net
    except TypeError:  # IPv4 与 IPv6 网段不互通
        return False


def resolve_client_ip(remote: str, headers: Any, trusted: tuple[Any, ...]) -> str:
    """解析限流用客户端 IP。

    - 未配置可信代理，或直连来源不在可信网段：一律返回 TCP 对端地址，
      忽略所有转发头（防伪造）。
    - 直连来源可信：`X-Forwarded-For` 从右向左跳过可信代理，取第一个
      不可信跳（标准做法）；全部可信则取最左；无 XFF 时回退 `X-Real-IP`。
    - 畸形转发头 → 回退 TCP 对端地址（fail-closed）。
    """
    remote_s = str(remote or "")
    if not trusted:
        return remote_s
    try:
        peer = ipaddress.ip_address(remote_s)
    except ValueError:
        return remote_s
    if not any(_in_net(peer, net) for net in trusted):
        return remote_s

    xff = str(headers.get("X-Forwarded-For") or "") if headers is not None else ""
    hops = [h.strip() for h in xff.split(",") if h.strip()]
    if hops:
        parsed: list[Any] = []
        for hop in hops:
            try:
                parsed.append(ipaddress.ip_address(hop))
            except ValueError:
                return remote_s
        for addr in reversed(parsed):
            if any(_in_net(addr, net) for net in trusted):
                continue
            return str(addr)
        return str(parsed[0])

    real = str(headers.get("X-Real-IP") or "").strip() if headers is not None else ""
    if real:
        try:
            return str(ipaddress.ip_address(real))
        except ValueError:
            return remote_s
    return remote_s


def _ambient_status_path(session: WorldSession) -> Path:
    raw = os.environ.get("XKX_AMBIENT_STATUS") or ""
    if raw:
        return Path(raw)
    return Path(session.run_dir) / "ambient" / "ambient_status.json"


def _ambient_counts(session: WorldSession) -> tuple[int, int]:
    """ambient_status.json 的累计计数：(暂停次数, LLM 调用数)；缺失/损坏都按 0。"""
    try:
        data = json.loads(_ambient_status_path(session).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0, 0
    if not isinstance(data, dict):
        return 0, 0
    cpu = data.get("cpu") if isinstance(data.get("cpu"), dict) else {}
    try:
        paused = int(cpu.get("pauses") or 0)
    except (TypeError, ValueError):
        paused = 0
    try:
        calls = int(data.get("llm_calls") or 0)
    except (TypeError, ValueError):
        calls = 0
    return max(0, paused), max(0, calls)


def _dialogue_llm_payload(session: WorldSession) -> dict[str, Any]:
    """只读进程内计数器与开关状态；探活里不发网络请求、不读全库。"""
    from ..agent import dialogue_runtime

    on = dialogue_runtime.enabled()
    queue = getattr(session, "dialogue_queue", None)
    if not on:
        status = "disabled"
    elif queue is None:
        status = "unconfigured"
    else:
        status = "active"
    payload: dict[str, Any] = {
        "status": status,
        "enabled": on,
        "model": str(getattr(session, "dialogue_model", "") or ""),
        "source": str(getattr(queue, "config_source", "") or "") if queue else "",
        "calls": 0,
        "ok": 0,
        "failures": 0,
        "timeouts": 0,
        "dropped_backlog": 0,
        "last_latency_s": 0.0,
        "by_slot": {"dialogue": 0},
        "decision_calls": 0,
    }
    if queue is not None:
        stats = queue.stats()
        calls = int(stats.get("calls") or 0)
        by_reason = dict(stats.get("fallback_by_reason") or {})
        payload.update(
            calls=calls,
            ok=int(stats.get("ok") or 0),
            failures=int(stats.get("fallback") or 0),
            timeouts=int(stats.get("timeouts") or by_reason.get("timeout") or 0),
            dropped_backlog=int(stats.get("dropped_backlog") or 0),
            last_latency_s=float(stats.get("last_latency_s") or 0.0),
            by_slot={"dialogue": calls},
        )
    return payload


def _ambient_health_payload(session: WorldSession) -> dict[str, Any]:
    """只读 ambient_status.json；缺失/过期/进程消失都收敛为 stopped。"""
    base: dict[str, Any] = {
        "ambient": "stopped",
        "ambient_tick": None,
        "ambient_last_tick_at": "",
        "ambient_reason": "status_file_missing",
        "ambient_pid": None,
        "ambient_speed": None,
    }
    path = _ambient_status_path(session)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return base
    if not isinstance(data, dict):
        return base
    state = str(data.get("state") or "")
    if state not in ("running", "paused"):
        base.update(
            {
                "ambient": "stopped",
                "ambient_tick": data.get("tick"),
                "ambient_last_tick_at": str(data.get("last_tick_at") or ""),
                "ambient_reason": str(data.get("reason") or state or "stopped"),
                "ambient_pid": data.get("pid"),
                "ambient_speed": data.get("speed"),
            }
        )
        return base
    try:
        stale_s = float(os.environ.get("XKX_AMBIENT_STALE_S") or 120.0)
    except ValueError:
        stale_s = 120.0
    try:
        age = max(0.0, time.clock_gettime(time.CLOCK_REALTIME) - path.stat().st_mtime)
    except OSError:
        age = stale_s + 1.0
    if age > stale_s:
        base.update(
            {
                "ambient": "stopped",
                "ambient_tick": data.get("tick"),
                "ambient_last_tick_at": str(data.get("last_tick_at") or ""),
                "ambient_reason": "status_stale",
                "ambient_pid": data.get("pid"),
                "ambient_speed": data.get("speed"),
            }
        )
        return base
    pid = data.get("pid")
    if isinstance(pid, int):
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            base.update(
                {
                    "ambient": "stopped",
                    "ambient_tick": data.get("tick"),
                    "ambient_last_tick_at": str(data.get("last_tick_at") or ""),
                    "ambient_reason": "process_gone",
                    "ambient_pid": pid,
                    "ambient_speed": data.get("speed"),
                }
            )
            return base
    return {
        "ambient": state,
        # 自走与玩家共用同一份事件日志：running/paused 时以世界 tick 为准。
        "ambient_tick": int(session.state.tick),
        "ambient_last_tick_at": str(data.get("last_tick_at") or ""),
        "ambient_reason": str(data.get("reason") or ""),
        "ambient_pid": pid,
        "ambient_speed": data.get("speed"),
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

    def setup(self) -> None:
        super().setup()
        timeout = float(getattr(self.server, "request_timeout", _REQUEST_TIMEOUT))
        if timeout > 0:
            try:
                self.connection.settimeout(timeout)
            except OSError:
                pass

    def _max_body_bytes(self) -> int:
        return int(getattr(self.server, "max_body_bytes", _MAX_BODY_BYTES))

    def _max_header_bytes(self) -> int:
        return int(getattr(self.server, "max_header_bytes", _MAX_HEADER_BYTES))

    def _cors_origin(self) -> str:
        """为空表示只允许同源（不下发 CORS 头，由浏览器拦截跨源）。"""
        return str(getattr(self.server, "cors_origin", "") or "")

    def _cors_headers(self) -> dict[str, str]:
        origin = self._cors_origin()
        if not origin:
            return {}
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Metrics-Token",
            "Vary": "Origin",
        }

    def _check_headers(self) -> bool:
        """请求头总字节数超限返回 False（调用方回 431）。"""
        total = 0
        for name, value in self.headers.items():
            total += len(str(name)) + len(str(value)) + 4  # ": " + CRLF
        return total <= self._max_header_bytes()

    # ------------------------------------------------------------- primitives
    def _send(
        self, status: int, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> None:
        self._status_code = int(status)
        metrics = getattr(self.server, "metrics", None)  # type: ignore[attr-defined]
        if metrics is not None and isinstance(payload, dict) and payload.get("ok") is False:
            code = str(payload.get("reject_reason") or payload.get("code") or "")
            if code:
                metrics.rejection(code)
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for name, value in self._cors_headers().items():
            self.send_header(name, value)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, status: int, text: str, content_type: str) -> None:
        self._status_code = int(status)
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in self._cors_headers().items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length > self._max_body_bytes():
            raise _PayloadTooLarge
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

    def _client_ip(self) -> str:
        """限流用客户端 IP。

        默认只信 TCP 对端地址（X-Forwarded-For 可伪造）。仅当对端落在
        `XKX_TRUSTED_PROXIES` 网段内时，才按“最右侧可信跳”解析转发头。
        """
        remote = str(self.client_address[0]) if self.client_address else ""
        trusted = getattr(self.server, "trusted_proxies", ())  # type: ignore[attr-defined]
        return resolve_client_ip(remote, self.headers, trusted)

    def _send_auth_error(
        self, code: str, status: int | None = None, retry_after: int | None = None
    ) -> None:
        payload = error_payload(code, self._session().state.tick)
        headers: dict[str, str] = {}
        if retry_after is not None:
            payload["retry_after"] = int(retry_after)
            headers["Retry-After"] = str(int(retry_after))
        self._send(status or _AUTH_STATUS.get(code, 401), payload, headers)

    def _resolve_token(self) -> tuple[dict[str, Any] | None, str | None]:
        accounts = self._accounts()
        if accounts is None:
            return None, "auth_disabled"
        return accounts.resolve_token(self._bearer())

    def _read_body_or_400(self) -> dict[str, Any] | None:
        try:
            return self._body()
        except _PayloadTooLarge:
            self._send(
                413,
                {
                    "ok": False,
                    "code": "payload_too_large",
                    "reject_reason": "payload_too_large",
                    "event_seq": 0,
                    "tick": self._session().state.tick,
                    "reason_text": f"请求体超过 {self._max_body_bytes()} 字节上限。",
                },
            )
            return None
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

    def _reject_headers(self) -> None:
        self._send(
            431,
            {
                "ok": False,
                "code": "header_too_large",
                "reject_reason": "header_too_large",
                "tick": self._session().state.tick,
                "reason_text": f"请求头超过 {self._max_header_bytes()} 字节上限。",
            },
        )

    def _send_timeout(self) -> None:
        try:
            self._send(
                408,
                {
                    "ok": False,
                    "code": "request_timeout",
                    "reject_reason": "request_timeout",
                    "tick": self._session().state.tick,
                    "reason_text": "请求读取超时。",
                },
            )
        except OSError:
            pass

    # ------------------------------------------------------------------- GET
    def do_GET(self) -> None:  # noqa: N802
        """把每次 GET 的耗时/状态码计入内存指标；不进入任何写事务。"""
        started = time.perf_counter()
        self._status_code = 200
        try:
            if not self._check_headers():
                self._reject_headers()
                return
            self._do_GET()
        except (socket.timeout, TimeoutError):
            self._send_timeout()
        finally:
            self._observe(started)

    def do_OPTIONS(self) -> None:  # noqa: N802
        """CORS 预检：只有配置了 XKX_CORS_ORIGIN 才下发跨源头。"""
        self._status_code = 204
        self.send_response(204)
        self.send_header("Content-Length", "0")
        for name, value in self._cors_headers().items():
            self.send_header(name, value)
        self.end_headers()

    def _do_GET(self) -> None:
        session = self._session()
        path = self._path()
        query = self._query()
        account = query.get("account_id", "")
        if path == _METRICS:
            self._handle_metrics()
            return
        if path == _HEALTH:
            # 真实探活：events 可读/seq 连续/integrity（60s 缓存）/磁盘/写者停滞；
            # 既有键（ok/mode/readonly/profile_version/upstream_ok）语义保持不变。
            payload = self.server.health_probe.snapshot()  # type: ignore[attr-defined]
            payload.update(_ambient_health_payload(session))
            payload["llm"] = _dialogue_llm_payload(session)
            payload["llm_calls"] = payload["llm"]["calls"]
            payload["llm_by_slot"] = dict(payload["llm"]["by_slot"])
            self._send(200 if payload.get("ok") else 503, payload)
            return
        if path == _SERVICE_INFO:
            self._send(
                200,
                {
                    "ok": True,
                    "auth_required": self._auth_required(),
                    "tick": int(session.state.tick),
                    "run": session.run_id,
                    "profile_version": 1,
                    **_ambient_health_payload(session),
                },
            )
            return
        if path == _WORLD_FEED:
            # 公共世界事件流：匿名可读（service/legacy 两种模式相同），不含玩家私有状态。
            self._send(200, session.world_feed(query.get("limit", "20")))
            return
        if path == _WORLD_MAP:
            # 公共世界地图：节点/邻接/可达性，只读，无价格库存等敏感字段。
            self._send(200, session.world_map())
            return
        if path == _XKX_HELP:
            # 游戏内 help 与输入补全共用；公共静态目录，匿名可读。
            self._send(200, session.xkx_help_catalog())
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
        identity, auth_err = self._read_identity()
        if auth_err:
            self._send_auth_error(auth_err)
            return
        requested = str(account or "").strip().lower()
        if identity is not None and requested and requested != identity:
            # 与写端点同规则：令牌账号是唯一事实源，不接受读别人的状态。
            self._send(403, error_payload("auth_account_mismatch", session.state.tick))
            return
        # 服务模式：无令牌只看公共摘要；legacy 模式保持匿名全量（行为不变）。
        anonymous = identity is None and self._auth_required()
        target = identity or account
        if path.startswith(_INTENTS_PREFIX):
            intent_id = unquote(path[len(_INTENTS_PREFIX):])
            if not intent_id:
                self._send(400, {"ok": False, "reject_reason": "bad_intent_id",
                                 "reason_text": "intent_id 不能为空。", "tick": session.state.tick})
                return
            if anonymous:
                # 个人动作结果，且 intent_id/request_id 可枚举：服务模式必须带令牌。
                self._send_auth_error("auth_missing_token")
                return
            self._send(200, session.intent(target, intent_id))
            return
        if path == "/api/player/messages":
            since = query.get("since_tick", "0")
            since_seq = query.get("since_seq")
            if anonymous:
                self._send(200, session.public_messages(target, since))
            else:
                self._send(200, session.messages(target, since, since_seq))
            return
        route = _GET_ROUTES.get(path)
        if route is None:
            self._send(404, {"ok": False, "reject_reason": "unknown_action",
                             "reason_text": "未知接口；可用接口见 docs/player-api.md。"})
            return
        if anonymous:
            if route == "quests":
                other = session.public_quests()
            elif route == "sects":
                # 公共门派列表：匿名只给门派自身的公开字段，不按 account_id 个性化。
                other = session.sects_view("")
            else:
                other = session.public_state(target)
            self._send(200, other)
            return
        if route == "state":
            payload = session.player_state(target)
        elif route == "status":
            payload = session.status(target)
        elif route == "sects":
            payload = session.sects_view(identity or "")
        else:
            payload = session.quests(target)
        payload.setdefault("profile_version", 1)
        self._send(200, payload)

    # ---------------------------------------------------------------- metrics
    def _observe(self, started: float) -> None:
        metrics = getattr(self.server, "metrics", None)  # type: ignore[attr-defined]
        if metrics is None:
            return
        try:
            path = self._path()
        except Exception:
            path = "/"
        metrics.observe(
            path, int(getattr(self, "_status_code", 0) or 0), time.perf_counter() - started
        )

    def _metrics_allowed(self) -> bool:
        """默认只在绑定回环地址时开放；否则必须带正确的 XKX_METRICS_TOKEN。"""
        public = bool(getattr(self.server, "metrics_public", True))  # type: ignore[attr-defined]
        if public:
            return True
        token = str(getattr(self.server, "metrics_token", "") or "")  # type: ignore[attr-defined]
        if not token:
            return False
        supplied = self._bearer() or str(self.headers.get("X-Metrics-Token") or "")
        return bool(supplied) and hmac.compare_digest(supplied, token)

    def _handle_metrics(self) -> None:
        metrics = getattr(self.server, "metrics", None)  # type: ignore[attr-defined]
        if metrics is None:
            metrics = HttpMetrics()
        if not self._metrics_allowed():
            # 不暴露端点存在：与未知路由同形。
            self._send(
                404,
                {
                    "ok": False,
                    "reject_reason": "unknown_action",
                    "reason_text": "未知接口；可用接口见 docs/player-api.md。",
                },
            )
            return
        session = self._session()
        accounts = self._accounts()
        db_sizes: dict[str, int] = {}
        for label, raw in (
            ("events.sqlite", str(getattr(session.store, "path", "") or "")),
            ("service.sqlite", str(getattr(accounts, "path", "") or "") if accounts else ""),
        ):
            if not raw or raw == ":memory:":
                continue
            try:
                db_sizes[label] = int(Path(raw).stat().st_size)
            except OSError:
                db_sizes[label] = 0
        paused, llm_calls = _ambient_counts(session)
        dialogue_calls = int(_dialogue_llm_payload(session)["calls"])
        text = metrics.render(
            world_tick=int(session.state.tick),
            events_total=int(session.store.count()),
            players_online=sum(1 for p in session.state.players.values() if getattr(p, "online", False)),
            ambient_paused_total=paused,
            llm_calls=llm_calls,
            dialogue_llm_calls=dialogue_calls,
            db_sizes=db_sizes,
        )
        self._send_text(200, text, "text/plain; version=0.0.4; charset=utf-8")

    def _read_identity(self) -> tuple[str | None, str | None]:
        """读请求的令牌身份：service 模式校验，legacy 模式完全不解析。

        返回 `(令牌账号, 错误码)`；`(None, None)` 表示匿名。
        读请求不刷新 `last_seen_at`（`touch=False`），否则读一次就把
        60 秒断线宽限续上，重连语义会被读请求污染。
        """
        if not self._auth_required() or not self._bearer():
            return None, None
        accounts = self._accounts()
        if accounts is None:
            return None, "auth_disabled"
        row, err = accounts.resolve_token(self._bearer(), touch=False)
        if err or row is None:
            return None, err or "auth_invalid_token"
        return str(row["account_id"]), None

    # ------------------------------------------------------------------ POST
    def do_POST(self) -> None:  # noqa: N802
        """把每次 POST 的耗时/状态码计入内存指标；不进入任何写事务。"""
        started = time.perf_counter()
        self._status_code = 200
        try:
            if not self._check_headers():
                self._reject_headers()
                return
            self._do_POST()
        except (socket.timeout, TimeoutError):
            self._send_timeout()
        finally:
            self._observe(started)

    def _do_POST(self) -> None:
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
            # 模型台词只覆盖本次 HTTP 响应的 details.reply；状态/事件仍是模板。
            # 事务已提交、会话锁已释放，模型慢/失败都不会阻塞 tick。
            result.setdefault("verb", verb)
            session.finalize_dialogue(result, args)
        elif route == "xkx/command":
            # 文字壳：读命令不写事件；走/问/买等写命令仍走 session.perform。
            result = session.xkx_command(account, body, request_id)
        elif route == "heartbeat" and bool(body.get("keepalive")):
            # 页面停留保活：按世界 tick 刷新活跃时间（≥12 tick 才补心跳事件）；离线仍
            # 返回 player_offline 供客户端自愈。
            result = session.keepalive(account)
        else:
            result = session.perform(account, route, body, request_id)
            if route == "talk":
                # 专用 /api/player/talk 走 perform 分支，同样要在事务提交后补配音。
                session.finalize_dialogue(result, body)
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
            res = accounts.login(
                body.get("username"), body.get("password"), ip=self._client_ip()
            )
            if not res.get("ok"):
                code = str(res.get("code") or "auth_invalid_credentials")
                retry = res.get("retry_after")
                self._send_auth_error(code, retry_after=(int(retry) if retry else None))
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
    metrics_public: bool | None = None,
    metrics_token: str | None = None,
    max_body_bytes: int | None = None,
    max_header_bytes: int | None = None,
    request_timeout: float | None = None,
    cors_origin: str | None = None,
    disk_min_free_pct: float | None = None,
    writer_idle_max_s: float | None = None,
    integrity_cache_s: float | None = None,
    trusted_proxies: str | tuple[Any, ...] | None = None,
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, int(port)), _Handler)
    server.session = session  # type: ignore[attr-defined]
    server.accounts = accounts  # type: ignore[attr-defined]
    server.auth_required = bool(auth_required)  # type: ignore[attr-defined]
    server.metrics = HttpMetrics()  # type: ignore[attr-defined]
    # 默认只有回环绑定才允许匿名抓取；非回环必须配置 XKX_METRICS_TOKEN。
    if metrics_public is None:
        metrics_public = host in ("127.0.0.1", "::1", "localhost")
    server.metrics_public = bool(metrics_public)  # type: ignore[attr-defined]
    if metrics_token is None:
        metrics_token = os.environ.get("XKX_METRICS_TOKEN", "")
    server.metrics_token = str(metrics_token or "")  # type: ignore[attr-defined]
    # 请求加固：上限与超时都可由环境变量覆盖，测试可显式注入更小值。
    if max_body_bytes is None:
        max_body_bytes = int(os.environ.get("XKX_MAX_BODY_BYTES", _MAX_BODY_BYTES))
    server.max_body_bytes = max(1, int(max_body_bytes))  # type: ignore[attr-defined]
    if max_header_bytes is None:
        max_header_bytes = int(os.environ.get("XKX_MAX_HEADER_BYTES", _MAX_HEADER_BYTES))
    server.max_header_bytes = max(1, int(max_header_bytes))  # type: ignore[attr-defined]
    if request_timeout is None:
        request_timeout = float(os.environ.get("XKX_REQUEST_TIMEOUT", _REQUEST_TIMEOUT))
    server.request_timeout = float(request_timeout)  # type: ignore[attr-defined]
    # CORS：默认空 = 只允许同源；XKX_CORS_ORIGIN 可显式放开某个来源（或 `*`）。
    if cors_origin is None:
        cors_origin = os.environ.get("XKX_CORS_ORIGIN", "")
    server.cors_origin = str(cors_origin or "")  # type: ignore[attr-defined]
    # 健康探针：阈值可显式注入（测试可把小阈值调大来构造降级场景）。
    server.health_probe = probe_from_session(  # type: ignore[attr-defined]
        session,
        disk_min_free_pct=disk_min_free_pct,
        writer_idle_max_s=writer_idle_max_s,
        integrity_cache_s=integrity_cache_s,
    )
    # 可信代理：仅这些直连来源的 X-Forwarded-For / X-Real-IP 才被采信。
    # 默认空（未配置）= 只用 TCP 对端地址，保持原有行为。
    if trusted_proxies is None:
        trusted_proxies = os.environ.get("XKX_TRUSTED_PROXIES", "")
    if isinstance(trusted_proxies, str):
        server.trusted_proxies = parse_trusted_proxies(trusted_proxies)  # type: ignore[attr-defined]
    else:
        server.trusted_proxies = tuple(trusted_proxies)  # type: ignore[attr-defined]
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
