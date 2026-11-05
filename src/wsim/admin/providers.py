"""provider 注册表、按能力路由与主服务快照缓存。

关键约束：
- 快照检查间隔 ≥5s（时间戳比较），主服务每次 LLM 调用只做一次内存读，不查库。
- 解析顺序：DB 配置（providers + routing + admin_secrets）→ 环境变量（调用方现有配置）
  → `llm_not_configured`。
- 未配置主密钥时绝不回退明文：DB 里的 provider 因取不到 key 而跳过，最终由环境变量兜底。
- killswitch（`llm.global_enabled=false`）命中时抛 `Killswitch`，调用方不得继续建连。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from .settings import DEFAULT_SETTINGS, deep_merge, default_settings, resolve_db_path
from . import secrets

CAPABILITIES = (
    "dialogue",
    "strategy",
    "self_goal",
    "night_batch",
    "quest_gen",
    "persona_gen",
    "validation",
)

# 依据真实调用点填写；stub 必须如实标 stub（见 docs/ADMIN-CONSOLE.md）。
CAPABILITY_STATUS: dict[str, str] = {
    "dialogue": "active",
    "strategy": "active",
    "night_batch": "active",
    "self_goal": "stub",
    "quest_gen": "stub",
    "persona_gen": "stub",
    "validation": "stub",
}

CAPABILITY_EVIDENCE: dict[str, str] = {
    "dialogue": "agent/dialogue_async.py::AsyncDialogueQueue -> sim/headless.py 挂到 talk 动作（WSIM_DIALOGUE=1）",
    "strategy": "agent/strategy_client.py::StrategyClient -> sim/headless.py 真实模式（--strategy，非 fake）",
    "night_batch": "sim/headless.py::run_night_batch -> LLMReasoner.decide_batch（decision 槽，与实时决策共用 client）",
    "self_goal": "sim/self_goal.py 为确定性规则台账，无 model 调用",
    "quest_gen": "content/ + actions/quests.py 规则生成，无 model 调用",
    "persona_gen": "content/personas/*.md 静态文本，无生成调用",
    "validation": "agent/prompt.py / agent/intent_model.py 为确定性 schema 校验，无 model 校验器",
}

# 主服务 ProviderClient 的槽位 → 管理面 capability；intent 槽不纳入管理面。
SLOT_CAPABILITY: dict[str, str] = {
    "dialogue": "dialogue",
    "strategy": "strategy",
    "decision": "night_batch",
}

CAPABILITY_SLOT: dict[str, str] = {
    "dialogue": "dialogue",
    "strategy": "strategy",
    "night_batch": "decision",
}


class AdminProviderError(RuntimeError):
    code = "llm_not_configured"

    def __init__(self, code: str = "", detail: str = ""):
        super().__init__(detail or code or self.code)
        if code:
            self.code = code


class NotConfigured(AdminProviderError):
    code = "llm_not_configured"


class Killswitch(AdminProviderError):
    code = "llm_killswitch"


def capabilities_status() -> dict[str, str]:
    return dict(CAPABILITY_STATUS)


def capabilities_evidence() -> dict[str, str]:
    return dict(CAPABILITY_EVIDENCE)


def _read_snapshot(db_path: Path) -> tuple[int, dict[str, Any]]:
    """只读打开库，取全局版本与配置段落；不建表、不写文件。"""
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=1.0)
    try:
        row = conn.execute("SELECT MAX(version) FROM admin_settings").fetchone()
        version = int(row[0] or 0) if row else 0
        rows = conn.execute("SELECT key, value_json FROM admin_settings").fetchall()
    finally:
        conn.close()
    merged = default_settings()
    for key, value_json in rows:
        section = str(key)
        if section not in DEFAULT_SETTINGS:
            continue
        try:
            value = json.loads(str(value_json))
        except (TypeError, ValueError):
            continue
        merged[section] = deep_merge(merged[section], value)
    return version, merged


class SnapshotCache:
    """进程内配置快照；默认每 5 秒最多查一次版本号。"""

    def __init__(
        self,
        db_path: str | Path | None = None,
        max_age_s: float = 5.0,
        clock: Callable[[], float] | None = None,
    ):
        self._fixed_path = Path(db_path) if db_path else None
        self.max_age_s = max(1.0, float(max_age_s))
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._version = -1
        self._settings = default_settings()
        self._checked_at: float | None = None
        self._loads = 0
        self._failures = 0

    @property
    def db_path(self) -> Path:
        return self._fixed_path or resolve_db_path()

    def get(self, force: bool = False) -> dict[str, Any]:
        now = float(self._clock())
        with self._lock:
            if (
                not force
                and self._checked_at is not None
                and now - self._checked_at < self.max_age_s
            ):
                return self._settings
            self._checked_at = now
            path = self.db_path
            if not path.exists():
                return self._settings
            try:
                version, settings = _read_snapshot(path)
            except Exception:  # noqa: BLE001 - DB 不可用时沿用旧快照，不阻塞调用
                self._failures += 1
                return self._settings
            if version != self._version:
                self._version = version
                self._settings = settings
                self._loads += 1
            return self._settings

    def invalidate(self) -> None:
        with self._lock:
            self._checked_at = None

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "version": self._version,
                "loads": self._loads,
                "failures": self._failures,
                "max_age_s": self.max_age_s,
            }


_CACHE = SnapshotCache()


def default_cache() -> SnapshotCache:
    return _CACHE


def current_snapshot(cache: SnapshotCache | None = None, force: bool = False) -> dict[str, Any]:
    return (cache or _CACHE).get(force=force)


def killswitch_enabled(cache: SnapshotCache | None = None) -> bool:
    return not bool(current_snapshot(cache).get("llm", {}).get("global_enabled", True))


def _extra_body(entry: dict[str, Any], slot: str) -> tuple[dict[str, Any], ...]:
    params = entry.get("params") if isinstance(entry.get("params"), dict) else {}
    configured = params.get("extra_body")
    if isinstance(configured, list):
        return tuple(item for item in configured if isinstance(item, dict))
    if bool(entry.get("thinking", False)):
        return ()
    if slot == "dialogue":
        return ({"enable_thinking": False},)
    return ({"reasoning_effort": "none"},)


def _config_from_entry(
    entry: dict[str, Any],
    slot: str,
    *,
    db_path: Path | None = None,
    strict: bool = False,
):
    """把 provider 行 + admin_secrets 组装成 ProviderConfig；不可用时返回 None。"""
    from ..agent.provider import ProviderConfig

    provider_id = str(entry.get("id") or "").strip()
    if not provider_id:
        return None
    base_url = str(entry.get("base_url") or "").strip()
    model = str(entry.get("model") or "").strip()
    if not base_url or not model:
        return None
    secret_store = secrets.SecretStore(db_path or resolve_db_path())
    try:
        key = secret_store.get(provider_id)
    except (KeyError, secrets.MasterKeyMissing, ValueError) as exc:
        if strict and isinstance(exc, secrets.MasterKeyMissing):
            raise
        return None
    params = entry.get("params") if isinstance(entry.get("params"), dict) else {}
    try:
        cfg = ProviderConfig(
            base_url=base_url,
            key=key,
            model=model,
            timeout_s=float(entry.get("timeout_s", 30) or 30),
            max_retries=int(params.get("max_retries", 2) or 0),
            offline=False,
            backoff_base_s=float(params.get("backoff_base_s", 1.0) or 0.0),
            max_tokens=int(params.get("max_tokens", 240) or 1),
            temperature=float(params.get("temperature", 0.7) or 0.0),
            json_mode=bool(params.get("json_mode", slot != "dialogue")),
            slot=slot,
            extra_body=_extra_body(entry, slot),
            backoff_429_s=float(params.get("backoff_429_s", 25.0) or 0.0),
            provider_id=provider_id,
        )
        cfg.validate()
    except Exception:  # noqa: BLE001
        return None
    return cfg


def _ids_for_route(route: Any) -> list[str]:
    if not isinstance(route, dict):
        return []
    ids: list[str] = []
    primary = str(route.get("provider") or "").strip()
    if primary:
        ids.append(primary)
    for item in route.get("fallback") or []:
        name = str(item or "").strip()
        if name and name not in ids:
            ids.append(name)
    return ids


def chain_for_capability(
    capability: str,
    *,
    cache: SnapshotCache | None = None,
    slot: str | None = None,
) -> list[Any]:
    """返回 [primary, fallback...] 的 ProviderConfig 链；未配置返回 []。"""
    snapshot = (cache or _CACHE).get()
    if not bool(snapshot.get("llm", {}).get("global_enabled", True)):
        raise Killswitch()
    route = snapshot.get("routing", {}).get(capability, {})
    providers = {
        str(item.get("id")): item
        for item in (snapshot.get("providers") or [])
        if isinstance(item, dict) and item.get("id")
    }
    chain: list[Any] = []
    path = (cache or _CACHE).db_path
    for provider_id in _ids_for_route(route):
        entry = providers.get(provider_id)
        if entry is None:
            continue
        cfg = _config_from_entry(entry, slot or CAPABILITY_SLOT.get(capability, capability), db_path=path)
        if cfg is not None:
            chain.append(cfg)
    return chain


def chain_for_slot(slot: str, *, cache: SnapshotCache | None = None) -> list[Any]:
    capability = SLOT_CAPABILITY.get(str(slot))
    if not capability:
        # intent 等未纳入管理面的槽位：仍要检查 killswitch。
        if killswitch_enabled(cache):
            raise Killswitch()
        return []
    return chain_for_capability(capability, cache=cache, slot=str(slot))


def resolve(capability: str, *, cache: SnapshotCache | None = None):
    """DB 配置优先；无可用配置抛 `NotConfigured`，调用方再用环境变量兜底。"""
    chain = chain_for_capability(str(capability), cache=cache)
    if not chain:
        raise NotConfigured(f"no_provider_for:{capability}")
    return chain[0]


def masked_key(provider_id: str, db_path: str | Path | None = None) -> str:
    return secrets.SecretStore(db_path or resolve_db_path()).masked(provider_id)


def has_key(provider_id: str, db_path: str | Path | None = None) -> bool:
    return secrets.SecretStore(db_path or resolve_db_path()).has(provider_id)


def test_provider(
    provider_id: str,
    *,
    cache: SnapshotCache | None = None,
) -> dict[str, Any]:
    """发一次最小请求（max_tokens=1），返回 ok/latency_ms/model/error；不回传 key。"""
    cache = cache or _CACHE
    snapshot = cache.get(force=True)
    providers = {
        str(item.get("id")): item
        for item in (snapshot.get("providers") or [])
        if isinstance(item, dict) and item.get("id")
    }
    entry = providers.get(str(provider_id))
    result: dict[str, Any] = {
        "ok": False,
        "latency_ms": None,
        "model": "",
        "provider_id": str(provider_id),
        "error": "",
    }
    if entry is None:
        result["error"] = "provider_not_found"
        return result
    result["model"] = str(entry.get("model") or "")
    try:
        cfg = _config_from_entry(entry, "admin_test", db_path=cache.db_path, strict=True)
    except secrets.MasterKeyMissing:
        raise
    if cfg is None:
        result["error"] = "provider_key_missing"
        return result
    from ..agent.provider import ProviderClient

    probe = ProviderClient(
        replace(cfg, max_tokens=1, max_retries=0, timeout_s=min(float(cfg.timeout_s), 30.0))
    )
    started = time.perf_counter()
    try:
        probe.complete("只回复一个字符。", "ping")
    except Exception as exc:  # noqa: BLE001
        result["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
        result["error"] = str(getattr(exc, "kind", "") or type(exc).__name__)
        return result
    result["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
    result["ok"] = True
    return result
