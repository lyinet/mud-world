"""W5 席位探测：200 席填满后第 201 席必须 429 player_cap_reached（不是超时/500）。"""
from __future__ import annotations

import importlib.util
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

from wsim.api.auth import AccountStore
from wsim.api.server import create_server
from wsim.api.session import WorldSession

ROOT = Path(__file__).resolve().parents[1]


def _load_tool():
    spec = importlib.util.spec_from_file_location("w5_load_test", ROOT / "tools" / "load_test.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


w5 = _load_tool()


def test_capacity_probe_200_seats_then_429(tmp_path):
    run = tmp_path / "run"
    session = WorldSession(ROOT, run_id="w5-cap", run_dir=run, seed=42, endow=False)
    accounts = AccountStore(tmp_path / "service.sqlite", iterations=1_000)
    server = create_server(session, "127.0.0.1", 0, accounts=accounts, auth_required=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        args = SimpleNamespace(
            base_url=base,
            players=200,
            probe_count=2,
            concurrency=8,
            timeout=30.0,
            tag="c4p",
            preexisting_seats=0,
        )
        report = w5.run_capacity_check(args)
        assert report["ok"] is True, report["checks"]
        assert report["fill"]["filled"] == 200
        assert report["fill"]["failures"] == []
        probes = report["probes"]
        assert len(probes) == 2
        for row in probes:
            assert row["status"] == 429, row
            assert row["code"] == "player_cap_reached", row
            assert row["is_429_capacity"] is True
            assert row["player_registered"] is False
            assert row["state_reject"] == "player_not_entered"
        assert report["totals"]["http_5xx"] == 0
        # 被拒的账号没有进入世界，席位仍恰好 200。
        assert len(session.state.players) == 200
        assert "player:w5capc4p201" not in session.state.players

        invariants = w5.verify_event_log(run)
        assert invariants["ok"] is True, invariants["errors"]
        assert invariants["wallet"]["delta"] == 0
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        accounts.close()
        session.close()
