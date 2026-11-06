"""MCP 沙箱网关（P2）。

三级权限由**服务端身份映射**决定，调用方不能自称权限：
- read          只读世界快照；
- propose_intent 只能产生 Intent 放入 outbox，交 Writer，不能改状态；
- admin_apply   改状态，必须二次 token + 第二人批准，且 apply 只能由批准人执行。

所有调用（含被拒绝的越权调用）都写审计日志：调用者、服务端解析的权限、参数摘要、
结果、虚拟时间、身份来源。
"""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..core.canonical import hash_obj, normalize
from ..core.models import Intent
from ..world.state import WorldState
from .audit import AuditLog, AuditRecord

PERMISSION_READ = "read"
PERMISSION_PROPOSE = "propose_intent"
PERMISSION_ADMIN = "admin_apply"
PERMISSION_RANK = {
    PERMISSION_READ: 0,
    PERMISSION_PROPOSE: 1,
    PERMISSION_ADMIN: 2,
}


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


class PermissionDenied(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class GatewayError(Exception):
    pass


@dataclass
class _AdminToken:
    token: str
    # apply 成功后置空：提案已被唯一写者消费，不再需要常驻内存。
    intent: Intent | None
    requester: str
    reason: str = ""
    approved_by: str | None = None
    used: bool = False
    tick: int = 0


class MCPGateway:
    def __init__(
        self,
        state_provider: Callable[[], WorldState],
        writer: Any | None = None,
        audit: AuditLog | None = None,
        audit_path: Path | None = None,
        tick_provider: Callable[[], int] | None = None,
        identities: dict[str, str] | None = None,
    ):
        self._state_provider = state_provider
        self.writer = writer
        self.audit = audit or AuditLog(audit_path)
        self._tick_provider = tick_provider
        self._seq = 0
        self._token_seq = 0
        self.identities: dict[str, str] = dict(identities or {})
        # 有界队列（纯派生内存，不进世界状态 / state_hash）：
        # - token：按世界 tick 过期（默认 144 tick = 1 世界日 = 24h，覆盖人工二次审批窗口）；
        #   过期只由 world tick 决定，不读墙钟，同输入可复现；
        #   硬上限 4096 = 50 并发 × 80 倍余量，单条约 1KB，最坏约 4MB；
        #   达到上限时**拒绝签发新令牌**（token_capacity_exceeded），
        #   绝不静默淘汰仍然有效的旧令牌——避免重连/审批人拿到错误语义。
        # - outbox：只允许"已投递即清理"（apply 成功后精确移除）；
        #   达到上限 4096 时**拒绝新的 propose**（outbox_full），
        #   不丢任何未投递提案（宁可背压，也不让静默丢失）。
        self.outbox: list[Intent] = []
        self.outbox_keys: list[str] = []
        self.tokens: dict[str, _AdminToken] = {}
        self.token_ttl_ticks = _env_int("XKX_MCP_TOKEN_TTL_TICKS", 144)
        self.token_max = _env_int("XKX_MCP_TOKEN_MAX", 4096)
        self.outbox_max = _env_int("XKX_MCP_OUTBOX_MAX", 4096)
        self.token_expired = 0
        self._calls = 0

    def register_identity(self, caller: str, permission: str) -> None:
        """仅服务端可调用；调用方永远不能通过请求参数声明权限。"""
        if permission not in PERMISSION_RANK:
            raise ValueError(f"unknown_permission:{permission}")
        self.identities[caller] = permission

    def permission_of(self, caller: str) -> str | None:
        return self.identities.get(caller)

    # ---------------------------------------------------------------- public
    def call(self, caller: str, method: str, params: dict[str, Any] | None = None) -> Any:
        params = dict(params or {})
        seq = self._seq
        self._seq += 1
        tick = self._tick()
        self._calls += 1
        # 每 64 次调用（或超硬上限时）清理一次过期令牌，避免按请求 O(n) 全扫。
        if self._calls % 64 == 0 or len(self.tokens) > self.token_max:
            self._prune_tokens(tick)
        digest = hash_obj(normalize(params))
        permission = self.identities.get(caller)
        if permission not in PERMISSION_RANK:
            permission = "unregistered"
        ok = True
        result: dict[str, Any] = {}
        error = ""
        try:
            if permission == "unregistered":
                raise PermissionDenied("unregistered_identity")
            result = self._dispatch(caller, permission, method, params)
        except PermissionDenied as exc:
            ok, error = False, f"permission_denied:{exc.reason}"
            result = {"error": error}
        except GatewayError as exc:
            ok, error = False, str(exc)
            result = {"error": error}
        except Exception as exc:  # 网关内部错误也必须留痕
            ok, error = False, f"{type(exc).__name__}:{exc}"
            result = {"error": error}
        finally:
            self.audit.append(
                AuditRecord(
                    seq=seq,
                    tick=tick,
                    caller=caller,
                    permission=permission,
                    method=method,
                    params_digest=digest,
                    ok=ok,
                    result=self._summary(method, result),
                    error=error,
                    identity_source="server_map",
                )
            )
        if not ok:
            raise GatewayError(error)
        return result

    def tools(self) -> list[dict[str, Any]]:
        return [
            {"name": "world.read_snapshot", "permission": PERMISSION_READ},
            {"name": "world.propose_intent", "permission": PERMISSION_PROPOSE},
            {"name": "admin.request", "permission": PERMISSION_ADMIN},
            {"name": "admin.approve", "permission": PERMISSION_ADMIN},
            {"name": "admin.apply", "permission": PERMISSION_ADMIN},
        ]

    # --------------------------------------------------------------- internal
    def _tick(self) -> int:
        if self._tick_provider is not None:
            return int(self._tick_provider())
        return int(self._state_provider().tick)

    def _require(self, permission: str) -> None:
        if permission not in PERMISSION_RANK:
            raise PermissionDenied(f"unknown_permission:{permission}")

    def _dispatch(self, caller: str, permission: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
        state = self._state_provider()
        if method == "world.read_snapshot":
            self._need(permission, PERMISSION_READ, method)
            snapshot = copy.deepcopy(state.to_dict())
            return {"tick": state.tick, "keys": sorted(snapshot), "snapshot": snapshot}

        if method == "world.propose_intent":
            self._need(permission, PERMISSION_PROPOSE, method)
            intent = self._intent_from(params, default_source="mcp:propose")
            if not intent.actor or not intent.verb:
                raise GatewayError("bad_intent")
            if len(self.outbox) >= self.outbox_max:
                # 背压而不是丢弃：outbox 里全是尚未投递的提案，
                # 静默淘汰会让客户端以为"已受理"却永不被写者消费。
                raise GatewayError("outbox_full")
            self.outbox.append(intent)
            self.outbox_keys.append(self._outbox_key(intent))
            return {"accepted": True, "intent_id": intent.intent_id or f"outbox:{len(self.outbox) - 1}",
                    "tick": intent.tick, "state_unchanged": True}

        if method == "admin.request":
            self._need(permission, PERMISSION_ADMIN, method)
            intent = self._intent_from(params, default_source="mcp:admin")
            now = self._tick()
            # 先清理已过期令牌，再判硬上限；满了只拒绝新签发，不淘汰旧的有效令牌。
            self._prune_tokens(now)
            if len(self.tokens) >= self.token_max:
                raise GatewayError("token_capacity_exceeded")
            self._token_seq += 1
            token = "admin:" + hash_obj(
                {"requester": caller, "seq": self._token_seq, "tick": now, "intent": normalize(params.get("intent", {}))}
            )[:16]
            self.tokens[token] = _AdminToken(
                token=token, intent=intent, requester=caller, reason=str(params.get("reason", "")), tick=now
            )
            return {"token": token, "approved": False, "requester": caller}

        if method == "admin.approve":
            self._need(permission, PERMISSION_ADMIN, method)
            token = str(params.get("token", ""))
            rec = self.tokens.get(token)
            if rec is None:
                raise GatewayError("unknown_token")
            if self._token_is_expired(rec):
                raise GatewayError("token_expired")
            if rec.used:
                raise GatewayError("token_used")
            if rec.approved_by is not None:
                raise GatewayError("already_approved")
            if caller == rec.requester:
                raise GatewayError("second_person_required")
            rec.approved_by = caller
            return {"token": token, "approved": True, "approved_by": caller, "requester": rec.requester}

        if method == "admin.apply":
            self._need(permission, PERMISSION_ADMIN, method)
            token = str(params.get("token", ""))
            rec = self.tokens.get(token)
            if rec is None:
                raise GatewayError("unknown_token")
            if self._token_is_expired(rec):
                raise GatewayError("token_expired")
            if rec.used:
                raise GatewayError("token_used")
            if rec.approved_by is None:
                raise GatewayError("not_approved:second_person_required")
            if caller == rec.requester:
                raise GatewayError("second_person_required")
            if caller != rec.approved_by:
                raise GatewayError("apply_by_approver_only")
            if self.writer is None:
                raise GatewayError("gateway_not_bound_to_writer")
            rec.used = True
            intent = rec.intent
            if intent is None:  # 防御：理论上 used 之后不会走到这里
                raise GatewayError("token_used")
            event = self.writer.submit(intent)
            self._drop_delivered(intent)
            rec.intent = None
            return {
                "token": token,
                "seq": event.seq,
                "outcome": event.outcome,
                "reason": event.reason,
                "verb": event.verb,
            }

        raise GatewayError(f"unknown_method:{method}")

    def _need(self, actual: str, required: str, method: str) -> None:
        if PERMISSION_RANK.get(actual, -1) < PERMISSION_RANK[required]:
            raise PermissionDenied(f"{method}_requires_{required}")

    # ------------------------------------------------------- bounded queues
    def _token_is_expired(self, rec: _AdminToken) -> bool:
        """有效窗口只看世界 tick：超过 TTL 即过期，与墙钟无关。"""
        return self._tick() - int(rec.tick) > self.token_ttl_ticks

    def _prune_tokens(self, now_tick: int) -> None:
        """只删除已过期令牌；硬上限由 admin.request 的背压保证，不淘汰有效令牌。"""
        ttl = self.token_ttl_ticks
        expired = [tok for tok, rec in self.tokens.items() if now_tick - int(rec.tick) > ttl]
        for tok in expired:
            self.tokens.pop(tok, None)
        self.token_expired += len(expired)

    @staticmethod
    def _outbox_key(intent: Intent) -> str:
        return hash_obj(normalize({
            "actor": intent.actor,
            "verb": intent.verb,
            "tick": int(intent.tick),
            "args": intent.args,
            "reason": intent.reason,
        }))

    def _drop_delivered(self, intent: Intent) -> None:
        """apply 成功后移除对应提案（“已投递即清理”）。"""
        key = self._outbox_key(intent)
        for i, existing in enumerate(self.outbox_keys):
            if existing == key:
                del self.outbox[i]
                del self.outbox_keys[i]
                return

    def _intent_from(self, params: dict[str, Any], default_source: str) -> Intent:
        raw = params.get("intent") if isinstance(params.get("intent"), dict) else params
        args = raw.get("args")
        if not isinstance(args, dict):
            raise GatewayError("bad_intent_args")
        try:
            tick = int(raw.get("tick", self._tick()))
        except (TypeError, ValueError) as exc:
            raise GatewayError(f"bad_tick:{exc}") from exc
        return Intent(
            actor=str(raw.get("actor", "")),
            verb=str(raw.get("verb", "")),
            args=dict(args),
            tick=tick,
            source=str(raw.get("source", default_source)),
            intent_id=str(raw.get("intent_id", "")),
        )

    @staticmethod
    def _summary(method: str, result: dict[str, Any]) -> dict[str, Any]:
        if method == "world.read_snapshot" and "snapshot" in result:
            return {"tick": result.get("tick"), "keys": result.get("keys")}
        return {k: v for k, v in result.items() if k != "snapshot"}
