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
    slot: str = "decision"

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
                "json_mode": self.json_mode, "slot": self.slot}

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
        result = self._run(system, user)
        self.last_attempts = result["attempts"]
        self.last_error_kind = result["error_kind"]
        self.last_usage = dict(result["usage"] or {})
        self.last_latency_s = result["latency_s"]
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
                }]
            return [{
                "text": text, "usage": dict(self.last_usage), "latency_s": self.last_latency_s,
                "attempts": self.last_attempts, "error_kind": "", "error": None,
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
                "text": "", "usage": {}, "latency_s": 0.0, "attempts": 1,
                "error_kind": "offline", "error": ProviderUnavailable("offline_mode"),
            }
        last: ProviderError | None = None
        attempts = 0
        for attempt in range(self.config.max_retries + 1):
            attempts = attempt + 1
            started = time.perf_counter()
            try:
                result = self._transport(self.config, system, user)
                latency = time.perf_counter() - started
                if isinstance(result, Completion):
                    return {"text": result.text, "usage": dict(result.usage or {}),
                            "latency_s": latency, "attempts": attempts,
                            "error_kind": "", "error": None}
                return {"text": str(result), "usage": {}, "latency_s": latency,
                        "attempts": attempts, "error_kind": "", "error": None}
            except ProviderError as exc:
                latency = time.perf_counter() - started
                last = exc
                if not exc.retryable:
                    return {"text": "", "usage": dict(exc.usage or {}), "latency_s": latency,
                            "attempts": attempts, "error_kind": exc.kind, "error": exc}
                if attempt < self.config.max_retries:
                    self._sleep(self.config.backoff_base_s * (2 ** attempt))
            except ProviderUnavailable as exc:
                return {"text": "", "usage": {}, "latency_s": time.perf_counter() - started,
                        "attempts": attempts, "error_kind": "offline", "error": exc}
        err = ProviderError(last.kind if last else "unknown", f"failed_after_attempts:{attempts}",
                            usage=dict(last.usage or {}) if last else None)
        return {"text": "", "usage": dict(last.usage or {}) if last else {}, "latency_s": 0.0,
                "attempts": attempts, "error_kind": err.kind, "error": err}

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
        usage = dict(res.get("usage") or {})
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
                client=self.intent_client, charge_budget=False,
            )
            return []
        resolution = parse_intent_response(
            out_text, self.intent_catalog, p["content"], p["state"], p["npc"]
        )
        self._record(
            tick=p["tick"], npc=p["npc"], tier=p["tier"], prompt=intent_prompt,
            output=out_text, day=p["day"], usage=usage, latency_s=latency, calls=1,
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
        client: ProviderClient | None = None,
        charge_budget: bool = True,
    ) -> dict[str, Any] | None:
        if self.telemetry is None:
            return None
        usage = usage or {}
        cfg = (client or self.provider).config
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
