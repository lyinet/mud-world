"""对话模型运行时：双路配置 + 唯一模型调用入口。

红线：
- 模型台词只进入 `dialogue_aside.jsonl`、`dialogue.jsonl` 与本次 HTTP 响应的
  `details.reply`；
- 绝不写入 `memory_add` / 事件库 / 世界状态 / `state_hash` / `event_hash`；
- 回放与离线路径仍走 `agent/dialogue.py::compose_talk` 的确定性模板。

配置来源：
1. 管理台 provider 注册表（`wsim.admin.providers.resolve("dialogue")`，DB 优先）；
2. 环境变量 / `config/llm.yaml`（`ProviderConfig.from_env`）。
两条路都走同一个 `ProviderClient`，因此能力位回写、预算与降级链一致。

开关：`XKX_DIALOGUE_MODEL=1`（默认关）。关闭时不构造任何 provider，
也不会影响 `llm_enabled` / decision / intent / strategy 槽。

模型调用只有一条代码路径：
`api/session.py::WorldSession.finalize_dialogue` → `AsyncDialogueQueue.reply_sync`
（事件事务提交后、不持锁）。本模块只负责构造队列与开关判定。
"""
from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..admin.providers import resolve as resolve_admin_provider
from .budget import BudgetGuard, BudgetLimits
from .cost import CostTelemetry, load_cost_rates
from .dialogue_async import AsyncDialogueQueue, dialogue_timeout_s
from .provider import ProviderClient, ProviderConfig, ProviderConfigError
from .provider import load_model_slots, load_provider_settings

_ENABLED_VALUES = ("1", "true", "yes", "on")


class _DialogueClient(ProviderClient):
    """对话槽不重试、单次调用硬超时：管理台降级链在调用时重新解析，这里逐个钳制。"""

    def _config_chain(self) -> tuple[list[ProviderConfig], str]:
        chain, blocked = super()._config_chain()
        limit = dialogue_timeout_s()
        return [
            replace(
                cfg,
                max_retries=0,
                timeout_s=min(float(cfg.timeout_s or limit), limit),
            )
            for cfg in chain
        ], blocked


def enabled() -> bool:
    return os.environ.get("XKX_DIALOGUE_MODEL", "").strip().lower() in _ENABLED_VALUES


def _env_client(root: Path) -> ProviderClient | None:
    settings = load_provider_settings(root)
    slots = load_model_slots(root)
    s = dict(slots.get("dialogue") or settings)
    if not s:
        return None
    try:
        cfg = ProviderConfig.from_env(
            True,
            timeout_s=float(s.get("timeout_s", 60)),
            # 对话只服务本次 HTTP 补配音：失败/超时直接降级模板，不在 provider 层重试。
            max_retries=0,
            backoff_base_s=float(s.get("backoff_base_s", 1.0)),
            max_tokens=int(s.get("max_tokens", 4096)),
            temperature=float(s.get("temperature", 0.8)),
            base_url=str(s.get("base_url", "")),
            model=str(s.get("model", "")),
            key_env=str(s.get("api_key_env", "LLM_API_KEY")),
            json_mode=False,  # 对话是纯文本，不能强制 JSON
            slot="dialogue",
            model_env=str(s.get("model_env", "")),
            extra_body=list(s.get("extra_body") or []),
            backoff_429_s=float(s.get("backoff_429_s", 25.0)),
        )
    except ProviderConfigError:
        return None
    return _DialogueClient(cfg)


def _dialogue_budget(root: Path) -> BudgetGuard | None:
    """独立对话预算；配置缺失时返回 None（不加日限，失败仍走 provider 降级）。"""
    try:
        settings = load_provider_settings(root)
        rates = load_cost_rates(root)
        raw = dict(settings.get("budget") or {})
        return BudgetGuard(
            BudgetLimits.from_dict(raw),
            input_rate=float(rates.per_1k_input_tokens) / 1000.0,
            output_rate=float(rates.per_1k_output_tokens) / 1000.0,
        )
    except Exception:  # noqa: BLE001 - 预算配置坏了不能阻断对话，退化为无日限
        return None


def build_queue(root: Path | str, run_dir: Path | str) -> AsyncDialogueQueue | None:
    """按“管理台优先、env 兜底”的顺序构造对话队列；无可用配置返回 None。

    开关是显式 opt-in：只有 `enabled()` 为真且拿到可用 provider 才会走到这里。
    对话调用不做 provider 层重试（超时即模板），避免慢网关拖住后续请求。
    """
    root_path = Path(root)
    client: ProviderClient | None = None
    source = ""
    try:
        cfg = resolve_admin_provider("dialogue")
    except Exception:  # noqa: BLE001 - 管理库缺失/主密钥缺失都必须回退 env
        cfg = None
    if cfg is not None:
        client = _DialogueClient(replace(cfg, max_retries=0))
        source = "admin_registry"
    if client is None:
        client = _env_client(root_path)
        if client is not None:
            source = "env"
    if client is None:
        return None
    queue = AsyncDialogueQueue(
        client,
        budget=_dialogue_budget(root_path),
        telemetry=CostTelemetry(rates=load_cost_rates(root_path)),
        log_dir=Path(run_dir),
        tier="active",
        # API 会话：模型调用延迟到事件事务提交之后，避免占用 Writer 锁。
        defer_calls=True,
    )
    queue.config_source = source  # 运行态说明用，不写世界状态
    return queue


def attach_if_enabled(talk: Any, root: Path | str, run_dir: Path | str) -> AsyncDialogueQueue | None:
    """`XKX_DIALOGUE_MODEL=1` 时把队列挂到 TalkAction；失败不阻断服务。"""
    if talk is None or not enabled():
        return None
    try:
        queue = build_queue(root, run_dir)
    except Exception:  # noqa: BLE001 - 对话能力失败不得影响动作注册
        return None
    if queue is None:
        return None
    talk.dialogue = queue
    return queue
