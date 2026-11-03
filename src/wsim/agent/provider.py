"""Provider 客户端（P3 预备）。

- 默认关闭：只有 `--enable-llm` 且环境变量 `LLM_BASE_URL` / `LLM_API_KEY` /
  `LLM_MODEL` 齐全时才启用真实 HTTP；否则 `offline=True`，不联网。
- 真实实现：OpenAI 兼容的 `/chat/completions`，标准库 `urllib`（不加依赖）。
- 超时、重试（仅对超时/网络/5xx）、错误分类；4xx 与 schema 错误不重试。
- 失败或 schema 校验失败一律降级到 MockReasoner，并记录 `schema_reject` / `fallback`。
"""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from collections import deque
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
from .prompt import NPC_VERBS, Prompt, SchemaError, build_prompt, parse_llm_output


class ProviderConfigError(ValueError):
    pass


class ProviderUnavailable(Exception):
    pass


class ProviderError(ProviderUnavailable):
    """带错误分类的 Provider 故障。kind 用于重试判定与遥测。"""

    RETRYABLE = {"timeout", "network", "http_5xx"}

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
        if errors:
            raise ProviderConfigError("; ".join(errors))

    def fingerprint(self) -> dict[str, Any]:
        """可安全写入日志的配置摘要（不含 key）。"""
        return {"base_url": self.base_url, "model": self.model, "timeout_s": self.timeout_s,
                "max_retries": self.max_retries, "offline": self.offline,
                "max_tokens": self.max_tokens, "temperature": self.temperature,
                "json_mode": self.json_mode}

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
    ) -> "ProviderConfig | None":
        """`enabled=False` 返回 None（不联网）；环境变量缺失也返回 None 并抛错。

        base_url/model 可来自 config/llm.yaml，环境变量优先；key 只从 `key_env` 指定的
        环境变量读取，绝不落盘。
        """
        if not enabled:
            return None
        env = env if env is not None else os.environ
        base = str(env.get("LLM_BASE_URL", base_url)).strip()
        key = str(env.get(key_env, "")).strip()
        model = str(env.get("LLM_MODEL", model)).strip()
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
        )


@dataclass(frozen=True)
class Completion:
    """一次模型返回：文本 + 网关 usage（用于按实测 token 计费）。"""

    text: str
    usage: dict[str, int] | None = None


Transport = Callable[[ProviderConfig, str, str], "str | Completion"]


def load_provider_settings(root: Path | str) -> dict[str, Any]:
    path = Path(root) / "config" / "llm.yaml"
    if yaml is None or not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return dict(raw.get("provider") or {})


def _chat_url(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def _http_transport(cfg: ProviderConfig, system: str, user: str) -> Completion:
    """OpenAI 兼容协议的真实 HTTP 实现（默认只有在启用时才会被调用）。

    `urllib.request.urlopen(timeout=...)` 对连接、发送与读取都生效；
    超时/网络/5xx 由 ProviderClient 指数退避重试，4xx 与 parse 不重试。
    """
    url = _chat_url(cfg.base_url)
    payload: dict[str, Any] = {
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
        payload["response_format"] = {"type": "json_object"}
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
        kind = "http_4xx" if 400 <= int(exc.code) < 500 else "http_5xx"
        raise ProviderError(kind, str(exc.code)) from exc
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
        payload = json.loads(raw)
        content = payload["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProviderError("parse", str(exc)[:80]) from exc
    usage_raw = payload.get("usage") if isinstance(payload, dict) else None
    usage: dict[str, int] = {}
    if isinstance(usage_raw, dict):
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage_raw.get(key)
            if isinstance(value, (int, float)):
                usage[key] = int(value)
    if not isinstance(content, str) or not content.strip():
        # 典型场景：max_tokens 被 reasoning 吃光，finish_reason=length，content=null。
        raise ProviderError("parse", "empty_content", usage=usage)
    return Completion(text=content, usage=usage)


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
        self._transport = transport or _http_transport
        self._sleep = sleep_fn or (lambda _s: None)

    def complete(self, system: str, user: str) -> str:
        """返回模型原始文本；失败按错误分类重试。"""
        self.last_attempts = 0
        self.last_error_kind = ""
        self.last_usage = {}
        self.last_latency_s = 0.0
        if self.config.offline:
            self.last_attempts = 1
            self.last_error_kind = "offline"
            raise ProviderUnavailable("offline_mode")
        last: ProviderError | None = None
        for attempt in range(self.config.max_retries + 1):
            self.last_attempts = attempt + 1
            started = time.perf_counter()
            try:
                result = self._transport(self.config, system, user)
                self.last_latency_s = time.perf_counter() - started
                if isinstance(result, Completion):
                    self.last_usage = dict(result.usage or {})
                    return result.text
                self.last_usage = {}
                return str(result)
            except ProviderError as exc:
                self.last_latency_s = time.perf_counter() - started
                # 失败响应里的 usage 也要保留，否则成本会被系统性低估。
                self.last_usage = dict(exc.usage or {})
                last = exc
                self.last_error_kind = exc.kind
                if not exc.retryable:
                    raise
                if attempt < self.config.max_retries:
                    self._sleep(self.config.backoff_base_s * (2 ** attempt))
            except ProviderUnavailable:
                raise
        raise ProviderError(last.kind if last else "unknown", f"failed_after_attempts:{self.last_attempts}")

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
    """Prompt → HTTP → JSON schema 校验 → Intent；失败/schema 拒绝降级 Mock。"""

    def __init__(
        self,
        provider: ProviderClient,
        fallback: Reasoner,
        content,
        telemetry: CostTelemetry | None = None,
        max_actions: int = 4,
        budget: BudgetGuard | None = None,
    ):
        self.provider = provider
        self.fallback = fallback
        self.content = content
        self.telemetry = telemetry
        self.max_actions = max_actions
        self.budget = budget
        self.fallback_count = 0
        self.calls = 0
        self.ok_calls = 0
        self.calls_by_tier: dict[str, int] = {}
        self.calls_by_timing: dict[str, int] = {}
        self.mock_by_timing: dict[str, int] = {}
        self._recent: dict[str, deque[str]] = {}

    def observe(self, event) -> None:
        actor = str(getattr(event, "actor", "") or "")
        if actor:
            self._recent.setdefault(actor, deque(maxlen=10)).append(
                f"{event.verb}:{event.outcome}"
            )
        observe = getattr(self.fallback, "observe", None)
        if callable(observe):
            observe(event)

    def replan(
        self,
        npc_state: dict[str, Any],
        world_view: dict[str, Any],
        timing: str = "replan",
    ) -> list[Intent]:
        """运行期被拒后的重规划：确定性走 MockReasoner，不额外消耗模型调用。"""
        replan = getattr(self.fallback, "replan", None)
        if callable(replan):
            return replan(npc_state, world_view)
        return []

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
    ) -> dict[str, Any] | None:
        if self.telemetry is None:
            return None
        usage = usage or {}
        rec = self.telemetry.record(
            tick=tick,
            npc=npc,
            tier=tier,
            prompt_text=prompt.text(),
            output_text=output,
            model=self.provider.config.model,
            calls=calls,
            schema_reject=schema_reject,
            fallback=fallback,
            error=error,
            persona_tokens=prompt.persona_tokens,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            latency_s=latency_s,
        )
        if schema_reject:
            self.telemetry.reject("schema_reject")
        if self.budget is not None and calls > 0:
            self.budget.record(
                int(day if day is not None else tick // 144),
                int(rec["input_tokens"]),
                int(rec["output_tokens"]),
                float(rec["estimated_cost"]),
            )
        return rec

    def decide(
        self,
        npc_state: dict[str, Any],
        world_view: dict[str, Any],
        timing: str = "realtime",
    ) -> list[Intent]:
        npc = str(npc_state.get("id", ""))
        tick = int(world_view.get("tick", 0))
        tier = str(world_view.get("tier", "active"))
        day = int(world_view.get("day", tick // 144))
        state = world_view.get("state")
        content = world_view.get("content") or self.content
        if state is None or content is None:
            return self.fallback.decide(npc_state, world_view)
        # 目标层（GoalSelector）仍由确定性 Mock 逻辑负责：先拿到 system_goal，
        # 模型只负责把当前目标展开成动作。失败时直接使用这份确定性底稿。
        draft = self.fallback.decide(npc_state, world_view)
        if not draft:
            # 没有待展开的目标/计划时不要浪费模型调用（夜间已排好当日计划的 NPC 会走到这里）。
            return []
        goal_intents = [i for i in draft if i.verb == "system_goal"]
        prompt = build_prompt(content, state, npc, tick, tier, self._recent.get(npc, ()))
        effective_tier = tier
        if self.budget is not None:
            effective_tier, budget_reason = self.budget.check(
                day, tier, prompt.text(), self.provider.config.max_tokens
            )
            if effective_tier is None:
                self.fallback_count += 1
                self.mock_by_timing[timing] = self.mock_by_timing.get(timing, 0) + 1
                self._record(
                    tick=tick, npc=npc, tier=tier, prompt=prompt, output="",
                    fallback=True, error=budget_reason, calls=0, day=day,
                )
                return draft
            if effective_tier != tier:
                prompt = build_prompt(content, state, npc, tick, effective_tier, self._recent.get(npc, ()))
        self.calls += 1
        self.calls_by_tier[effective_tier] = self.calls_by_tier.get(effective_tier, 0) + 1
        self.calls_by_timing[timing] = self.calls_by_timing.get(timing, 0) + 1
        usage: dict[str, int] = {}
        try:
            text = self.provider.complete(prompt.system, prompt.user)
            usage = dict(self.provider.last_usage)
            intents = parse_llm_output(text, tick, NPC_VERBS, self.max_actions)
        except SchemaError as exc:
            self.fallback_count += 1
            self._record(
                tick=tick, npc=npc, tier=effective_tier, prompt=prompt, output=text[:800],
                schema_reject=True, fallback=True, error=f"schema_reject:{exc.code}",
                day=day, usage=usage, latency_s=self.provider.last_latency_s,
            )
            return draft
        except ProviderUnavailable as exc:
            self.fallback_count += 1
            self._record(
                tick=tick, npc=npc, tier=effective_tier, prompt=prompt, output="",
                fallback=True, error=str(exc), day=day,
                usage=dict(self.provider.last_usage),
                latency_s=self.provider.last_latency_s,
            )
            return draft
        intents = [replace(i, actor=npc) for i in intents]
        self.ok_calls += 1
        self._record(
            tick=tick, npc=npc, tier=effective_tier, prompt=prompt, output=text,
            day=day, usage=usage, latency_s=self.provider.last_latency_s,
        )
        return goal_intents + intents
