"""W5 压测工具的核心逻辑单测：百分比、CPU 时间解析、事件日志不变量校验。"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_tool():
    spec = importlib.util.spec_from_file_location("w5_load_test", ROOT / "tools" / "load_test.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


w5 = _load_tool()


def _make_db(path: Path, rows: list[dict]) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE events (
            seq INTEGER PRIMARY KEY, tick INTEGER NOT NULL, day INTEGER NOT NULL,
            intent_id TEXT NOT NULL, actor TEXT NOT NULL, verb TEXT NOT NULL,
            args TEXT NOT NULL, effects TEXT NOT NULL, outcome TEXT NOT NULL,
            reason TEXT NOT NULL, source TEXT NOT NULL, legacy INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    for row in rows:
        conn.execute(
            "INSERT INTO events(seq,tick,day,intent_id,actor,verb,args,effects,outcome,reason,source,legacy)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,0)",
            (
                row["seq"], row.get("tick", row["seq"]), row.get("day", 0),
                row.get("intent_id", f"i{row['seq']}"), row.get("actor", "system"),
                row.get("verb", "test"), json.dumps(row.get("args", {})),
                json.dumps(row.get("effects", [])), row.get("outcome", "ok"),
                row.get("reason", ""), row.get("source", "test"),
            ),
        )
    conn.commit()
    conn.close()


def test_percentiles_known_values():
    values = [float(i) for i in range(1, 101)]
    stats = w5.percentiles(values)
    assert stats["count"] == 100
    assert stats["p50"] == 50.0
    assert stats["p95"] == 95.0
    assert stats["p99"] == 99.0
    assert stats["max"] == 100.0
    assert w5.percentiles([])["p95"] == 0.0


def test_parse_cpu_time_macos_and_linux_formats():
    assert w5._parse_cpu_time("0:00.90") == 0.9
    assert w5._parse_cpu_time("01:02.50") == 62.5
    assert abs(w5._parse_cpu_time("02:03:04") - 7384.0) < 1e-6
    assert abs(w5._parse_cpu_time("1-02:03:04") - 93784.0) < 1e-6
    assert w5._parse_cpu_time("garbage") is None


def test_verify_event_log_good_wallet_conservation(tmp_path):
    rows = [
        {"seq": 1, "verb": "genesis", "effects": [
            {"op": "create_account", "data": {"account": "a"}},
            {"op": "create_account", "data": {"account": "b"}},
            {"op": "mint", "data": {"account": "a", "amount": 100}},
            {"op": "mint", "data": {"account": "b", "amount": 50}},
        ]},
        {"seq": 2, "effects": [{"op": "transfer", "data": {"src": "a", "dst": "b", "amount": 10}}]},
        {"seq": 3, "effects": [{"op": "burn", "data": {"account": "b", "amount": 5}}]},
        {"seq": 4, "effect": "rejected", "outcome": "rejected", "effects": []},
    ]
    run = tmp_path / "run"
    run.mkdir()
    _make_db(run / "events.sqlite", rows)
    report = w5.verify_event_log(run)
    assert report["ok"] is True, report["errors"]
    assert report["seq"]["contiguous"] is True
    assert report["wallet"]["sum_balance"] == 145
    assert report["wallet"]["delta"] == 0
    assert report["integrity_check"] == "ok"
    assert report["genesis_ok_events"] == 1


def test_verify_event_log_detects_wallet_break_and_gap(tmp_path):
    rows = [
        {"seq": 1, "verb": "genesis", "effects": [
            {"op": "create_account", "data": {"account": "a", "balance": 999}},
            {"op": "mint", "data": {"account": "a", "amount": 100}},
        ]},
        # seq 3 缺失，制造 seq 断裂
        {"seq": 3, "effects": [{"op": "transfer", "data": {"src": "a", "dst": "b", "amount": 1500}}]},
    ]
    run = tmp_path / "run"
    run.mkdir()
    _make_db(run / "events.sqlite", rows)
    report = w5.verify_event_log(run)
    assert report["ok"] is False
    assert report["seq"]["contiguous"] is False
    assert any("seq 不连续" in e for e in report["errors"])
    # create_account 自带余额 999 且没有 mint，等价于凭空造钱
    assert report["wallet"]["delta"] == 999
    assert any("钱包不守恒" in e for e in report["errors"])
    assert any("透支" in e for e in report["errors"])


def test_verify_event_log_detects_half_write(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    conn = sqlite3.connect(run / "events.sqlite")
    conn.executescript(
        """
        CREATE TABLE events (
            seq INTEGER PRIMARY KEY, tick INTEGER NOT NULL, day INTEGER NOT NULL,
            intent_id TEXT NOT NULL, actor TEXT NOT NULL, verb TEXT NOT NULL,
            args TEXT NOT NULL, effects TEXT NOT NULL, outcome TEXT NOT NULL,
            reason TEXT NOT NULL, source TEXT NOT NULL, legacy INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    conn.execute(
        "INSERT INTO events VALUES(1,0,0,'i1','system','genesis','{}','{\"ok\":true','ok','','test',0)"
    )
    conn.commit()
    conn.close()
    report = w5.verify_event_log(run)
    assert report["ok"] is False
    assert any("半写" in e or "JSON" in e for e in report["errors"])


def test_verify_event_log_missing_db(tmp_path):
    report = w5.verify_event_log(tmp_path / "missing")
    assert report["ok"] is False
    assert report["db_exists"] is False
