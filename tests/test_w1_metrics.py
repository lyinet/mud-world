"""HTTP 指标端点：Prometheus 文本格式、鉴权边界、只读不改既有响应。"""
from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from wsim.api.auth import AccountStore
from wsim.api.metrics import HttpMetrics, normalize_path
from wsim.api.server import create_server
from wsim.api.session import WorldSession

ROOT = Path(__file__).resolve().parents[1]

_LINE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})? (\S+)$")


def _request(base, path, method="GET", body=None, headers=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    hdrs = {"Content-Type": "application/json"}
    hdrs.update(headers or {})
    req = urllib.request.Request(base + path, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.headers.get("Content-Type", ""), resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type", ""), exc.read().decode("utf-8")


def _parse(text: str) -> dict[str, dict[str, float]]:
    samples: dict[str, dict[str, float]] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        assert m, f"非法 Prometheus 行：{line!r}"
        name, labels, value = m.group(1), m.group(2) or "", m.group(3)
        labels = labels[1:-1] if labels.startswith("{") else labels
        samples.setdefault(name, {})[labels] = float(value)
    return samples


@pytest.fixture()
def service(tmp_path):
    session = WorldSession(ROOT, run_id="w1-metrics", run_dir=tmp_path / "run", seed=42)
    accounts = AccountStore(tmp_path / "service.sqlite", iterations=1_000)
    server = create_server(session, "127.0.0.1", 0, accounts=accounts, auth_required=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield base, session, accounts, server
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        accounts.close()
        session.close()


def test_metrics_format_is_prometheus_parseable(service):
    base, _session, _accounts, _server = service
    _request(base, "/api/health")
    _request(base, "/api/state?account_id=u_demo")
    _request(base, "/api/not-a-route")
    _request(base, "/api/player/intents/i%3Aabc")

    status, ctype, text = _request(base, "/api/metrics")
    assert status == 200
    assert "text/plain" in ctype
    assert text.endswith("\n")
    samples = _parse(text)

    assert 'code="200",path="/api/state"' in samples["xkx_http_requests_total"]
    assert samples["xkx_http_requests_total"]['code="200",path="/api/state"'] >= 1
    assert samples["xkx_http_requests_total"]['code="404",path="/api/not-a-route"'] >= 1
    # 动态路径归一化，避免标签基数爆炸
    assert 'code="200",path="/api/player/intents/:id"' in samples["xkx_http_requests_total"]
    assert 'code="unknown_action"' in samples["xkx_http_rejections_total"]

    tick = samples["xkx_world_tick"][""]
    assert tick >= 0
    assert samples["xkx_events_total"][""] > 0
    assert samples["xkx_players_online"][""] >= 0
    assert samples["xkx_ambient_paused_total"][""] >= 0
    assert samples["xkx_llm_calls_total"][""] >= 0
    assert samples["xkx_db_bytes"]['file="events.sqlite"'] > 0
    assert samples["xkx_db_bytes"]['file="service.sqlite"'] > 0

    # 直方图：桶累积单调，+Inf 等于 count
    buckets = samples["xkx_http_request_duration_seconds_bucket"]
    le_values = [
        float(v) for k, v in buckets.items() if 'path="/api/state"' in k and 'le="+Inf"' not in k
    ]
    inf = buckets['le="+Inf",path="/api/state"']
    count = samples["xkx_http_request_duration_seconds_count"]['path="/api/state"']
    assert inf == count
    assert all(a <= b for a, b in zip(le_values, le_values[1:]))
    assert len(le_values) == 8
    assert samples["xkx_http_request_duration_seconds_sum"]['path="/api/state"'] >= 0


def test_metrics_hidden_when_non_loopback_without_token(tmp_path):
    session = WorldSession(ROOT, run_id="w1-metrics-auth", run_dir=tmp_path / "run", seed=42)
    server = create_server(
        session, "127.0.0.1", 0, auth_required=False, metrics_public=False, metrics_token=""
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, _ctype, text = _request(base, "/api/metrics")
        assert status == 404
        assert "xkx_http_requests_total" not in text
        assert "xkx_world_tick" not in text
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        session.close()


def test_metrics_token_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("XKX_METRICS_TOKEN", "s3cret-token")
    session = WorldSession(ROOT, run_id="w1-metrics-token", run_dir=tmp_path / "run", seed=42)
    server = create_server(session, "127.0.0.1", 0, auth_required=False, metrics_public=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert _request(base, "/api/metrics")[0] == 404
        assert _request(base, "/api/metrics", headers={"Authorization": "Bearer wrong"})[0] == 404
        ok_auth = _request(base, "/api/metrics", headers={"Authorization": "Bearer s3cret-token"})
        assert ok_auth[0] == 200 and "xkx_world_tick" in ok_auth[2]
        ok_header = _request(base, "/api/metrics", headers={"X-Metrics-Token": "s3cret-token"})
        assert ok_header[0] == 200
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        session.close()


def test_metrics_public_ignores_token(tmp_path):
    session = WorldSession(ROOT, run_id="w1-metrics-public", run_dir=tmp_path / "run", seed=42)
    server = create_server(
        session, "127.0.0.1", 0, auth_required=False, metrics_public=True, metrics_token="abc"
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert _request(base, "/api/metrics")[0] == 200
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        session.close()


def test_normalize_path_limits_cardinality():
    assert normalize_path("/api/player/intents/i:abcdef") == "/api/player/intents/:id"
    assert normalize_path("/api/player/state") == "/api/player/state"
    assert normalize_path("") == "/"


def test_metrics_unit_renders_histogram_buckets():
    metrics = HttpMetrics()
    metrics.observe("/api/x", 200, 0.004)  # <=5ms
    metrics.observe("/api/x", 200, 0.03)  # <=50ms
    metrics.observe("/api/x", 500, 2.0)  # 超 1s，落 +Inf
    metrics.rejection("item_missing")
    text = metrics.render(
        world_tick=7,
        events_total=9,
        players_online=1,
        ambient_paused_total=0,
        llm_calls=0,
        db_sizes={"events.sqlite": 12},
    )
    samples = _parse(text)
    assert samples["xkx_http_requests_total"]['code="200",path="/api/x"'] == 2
    assert samples["xkx_http_requests_total"]['code="500",path="/api/x"'] == 1
    assert samples["xkx_http_rejections_total"]['code="item_missing"'] == 1
    assert samples["xkx_http_request_duration_seconds_bucket"]['le="0.005",path="/api/x"'] == 1
    assert samples["xkx_http_request_duration_seconds_bucket"]['le="0.025",path="/api/x"'] == 1
    assert samples["xkx_http_request_duration_seconds_bucket"]['le="0.05",path="/api/x"'] == 2
    assert samples["xkx_http_request_duration_seconds_bucket"]['le="+Inf",path="/api/x"'] == 3
    assert samples["xkx_http_request_duration_seconds_count"]['path="/api/x"'] == 3
