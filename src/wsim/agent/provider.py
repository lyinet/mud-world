"""Provider 客户端抽象（P2 桩）。

单一配置：base_url + key + model。带超时、重试、失败降级到 MockReasoner。
本阶段不联网、不做真实 HTTP 调用；`ProviderClient._request` 有意抛
ProviderUnavailable，由 `FallbackReasoner` 捕获后改走确定性 MockReasoner。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.models import Intent
from ..sim.lod import MockReasoner, Reasoner


class ProviderConfigError(ValueError):
    pass


class ProviderUnavailable(Exception):
    pass


@dataclass(frozen=True)
class ProviderConfig:
    base_url: str
    key: str
    model: str
    timeout_s: float = 10.0
    max_retries: int = 2
    offline: bool = True

    def validate(self) -> None:
        errors: list[str] = []
        if not self.base_url.strip():
            errors.append("base_url 不能为空")
        if not self.key.strip():
            errors.append("key 不能为空")
        if not self.model.strip():
            errors.append("model 不能为空")
        if self.timeout_s <= 0:
            errors.append("timeout_s 必须 > 0")
        if self.max_retries < 0:
            errors.append("max_retries 不能为负")
        if errors:
            raise ProviderConfigError("; ".join(errors))

    def fingerprint(self) -> dict[str, Any]:
        """可安全写入日志的配置摘要（不含 key）。"""
        return {"base_url": self.base_url, "model": self.model, "timeout_s": self.timeout_s,
                "max_retries": self.max_retries, "offline": self.offline}


class ProviderClient:
    def __init__(self, config: ProviderConfig):
        config.validate()
        self.config = config
        self.last_attempts = 0

    def decide(self, npc_state: dict[str, Any], world_view: dict[str, Any]) -> list[Intent]:
        self.last_attempts = 0
        if self.config.offline:
            self.last_attempts = 1
            raise ProviderUnavailable("offline_mode")
        last: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            self.last_attempts = attempt + 1
            try:
                return self._request(npc_state, world_view)
            except ProviderUnavailable as exc:
                last = exc
        raise ProviderUnavailable(f"failed_after_attempts:{self.last_attempts}:{last}")

    def _request(self, npc_state: dict[str, Any], world_view: dict[str, Any]) -> list[Intent]:
        # P2 明确不联网：真实 HTTP 客户端留到 P3。这里只做契约桩。
        raise ProviderUnavailable("network_not_implemented")


class FallbackReasoner(Reasoner):
    """Provider 失败时确定性降级到 MockReasoner，并记录降级次数。"""

    def __init__(self, provider: ProviderClient, fallback: Reasoner | None = None):
        self.provider = provider
        self.fallback = fallback or MockReasoner(seed=42)
        self.degraded = False
        self.degraded_count = 0
        self.last_attempts = 0

    def decide(self, npc_state: dict[str, Any], world_view: dict[str, Any]) -> list[Intent]:
        try:
            intents = self.provider.decide(npc_state, world_view)
            self.last_attempts = self.provider.last_attempts
            return intents
        except ProviderUnavailable:
            self.degraded = True
            self.degraded_count += 1
            self.last_attempts = self.provider.last_attempts
            return self.fallback.decide(npc_state, world_view)
