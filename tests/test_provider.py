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


@pytest.mark.parametrize(
    ("kind", "retryable"),
    [
        ("timeout", True),
        ("network", True),
        ("http_5xx", True),
        ("http_4xx", False),
        ("parse", False),
    ],
)
def test_error_classification_and_retry_policy(kind, retryable):
    """五类错误各一条测试：可重试的走满 max_retries+1 次，不可重试的立刻返回。"""
    from wsim.agent.provider import ProviderError

    sleeps: list[float] = []

    def transport(_cfg, _system, _user):
        raise ProviderError(kind, "stub")

    cfg = ProviderConfig(
        base_url="https://example.invalid", key="secret", model="m",
        offline=False, max_retries=2, backoff_base_s=0.5,
    )
    client = ProviderClient(cfg, transport=transport, sleep_fn=sleeps.append)
    with pytest.raises(ProviderUnavailable):
        client.complete("sys", "user")
    assert client.last_error_kind == kind
    assert client.last_attempts == (3 if retryable else 1)
    assert len(sleeps) == (2 if retryable else 0)
    if retryable:
        # 指数退避：0.5, 1.0
        assert sleeps == [0.5, 1.0]


def test_http_transport_maps_real_exceptions_without_network(monkeypatch):
    """不联网：monkeypatch urlopen，验证 HTTP/超时/网络/解析四类映射。"""
    import socket
    import urllib.error

    from wsim.agent import provider as mod

    cfg = ProviderConfig(base_url="http://127.0.0.1:1/v1", key="k", model="m", offline=False)

    def _http_error(code):
        def _open(*_args, **_kwargs):
            raise urllib.error.HTTPError(cfg.base_url, code, "stub", {}, None)

        return _open

    for code, expected in ((429, "http_429"), (400, "http_4xx"), (503, "http_5xx")):
        monkeypatch.setattr(mod.urllib.request, "urlopen", _http_error(code))
        with pytest.raises(mod.ProviderError) as exc:
            mod._http_transport(cfg, "s", "u")
        assert exc.value.kind == expected

    def _url_error(reason):
        def _open(*_args, **_kwargs):
            raise urllib.error.URLError(reason)

        return _open

    monkeypatch.setattr(mod.urllib.request, "urlopen", _url_error(socket.timeout("timed out")))
    with pytest.raises(mod.ProviderError) as exc:
        mod._http_transport(cfg, "s", "u")
    assert exc.value.kind == "timeout"

    monkeypatch.setattr(mod.urllib.request, "urlopen", _url_error(ConnectionRefusedError("refused")))
    with pytest.raises(mod.ProviderError) as exc:
        mod._http_transport(cfg, "s", "u")
    assert exc.value.kind == "network"

    class _BadJson:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self):
            return b"<html>not json</html>"

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *_a, **_k: _BadJson())
    with pytest.raises(mod.ProviderError) as exc:
        mod._http_transport(cfg, "s", "u")
    assert exc.value.kind == "parse"

    class _NullContent:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self):
            return (
                b'{"choices":[{"message":{"content":null},"finish_reason":"length"}],'
                b'"usage":{"prompt_tokens":700,"completion_tokens":2048}}'
            )

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *_a, **_k: _NullContent())
    with pytest.raises(mod.ProviderError) as exc:
        mod._http_transport(cfg, "s", "u")
    assert exc.value.kind == "parse" and exc.value.detail == "empty_content"
    assert exc.value.usage["completion_tokens"] == 2048, "失败响应也要保留 usage 计费"


def test_http_transport_body_and_timeout(monkeypatch):
    """请求体必须含 model/messages/temperature/max_tokens，且 timeout 真的传到 urlopen。"""
    import json
    import urllib.error

    from wsim.agent import provider as mod

    captured: dict[str, object] = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self):
            return json.dumps(
                {"choices": [{"message": {"content": '{"actions":[]}'}}]}
            ).encode("utf-8")

    def _open(req, timeout=None):
        captured["req"] = req
        captured["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(mod.urllib.request, "urlopen", _open)
    cfg = ProviderConfig(
        base_url="http://127.0.0.1:3425/v1", key="k", model="group/all",
        offline=False, timeout_s=7.5, max_tokens=77, temperature=0.2,
    )
    out = mod._http_transport(cfg, "sys", "usr")
    assert out.text == '{"actions":[]}'
    assert captured["timeout"] == 7.5
    body = json.loads(captured["req"].data)
    assert body["model"] == "group/all"
    assert body["max_tokens"] == 77
    assert body["temperature"] == 0.2
    assert body["response_format"] == {"type": "json_object"}
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert mod._chat_url("http://127.0.0.1:3425/v1") == "http://127.0.0.1:3425/v1/chat/completions"
    assert mod._chat_url("http://127.0.0.1:3425") == "http://127.0.0.1:3425/v1/chat/completions"
    assert mod._chat_url("http://127.0.0.1:3425/v1/chat/completions").endswith("/chat/completions")


def test_provider_disabled_by_default_and_env_required():
    from wsim.agent.provider import ProviderConfig, ProviderConfigError

    assert ProviderConfig.from_env(False) is None
    with pytest.raises(ProviderConfigError):
        ProviderConfig.from_env(True, env={})
    cfg = ProviderConfig.from_env(
        True,
        env={
            "LLM_BASE_URL": "https://example.invalid/v1",
            "LLM_API_KEY": "sk-very-secret-key",
            "LLM_MODEL": "m",
        },
    )
    assert cfg is not None and cfg.offline is False
    assert "sk-very-secret-key" not in str(cfg.fingerprint())
