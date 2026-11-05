"""provider 注册表 / 路由 / 快照缓存 / provider.py 降级链接线测试。"""
from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.admin import providers, secrets, settings  # noqa: E402
from wsim.agent import provider as provider_mod  # noqa: E402
from wsim.agent.provider import (  # noqa: E402
    Completion,
    ProviderClient,
    ProviderConfig,
    ProviderError,
    ProviderUnavailable,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "service.sqlite"
    settings.init_schema(path)
    return path


@pytest.fixture
def master(monkeypatch: pytest.MonkeyPatch) -> str:
    key = base64.b64encode(os.urandom(32)).decode("ascii")
    monkeypatch.setenv(secrets.MASTER_KEY_ENV, key)
    return key


def _add_provider(
    db_path: Path,
    provider_id: str,
    *,
    key: str | None = "",
    base_url: str = "http://127.0.0.1:9911",
    model: str = "mock-model",
    params: dict | None = None,
    thinking: bool = False,
) -> None:
    store = settings.SettingsStore(db_path)
    entries = store.providers()
    entries = [item for item in entries if item["id"] != provider_id]
    entries.append(
        {
            "id": provider_id,
            "name": provider_id,
            "base_url": base_url,
            "model": model,
            "thinking": thinking,
            "timeout_s": 5,
            "params": params or {"max_retries": 0, "backoff_429_s": 0, "max_tokens": 240},
        }
    )
    store.update({"providers": entries}, who="test")
    if key is not None:
        secrets.SecretStore(db_path).put(provider_id, key or f"sk-{provider_id}", who="test")


def _route(db_path: Path, capability: str, primary: str, fallback: list[str] | None = None) -> None:
    settings.SettingsStore(db_path).update(
        {"routing": {capability: {"provider": primary, "fallback": fallback or []}}},
        who="test",
    )


def _env_client(slot: str = "dialogue", **overrides) -> ProviderClient:
    base = dict(
        base_url="http://env.example",
        key="sk-env-fallback",
        model="env-model",
        timeout_s=3,
        max_retries=0,
        offline=False,
        max_tokens=8,
        temperature=0.0,
        slot=slot,
    )
    base.update(overrides)
    return ProviderClient(ProviderConfig(**base))


@pytest.fixture
def use_db(db_path: Path, master: str, monkeypatch: pytest.MonkeyPatch) -> providers.SnapshotCache:
    cache = providers.SnapshotCache(db_path, max_age_s=5.0, clock=lambda: 0.0)
    monkeypatch.setattr(providers, "_CACHE", cache)
    return cache


def test_chain_order_fields_and_secret(db_path: Path, master: str) -> None:
    _add_provider(db_path, "relay-a")
    _add_provider(db_path, "relay-b", model="mock-b")
    _route(db_path, "dialogue", "relay-a", ["relay-b"])
    cache = providers.SnapshotCache(db_path, clock=lambda: 0.0)
    chain = providers.chain_for_capability("dialogue", cache=cache)
    assert [cfg.provider_id for cfg in chain] == ["relay-a", "relay-b"]
    assert chain[0].key == "sk-relay-a"
    assert chain[0].slot == "dialogue"
    assert chain[0].max_retries == 0
    assert chain[0].extra_body == ({"enable_thinking": False},)
    assert providers.masked_key("relay-a", db_path=db_path) == "sk-r…ay-a"
    assert providers.has_key("relay-a", db_path=db_path) is True
    assert providers.capabilities_status()["quest_gen"] == "stub"
    assert "无 model 调用" in providers.capabilities_evidence()["quest_gen"]


def test_snapshot_throttle_and_reload(db_path: Path, master: str) -> None:
    clock = [0.0]
    cache = providers.SnapshotCache(db_path, max_age_s=5.0, clock=lambda: clock[0])
    store = settings.SettingsStore(db_path)
    store.update({"world": {"ambient_speed": 1}}, who="test")
    assert cache.get()["world"]["ambient_speed"] == 1
    store.update({"world": {"ambient_speed": 4}}, who="test")

    clock[0] = 2.0
    assert cache.get()["world"]["ambient_speed"] == 1  # 5 秒内不查库
    clock[0] = 5.0
    assert cache.get()["world"]["ambient_speed"] == 4
    assert cache.stats()["loads"] == 2


def test_resolve_priority_and_not_configured(db_path: Path, master: str) -> None:
    cache = providers.SnapshotCache(db_path, clock=lambda: 0.0)
    with pytest.raises(providers.NotConfigured):
        providers.resolve("dialogue", cache=cache)
    _add_provider(db_path, "relay-a")
    _route(db_path, "dialogue", "relay-a")
    resolved = providers.resolve("dialogue", cache=providers.SnapshotCache(db_path))
    assert resolved.provider_id == "relay-a"
    # intent 槽不纳入管理面，但 killswitch 仍生效。
    assert providers.chain_for_slot("intent", cache=cache) == []


def test_missing_key_falls_back_to_env(db_path: Path, master: str) -> None:
    _add_provider(db_path, "relay-a", key=None)
    _route(db_path, "dialogue", "relay-a")
    cache = providers.SnapshotCache(db_path)
    assert providers.chain_for_capability("dialogue", cache=cache) == []

    seen: list[tuple[str, str]] = []

    def transport(cfg: ProviderConfig, system: str, user: str):
        seen.append((cfg.base_url, cfg.key))
        return Completion(text="env-ok", usage={"prompt_tokens": 1, "completion_tokens": 1})

    client = _env_client("dialogue")
    client._transport = transport
    assert client.complete("s", "u") == "env-ok"
    assert seen == [("http://env.example", "sk-env-fallback")]
    assert client.stats_snapshot()["fallbacks"] == 0


def test_db_config_overrides_env(db_path: Path, master: str, use_db) -> None:
    _add_provider(db_path, "relay-a", base_url="http://db.example", model="db-model")
    _route(db_path, "dialogue", "relay-a")
    seen: list[ProviderConfig] = []

    def transport(cfg: ProviderConfig, system: str, user: str):
        seen.append(cfg)
        return Completion(text="db-ok", usage={"prompt_tokens": 1, "completion_tokens": 1})

    client = _env_client("dialogue")
    client._transport = transport
    assert client.complete("s", "u") == "db-ok"
    assert seen and seen[0].base_url == "http://db.example"
    assert seen[0].key == "sk-relay-a"
    assert seen[0].provider_id == "relay-a"


def test_killswitch_blocks_before_transport(db_path: Path, master: str, use_db) -> None:
    _add_provider(db_path, "relay-a")
    _route(db_path, "dialogue", "relay-a")
    settings.SettingsStore(db_path).update({"llm": {"global_enabled": False}}, who="ops")
    providers._CACHE.invalidate()

    calls: list[int] = []

    def transport(cfg: ProviderConfig, system: str, user: str):
        calls.append(1)
        return Completion(text="should-not-happen")

    client = _env_client("dialogue")
    client._transport = transport
    with pytest.raises(ProviderUnavailable) as exc:
        client.complete("s", "u")
    assert "llm_killswitch" in str(exc.value)
    assert calls == []
    snapshot = client.stats_snapshot()
    assert snapshot["calls"] == 1
    assert snapshot["success"] == 0
    assert snapshot["errors_by_kind"]["llm_killswitch"] == 1


def test_fallback_chain_429_then_success(db_path: Path, master: str, use_db) -> None:
    _add_provider(db_path, "relay-a")
    _add_provider(db_path, "relay-b", model="mock-b")
    _route(db_path, "dialogue", "relay-a", ["relay-b"])

    seen: list[str] = []

    def transport(cfg: ProviderConfig, system: str, user: str):
        seen.append(cfg.provider_id)
        if cfg.provider_id == "relay-a":
            raise ProviderError("http_429", "rate_limited")
        return Completion(
            text="fallback-ok",
            usage={"prompt_tokens": 3, "completion_tokens": 2},
            thinking_disabled_by="enable_thinking",
        )

    client = _env_client("dialogue")
    client._transport = transport
    assert client.complete("s", "u") == "fallback-ok"
    assert seen == ["relay-a", "relay-b"]
    snapshot = client.stats_snapshot()
    assert snapshot["fallbacks"] == 1
    assert snapshot["http_429"] == 1
    assert snapshot["success"] == 1
    assert client.last_fallback is True
    assert client.config.provider_id == "relay-b"


def test_test_provider_reports_latency_and_error(
    db_path: Path, master: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _add_provider(db_path, "relay-a")
    cache = providers.SnapshotCache(db_path, clock=lambda: 0.0)
    monkeypatch.setattr(providers, "_CACHE", cache)

    def ok_transport(cfg: ProviderConfig, system: str, user: str):
        return Completion(text="ok", usage={"prompt_tokens": 1, "completion_tokens": 1})

    monkeypatch.setattr(provider_mod, "_http_transport", ok_transport)
    good = providers.test_provider("relay-a", cache=cache)
    assert good["ok"] is True and good["model"] == "mock-model"
    assert isinstance(good["latency_ms"], float)
    assert "key" not in good

    def bad_transport(cfg: ProviderConfig, system: str, user: str):
        raise ProviderError("network", "connection_refused")

    monkeypatch.setattr(provider_mod, "_http_transport", bad_transport)
    bad = providers.test_provider("relay-a", cache=cache)
    assert bad["ok"] is False
    assert bad["error"] == "network"
    assert "sk-relay-a" not in str(bad)


def test_test_provider_key_missing_without_master(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(secrets.MASTER_KEY_ENV, raising=False)
    _add_provider(db_path, "relay-a", key=None)
    cache = providers.SnapshotCache(db_path)
    monkeypatch.setattr(providers, "_CACHE", cache)
    result = providers.test_provider("relay-a", cache=cache)
    assert result["ok"] is False
    assert result["error"] == "provider_key_missing"


def test_test_provider_master_key_missing_after_secret_exists(
    db_path: Path, master: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _add_provider(db_path, "relay-a")
    monkeypatch.delenv(secrets.MASTER_KEY_ENV, raising=False)
    cache = providers.SnapshotCache(db_path)
    monkeypatch.setattr(providers, "_CACHE", cache)
    with pytest.raises(secrets.MasterKeyMissing):
        providers.test_provider("relay-a", cache=cache)
