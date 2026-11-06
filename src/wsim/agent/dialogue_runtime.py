"""对话模型运行时：双路配置 + 非持久化台词缓存。

红线：
- 模型台词只进入 `dialogue_aside.jsonl`、`dialogue.jsonl` 与本次 HTTP 响应的
  `details.reply`；
- 绝不写入 `memory_add` / 事件库 / 世界状态 / `state_hash` / `event_hash`；
- 回放与离线路径仍走 `agent/dialogue.py::compose_talk` 的确定性模板。

配置来源：
1. 管理台 provider 注册表（`wsim.admin.providers.resolve("dialogue")`，DB 优先）；
2. 环境变量 / `config/llm.yaml`（`ProviderConfig.from_env`）。
两条路都走同一个 `ProviderClient`，因此能力位回写、预算与降级链一致。

开关：`WSIM_DIALOGUE=1`（默认关）。关闭时不构造任何 provider，
也不会影响 `llm_enabled` / decision / intent / strategy 槽。
"""
from __future__ import annotations

import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

from ..admin.providers import resolve as resolve_admin_provider
from .budget import BudgetGuard, BudgetLimits
from .cost import CostTelemetry, load_cost_rates
from .dialogue_async import AsyncDialogueQueue
from .provider import ProviderClient, ProviderConfig, ProviderConfigError
from .provider import load_model_slots, load_provider_settings

_ENABLED_VALUES = ("1", "true", "yes", "on")
_CACHE_LIMIT = 256
# key -> (模型文本, 模型名)
_CACHE: "OrderedDict[tuple[str, str, int, str], tuple[str, str]]" = OrderedDict()
_CACHE_LOCK = threading.Lock()


def enabled() -> bool:
    return os.environ.get("WSIM_DIALOGUE", "").strip().lower() in _ENABLED_VALUES


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
            max_retries=int(s.get("max_retries", 2)),
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
    return ProviderClient(cfg)


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
    """按“管理台优先、env 兜底”的顺序构造对话队列；无可用配置返回 None。"""
    root_path = Path(root)
    client: ProviderClient | None = None
    source = ""
    try:
        cfg = resolve_admin_provider("dialogue")
    except Exception:  # noqa: BLE001 - 管理库缺失/主密钥缺失都必须回退 env
        cfg = None
    if cfg is not None:
        client = ProviderClient(cfg)
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
    )
    queue.config_source = source  # 运行态说明用，不写世界状态
    return queue


def attach_if_enabled(talk: Any, root: Path | str, run_dir: Path | str) -> AsyncDialogueQueue | None:
    """`WSIM_DIALOGUE=1` 时把队列挂到 TalkAction；失败不阻断服务。"""
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


def remember(
    player_id: str, npc_id: str, tick: int, message: str, text: str, model: str = ""
) -> None:
    """把模型台词放进进程内缓存，供事务提交后的旁路写入；不进事件。"""
    key = (str(player_id), str(npc_id), int(tick), str(message))
    with _CACHE_LOCK:
        _CACHE[key] = (str(text), str(model))
        _CACHE.move_to_end(key)
        while len(_CACHE) > _CACHE_LIMIT:
            _CACHE.popitem(last=False)


def overlay_reply(
    result: dict[str, Any],
    args: dict[str, Any],
    aside: Any = None,
    model: str = "",
) -> None:
    """事务提交后调用：把本次模型的台词写入旁路，并只覆盖 HTTP 的 `details.reply`。"""
    if not isinstance(result, dict) or str(result.get("verb") or "") != "talk":
        return
    details = result.get("details")
    if not isinstance(details, dict):
        details = {}
        result["details"] = details
    details.setdefault("dialogue_source", "template")
    if not result.get("ok"):
        return
    target = str(args.get("target") or "")
    message = str(args.get("message") or "").strip()
    tick = int(result.get("tick") or 0)
    player = str(result.get("player") or "")
    key = (player, target, tick, message)
    with _CACHE_LOCK:
        item = _CACHE.pop(key, None)
    if not item:
        return
    text, cached_model = item
    if not text:
        return
    if aside is not None:
        try:
            aside.put(int(result.get("event_seq") or 0), target, text, cached_model or model)
        except Exception:  # noqa: BLE001 - 旁路写失败不能影响玩家操作
            pass
    details["reply"] = text
    details["dialogue_source"] = "model"
    details["dialogue_model"] = cached_model or model
