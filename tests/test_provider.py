"""Provider 桩：单配置、超时/重试参数、离线降级到 MockReasoner。"""
from __future__ import annotations

import pytest

from wsim.agent.provider import (
    FallbackReasoner,
    ProviderClient,
    ProviderConfig,
    ProviderConfigError,
    ProviderUnavailable,
)
from wsim.sim.lod import MockReasoner

N1 = {"id": "n1", "stamina": 80.0, "exits": ["b"]}
VIEW = {"tick": 10}


def test_config_requires_base_url_key_model():
    with pytest.raises(ProviderConfigError):
        ProviderConfig(base_url="", key="k", model="m").validate()
    with pytest.raises(ProviderConfigError):
        ProviderConfig(base_url="https://example", key="", model="m").validate()
    with pytest.raises(ProviderConfigError):
        ProviderConfig(base_url="https://example", key="k", model="").validate()
    with pytest.raises(ProviderConfigError):
        ProviderConfig(base_url="https://example", key="k", model="m", timeout_s=0).validate()


def test_offline_provider_does_not_network_and_falls_back():
    cfg = ProviderConfig(base_url="https://example.invalid", key="secret", model="m", offline=True)
    provider = ProviderClient(cfg)
    with pytest.raises(ProviderUnavailable, match="offline"):
        provider.decide(N1, VIEW)
    assert provider.last_attempts == 1

    fallback = FallbackReasoner(provider, MockReasoner(seed=42))
    intents = fallback.decide(N1, VIEW)
    assert fallback.degraded is True
    assert fallback.degraded_count == 1
    assert intents == MockReasoner(seed=42).decide(N1, VIEW)


def test_retry_count_and_failure_degrades():
    cfg = ProviderConfig(
        base_url="https://example.invalid", key="secret", model="m",
        offline=False, max_retries=2,
    )
    provider = ProviderClient(cfg)
    fallback = FallbackReasoner(provider, MockReasoner(seed=42))
    assert fallback.decide(N1, VIEW) == MockReasoner(seed=42).decide(N1, VIEW)
    assert provider.last_attempts == 3
    assert fallback.last_attempts == 3


def test_fingerprint_does_not_leak_key():
    cfg = ProviderConfig(base_url="https://example.invalid", key="secret", model="m")
    assert "secret" not in str(cfg.fingerprint())
    assert cfg.fingerprint()["model"] == "m"
