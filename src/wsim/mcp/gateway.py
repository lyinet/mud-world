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


class PermissionDenied(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class GatewayError(Exception):
    pass


@dataclass
class _AdminToken:
    token: str
    intent: Intent
    requester: str
    reason: str = ""
    approved_by: str | None = None
    used: bool = False


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
        self.outbox: list[Intent] = []
        self.tokens: dict[str, _AdminToken] = {}

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
            self.outbox.append(intent)
            return {"accepted": True, "intent_id": intent.intent_id or f"outbox:{len(self.outbox) - 1}",
                    "tick": intent.tick, "state_unchanged": True}

        if method == "admin.request":
            self._need(permission, PERMISSION_ADMIN, method)
            intent = self._intent_from(params, default_source="mcp:admin")
            self._token_seq += 1
            token = "admin:" + hash_obj(
                {"requester": caller, "seq": self._token_seq, "tick": self._tick(), "intent": normalize(params.get("intent", {}))}
            )[:16]
            self.tokens[token] = _AdminToken(
                token=token, intent=intent, requester=caller, reason=str(params.get("reason", ""))
            )
            return {"token": token, "approved": False, "requester": caller}

        if method == "admin.approve":
            self._need(permission, PERMISSION_ADMIN, method)
            token = str(params.get("token", ""))
            rec = self.tokens.get(token)
            if rec is None:
                raise GatewayError("unknown_token")
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
            event = self.writer.submit(rec.intent)
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
