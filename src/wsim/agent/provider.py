"""Provider 客户端（P3 预备）。

- 默认关闭：只有 `--enable-llm` 且环境变量 `LLM_BASE_URL` / `LLM_API_KEY` /
  `LLM_MODEL` 齐全时才启用真实 HTTP；否则 `offline=True`，不联网。
- 真实实现：OpenAI 兼容的 `/chat/completions`，标准库 `urllib`（不加依赖）。
- 超时、重试（仅对超时/网络/5xx）、错误分类；4xx 与 schema 错误不重试。
- 失败或 schema 校验失败一律降级到 MockReasoner，并记录 `schema_reject` / `fallback`。
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from collections import Counter, deque
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

from ..core.models import Intent
from ..sim.lod import MockReasoner, Reasoner
from .budget import BudgetGuard
from .cost import CostTelemetry
from .intent_model import (
    IntentCatalog,
    build_intent_prompt,
    load_intent_catalog,
    parse_intent_response,
)
from .prompt import (
    MODEL_VERBS,
    NPC_VERBS,
    RULE_VERBS,
    Prompt,
    SchemaError,
    build_decision_prompt,
    build_prompt,
    build_state_summary,
    build_repair_prompt,
    parse_decision_payload,
    parse_llm_output,
    parse_llm_payload,
)


class ProviderConfigError(ValueError):
    pass


class ProviderUnavailable(Exception):
    pass


class ProviderError(ProviderUnavailable):
    """带错误分类的 Provider 故障。kind 用于重试判定与遥测。"""

    RETRYABLE = {"timeout", "network", "http_5xx", "http_429"}

    def __init__(self, kind: str, detail: str = "", usage: dict[str, int] | None = None):
        super().__init__(f"{kind}:{detail}" if detail else kind)
        self.kind = kind
        self.detail = detail
        # 失败也可能已经消耗 token（如 content=null 但 completion_tokens 已计费）。
        self.usage = dict(usage or {})

    @property
    def retryable(self) -> bool:
        return self.kind in self.RETRYABLE


@dataclass(frozen=True)
class ProviderConfig:
    base_url: str
    key: str
    model: str
    timeout_s: float = 10.0
    max_retries: int = 2
    offline: bool = True
    backoff_base_s: float = 1.0
    max_tokens: int = 512
    temperature: float = 0.0
    json_mode: bool = True
    slot: str = "decision"
    # 关闭思维链的候选请求体，按顺序尝试；第一组被上游接受即停止。
    extra_body: tuple[dict[str, Any], ...] = ()
    backoff_429_s: float = 25.0

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
        if self.backoff_base_s < 0:
            errors.append("backoff_base_s 不能为负")
        if self.max_tokens <= 0:
            errors.append("max_tokens 必须 > 0")
        if self.backoff_429_s < 0:
            errors.append("backoff_429_s 不能为负")
        if not isinstance(self.extra_body, tuple) or any(
            not isinstance(item, dict) for item in self.extra_body
        ):
            errors.append("extra_body 必须是对象列表")
        if errors:
            raise ProviderConfigError("; ".join(errors))

    def fingerprint(self) -> dict[str, Any]:
        """可安全写入日志的配置摘要（不含 key）。"""
        return {"base_url": self.base_url, "model": self.model, "timeout_s": self.timeout_s,
                "max_retries": self.max_retries, "offline": self.offline,
                "max_tokens": self.max_tokens, "temperature": self.temperature,
                "json_mode": self.json_mode, "slot": self.slot,
                "extra_body": list(self.extra_body), "backoff_429_s": self.backoff_429_s}

    @classmethod
    def from_env(
        cls,
        enabled: bool,
        env: dict[str, str] | None = None,
        timeout_s: float = 10.0,
        max_retries: int = 2,
        backoff_base_s: float = 1.0,
        max_tokens: int = 512,
        temperature: float = 0.0,
        base_url: str = "",
        model: str = "",
        key_env: str = "LLM_API_KEY",
        json_mode: bool = True,
        slot: str = "decision",
        model_env: str = "",
        extra_body: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
        backoff_429_s: float = 25.0,
    ) -> "ProviderConfig | None":
        """`enabled=False` 返回 None（不联网）；环境变量缺失也返回 None 并抛错。

        base_url/model 可来自 config/llm.yaml，环境变量优先；key 只从 `key_env` 指定的
        环境变量读取，绝不落盘。
        """
        if not enabled:
            return None
        env = env if env is not None else os.environ
        slot_base = str(env.get(f"LLM_{slot.upper()}_BASE_URL", "")).strip()
        base = slot_base or str(env.get("LLM_BASE_URL", base_url)).strip()
        key = str(env.get(key_env, "")).strip()
        slot_model = str(env.get(model_env, "")).strip() if model_env else ""
        model = slot_model or str(env.get("LLM_MODEL", model)).strip()
        if not (base and key and model):
            raise ProviderConfigError(
                f"--enable-llm 需要 LLM_BASE_URL / {key_env} / LLM_MODEL 环境变量（或 config/llm.yaml 里的 base_url/model）"
            )
        return cls(
            base_url=base,
            key=key,
            model=model,
            timeout_s=timeout_s,
            max_retries=max_retries,
            offline=False,
            backoff_base_s=backoff_base_s,
            max_tokens=max_tokens,
            temperature=temperature,
            json_mode=json_mode,
            slot=slot,
            extra_body=tuple(extra_body or ()),
            backoff_429_s=backoff_429_s,
        )


@dataclass(frozen=True)
class Completion:
    """一次模型返回：文本 + 网关 usage（用于按实测 token 计费）。"""

    text: str
    usage: dict[str, int] | None = None
    # 真正被上游接受的关闭思考参数；未生效时为空，ignored=True。
    thinking_disabled_by: str = ""
    thinking_param_ignored: bool = False


Transport = Callable[[ProviderConfig, str, str], "str | Completion"]


def prompt_sha(system: str, user: str) -> str:
    """意图槽 prompt 指纹：录制/回放按同一条 prompt 对齐。"""
    return hashlib.sha256((system + "\n" + user).encode("utf-8")).hexdigest()


def make_replay_transport(path: Path | str) -> Transport:
    """从录制的 JSONL 回放意图槽返回，用于真实调用不可复现时的确定性重跑。

    录制行格式见 LLMReasoner.record_path。回放不联网；缺记录直接报 replay_miss，
    不伪造返回。
    """
    records: dict[str, dict[str, Any]] = {}
    p = Path(path)
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("prompt_sha"):
            records[str(row["prompt_sha"])] = row

    def _replay(cfg: ProviderConfig, system: str, user: str) -> Completion:
        row = records.get(prompt_sha(system, user))
        if row is None:
            raise ProviderError("replay_miss", prompt_sha(system, user)[:12])
        return Completion(text=str(row.get("output") or ""), usage=dict(row.get("usage") or {}))

    return _replay


def load_provider_settings(root: Path | str) -> dict[str, Any]:
    path = Path(root) / "config" / "llm.yaml"
    if yaml is None or not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return dict(raw.get("provider") or {})


def load_model_slots(root: Path | str) -> dict[str, dict[str, Any]]:
    """读取三模型槽位；slot 内的字段覆盖 provider 的共用字段。"""
    path = Path(root) / "config" / "llm.yaml"
    if yaml is None or not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    provider = dict(raw.get("provider") or {})
    models = dict(raw.get("models") or {})
    out: dict[str, dict[str, Any]] = {}
    for slot in ("decision", "dialogue", "intent"):
        cfg = dict(provider)
        slot_cfg = dict(models.get(slot) or {})
        cfg.update(slot_cfg)
        cfg.setdefault("slot", slot)
        cfg.setdefault("type", "xing_intent" if slot == "intent" else "openai_chat")
        # 槽位未写 model 时沿用 provider.model（允许双/三槽同一个模型名）。
        if not cfg.get("model"):
            cfg["model"] = str(provider.get("model") or "")
        out[slot] = cfg
    return out


def _chat_url(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def describe_extra_body(extra: dict[str, Any]) -> str:
    if not extra:
        return "none"
    if extra.get("reasoning_effort") == "none":
        return "reasoning_effort=none"
    if extra.get("enable_thinking") is False:
        return "enable_thinking=false"
    thinking = extra.get("thinking")
    if isinstance(thinking, dict) and thinking.get("type") == "disabled":
        return "thinking.type=disabled"
    return json.dumps(extra, ensure_ascii=False, sort_keys=True)


def _unknown_param_text(text: str) -> bool:
    """4xx 且上游明确说字段不认识时，才尝试下一组 extra_body。"""
    low = str(text or "").lower()
    markers = ("unknown", "unsupported", "unrecognized", "invalid parameter",
               "extra fields", "unexpected keyword", "not permitted")
    return any(m in low for m in markers)


def _http_transport(cfg: ProviderConfig, system: str, user: str) -> Completion:
    """OpenAI 兼容协议的真实 HTTP 实现（默认只有在启用时才会被调用）。

    `extra_body` 逐组尝试：上游 400/422 明确拒绝未知字段时换下一组；
    全部被拒后回退到不带 extra_body 的原始请求，并标记 ignored=True。
    超时/网络/5xx 由 ProviderClient 指数退避重试，4xx 与 parse 不重试。
    """
    url = _chat_url(cfg.base_url)
    base: dict[str, Any] = {
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
    }
    if cfg.json_mode:
        # 实测本地聚合网关接受该字段；不支持的网关应把 config 里的 json_mode 设为 false，
        # Prompt 本身仍有严格 JSON 指令，不影响可用性。
        base["response_format"] = {"type": "json_object"}

    def send(payload: dict[str, Any], *, thinking_by: str, ignored: bool) -> Completion:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {cfg.key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=cfg.timeout_s) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            code = int(exc.code)
            if code == 429:
                kind = "http_429"
            elif 400 <= code < 500:
                kind = "http_4xx"
            else:
                kind = "http_5xx"
            try:
                detail = exc.read().decode("utf-8", errors="ignore")[:200]
            except Exception:
                detail = ""
            raise ProviderError(kind, f"{code}:{detail}") from exc
        except (socket.timeout, TimeoutError) as exc:
            raise ProviderError("timeout", str(exc)[:80]) from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise ProviderError("timeout", str(reason)[:80]) from exc
            raise ProviderError("network", str(reason)[:80]) from exc
        except OSError as exc:
            raise ProviderError("network", str(exc)[:80]) from exc
        try:
            data = json.loads(raw)
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("parse", str(exc)[:80]) from exc
        usage_raw = data.get("usage") if isinstance(data, dict) else None
        usage: dict[str, int] = {}
        if isinstance(usage_raw, dict):
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = usage_raw.get(key)
                if isinstance(value, (int, float)):
                    usage[key] = int(value)
        if not isinstance(content, str) or not content.strip():
            # 典型场景：max_tokens 被 reasoning 吃光，finish_reason=length，content=null。
            raise ProviderError("parse", "empty_content", usage=usage)
        return Completion(text=content, usage=usage, thinking_disabled_by=thinking_by,
                          thinking_param_ignored=ignored)

    candidates = list(cfg.extra_body)
    if not candidates:
        return send(base, thinking_by="none", ignored=False)

    for extra in candidates:
        payload = dict(base)
        payload.update(extra)
        try:
            return send(payload, thinking_by=describe_extra_body(extra), ignored=False)
        except ProviderError as exc:
            if exc.kind == "http_4xx" and _unknown_param_text(str(exc)):
                continue
            raise
    # 全部候选都被上游以“未知字段”拒绝：退回不含 extra_body 的请求，并如实标记未生效。
    return send(base, thinking_by="none", ignored=True)


class ProviderClient:
    def __init__(
        self,
        config: ProviderConfig,
        transport: Transport | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ):
        config.validate()
        self.config = config
        self.last_attempts = 0
        self.last_error_kind = ""
        self.last_usage: dict[str, int] = {}
        self.last_latency_s = 0.0
        self.last_attempt_latency_s = 0.0
        self.last_thinking_disabled_by = ""
        self.last_thinking_param_ignored = False
        self._transport = transport or _http_transport
        # P0-1：生产默认必须真的等待；旧的 no-op 会让 429 退避变成空操作。
        self._sleep: Callable[[float], None] = sleep_fn or time.sleep
        # 线程安全的调用/延迟/429 统计（complete_many 会并发）。
        self._stats_lock = threading.Lock()
        self.stats: dict[str, Any] = {
            "calls": 0, "attempts": 0, "retries": 0, "http_429": 0,
            "errors_by_kind": Counter(), "success": 0,
            "latency_s": [], "attempt_latency_s": [],
        }

    def _stat(self, **kwargs: Any) -> None:
        with self._stats_lock:
            for key, value in kwargs.items():
                if key == "errors_by_kind":
                    self.stats["errors_by_kind"][str(value)] += 1
                elif key in ("latency_s", "attempt_latency_s"):
                    self.stats[key].append(float(value))
                else:
                    self.stats[key] = int(self.stats.get(key, 0)) + int(value)

    def stats_snapshot(self) -> dict[str, Any]:
        with self._stats_lock:
            call_latencies = sorted(float(x) for x in self.stats["latency_s"])
            attempt_latencies = sorted(float(x) for x in self.stats["attempt_latency_s"])

            def group(values: list[float]) -> dict[str, Any]:
                count = len(values)

                def quantile(q: float) -> float:
                    if not values:
                        return 0.0
                    idx = min(count - 1, max(0, int(round(q * (count - 1)))))
                    return round(values[idx], 3)

                return {
                    "n": count,
                    "mean": round(sum(values) / count, 3) if count else 0.0,
                    "min": round(values[0], 3) if count else 0.0,
                    "p50": quantile(0.50),
                    "p90": quantile(0.90),
                    "p95": quantile(0.95),
                    "max": round(values[-1], 3) if count else 0.0,
                }

            return {
                "calls": int(self.stats["calls"]),
                "attempts": int(self.stats["attempts"]),
                "retries": int(self.stats["retries"]),
                "http_429": int(self.stats["http_429"]),
                "success": int(self.stats["success"]),
                "errors_by_kind": dict(sorted(self.stats["errors_by_kind"].items())),
                # call_latency 含每次尝试 + 全部退避等待；attempt_latency 只含单次 HTTP。
                "call_latency": group(call_latencies),
                "attempt_latency": group(attempt_latencies),
                "latency": group(call_latencies),
            }

    def complete(self, system: str, user: str) -> str:
        """返回模型原始文本；失败按错误分类重试。"""
        result = self._run(system, user)
        self.last_attempts = result["attempts"]
        self.last_error_kind = result["error_kind"]
        self.last_usage = dict(result["usage"] or {})
        self.last_latency_s = result["latency_s"]
        self.last_attempt_latency_s = float(result.get("attempt_latency_s") or 0.0)
        self.last_thinking_disabled_by = result.get("thinking_disabled_by", "")
        self.last_thinking_param_ignored = bool(result.get("thinking_param_ignored", False))
        if result["error"] is not None:
            raise result["error"]
        return result["text"]

    def complete_many(
        self,
        requests: list[tuple[str, str]],
        concurrency: int = 4,
    ) -> list[dict[str, Any]]:
        """批量提交（线程池并发），按输入顺序返回；结果不写实例状态，避免竞态。"""
        if not requests:
            return []
        if len(requests) == 1:
            system, user = requests[0]
            try:
                text = self.complete(system, user)
            except ProviderUnavailable as exc:
                return [{
                    "text": "", "usage": dict(self.last_usage), "latency_s": self.last_latency_s,
                    "attempts": self.last_attempts, "error_kind": self.last_error_kind, "error": exc,
                    "thinking_disabled_by": self.last_thinking_disabled_by,
                    "thinking_param_ignored": self.last_thinking_param_ignored,
                }]
            return [{
                "text": text, "usage": dict(self.last_usage), "latency_s": self.last_latency_s,
                "attempts": self.last_attempts, "error_kind": "", "error": None,
                "thinking_disabled_by": self.last_thinking_disabled_by,
                "thinking_param_ignored": self.last_thinking_param_ignored,
            }]
        if concurrency <= 1:
            return [self._run(system, user) for system, user in requests]
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=max(1, int(concurrency)) ) as pool:
            return list(pool.map(lambda pair: self._run(pair[0], pair[1]), requests))

    def _run(self, system: str, user: str) -> dict[str, Any]:
        """单次调用（含重试）的无状态实现，供 complete/complete_many 共用。"""
        if self.config.offline:
            return {
                "text": "", "usage": {}, "latency_s": 0.0, "attempt_latency_s": 0.0,
                "attempts": 1,
                "error_kind": "offline", "error": ProviderUnavailable("offline_mode"),
                "thinking_disabled_by": "none", "thinking_param_ignored": False,
            }
        last: ProviderError | None = None
        attempts = 0
        call_started = time.perf_counter()
        self._stat(calls=1)
        for attempt in range(self.config.max_retries + 1):
            attempts = attempt + 1
            started = time.perf_counter()
            try:
                result = self._transport(self.config, system, user)
                attempt_latency = time.perf_counter() - started
                call_latency = time.perf_counter() - call_started
                self._stat(attempts=1, success=1, latency_s=call_latency,
                           attempt_latency_s=attempt_latency)
                if isinstance(result, Completion):
                    return {"text": result.text, "usage": dict(result.usage or {}),
                            "latency_s": call_latency, "attempt_latency_s": attempt_latency,
                            "attempts": attempts, "error_kind": "", "error": None,
                            "thinking_disabled_by": result.thinking_disabled_by,
                            "thinking_param_ignored": result.thinking_param_ignored}
                return {"text": str(result), "usage": {}, "latency_s": call_latency,
                        "attempt_latency_s": attempt_latency,
                        "attempts": attempts, "error_kind": "", "error": None,
                        "thinking_disabled_by": "none", "thinking_param_ignored": False}
            except ProviderError as exc:
                attempt_latency = time.perf_counter() - started
                call_latency = time.perf_counter() - call_started
                last = exc
                self._stat(attempts=1, errors_by_kind=exc.kind, latency_s=call_latency,
                           attempt_latency_s=attempt_latency)
                if exc.kind == "http_429":
                    self._stat(http_429=1)
                if not exc.retryable:
                    return {"text": "", "usage": dict(exc.usage or {}), "latency_s": call_latency,
                            "attempt_latency_s": attempt_latency,
                            "attempts": attempts, "error_kind": exc.kind, "error": exc,
                            "thinking_disabled_by": "", "thinking_param_ignored": False}
                if attempt < self.config.max_retries:
                    self._stat(retries=1)
                    # 429 用配置的固定退避（默认 25s）；其余错误指数退避。
                    delay = (
                        self.config.backoff_429_s
                        if exc.kind == "http_429"
                        else self.config.backoff_base_s * (2 ** attempt)
                    )
                    self._sleep(delay)
            except ProviderUnavailable as exc:
                call_latency = time.perf_counter() - call_started
                self._stat(attempts=1, errors_by_kind="offline", latency_s=call_latency,
                           attempt_latency_s=call_latency)
                return {"text": "", "usage": {}, "latency_s": call_latency,
                        "attempt_latency_s": call_latency,
                        "attempts": attempts, "error_kind": "offline", "error": exc,
                        "thinking_disabled_by": "none", "thinking_param_ignored": False}
        call_latency = time.perf_counter() - call_started
        err = ProviderError(last.kind if last else "unknown", f"failed_after_attempts:{attempts}",
                            usage=dict(last.usage or {}) if last else None)
        return {"text": "", "usage": dict(last.usage or {}) if last else {}, "latency_s": call_latency,
                "attempt_latency_s": call_latency,
                "attempts": attempts, "error_kind": err.kind, "error": err,
                "thinking_disabled_by": "", "thinking_param_ignored": False}

    # 兼容 P2 的 Reasoner 契约桩（真实接入走 LLMReasoner）。
    def decide(self, npc_state: dict[str, Any], world_view: dict[str, Any]) -> list[Intent]:
        self.complete("wsim provider stub", json.dumps(npc_state, ensure_ascii=False, sort_keys=True))
        return []


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


class LLMReasoner(Reasoner):
    """混合决策：规则调度器保留生存/生计动作，模型只做目标选择与社交。

    每条模型动作先过动作注册表前置条件，不满足则逐条丢弃并记原因；
    整包 schema 失败做 1 次修复重试，仍失败回退 Mock 底稿。
    """

    def __init__(
        self,
        provider: ProviderClient,
        fallback: Reasoner,
        content,
        telemetry: CostTelemetry | None = None,
        max_actions: int = 4,
        budget: BudgetGuard | None = None,
        registry=None,
        concurrency: int = 4,
        intent_client: ProviderClient | None = None,
        intent_catalog: IntentCatalog | None = None,
        record_path: Path | str | None = None,
    ):
        self.provider = provider
        self.fallback = fallback
        self.content = content
        self.telemetry = telemetry
        self.max_actions = max_actions
        self.budget = budget
        self.registry = registry
        self.concurrency = max(1, int(concurrency))
        self.intent_client = intent_client
        self.intent_catalog = intent_catalog
        if intent_client is not None and intent_catalog is None:
            self.intent_catalog = load_intent_catalog(
                Path(__file__).resolve().parents[3], content.verbs
            )
        self.fallback_count = 0
        self.calls = 0
        self.ok_calls = 0
        self.repair_calls = 0
        self.calls_by_tier: dict[str, int] = {}
        self.calls_by_timing: dict[str, int] = {}
        self.mock_by_timing: dict[str, int] = {}
        self.rule_actions = 0
        self.model_actions = 0
        self.dropped_by_reason: Counter[str] = Counter()
        self.goal_from_model = 0
        self.goal_from_rule = 0
        self.goal_ignored = 0
        self.first_pass = 0
        self.first_fail = 0
        self.repair_attempted = 0
        self.repair_succeeded = 0
        self.final_fail = 0
        self.intent_calls = 0
        self.intent_ok = 0
        self.intent_clarification = 0
        self.intent_reject = 0
        self.intent_reject_by_reason: Counter[str] = Counter()
        self.intent_missing: Counter[str] = Counter()
        # P3-D：适配层观测（未知键 / grounding 拦截 / 延迟）。
        self.intent_unknown_keys: Counter[str] = Counter()
        self.intent_grounding_blocked = 0
        self.intent_grounding_by_field: Counter[str] = Counter()
        self.intent_grounding_cases: list[dict[str, Any]] = []
        self.intent_latencies: list[float] = []
        self.intent_normalizations: Counter[str] = Counter()
        self.intent_dropped_fields: Counter[str] = Counter()
        self.intent_unknown_category = 0
        self.intent_unknown_category_cases: list[dict[str, Any]] = []
        self.intent_usage_prompt = 0
        self.intent_usage_completion = 0
        self.intent_usage_missing = 0
        self.record_path = Path(record_path) if record_path else None
        self._recent: dict[str, deque[str]] = {}
        # P3-C：记录每个 NPC 上一轮决策时的地点，供意图 prompt 的“你刚才在哪”。
        self._last_locations: dict[str, dict[str, str]] = {}
        # 夜间批处理产出的“今日意图”缓存；白天唤醒时套上规则底稿再落地。
        self._hints: dict[str, dict[str, Any]] = {}
        self.wall_time_s = 0.0

    def observe(self, event) -> None:
        actor = str(getattr(event, "actor", "") or "")
        if actor:
            self._recent.setdefault(actor, deque(maxlen=10)).append(
                f"{event.verb}:{event.outcome}"
            )
        observe = getattr(self.fallback, "observe", None)
        if callable(observe):
            observe(event)

    # ---- 对外入口：单条 / 批量 -------------------------------------------------
    def decide(
        self,
        npc_state: dict[str, Any],
        world_view: dict[str, Any],
        timing: str = "realtime",
    ) -> list[Intent]:
        return self.decide_batch([{"npc_state": npc_state, "world_view": world_view, "timing": timing}])[0]

    def decide_batch(self, items: list[dict[str, Any]], draft: bool = True) -> list[list[Intent]]:
        """批量决策：决策槽并发 → 意图槽并发 → 按序解析/过滤。

        draft=False 是夜间批处理模式：只把“今日意图”算好并缓存，
        不生成规则动作、不推进世界；白天 LOD 唤醒时再套规则底稿落地。
        """
        started = time.perf_counter()
        prepared: list[dict[str, Any]] = []
        for item in items:
            p = self._prepare(item)
            p["draft_mode"] = bool(draft)
            prepared.append(p)

        pending = [p for p in prepared if p.get("prompt") is not None]
        if pending:
            results = self.provider.complete_many(
                [(p["prompt"].system, p["prompt"].user) for p in pending],
                concurrency=self.concurrency,
            )
            for p, res in zip(pending, results):
                p["result"] = res

        # 决策输出解析（不触发网络）：成功解析出自然语言的进入意图批处理。
        intent_items: list[dict[str, Any]] = []
        for p in pending:
            res = p.get("result") or {}
            if res.get("error") is not None:
                continue
            try:
                goal, utterance, legacy = parse_decision_payload(str(res.get("text") or ""))
            except SchemaError as exc:
                p["decision_error"] = exc
                continue
            p["goal"], p["utterance"], p["legacy_intents"] = goal, utterance, legacy
            if self.intent_client is not None and utterance and p.get("state") is not None:
                self._prepare_intent(p, utterance)
                if p.get("intent_prompt") is not None:
                    intent_items.append(p)

        if intent_items:
            results = self.intent_client.complete_many(
                [(p["intent_prompt"][0], p["intent_prompt"][1]) for p in intent_items],
                concurrency=self.concurrency,
            )
            for p, res in zip(intent_items, results):
                p["intent_result"] = res

        out: list[list[Intent]] = []
        for p in prepared:
            out.append(self._finish(p))
        self.wall_time_s += time.perf_counter() - started
        return out

    def replan(
        self,
        npc_state: dict[str, Any],
        world_view: dict[str, Any],
        timing: str = "replan",
    ) -> list[Intent]:
        return self.decide_batch(
            [{"npc_state": npc_state, "world_view": world_view, "timing": timing, "is_replan": True}]
        )[0]

    # ---- 阶段 1：备 prompt（不做规则规划，等模型选目标） -----------------------
    def _prepare(self, item: dict[str, Any]) -> dict[str, Any]:
        npc_state = item["npc_state"]
        world_view = item["world_view"]
        timing = str(item.get("timing", "realtime"))
        npc = str(npc_state.get("id", ""))
        tick = int(world_view.get("tick", 0))
        tier = str(world_view.get("tier", "active"))
        day = int(world_view.get("day", tick // 144))
        state = world_view.get("state")
        content = world_view.get("content") or self.content
        base = {
            "npc_state": npc_state, "world_view": world_view, "timing": timing,
            "npc": npc, "tick": tick, "tier": tier, "day": day,
            "is_replan": bool(item.get("is_replan")),
        }
        if state is None or content is None:
            base["fallback_draft"] = True
            return base
        base["state"] = state
        base["content"] = content
        # 规则底稿已覆盖该 tick（MockReasoner._planned_through）时不再花模型调用。
        planned = getattr(self.fallback, "_planned_through", None)
        if isinstance(planned, dict) and planned.get(npc, -1) > tick:
            base["fallback_draft"] = True
            base["skip_reason"] = "already_planned"
            return base
        hint = self._hints.get(npc)
        if hint is not None and int(hint.get("day", -1)) == int(day):
            base["hint"] = hint
            base["prompt"] = None
            return base
        make = build_decision_prompt if self.intent_client is not None else build_prompt
        prompt = make(content, state, npc, tick, tier, self._recent.get(npc, ()))
        if self.budget is not None:
            effective_tier, reason = self.budget.check(
                day, tier, prompt.text(), self.provider.config.max_tokens
            )
            if effective_tier is None:
                self.fallback_count += 1
                self.mock_by_timing[timing] = self.mock_by_timing.get(timing, 0) + 1
                self._record(
                    tick=tick, npc=npc, tier=tier, prompt=prompt, output="",
                    fallback=True, error=reason, calls=0, day=day,
                )
                base["fallback_draft"] = True
                return base
            if effective_tier != tier:
                tier = effective_tier
                prompt = make(content, state, npc, tick, tier, self._recent.get(npc, ()))
        base.update({"tier": tier, "prompt": prompt})
        return base

    def _prepare_intent(self, p: dict[str, Any], utterance: str) -> None:
        """组织意图槽 prompt；预算超限则只回退规则，不阻断决策。"""
        if self.intent_catalog is None:
            return
        summary = build_state_summary(
            p["content"], p["state"], p["npc"], p["tick"], p["tier"],
            self._recent.get(p["npc"], ()),
        )
        if summary.get("location"):
            npc = str(p["npc"])
            summary["previous_location"] = dict(
                self._last_locations.get(npc)
                or {
                    "id": str(summary["location"]),
                    "name": str(summary.get("location_name") or summary["location"]),
                }
            )
            self._last_locations[npc] = {
                "id": str(summary["location"]),
                "name": str(summary.get("location_name") or summary["location"]),
            }
        system, user = build_intent_prompt(
            self.intent_catalog, utterance, summary, list(self._recent.get(p["npc"], ()))[-4:]
        )
        p["intent_prompt"] = (system, user)
        p["intent_text"] = utterance
        # P3-D：grounding 语料只放「原话 + 前几轮 + 实体名」，
        # 不放功能表的 source 提示，避免“商店/地点清单”这类模板文字被当成依据。
        p["intent_ground"] = json.dumps(
            {
                "history": list(self._recent.get(p["npc"], ()))[-4:],
                "location": summary.get("location"),
                "location_name": summary.get("location_name"),
                "exits": summary.get("exits"),
                "reachable_locations": summary.get("reachable_locations"),
                "nearby_npcs": summary.get("nearby_npcs"),
            },
            ensure_ascii=False, sort_keys=True,
        )
        if self.budget is not None:
            ok, reason = self.budget.check_intent(p["day"])
            if not ok:
                p["intent_budget_denied"] = reason
                p["intent_prompt"] = None

    # ---- 阶段 3：解析、修复、过滤、合并 ---------------------------------------
    def _finish(self, p: dict[str, Any]) -> list[Intent]:
        npc_state = p["npc_state"]
        world_view = p["world_view"]
        npc, tick, tier, day = p["npc"], p["tick"], p["tier"], p["day"]
        timing = p["timing"]
        state = world_view.get("state")
        if p.get("hint"):
            return self._finish_hint(p)
        if p.get("fallback_draft") or state is None:
            if p.get("skip_reason") == "already_planned":
                return self._rule_draft(p, count_goal=False)
            return self._fallback(p, error="budget_or_missing_state")

        prompt: Prompt = p["prompt"]
        res = p.get("result") or {}
        usage = dict(res.get("usage") or {})
        latency = float(res.get("latency_s") or 0.0)
        self.calls += 1
        self.calls_by_tier[tier] = self.calls_by_tier.get(tier, 0) + 1
        self.calls_by_timing[timing] = self.calls_by_timing.get(timing, 0) + 1

        if res.get("error") is not None:
            return self._fallback(
                p, error=f"{res.get('error_kind') or 'error'}:{res.get('error')}",
                usage=usage, latency_s=latency,
            )

        text = str(res.get("text") or "")
        if self.intent_client is None:
            return self._finish_legacy(p, text, usage, latency)

        goal = str(p.get("goal") or "")
        utterance = str(p.get("utterance") or "")
        intents: list[Intent] = list(p.get("legacy_intents") or [])
        error = p.get("decision_error")
        if error is not None:
            self.first_fail += 1
            self.repair_attempted += 1
            repaired = self._repair_decision(p, text, error)
            if repaired is None:
                self.final_fail += 1
                self.fallback_count += 1
                self._record(
                    tick=tick, npc=npc, tier=tier, prompt=prompt, output=text[:800],
                    schema_reject=True, fallback=True,
                    error=f"schema_reject:{getattr(error, 'code', 'decision')}",
                    day=day, usage=usage, latency_s=latency, calls=1,
                )
                return self._rule_draft(p)
            text, goal, utterance, usage, latency = repaired
            self.repair_succeeded += 1
        else:
            self.first_pass += 1

        if utterance:
            intents.extend(self._resolve_intent(p))
        self.ok_calls += 1
        self._record(
            tick=tick, npc=npc, tier=tier, prompt=prompt, output=text,
            day=day, usage=usage, latency_s=latency, calls=1,
        )
        if not p.get("draft_mode", True):
            # 夜间批处理：只缓存意图，不推进世界；白天唤醒时套规则底稿。
            self._hints[npc] = {
                "day": int(day), "tick": int(tick), "goal": goal, "intents": intents,
            }
            return []
        draft = self._rule_draft(p, goal_override=goal)
        kept = self._filter_model_actions(intents, draft, state, npc)
        return draft + kept

    def _finish_hint(self, p: dict[str, Any]) -> list[Intent]:
        """白天唤醒时消费夜间意图：规则底稿保生存，模型社交逐条复核后落地。"""
        hint = self._hints.pop(p["npc"], None) or p.get("hint") or {}
        goal = str(hint.get("goal") or "")
        intents = list(hint.get("intents") or [])
        draft = self._rule_draft(p, goal_override=goal)
        kept = self._filter_model_actions(intents, draft, p["state"], p["npc"])
        return draft + kept

    def _finish_legacy(self, p: dict[str, Any], text: str, usage: dict[str, int], latency: float) -> list[Intent]:
        """P3-A 兼容路径：没有 intent 槽时，决策槽直接给 JSON 动作。"""
        npc, tick, tier, day = p["npc"], p["tick"], p["tier"], p["day"]
        prompt: Prompt = p["prompt"]
        state = p["state"]
        try:
            intents, meta = parse_llm_payload(text, tick, NPC_VERBS, self.max_actions)
            self.first_pass += 1
        except SchemaError as exc:
            self.first_fail += 1
            self.repair_attempted += 1
            repaired = self._repair(prompt, text, exc, p)
            if repaired is None:
                self.final_fail += 1
                self.fallback_count += 1
                self._record(
                    tick=tick, npc=npc, tier=tier, prompt=prompt, output=text[:800],
                    schema_reject=True, fallback=True,
                    error=f"schema_reject:{exc.code}", day=day, usage=usage,
                    latency_s=latency, calls=1,
                )
                return self._rule_draft(p)
            text, intents, meta, usage, latency = repaired
            self.repair_succeeded += 1
        goal = str(meta.get("goal") or "")
        draft = self._rule_draft(p, goal_override=goal)
        kept = self._filter_model_actions(intents, draft, state, npc)
        self.ok_calls += 1
        self._record(
            tick=tick, npc=npc, tier=tier, prompt=prompt, output=text,
            day=day, usage=usage, latency_s=latency, calls=1,
        )
        return draft + kept

    def _resolve_intent(self, p: dict[str, Any]) -> list[Intent]:
        """调用意图槽结果：ok 才落地；缺字段不猜；未知类别拒绝。"""
        res = p.get("intent_result") or {}
        prompt_pair = p.get("intent_prompt")
        if not prompt_pair:
            return []
        self.intent_calls += 1
        latency = float(res.get("latency_s") or 0.0)
        self.intent_latencies.append(latency)
        usage = dict(res.get("usage") or {})
        if usage:
            self.intent_usage_prompt += int(usage.get("prompt_tokens") or 0)
            self.intent_usage_completion += int(usage.get("completion_tokens") or 0)
        else:
            self.intent_usage_missing += 1
        out_text = str(res.get("text") or "")
        system, user = prompt_pair
        intent_prompt = Prompt(system=system, user=user)
        if res.get("error") is not None:
            self.intent_reject += 1
            self.intent_reject_by_reason[f"transport:{res.get('error_kind') or 'error'}"] += 1
            self._record(
                tick=p["tick"], npc=p["npc"], tier=p["tier"], prompt=intent_prompt,
                output=out_text[:800], error=f"{res.get('error_kind')}:{res.get('error')}",
                day=p["day"], usage=usage, latency_s=latency, calls=1,
                thinking_disabled_by=str(res.get("thinking_disabled_by") or ""),
                thinking_param_ignored=bool(res.get("thinking_param_ignored")),
                attempt_latency_s=res.get("attempt_latency_s"),
                client=self.intent_client, charge_budget=False,
            )
            return []
        resolution = parse_intent_response(
            out_text, self.intent_catalog, p["content"], p["state"], p["npc"],
            utterance=str(p.get("intent_text") or ""),
            context_text=str(p.get("intent_ground") or ""),
        )
        for key in resolution.unknown_keys:
            self.intent_unknown_keys[key] += 1
        if resolution.blocked:
            self.intent_grounding_blocked += 1
            for row in resolution.blocked:
                self.intent_grounding_by_field[str(row.get("field") or "")] += 1
            if len(self.intent_grounding_cases) < 20:
                self.intent_grounding_cases.append({
                    "tick": int(p["tick"]), "npc": str(p["npc"]),
                    "utterance": str(p.get("intent_text") or ""),
                    "model_output": out_text[:400],
                    "blocked": list(resolution.blocked),
                })
        for name in resolution.dropped_fields:
            self.intent_dropped_fields[name] += 1
        if resolution.reason == "unknown_category":
            self.intent_unknown_category += 1
            if len(self.intent_unknown_category_cases) < 20:
                self.intent_unknown_category_cases.append({
                    "tick": int(p["tick"]), "npc": str(p["npc"]),
                    "utterance": str(p.get("intent_text") or ""),
                    "model_output": out_text[:400],
                })
        for name, row in resolution.normalizations.items():
            self.intent_normalizations[f"{name}:{row.get('value')}{row.get('unit') or ''}"] += 1
        if self.record_path is not None:
            self.record_path.parent.mkdir(parents=True, exist_ok=True)
            with self.record_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "prompt_sha": prompt_sha(system, user),
                    "utterance": str(p.get("intent_text") or ""),
                    "output": out_text,
                    "usage": usage,
                    "latency_s": latency,
                    "status": resolution.status,
                }, ensure_ascii=False) + "\n")
        self._record(
            tick=p["tick"], npc=p["npc"], tier=p["tier"], prompt=intent_prompt,
            output=out_text, day=p["day"], usage=usage, latency_s=latency, calls=1,
            thinking_disabled_by=str(res.get("thinking_disabled_by") or ""),
            thinking_param_ignored=bool(res.get("thinking_param_ignored")),
            attempt_latency_s=res.get("attempt_latency_s"),
            client=self.intent_client, charge_budget=False,
        )
        if resolution.status == "ok":
            self.intent_ok += 1
            return [resolution.intent(p["npc"], p["tick"])]
        if resolution.status == "needs_clarification":
            self.intent_clarification += 1
            for name in resolution.missing:
                self.intent_missing[name] += 1
            self.intent_reject_by_reason["needs_clarification"] += 1
            return []
        reason = resolution.reason or resolution.status
        self.intent_reject += 1
        self.intent_reject_by_reason[reason] += 1
        return []

    def _repair(self, prompt: Prompt, bad_output: str, exc: SchemaError, p: dict[str, Any]) -> tuple[str, list[Intent], dict[str, Any], dict[str, int], float] | None:
        repair_prompt = build_repair_prompt(prompt, bad_output, str(exc))
        try:
            text = self.provider.complete(repair_prompt.system, repair_prompt.user)
            usage = dict(self.provider.last_usage)
            latency = float(self.provider.last_latency_s)
            intents, meta = parse_llm_payload(text, p["tick"], NPC_VERBS, self.max_actions)
        except (SchemaError, ProviderUnavailable):
            return None
        finally:
            self.repair_calls += 1
            self.calls += 1
            self.calls_by_tier[p["tier"]] = self.calls_by_tier.get(p["tier"], 0) + 1
            self.calls_by_timing[p["timing"]] = self.calls_by_timing.get(p["timing"], 0) + 1
            self._record(
                tick=p["tick"], npc=p["npc"], tier=p["tier"], prompt=repair_prompt,
                output="", day=p["day"], calls=1,
            )
        return text, intents, meta, usage, latency

    def _repair_decision(self, p: dict[str, Any], bad_output: str, exc: SchemaError) -> tuple[str, str, str, dict[str, int], float] | None:
        repair_prompt = build_repair_prompt(p["prompt"], bad_output, str(exc))
        try:
            text = self.provider.complete(repair_prompt.system, repair_prompt.user)
            usage = dict(self.provider.last_usage)
            latency = float(self.provider.last_latency_s)
            goal, utterance, _legacy = parse_decision_payload(text)
        except (SchemaError, ProviderUnavailable):
            return None
        finally:
            self.repair_calls += 1
            self.calls += 1
            self.calls_by_tier[p["tier"]] = self.calls_by_tier.get(p["tier"], 0) + 1
            self.calls_by_timing[p["timing"]] = self.calls_by_timing.get(p["timing"], 0) + 1
            self._record(
                tick=p["tick"], npc=p["npc"], tier=p["tier"], prompt=repair_prompt,
                output="", day=p["day"], calls=1,
            )
        return text, goal, utterance, usage, latency

    # ---- 规则底稿与逐条预检 -----------------------------------------------------
    def _rule_draft(self, p: dict[str, Any], goal_override: str = "", count_goal: bool = True) -> list[Intent]:
        npc_state = p["npc_state"]
        world_view = p["world_view"]
        try:
            if p.get("is_replan"):
                draft = self.fallback.replan(npc_state, world_view, timing=p["timing"])
            elif goal_override:
                draft = self.fallback.decide(npc_state, world_view, goal_override=goal_override)
            else:
                draft = self.fallback.decide(npc_state, world_view)
        except TypeError:
            draft = self.fallback.decide(npc_state, world_view)
        goal_intents = [i for i in (draft or []) if i.verb == "system_goal"]
        if not count_goal:
            return list(draft or [])
        if goal_override:
            last = (getattr(self.fallback, "decisions", None) or [{}])[-1]
            accepted = (
                bool(goal_intents) and str(goal_intents[0].args.get("goal", "")) == goal_override
            ) or str(last.get("goal", "")) == goal_override
            if accepted:
                self.goal_from_model += 1
            else:
                self.goal_ignored += 1
                self.goal_from_rule += 1
        else:
            self.goal_from_rule += 1
        self.rule_actions += len([i for i in (draft or []) if i.verb != "system_goal"])
        return list(draft or [])

    def _fallback(self, p: dict[str, Any], error: str, usage: dict[str, int] | None = None, latency_s: float = 0.0) -> list[Intent]:
        self.fallback_count += 1
        self.mock_by_timing[p["timing"]] = self.mock_by_timing.get(p["timing"], 0) + 1
        if p.get("prompt") is not None:
            self._record(
                tick=p["tick"], npc=p["npc"], tier=p["tier"], prompt=p["prompt"], output="",
                fallback=True, error=error, day=p["day"], usage=usage,
                latency_s=latency_s, calls=0,
            )
        return self._rule_draft(p)

    def _filter_model_actions(
        self,
        intents: list[Intent],
        draft: list[Intent],
        state,
        npc: str,
    ) -> list[Intent]:
        rule_pairs = {(i.verb, _args_key(i.args)) for i in draft if i.verb != "system_goal"}
        kept: list[Intent] = []
        seen: set[tuple[str, str]] = set()
        for intent in intents:
            intent = replace(intent, actor=npc, source="llm")
            key = (intent.verb, _args_key(intent.args))
            if intent.verb in RULE_VERBS:
                self.dropped_by_reason["rule_owned_survival"] += 1
                continue
            if intent.verb not in MODEL_VERBS:
                self.dropped_by_reason["not_model_scope"] += 1
                continue
            if key in rule_pairs:
                self.dropped_by_reason["duplicate_rule"] += 1
                continue
            if key in seen:
                self.dropped_by_reason["duplicate_model"] += 1
                continue
            action = self.registry.get(intent.verb) if self.registry is not None else None
            if action is None:
                self.dropped_by_reason["unknown_action"] += 1
                continue
            try:
                reasons = action.preconditions(state, intent)
            except Exception as exc:  # 前置条件自身异常不应拖垮整包
                self.dropped_by_reason[f"precheck_error:{type(exc).__name__}"] += 1
                continue
            if reasons:
                self.dropped_by_reason[str(reasons[0]).split(":")[0]] += 1
                continue
            kept.append(intent)
            seen.add(key)
            self.model_actions += 1
        return kept

    def _record(
        self,
        *,
        tick: int,
        npc: str,
        tier: str,
        prompt: Prompt,
        output: str,
        schema_reject: bool = False,
        fallback: bool = False,
        error: str = "",
        calls: int = 1,
        day: int | None = None,
        usage: dict[str, int] | None = None,
        latency_s: float = 0.0,
        attempt_latency_s: float | None = None,
        thinking_disabled_by: str | None = None,
        thinking_param_ignored: bool | None = None,
        client: ProviderClient | None = None,
        charge_budget: bool = True,
    ) -> dict[str, Any] | None:
        if self.telemetry is None:
            return None
        usage = usage or {}
        active = client or self.provider
        cfg = active.config
        if attempt_latency_s is None:
            attempt_latency_s = float(getattr(active, "last_attempt_latency_s", 0.0) or 0.0)
        if thinking_disabled_by is None:
            thinking_disabled_by = str(getattr(active, "last_thinking_disabled_by", "") or "")
        if thinking_param_ignored is None:
            thinking_param_ignored = bool(getattr(active, "last_thinking_param_ignored", False))
        reported = bool(usage)
        # intent 槽上游可能不返回 usage：按要求记 null，不做字符估算。
        missing_intent_usage = cfg.slot == "intent" and not reported
        rec = self.telemetry.record(
            tick=tick,
            npc=npc,
            tier=tier,
            prompt_text=prompt.text(),
            output_text=output,
            model=cfg.model,
            slot=cfg.slot,
            calls=calls,
            schema_reject=schema_reject,
            fallback=fallback,
            error=error,
            persona_tokens=prompt.persona_tokens,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            latency_s=latency_s,
            attempt_latency_s=attempt_latency_s,
            thinking_disabled_by=thinking_disabled_by,
            thinking_param_ignored=thinking_param_ignored,
            usage_source="reported" if reported else ("missing" if missing_intent_usage else "estimated"),
            estimate_missing=not missing_intent_usage,
        )
        if schema_reject:
            self.telemetry.reject("schema_reject")
        if self.budget is not None and calls > 0 and charge_budget:
            self.budget.record(
                int(day if day is not None else tick // 144),
                int(rec["input_tokens"] or 0),
                int(rec["output_tokens"] or 0),
                float(rec["estimated_cost"] or 0.0),
            )
        return rec

    def summary(self) -> dict[str, Any]:
        attempted = self.first_pass + self.first_fail
        final_ok = self.first_pass + self.repair_succeeded
        latencies = sorted(self.intent_latencies)
        n_lat = len(latencies)

        def quantile(q: float) -> float:
            if not latencies:
                return 0.0
            idx = min(n_lat - 1, max(0, int(round(q * (n_lat - 1)))))
            return round(latencies[idx], 3)

        return {
            "rule_actions": self.rule_actions,
            "model_actions": self.model_actions,
            "dropped_actions": sum(self.dropped_by_reason.values()),
            "dropped_by_reason": dict(sorted(self.dropped_by_reason.items())),
            "first_pass": self.first_pass,
            "first_fail": self.first_fail,
            "repair_attempted": self.repair_attempted,
            "repair_succeeded": self.repair_succeeded,
            "final_fail": self.final_fail,
            "first_pass_rate": round(self.first_pass / attempted, 4) if attempted else 0.0,
            "post_repair_pass_rate": round(final_ok / attempted, 4) if attempted else 0.0,
            "final_fail_rate": round(self.final_fail / attempted, 4) if attempted else 0.0,
            "goal_from_model": self.goal_from_model,
            "goal_from_rule": self.goal_from_rule,
            "goal_ignored": self.goal_ignored,
            "repair_calls": self.repair_calls,
            "intent_calls": self.intent_calls,
            "intent_ok": self.intent_ok,
            "intent_clarification": self.intent_clarification,
            "intent_reject": self.intent_reject,
            "intent_reject_by_reason": dict(sorted(self.intent_reject_by_reason.items())),
            "intent_missing": dict(sorted(self.intent_missing.items())),
            "intent_unknown_keys": dict(sorted(self.intent_unknown_keys.items())),
            "intent_grounding_blocked": self.intent_grounding_blocked,
            "intent_grounding_by_field": dict(sorted(self.intent_grounding_by_field.items())),
            "intent_grounding_cases": list(self.intent_grounding_cases),
            "intent_normalizations": dict(sorted(self.intent_normalizations.items())),
            "intent_dropped_fields": dict(sorted(self.intent_dropped_fields.items())),
            "intent_unknown_category": self.intent_unknown_category,
            "intent_unknown_category_cases": list(self.intent_unknown_category_cases),
            "intent_latency": {
                "n": n_lat,
                "mean": round(sum(latencies) / n_lat, 3) if n_lat else 0.0,
                "min": round(latencies[0], 3) if n_lat else 0.0,
                "p50": quantile(0.50),
                "p90": quantile(0.90),
                "p95": quantile(0.95),
                "max": round(latencies[-1], 3) if n_lat else 0.0,
            },
            "intent_usage": {
                "prompt_tokens": self.intent_usage_prompt,
                "completion_tokens": self.intent_usage_completion,
                "missing_usage_calls": self.intent_usage_missing,
            },
            "intent_client_stats": (
                self.intent_client.stats_snapshot() if self.intent_client is not None else {}
            ),
            "action_ratio": {
                "rule": round(
                    self.rule_actions / max(1, self.rule_actions + self.model_actions), 4
                ),
                "model": round(
                    self.model_actions / max(1, self.rule_actions + self.model_actions), 4
                ),
            },
            "calls_by_timing": dict(sorted(self.calls_by_timing.items())),
            "mock_by_timing": dict(sorted(self.mock_by_timing.items())),
            "wall_time_s": round(self.wall_time_s, 3),
        }


def _args_key(args: Any) -> str:
    return json.dumps(args or {}, ensure_ascii=False, sort_keys=True)
