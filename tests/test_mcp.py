"""能力层：MCP 三级权限、审计、越权拒绝、mock server。"""
from __future__ import annotations

import json

import pytest

from wsim.actions.registry import build_registry
from wsim.core.models import Intent
from wsim.mcp import (
    AuditLog,
    GatewayError,
    MCPGateway,
    MockMCPServer,
    PERMISSION_ADMIN,
    PERMISSION_PROPOSE,
    PERMISSION_READ,
)
from wsim.world.store import EventStore
from wsim.world.writer import Writer


def _gateway(state, tmp_path, registry=None):
    store = EventStore(tmp_path / "events.sqlite")
    writer = Writer(state, store, registry or _EmptyRegistry(), seed=42)
    audit_path = tmp_path / "audit.jsonl"
    gateway = MCPGateway(
        state_provider=lambda: writer.state,
        writer=writer,
        audit=AuditLog(audit_path),
        tick_provider=lambda: writer.state.tick,
        identities={
            "reader": PERMISSION_READ,
            "reader_lowpriv": PERMISSION_READ,
            "planner": PERMISSION_PROPOSE,
            "mallory": PERMISSION_PROPOSE,
            "alice": PERMISSION_ADMIN,
            "bob": PERMISSION_ADMIN,
            "carol": PERMISSION_ADMIN,
        },
    )
    return gateway, writer, store, audit_path


class _EmptyRegistry:
    def get(self, verb):
        return None

    def verbs(self):
        return []

    def has(self, verb):
        return False


def test_read_snapshot_is_a_copy_and_logged(state, tmp_path):
    gw, writer, store, audit_path = _gateway(state, tmp_path)
    before = writer.state.hash()
    result = gw.call("reader", "world.read_snapshot", {})
    snapshot = result["snapshot"]
    snapshot["entities"]["n1"]["stamina"] = 0
    assert writer.state.hash() == before
    rec = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert rec["caller"] == "reader"
    assert rec["permission"] == "read"
    assert rec["identity_source"] == "server_map"
    assert rec["method"] == "world.read_snapshot"
    assert rec["ok"] is True
    assert rec["params_digest"] and rec["tick"] == 0
    store.close()


def test_read_caller_cannot_propose_and_denial_is_audited(state, tmp_path):
    gw, writer, store, audit_path = _gateway(state, tmp_path)
    before = writer.state.hash()
    with pytest.raises(GatewayError, match="permission_denied"):
        gw.call("reader", "world.propose_intent", {
            "actor": "n1", "verb": "move", "args": {"to": "b"}, "tick": 1,
        })
    assert writer.state.hash() == before
    assert gw.outbox == []
    recs = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert len(recs) == 1
    assert recs[0]["ok"] is False
    assert "permission_denied" in recs[0]["error"]
    assert recs[0]["caller"] == "reader"
    store.close()


def test_propose_intent_only_queues_and_never_mutates_state(state, tmp_path):
    gw, writer, store, _ = _gateway(state, tmp_path)
    before = writer.state.hash()
    result = gw.call("planner", "world.propose_intent", {
        "actor": "n1", "verb": "move", "args": {"to": "b"}, "tick": 1, "source": "mcp",
    })
    assert result["accepted"] is True
    assert len(gw.outbox) == 1
    assert writer.state.hash() == before
    assert store.count() == 0
    store.close()


def test_admin_apply_requires_two_person_token_and_writes_audit(content, state, tmp_path):
    gw, writer, store, audit_path = _gateway(state, tmp_path, build_registry(content, seed=42))
    req = gw.call("alice", "admin.request", {
        "intent": {"actor": "n1", "verb": "move", "args": {"to": "b"}, "tick": 1},
        "reason": "test",
    })
    token = req["token"]
    with pytest.raises(GatewayError, match="second_person_required"):
        gw.call("alice", "admin.approve", {"token": token})
    approved = gw.call("bob", "admin.approve", {"token": token})
    assert approved["approved"] is True

    before = writer.state.hash()
    applied = gw.call("bob", "admin.apply", {"token": token})
    assert applied["outcome"] == "ok"
    assert writer.state.entities["n1"].location == "b"
    assert writer.state.hash() != before
    assert store.count() == 1
    assert gw.tokens[token].used is True
    assert gw.tokens[token].requester == "alice"
    assert gw.tokens[token].approved_by == "bob"

    recs = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert [r["method"] for r in recs] == [
        "admin.request", "admin.approve", "admin.approve", "admin.apply"
    ]
    assert recs[1]["ok"] is False and "second_person_required" in recs[1]["error"]
    assert recs[2]["caller"] == "bob" and recs[2]["ok"] is True
    assert recs[3]["caller"] == "bob" and recs[3]["ok"] is True
    assert all(r["identity_source"] == "server_map" for r in recs)
    store.close()


def test_apply_by_non_approver_is_rejected_and_audited(state, tmp_path):
    gw, writer, store, audit_path = _gateway(state, tmp_path)
    token = gw.call("alice", "admin.request", {
        "intent": {"actor": "n1", "verb": "move", "args": {"to": "b"}, "tick": 1},
    })["token"]
    gw.call("bob", "admin.approve", {"token": token})
    before = writer.state.hash()

    # 申请人自己不能 apply
    with pytest.raises(GatewayError, match="second_person_required"):
        gw.call("alice", "admin.apply", {"token": token})
    # 其他 admin 但不是批准人，也不能 apply
    with pytest.raises(GatewayError, match="apply_by_approver_only"):
        gw.call("carol", "admin.apply", {"token": token})

    assert writer.state.hash() == before
    assert store.count() == 0
    recs = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert recs[-2]["method"] == "admin.apply" and recs[-2]["ok"] is False
    assert "second_person_required" in recs[-2]["error"]
    assert recs[-1]["method"] == "admin.apply" and recs[-1]["ok"] is False
    assert "apply_by_approver_only" in recs[-1]["error"]
    store.close()


def test_unregistered_and_self_declared_permission_are_denied(state, tmp_path):
    gw, writer, store, audit_path = _gateway(state, tmp_path)
    with pytest.raises(GatewayError, match="permission_denied:unregistered_identity"):
        gw.call("stranger", "world.read_snapshot", {})
    assert writer.state.hash() == writer.state.hash()
    recs = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert recs[0]["permission"] == "unregistered"
    assert "server_map" in recs[0]["identity_source"]
    store.close()


def test_admin_apply_before_approval_is_rejected(state, tmp_path):
    gw, writer, store, _ = _gateway(state, tmp_path)
    token = gw.call("alice", "admin.request", {
        "intent": {"actor": "n1", "verb": "move", "args": {"to": "b"}, "tick": 1},
    })["token"]
    with pytest.raises(GatewayError, match="not_approved"):
        gw.call("alice", "admin.apply", {"token": token})
    store.close()


def test_mock_server_handles_request_and_denies_escalation(state, tmp_path):
    gw, writer, store, _ = _gateway(state, tmp_path)
    server = MockMCPServer(gw)
    assert any(t["name"] == "world.read_snapshot" for t in server.tools())
    ok = server.handle({"caller": "reader", "permission": "read", "method": "world.read_snapshot"})
    assert ok["ok"] is True
    denied = server.handle({
        "caller": "reader", "permission": "read", "method": "admin.request",
        "params": {"intent": {"actor": "n1", "verb": "move", "args": {"to": "b"}, "tick": 1}},
    })
    assert denied["ok"] is False and "permission_denied" in denied["error"]
    # 复核复现：低权用户自称 admin_apply 也不能提权
    escalated = server.handle({
        "caller": "reader_lowpriv", "permission": "admin_apply", "method": "admin.request",
        "params": {"intent": {"actor": "n1", "verb": "move", "args": {"to": "b"}, "tick": 1}},
    })
    assert escalated["ok"] is False and "permission_denied" in escalated["error"]
    store.close()


def _tick_gateway(state, tmp_path):
    """tick 由测试显式推进，验证令牌过期只依赖世界 tick、与墙钟无关。"""
    clock = {"tick": 0}
    store = EventStore(tmp_path / "events.sqlite")
    writer = Writer(state, store, _EmptyRegistry(), seed=42)
    gateway = MCPGateway(
        state_provider=lambda: writer.state,
        writer=writer,
        audit=AuditLog(tmp_path / "audit.jsonl"),
        tick_provider=lambda: clock["tick"],
        identities={"alice": PERMISSION_ADMIN, "bob": PERMISSION_ADMIN, "planner": PERMISSION_PROPOSE},
    )
    return gateway, writer, store, clock


def test_outbox_at_cap_backpressures_and_never_drops_undelivered(state, tmp_path):
    gw, writer, store, _ = _tick_gateway(state, tmp_path)
    gw.outbox_max = 2
    payload = {"actor": "n1", "verb": "move", "args": {"to": "b"}, "tick": 1, "source": "mcp"}
    assert gw.call("planner", "world.propose_intent", payload)["accepted"] is True
    assert gw.call("planner", "world.propose_intent", payload)["accepted"] is True
    with pytest.raises(GatewayError, match="outbox_full"):
        gw.call("planner", "world.propose_intent", payload)
    # 两条未投递提案原样保留，没有被静默淘汰
    assert len(gw.outbox) == 2 and len(gw.outbox_keys) == 2
    store.close()


def test_token_at_capacity_refuses_new_token_but_keeps_valid_ones(state, tmp_path):
    gw, writer, store, clock = _tick_gateway(state, tmp_path)
    gw.token_max = 2
    tokens = [
        gw.call("alice", "admin.request", {
            "intent": {"actor": "n1", "verb": "move", "args": {"to": "b"}, "tick": 1},
        })["token"]
        for _ in range(2)
    ]
    with pytest.raises(GatewayError, match="token_capacity_exceeded"):
        gw.call("alice", "admin.request", {
            "intent": {"actor": "n1", "verb": "move", "args": {"to": "b"}, "tick": 1},
        })
    # 满额后仍不淘汰旧令牌：旧令牌仍可走完两人审批
    assert set(gw.tokens) >= set(tokens)
    gw.call("bob", "admin.approve", {"token": tokens[0]})
    applied = gw.call("bob", "admin.apply", {"token": tokens[0]})
    assert applied["token"] == tokens[0]
    store.close()


def test_token_expires_by_world_tick_only(state, tmp_path):
    gw, writer, store, clock = _tick_gateway(state, tmp_path)
    gw.token_ttl_ticks = 2
    token = gw.call("alice", "admin.request", {
        "intent": {"actor": "n1", "verb": "move", "args": {"to": "b"}, "tick": 1},
    })["token"]
    clock["tick"] = 3  # 超过 2 个世界 tick，即使墙钟只过了几毫秒也已过期
    with pytest.raises(GatewayError, match="token_expired"):
        gw.call("bob", "admin.approve", {"token": token})
    gw._prune_tokens(clock["tick"])
    assert token not in gw.tokens
    assert gw.token_expired == 1
    store.close()


def test_apply_frees_token_intent_and_removes_outbox_entry(state, tmp_path):
    gw, writer, store, _ = _tick_gateway(state, tmp_path)
    payload = {"actor": "n1", "verb": "move", "args": {"to": "b"}, "tick": 1, "source": "mcp"}
    gw.call("planner", "world.propose_intent", payload)
    token = gw.call("alice", "admin.request", {
        "intent": {"actor": "n1", "verb": "move", "args": {"to": "b"}, "tick": 1},
    })["token"]
    gw.call("bob", "admin.approve", {"token": token})
    gw.call("bob", "admin.apply", {"token": token})
    # 已投递的提案出队，令牌不再持有 Intent（避免 2 小时 soak 里常驻）
    assert gw.outbox == [] and gw.outbox_keys == []
    assert gw.tokens[token].used is True
    assert gw.tokens[token].intent is None
    store.close()

