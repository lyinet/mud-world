"""W1 审计日志轮转：按大小切分、备份份数有上限、进程内缓存有上限。"""
from __future__ import annotations

from pathlib import Path

from wsim.mcp.audit import AuditLog, AuditRecord


def _rec(seq: int) -> AuditRecord:
    return AuditRecord(
        seq=seq,
        tick=0,
        caller="player:u_demo",
        permission="propose_intent",
        method="world.propose_intent",
        params_digest="d" * 8,
        ok=True,
        result={"ok": True},
    )


def _lines(path: Path) -> int:
    return len([ln for ln in path.read_text(encoding="utf-8").splitlines() if ln])


def test_rotation_keeps_every_line_within_retention(tmp_path):
    path = tmp_path / "mcp_audit.jsonl"
    # 备份份数足够多：60 条全部保留，证明轮转不丢行。
    log = AuditLog(path, max_bytes=1200, backups=20, memory_max=10)
    for i in range(60):
        log.append(_rec(i))
    assert log.wait_rotation(), "后台轮转未在超时内结束"
    assert log.rotation_count >= 1

    files = sorted(p.name for p in tmp_path.iterdir())
    total = sum(_lines(p) for p in tmp_path.iterdir())
    assert total == 60, f"轮转丢行：{total} != 60；files={files}"
    # 内存缓存只保留最近 memory_max 条
    assert len(log.records) == 10
    assert log.records[-1].seq == 59


def test_rotation_bounds_backup_count(tmp_path):
    path = tmp_path / "mcp_audit.jsonl"
    log = AuditLog(path, max_bytes=512, backups=3, memory_max=10)
    for i in range(60):
        log.append(_rec(i))
    assert log.wait_rotation()
    assert log.rotation_count >= 1
    files = sorted(p.name for p in tmp_path.iterdir())
    # 只保留 .1/.2/.3，不允许出现 .4
    assert (tmp_path / "mcp_audit.jsonl.4").exists() is False
    assert all(
        name in {"mcp_audit.jsonl", "mcp_audit.jsonl.1", "mcp_audit.jsonl.2", "mcp_audit.jsonl.3"}
        for name in files
    )


def test_memory_cache_respects_env(monkeypatch, tmp_path):
    monkeypatch.setenv("XKX_AUDIT_MEMORY_MAX", "3")
    monkeypatch.setenv("XKX_AUDIT_MAX_BYTES", "1048576")
    monkeypatch.setenv("XKX_AUDIT_BACKUPS", "2")
    log = AuditLog(tmp_path / "audit.jsonl")
    assert log.memory_max == 3 and log.max_bytes == 1048576 and log.backups == 2
    for i in range(10):
        log.append(_rec(i))
    assert len(log.records) == 3


def test_rotation_does_not_block_writer(monkeypatch, tmp_path):
    """阈值为 1 时每次 append 都可能触发轮转；写路径仍应快速返回。"""
    import time

    log = AuditLog(tmp_path / "audit.jsonl", max_bytes=1, backups=2, memory_max=5)
    started = time.perf_counter()
    for i in range(50):
        log.append(_rec(i))
    elapsed = time.perf_counter() - started
    assert log.wait_rotation()
    # 50 次 append 在纯文件 I/O 量级；轮转若同步做 50 次改名会明显更慢。
    assert elapsed < 2.0
