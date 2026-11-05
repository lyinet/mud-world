"""deploy/healthcheck.py 真探活：ok / events_ok / disk_free_mb 任一不满足即非零退出。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HEALTHCHECK = ROOT / "deploy" / "healthcheck.py"


def _load():
    spec = importlib.util.spec_from_file_location("xkx_healthcheck", HEALTHCHECK)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _fake_fetch(body_api_health: dict, *, status: int = 200):
    def fetch(url: str):
        if url.endswith("/api/health") and ":8020" in url:
            return status, body_api_health
        return 200, {"ok": True}

    return fetch


def _api_health(**storage):
    base = {"ok": True, "storage": {"events_ok": True, "disk_free_mb": 1024.0}}
    base["storage"].update(storage)
    return base


def test_healthcheck_returns_zero_when_storage_healthy(monkeypatch, capsys):
    module = _load()
    monkeypatch.setattr(module, "fetch", _fake_fetch(_api_health()))
    assert module.main() == 0
    out = capsys.readouterr().out
    assert "PASS api /api/health" in out


@pytest.mark.parametrize(
    "payload",
    [
        {"ok": False, "storage": {"events_ok": True, "disk_free_mb": 1024.0}},
        _api_health(events_ok=False),
        _api_health(disk_free_mb=10.0),
    ],
    ids=["ok_false", "events_not_ok", "disk_low"],
)
def test_healthcheck_fails_nonzero(monkeypatch, payload):
    module = _load()
    monkeypatch.setattr(module, "fetch", _fake_fetch(payload))
    assert module.main() == 1
