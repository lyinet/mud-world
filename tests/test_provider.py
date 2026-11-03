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
    """测试禁止联网：用注入的 transport 模拟网络失败，不实际发请求。"""
    from wsim.agent.provider import ProviderError

    def failing_transport(_cfg, _system, _user):
        raise ProviderError("network", "stub")

    cfg = ProviderConfig(
        base_url="https://example.invalid", key="secret", model="m",
        offline=False, max_retries=2,
    )
    provider = ProviderClient(cfg, transport=failing_transport, sleep_fn=lambda _s: None)
    fallback = FallbackReasoner(provider, MockReasoner(seed=42))
    assert fallback.decide(N1, VIEW) == MockReasoner(seed=42).decide(N1, VIEW)
    assert provider.last_attempts == 3
    assert fallback.last_attempts == 3
    assert provider.last_error_kind == "network"



def test_fingerprint_does_not_leak_key():
    cfg = ProviderConfig(base_url="https://example.invalid", key="secret", model="m")
    assert "secret" not in str(cfg.fingerprint())
    assert cfg.fingerprint()["model"] == "m"


def test_provider_disabled_by_default_and_env_required():
    from wsim.agent.provider import ProviderConfig, ProviderConfigError

    assert ProviderConfig.from_env(False) is None
    with pytest.raises(ProviderConfigError):
        ProviderConfig.from_env(True, env={})
    cfg = ProviderConfig.from_env(
        True,
        env={"LLM_BASE_URL": "https://example.invalid/v1", "LLM_API_KEY": "k", "LLM_MODEL": "m"},
    )
    assert cfg is not None and cfg.offline is False and "k" not in str(cfg.fingerprint())
