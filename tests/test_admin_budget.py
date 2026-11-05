"""预算闸门：自然日（UTC）口径、持久化、调用前拦截、降级链、审计与控制台展示。"""
from __future__ import annotations

import base64
import contextlib
import http.client
import json
import os
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.admin import audit, budget, providers, secrets, server, settings  # noqa: E402
from wsim.agent import provider as provider_module  # noqa: E402
from wsim.agent.provider import ProviderUnavailable  # noqa: E402

ADMIN_TOKEN = "admin-token-for-tests"


def _utc(y: int, m: int, d: int, h: int = 0, mi: int = 0, s: int = 0) -> float:
    return datetime(y, m, d, h, mi, s, tzinfo=timezone.utc).timestamp()


DAY_TS = _utc(2026, 11, 6, 12)  # 2026-11-06T12:00:00Z


class _RelayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt: str, *args) -> None:  # noqa: D401
        return

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        relay = self.server
        relay.calls.append({"model": body.get("model"), "auth": self.headers.get("Authorization", "")})
        payload = {
            "id": "mock-completion",
            "object": "chat.completion",
            "model": body.get("model") or "mock-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "pong"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
        }
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@contextlib.contextmanager
def mock_relay():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _RelayHandler)
    httpd.calls = []
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()


@contextlib.contextmanager
def admin_server(db_path: Path, root: Path):
    app = server.AdminApp(db_path, root=root, run="test")
    httpd = server.create_server(app, "127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield app, httpd
    finally:
        httpd.shutdown()
        httpd.server_close()


def call(httpd, method: str, path: str, payload: dict | None = None, token: str | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=5)
    headers = {"Content-Type": "application/json", "Connection": "close"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(payload) if payload is not None else None
    conn.request(method, path, body=body, headers=headers)
    response = conn.getresponse()
    raw = response.read()
    conn.close()
    return response.status, json.loads(raw.decode("utf-8") or "{}")


@pytest.fixture
def env(db_path: Path, monkeypatch: pytest.MonkeyPatch):
    key = base64.b64encode(os.urandom(32)).decode("ascii")
    monkeypatch.setenv(secrets.MASTER_KEY_ENV, key)
    monkeypatch.setenv("XKX_ADMIN_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("XKX_ADMIN_DB", str(db_path))
    cache = providers.SnapshotCache(db_path)
    monkeypatch.setattr(providers, "_CACHE", cache)
    return cache


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "service.sqlite"
    settings.init_schema(path)
    return path


def seed(db_path: Path, relay_url: str, *, fallback: list[str] | None = None) -> None:
    settings.SettingsStore(db_path).update(
        {
            "providers": [
                {"id": "relay-a", "base_url": relay_url, "model": "mock-model"},
            ]
            + [
                {"id": name, "base_url": relay_url, "model": f"{name}-model"}
                for name in (fallback or [])
            ],
            "routing": {"dialogue": {"provider": "relay-a", "fallback": list(fallback or [])}},
            "llm": {"daily_budget_usd": 0.000001, "per_capability_daily_tokens": {}},
        },
        who="tester",
    )
    secrets.SecretStore(db_path).put("relay-a", "sk-test-key-0001", who="tester")
    for name in fallback or []:
        secrets.SecretStore(db_path).put(name, f"sk-{name}-key", who="tester")
    providers._CACHE.invalidate()


def test_natural_day_is_utc_and_does_not_use_world_day() -> None:
    # 2026-11-06T23:59:00Z 与 2026-11-07T00:00:30Z 必须落在不同自然日。
    assert budget.natural_day(_utc(2026, 11, 6, 23, 59)) == "2026-11-06"
    assert budget.natural_day(_utc(2026, 11, 7, 0, 0, 30)) == "2026-11-07"


def test_usage_is_persisted_and_summarized(db_path: Path) -> None:
    first = budget.record_call(
        "dialogue", "relay-a", usage={"prompt_tokens": 100, "completion_tokens": 20},
        db_path=db_path, now=DAY_TS,
    )
    assert first["recorded"] is True
    assert first["cost_usd"] > 0

    # 重新打开（模拟重启）后仍在。
    reopened = settings.UsageStore(db_path)
    rows = reopened.rows("2026-11-06")
    assert len(rows) == 1
    assert rows[0]["tokens_in"] == 100 and rows[0]["tokens_out"] == 20

    summary = budget.summary(
        db_path=db_path,
        snapshot={"llm": {"daily_budget_usd": 1.0, "per_capability_daily_tokens": {"dialogue": 500}}},
        now=DAY_TS,
    )
    assert summary["day"] == "2026-11-06" and summary["timezone"] == "UTC"
    assert summary["used_tokens"] == 120
    assert summary["tokens"]["dialogue"] == {"used": 120, "limit": 500, "remaining": 380}
    assert summary["remaining_usd"] == round(1.0 - first["cost_usd"], 10)


def test_check_denies_budget_and_capability_tokens(db_path: Path) -> None:
    budget.record_call(
        "dialogue", "relay-a", usage={"prompt_tokens": 1000, "completion_tokens": 0},
        db_path=db_path, now=DAY_TS,
    )
    spent = budget.estimate_cost(1000, 0)

    denied = budget.check(
        "dialogue", "relay-a", db_path=db_path,
        snapshot={"llm": {"daily_budget_usd": spent - 0.000001}},
        now=DAY_TS,
    )
    assert denied["allowed"] is False
    assert denied["code"] == "budget_exhausted"
    assert denied["limit_usd"] > 0 and denied["used_usd"] > denied["limit_usd"]

    capped = budget.check(
        "dialogue", "relay-a", db_path=db_path,
        snapshot={"llm": {"daily_budget_usd": 0, "per_capability_daily_tokens": {"dialogue": 999}}},
        now=DAY_TS,
    )
    assert capped["allowed"] is False
    assert capped["code"] == "capability_token_limit"
    assert capped["token_limit"] == 999 and capped["used_tokens"] == 1000

    unlimited = budget.check(
        "dialogue", "relay-a", db_path=db_path,
        snapshot={"llm": {"daily_budget_usd": 0, "per_capability_daily_tokens": {}}},
        now=DAY_TS,
    )
    assert unlimited["allowed"] is True

    # 库不存在：不建库、不拦截。
    missing = budget.check("dialogue", "relay-a", db_path=Path("/nonexistent/x.sqlite"))
    assert missing["allowed"] is True


def test_provider_denies_before_network_records_usage_and_recovers(
    db_path: Path, env, monkeypatch: pytest.MonkeyPatch
) -> None:
    with mock_relay() as relay:
        base = f"http://127.0.0.1:{relay.server_address[1]}/v1"
        seed(db_path, base, fallback=["relay-b"])
        client = provider_module.ProviderClient(providers.chain_for_slot("dialogue")[0])

        # 第一次：额度内，真实请求打到 mock，并记账。
        assert client.complete("sys", "user") == "pong"
        assert len(relay.calls) == 1
        summary = providers.budget_summary(db_path=db_path, cache=providers._CACHE)
        assert summary["used_usd"] > 0 and summary["used_tokens"] == 6

        # 第二次：已超当日 0.000001 美元；必须拒绝且不发请求。
        with pytest.raises(ProviderUnavailable) as excinfo:
            client.complete("sys", "user")
        assert "budget_exhausted" in str(excinfo.value)
        assert len(relay.calls) == 1
        assert client.last_error_kind == "budget_exhausted"
        # 降级链按既有规则走完（relay-b 也被检查、也被拒），不崩溃。
        assert client.stats_snapshot()["fallbacks"] >= 1
        assert client.stats_snapshot()["errors_by_kind"]["budget_exhausted"] == 2

        summary = providers.budget_summary(db_path=db_path, cache=providers._CACHE)
        assert summary["denied"] == 2
        assert summary["by_provider"]["relay-a"]["denied"] == 1

        # 审计：谁/能力位/provider/已用/上限，且同一 provider 只记首次。
        records = audit.list_records(db_path, limit=50)
        denials = [r for r in records if r["action"] == "llm.budget_denied"]
        assert len(denials) == 2
        detail = denials[0]["detail"]
        assert detail["day"] == summary["day"] and detail["timezone"] == "UTC"
        assert detail["capability"] == "dialogue"
        assert detail["provider_id"] in ("relay-a", "relay-b")
        assert detail["used_usd"] > detail["limit_usd"]
        assert "sk-test-key-0001" not in json.dumps(denials, ensure_ascii=False)

        # 调回上限后恢复正常（快照失效）。
        settings.SettingsStore(db_path).update(
            {"llm": {"daily_budget_usd": 1.0, "per_capability_daily_tokens": {"dialogue": 0}}},
            who="tester",
        )
        providers._CACHE.invalidate()
        assert client.complete("sys", "user") == "pong"
        assert len(relay.calls) == 2


def test_denial_counter_audits_only_first_time(db_path: Path) -> None:
    decision = {
        "allowed": False,
        "code": "budget_exhausted",
        "day": "2026-11-06",
        "timezone": "UTC",
        "capability": "dialogue",
        "provider_id": "relay-a",
        "used_usd": 0.5,
        "limit_usd": 0.2,
        "used_tokens": 10,
        "token_limit": 0,
    }
    budget.record_denial(decision, slot="dialogue", db_path=db_path, now=DAY_TS)
    budget.record_denial(decision, slot="dialogue", db_path=db_path, now=DAY_TS + 1)
    rows = settings.UsageStore(db_path).rows("2026-11-06")
    assert rows[0]["denied"] == 2
    records = [r for r in audit.list_records(db_path) if r["action"] == "llm.budget_denied"]
    assert len(records) == 1

    # 同一天同 provider 换一种拒绝原因（token 上限）要单独留痕。
    decision["code"] = "capability_token_limit"
    decision["token_limit"] = 100
    budget.record_denial(decision, slot="dialogue", db_path=db_path, now=DAY_TS + 2)
    rows = settings.UsageStore(db_path).rows("2026-11-06")
    assert rows[0]["denied"] == 3
    records = [r for r in audit.list_records(db_path) if r["action"] == "llm.budget_denied"]
    assert len(records) == 2
    assert records[0]["detail"]["reason"] in ("budget_exhausted", "capability_token_limit")


def test_admin_api_exposes_budget(db_path: Path, env, tmp_path: Path) -> None:
    with admin_server(db_path, tmp_path) as (_app, httpd):
        status, login_payload = call(httpd, "POST", "/api/admin/login", {"token": ADMIN_TOKEN})
        assert status == 200
        token = str(login_payload["token"])

        status, payload = call(httpd, "GET", "/api/admin/budget", token=token)
        assert status == 200
        assert payload["ok"] is True
        assert payload["timezone"] == "UTC"
        assert payload["daily_budget_usd"] == 0.20
        assert payload["daily_budget_usd"] > 0
        assert payload["tokens"]["dialogue"] == {"used": 0, "limit": 0, "remaining": None}

        status, settings_payload = call(httpd, "GET", "/api/admin/settings", token=token)
        assert status == 200
        assert settings_payload["hot"]["daily_budget_usd"] is True
        assert settings_payload["budget"]["timezone"] == "UTC"
        assert settings_payload["capabilities"]["dialogue"]["budget"]["day"] == payload["day"]

        status, overview = call(httpd, "GET", "/api/admin/overview", token=token)
        assert status == 200
        assert overview["budget"]["timezone"] == "UTC"

        status, updated = call(
            httpd, "PUT", "/api/admin/settings",
            {"llm": {"daily_budget_usd": 0.000001, "per_capability_daily_tokens": {"dialogue": 100}}},
            token,
        )
        assert status == 200
        assert updated["requires_restart"] == []
        status, payload = call(httpd, "GET", "/api/admin/budget", token=token)
        assert payload["daily_budget_usd"] == 0.000001
        assert payload["tokens"]["dialogue"]["limit"] == 100
