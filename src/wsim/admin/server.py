"""管理 HTTP 服务（独立进程，默认 127.0.0.1:8031）。

- 只读写 `service.sqlite` 的 admin_* 表；不碰玩家端口、不 import `wsim.api`。
- 登录用 `XKX_ADMIN_TOKEN`，会话 24h 且库中只存 sha256；失败 10 次/5 分钟锁 15 分钟。
- 非回环来源必须命中 `XKX_ADMIN_ALLOW_IPS`，否则 403 `admin_ip_not_allowed`。
- 所有写接口鉴权 + 审计；响应/审计/日志不含明文 key。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets as pysecrets
import sqlite3
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import __version__, audit
from . import providers as provider_registry
from . import secrets as secret_store
from . import settings

SESSION_TTL_S = 24 * 3600
LOGIN_WINDOW_S = 300
LOGIN_MAX_FAILURES = 10
LOGIN_LOCK_S = 900
MAX_BODY_BYTES = 64 * 1024
PROVIDER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


class HttpError(RuntimeError):
    def __init__(self, status: int, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.status = status
        self.code = code
        self.detail = detail


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _iso(ts: float | None) -> str | None:
    if not ts:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(ts)))


def _file_size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except OSError:
        return 0


class AdminApp:
    """路由层的业务实现；与 HTTP handler 解耦，便于单测直接调用。"""

    def __init__(
        self,
        db_path: str | Path,
        *,
        root: str | Path = ".",
        run: str = "",
        clock=None,
    ):
        self.db_path = Path(db_path)
        settings.init_schema(self.db_path)
        self.root = Path(root).resolve()
        self.run = str(run or os.environ.get("XKX_RUN") or "player-demo")
        self.clock = clock or time.time
        self.started_at = float(self.clock())
        self.store = settings.SettingsStore(self.db_path, now_fn=self.clock)
        self.secrets = secret_store.SecretStore(self.db_path)
        self.snapshot = provider_registry.SnapshotCache(self.db_path)
        self.provider_test_cache = self.snapshot

    # ------------------------------------------------------------ 路径
    @property
    def run_dir(self) -> Path:
        return self.root / "runs" / self.run

    # ------------------------------------------------------------ 登录/会话
    def login(self, token: str, ip: str) -> dict[str, Any]:
        expected = str(os.environ.get("XKX_ADMIN_TOKEN", "")).strip()
        now = float(self.clock())
        conn = self._conn()
        try:
            conn.execute("DELETE FROM admin_login_failures WHERE failed_at < ?", (now - LOGIN_LOCK_S,))
            row = conn.execute(
                "SELECT COUNT(*) FROM admin_login_failures WHERE ip = ? AND failed_at >= ?",
                (ip, now - LOGIN_WINDOW_S),
            ).fetchone()
            failures = int(row[0] or 0)
            if failures >= LOGIN_MAX_FAILURES:
                raise HttpError(HTTPStatus.TOO_MANY_REQUESTS, "too_many_attempts")
            if not expected:
                raise HttpError(HTTPStatus.SERVICE_UNAVAILABLE, "admin_token_missing")
            if not token or not hmac.compare_digest(str(token), expected):
                conn.execute(
                    "INSERT INTO admin_login_failures(ip, failed_at) VALUES (?, ?)", (ip, now)
                )
                conn.execute("DELETE FROM admin_sessions WHERE expires_at <= ?", (now,))
                conn.commit()
                raise HttpError(HTTPStatus.UNAUTHORIZED, "invalid_admin_token")
            conn.execute("DELETE FROM admin_login_failures WHERE ip = ?", (ip,))
            conn.execute("DELETE FROM admin_sessions WHERE expires_at <= ?", (now,))
            session_token = pysecrets.token_urlsafe(32)
            expires_at = now + SESSION_TTL_S
            conn.execute(
                "INSERT INTO admin_sessions(token_hash, created_at, expires_at, who)"
                " VALUES (?, ?, ?, ?)",
                (_sha256(session_token), now, expires_at, "admin"),
            )
            conn.commit()
        finally:
            conn.close()
        return {
            "ok": True,
            "token": session_token,
            "expires_at": expires_at,
            "expires_at_iso": _iso(expires_at),
        }

    def require_auth(self, header: str | None) -> str:
        if not header or not header.startswith("Bearer "):
            raise HttpError(HTTPStatus.UNAUTHORIZED, "unauthorized")
        token = header[len("Bearer ") :].strip()
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT who, expires_at FROM admin_sessions WHERE token_hash = ?",
                (_sha256(token),),
            ).fetchone()
            if row is None:
                raise HttpError(HTTPStatus.UNAUTHORIZED, "unauthorized")
            if float(row[1]) <= float(self.clock()):
                conn.execute("DELETE FROM admin_sessions WHERE token_hash = ?", (_sha256(token),))
                conn.commit()
                raise HttpError(HTTPStatus.UNAUTHORIZED, "session_expired")
            return str(row[0])
        finally:
            conn.close()

    # ------------------------------------------------------------ settings
    def get_settings(self) -> dict[str, Any]:
        payload = self.store.get_all()
        payload["version"] = self.store.version()
        payload["hot"] = settings.hot_map()
        payload["hot_notes"] = settings.hot_notes()
        payload["capabilities_status"] = provider_registry.capabilities_status()
        payload["capabilities_evidence"] = provider_registry.capabilities_evidence()
        return payload

    def put_settings(self, patch: dict[str, Any], who: str) -> dict[str, Any]:
        result = self.store.update(patch, who=who, action="settings.update")
        return {
            "ok": True,
            "version": result["version"],
            "requires_restart": result["requires_restart"],
            "changed": result["changed"],
            "settings": self.get_settings(),
        }

    # ------------------------------------------------------------ providers
    def provider_view(self, entry: dict[str, Any]) -> dict[str, Any]:
        row = {key: value for key, value in entry.items() if key != "api_key"}
        provider_id = str(row.get("id") or "")
        has = self.secrets.has(provider_id)
        masked = self.secrets.masked(provider_id) if has else ""
        row["has_key"] = has
        row["key_masked"] = masked
        row["key_readable"] = bool(masked)
        return row

    def list_providers(self) -> list[dict[str, Any]]:
        return [self.provider_view(entry) for entry in self.store.providers()]

    def put_provider(self, provider_id: str, body: dict[str, Any], who: str) -> dict[str, Any]:
        if not PROVIDER_ID_RE.match(str(provider_id)):
            raise settings.AdminValidationError("invalid_provider_id")
        if not isinstance(body, dict):
            raise settings.AdminValidationError("providers_body_not_object")
        entries = self.store.providers()
        index = next(
            (i for i, item in enumerate(entries) if str(item.get("id")) == provider_id), -1
        )
        current = dict(entries[index]) if index >= 0 else {"id": provider_id}
        for field in ("name", "base_url", "model", "thinking", "timeout_s", "params"):
            if field in body:
                current[field] = body[field]
        if "api_key" in body:
            api_key = str(body.get("api_key") or "")
            if not api_key.strip():
                raise settings.AdminValidationError("api_key_empty")
            old_masked = self.secrets.masked(provider_id)
            self.secrets.put(provider_id, api_key, who=who)  # 无主密钥 -> 503
            self._audit(
                who, "secret.put", provider_id,
                {"api_key": old_masked} if old_masked else None,
                {"api_key": secret_store.mask_secret(api_key)},
            )
        if index >= 0:
            entries[index] = current
        else:
            entries.append(current)
        self.store.update(
            {"providers": entries}, who=who, action="provider.put", key=provider_id
        )
        return self.provider_view(current)

    def delete_provider(self, provider_id: str, who: str) -> dict[str, Any]:
        entries = [item for item in self.store.providers() if str(item.get("id")) != provider_id]
        routing = self.store.routing()
        for capability, route in routing.items():
            if not isinstance(route, dict):
                continue
            if str(route.get("provider") or "") == provider_id:
                route["provider"] = ""
            fallback = [item for item in route.get("fallback") or [] if str(item) != provider_id]
            route["fallback"] = fallback
        self.store.update(
            {"providers": entries, "routing": routing}, who=who, action="provider.delete", key=provider_id
        )
        self.secrets.delete(provider_id)
        self.snapshot.invalidate()
        return {"ok": True, "id": provider_id, "removed": True}

    def test_provider(self, provider_id: str) -> dict[str, Any]:
        result = provider_registry.test_provider(provider_id, cache=self.provider_test_cache)
        return result

    # ------------------------------------------------------------ 其它
    def list_audit(self, limit: int) -> list[dict[str, Any]]:
        return audit.list_records(self.db_path, limit=limit)

    def killswitch(self, enabled: bool, who: str) -> dict[str, Any]:
        """`enabled=true` 表示打开总闸（阻断 LLM），对应 `llm.global_enabled=false`。"""
        result = self.store.update(
            {"llm": {"global_enabled": not bool(enabled)}},
            who=who,
            action="llm.killswitch",
            key="global_enabled",
        )
        return {
            "ok": True,
            "enabled": bool(enabled),
            "llm_global_enabled": not bool(enabled),
            "version": result["version"],
            "note": "新请求将直接返回 llm_killswitch；在飞请求按各自超时结束",
        }

    def overview(self) -> dict[str, Any]:
        ambient = self._read_json(self.run_dir / "ambient" / "ambient_status.json")
        events = self._events_stats()
        cost = self._llm_cost()
        return {
            "ok": True,
            "health": {
                "admin": "ok",
                "ambient_state": ambient.get("state", "unknown"),
                "world_ok": ambient.get("ok"),
                "updated_at": ambient.get("updated_at"),
            },
            "world": {
                "tick": int(events.get("tick", ambient.get("tick", 0)) or 0),
                "events_total": int(events.get("events_total", ambient.get("events_total", 0)) or 0),
                "online_players": self._online_players(),
                "active_npcs": int(ambient.get("active_npcs", 0) or 0),
            },
            "db_bytes": {
                "service_sqlite": _file_size(self.db_path),
                "events_sqlite": _file_size(self.run_dir / "events.sqlite"),
            },
            "llm": {
                "calls": int(ambient.get("llm_calls", 0) or 0),
                "cost_usd": cost.get("cost_usd"),
                "cost_source": cost.get("source"),
            },
            "backup": {"last_at": _iso(self._last_backup_at()), "dir": str(self._backup_dir())},
            "runtime": {
                "settings_version": self.store.version(),
                "ambient_speed": ambient.get("speed"),
                "ambient_cpu_limit": (ambient.get("cpu") or {}).get("limit")
                if isinstance(ambient.get("cpu"), dict) else None,
                "configured": {
                    "ambient_speed": self.store.get_section("world").get("ambient_speed"),
                    "ambient_cpu": self.store.get_section("world").get("ambient_cpu"),
                },
                "note": "配置面与运行面可能不同；world.* 当前只在玩家 API 启动时读取",
            },
            "uptime_s": round(float(self.clock()) - self.started_at, 1),
            "admin_version": __version__,
        }

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "admin_version": __version__,
            "uptime_s": round(float(self.clock()) - self.started_at, 1),
            "providers": len(self.store.providers()),
            "settings_version": self.store.version(),
            "crypto_backend": secret_store.BACKEND_NAME,
            "master_key": secret_store.has_master_key(),
        }

    # ------------------------------------------------------------ 内部
    def _audit(
        self, who: str, action: str, key: str, before: Any, after: Any, detail: Any = None
    ) -> None:
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            audit.append(
                conn, who=who, action=action, key=key, before=before, after=after,
                detail=detail, at=float(self.clock()),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            raw = path.read_text(encoding="utf-8")
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except (OSError, TypeError, ValueError):
            return {}

    def _events_stats(self) -> dict[str, int]:
        path = self.run_dir / "events.sqlite"
        if not path.exists():
            return {}
        try:
            conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=2.0)
            try:
                row = conn.execute("SELECT COUNT(*), MAX(tick) FROM events").fetchone()
            finally:
                conn.close()
        except sqlite3.Error:
            return {}
        return {"events_total": int(row[0] or 0), "tick": int(row[1] or 0)}

    def _online_players(self) -> int:
        try:
            conn = sqlite3.connect(f"file:{self.db_path.as_posix()}?mode=ro", uri=True, timeout=2.0)
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM sessions WHERE status IN ('active','connected')"
                    " AND expires_at > ?",
                    (float(self.clock()),),
                ).fetchone()
            finally:
                conn.close()
        except sqlite3.Error:
            return 0
        return int(row[0] or 0)

    def _llm_cost(self) -> dict[str, Any]:
        """成本只在 ambient 的汇总行里出现；取最后一条，避免逐事件扫描。"""
        path = self.run_dir / "ambient" / "ambient_summaries.jsonl"
        if not path.exists():
            return {"cost_usd": None, "source": "unavailable"}
        try:
            last = ""
            with path.open("rb") as fh:
                fh.seek(0, 2)
                fh.seek(max(0, fh.tell() - 8192))
                tail = fh.read().decode("utf-8", errors="ignore").strip().splitlines()
                last = tail[-1] if tail else ""
            row = json.loads(last) if last else {}
        except (OSError, ValueError):
            return {"cost_usd": None, "source": "unavailable"}
        if isinstance(row, dict) and isinstance(row.get("cost_usd"), (int, float)):
            return {"cost_usd": round(float(row["cost_usd"]), 6), "source": "ambient_summaries.jsonl"}
        return {"cost_usd": None, "source": "unavailable"}

    def _backup_dir(self) -> Path:
        return Path(os.environ.get("XKX_BACKUP_DIR") or (self.root / "backups"))

    def _last_backup_at(self) -> float | None:
        directory = self._backup_dir()
        if not directory.exists():
            return None
        latest: float = 0.0
        try:
            for item in directory.rglob("*"):
                if item.is_file():
                    latest = max(latest, item.stat().st_mtime)
        except OSError:
            return None
        return latest or None


class AdminHandler(BaseHTTPRequestHandler):
    server_version = f"wsim-admin/{__version__}"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # 不把请求体/密钥打进日志
        return

    # ------------------------------------------------------------ HTTP
    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        try:
            self._check_ip()
            app: AdminApp = self.server.app  # type: ignore[attr-defined]
            url = urlparse(self.path)
            path = url.path.rstrip("/") or "/"
            query = parse_qs(url.query)
            body = self._read_body() if method in ("POST", "PUT") else {}
            self._route(app, method, path, query, body)
        except HttpError as exc:
            self._send(exc.status, {"ok": False, "code": exc.code, "detail": exc.detail})
        except secret_store.MasterKeyMissing as exc:
            self._send(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"ok": False, "code": exc.code, "detail": str(exc)},
            )
        except settings.AdminValidationError as exc:
            self._send(
                HTTPStatus.BAD_REQUEST, {"ok": False, "code": exc.code, "detail": str(exc)}
            )
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:  # noqa: BLE001
            self._send(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"ok": False, "code": "internal_error", "detail": type(exc).__name__},
            )

    def _route(
        self,
        app: AdminApp,
        method: str,
        path: str,
        query: dict[str, list[str]],
        body: dict[str, Any],
    ) -> None:
        if method == "GET" and path == "/api/admin/health":
            self._send(HTTPStatus.OK, app.health())
            return
        if method == "POST" and path == "/api/admin/login":
            self._send(HTTPStatus.OK, app.login(str(body.get("token") or ""), self._client_ip()))
            return

        who = app.require_auth(self.headers.get("Authorization"))
        if method == "GET" and path == "/api/admin/overview":
            self._send(HTTPStatus.OK, app.overview())
        elif method == "GET" and path == "/api/admin/settings":
            self._send(HTTPStatus.OK, app.get_settings())
        elif method == "PUT" and path == "/api/admin/settings":
            self._send(HTTPStatus.OK, app.put_settings(body, who))
        elif method == "GET" and path == "/api/admin/providers":
            self._send(HTTPStatus.OK, {"ok": True, "providers": app.list_providers()})
        elif method == "GET" and path == "/api/admin/audit":
            limit = 100
            try:
                limit = int((query.get("limit") or ["100"])[0])
            except (TypeError, ValueError):
                limit = 100
            self._send(
                HTTPStatus.OK,
                {"ok": True, "records": app.list_audit(limit)},
            )
        elif method == "POST" and path == "/api/admin/llm/killswitch":
            enabled = body.get("enabled")
            if not isinstance(enabled, bool):
                raise settings.AdminValidationError("enabled_not_bool")
            self._send(HTTPStatus.OK, app.killswitch(enabled, who))
        elif path.startswith("/api/admin/providers/"):
            provider_id, action = self._parse_provider_path(path)
            if method == "PUT" and action == "":
                self._send(HTTPStatus.OK, app.put_provider(provider_id, body, who))
            elif method == "DELETE" and action == "":
                self._send(HTTPStatus.OK, app.delete_provider(provider_id, who))
            elif method == "POST" and action == "test":
                self._send(HTTPStatus.OK, app.test_provider(provider_id))
            else:
                raise HttpError(HTTPStatus.NOT_FOUND, "not_found")
        else:
            raise HttpError(HTTPStatus.NOT_FOUND, "not_found")

    @staticmethod
    def _parse_provider_path(path: str) -> tuple[str, str]:
        parts = [part for part in path.split("/") if part]
        # /api/admin/providers/{id}[/{action}]
        if len(parts) < 4:
            raise HttpError(HTTPStatus.NOT_FOUND, "not_found")
        return parts[3], (parts[4] if len(parts) > 4 else "")

    def _read_body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise HttpError(HTTPStatus.BAD_REQUEST, "bad_content_length") from exc
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise HttpError(413, "body_too_large")
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise HttpError(HTTPStatus.BAD_REQUEST, "invalid_json") from exc
        if not isinstance(value, dict):
            raise HttpError(HTTPStatus.BAD_REQUEST, "body_not_object")
        return value

    def _client_ip(self) -> str:
        return str((self.client_address or ("",))[0])

    def _check_ip(self) -> None:
        ip = self._client_ip()
        if ip in ("127.0.0.1", "::1", "localhost"):
            return
        allowed = {
            item.strip()
            for item in str(os.environ.get("XKX_ADMIN_ALLOW_IPS", "")).split(",")
            if item.strip()
        }
        if ip not in allowed:
            raise HttpError(HTTPStatus.FORBIDDEN, "admin_ip_not_allowed")

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


def create_server(
    app: AdminApp, host: str = "127.0.0.1", port: int = 8031
) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, int(port)), AdminHandler)
    httpd.daemon_threads = True
    httpd.app = app  # type: ignore[attr-defined]
    return httpd


def run(
    *,
    root: str | Path = ".",
    run_id: str = "",
    db_path: str | Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8031,
) -> ThreadingHTTPServer:
    resolved = settings.resolve_db_path(root=root, run=run_id, explicit=db_path)
    app = AdminApp(resolved, root=root, run=run_id)
    httpd = create_server(app, host=host, port=port)
    print(
        json.dumps(
            {
                "admin_started": True,
                "host": host,
                "port": int(httpd.server_address[1]),
                "db": str(resolved),
                "run": app.run,
                "master_key": secret_store.has_master_key(),
                "crypto_backend": secret_store.BACKEND_NAME,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return httpd
