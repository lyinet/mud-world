"""LLM 费用/额度闸门：真实调用前的预算检查与用量记账。

口径（写死，详见 `docs/ADMIN-CONSOLE.md`）：
- 计费窗口 = **自然日（UTC）**，因为费用按真实时间结算；不按世界日。
- 墙钟只用于确定自然日与审计时间，**不进入世界状态、事件序列或 `content_hash`**。
- `daily_budget_usd <= 0` 或 `per_capability_daily_tokens[cap] <= 0` 表示不限。
- 检查/记账失败一律放行（fail-open），不因管理库故障拖垮 tick；失败原因记录在返回值里。
- 记账按每次 provider 配置调用的最终 `usage` 累加，重启后从 `admin_usage` 恢复。
- 同一自然日同一能力位/provider/原因的**首次**拒绝写一条审计，后续计入 `denied` 计数。
"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import audit, settings
from .clock import now_utc

TIMEZONE = "UTC"
# 与 `agent/cost.py::DEFAULT_COST` 保持一致的估算单价（USD / 1K tokens）。
PER_1K_INPUT_USD = 0.0015
PER_1K_OUTPUT_USD = 0.0060


def natural_day(now: float | None = None) -> str:
    """UTC 自然日 `YYYY-MM-DD`；墙钟仅用于计费窗口，不进入仿真。"""
    stamp = float(now_utc() if now is None else now)
    return datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%d")


def estimate_cost(tokens_in: int, tokens_out: int) -> float:
    return round(
        max(0, int(tokens_in)) / 1000.0 * PER_1K_INPUT_USD
        + max(0, int(tokens_out)) / 1000.0 * PER_1K_OUTPUT_USD,
        10,
    )


def _estimate_tokens(chars: int) -> int:
    chars = max(0, int(chars or 0))
    return max(1, math.ceil(chars / 4.0)) if chars else 0


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _limits(snapshot: dict[str, Any] | None) -> tuple[float, dict[str, int]]:
    llm = snapshot.get("llm") if isinstance(snapshot, dict) else {}
    llm = llm if isinstance(llm, dict) else {}
    daily_usd = _number(llm.get("daily_budget_usd"), 0.0)
    raw_tokens = llm.get("per_capability_daily_tokens")
    token_map: dict[str, int] = {}
    if isinstance(raw_tokens, dict):
        for capability, value in raw_tokens.items():
            token_map[str(capability)] = max(0, int(_number(value, 0.0)))
    return daily_usd, token_map


def _totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_cost = 0.0
    total_tokens = 0
    calls = 0
    denied = 0
    by_capability: dict[str, dict[str, Any]] = {}
    by_provider: dict[str, dict[str, Any]] = {}
    for row in rows:
        tokens = int(row.get("tokens_in", 0)) + int(row.get("tokens_out", 0))
        cost = float(row.get("cost_usd", 0.0))
        total_cost += cost
        total_tokens += tokens
        calls += int(row.get("calls", 0))
        denied += int(row.get("denied", 0))
        for bucket, key in (
            (by_capability, str(row.get("capability", ""))),
            (by_provider, str(row.get("provider_id") or "env")),
        ):
            entry = bucket.setdefault(
                key, {"calls": 0, "tokens": 0, "cost_usd": 0.0, "denied": 0}
            )
            entry["calls"] += int(row.get("calls", 0))
            entry["tokens"] += tokens
            entry["cost_usd"] = round(entry["cost_usd"] + cost, 10)
            entry["denied"] += int(row.get("denied", 0))
    return {
        "used_usd": round(total_cost, 10),
        "used_tokens": total_tokens,
        "calls": calls,
        "denied": denied,
        "by_capability": by_capability,
        "by_provider": by_provider,
    }


def _store(db_path: str | Path | None) -> settings.UsageStore:
    path = Path(db_path) if db_path else settings.resolve_db_path()
    return settings.UsageStore(path)


def check(
    capability: str,
    provider_id: str = "",
    *,
    db_path: str | Path | None = None,
    snapshot: dict[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """调用前预算检查；永不抛异常。`allowed=false` 时调用方必须跳过本次网络请求。"""
    day = natural_day(now)
    daily_usd, token_map = _limits(snapshot)
    token_limit = int(token_map.get(str(capability), 0) or 0)
    result: dict[str, Any] = {
        "allowed": True,
        "code": "",
        "day": day,
        "timezone": TIMEZONE,
        "capability": str(capability),
        "provider_id": str(provider_id or "env"),
        "used_usd": 0.0,
        "limit_usd": daily_usd,
        "used_tokens": 0,
        "token_limit": token_limit,
        "check_error": "",
    }
    try:
        totals = _totals(_store(db_path).rows(day))
    except Exception as exc:  # noqa: BLE001 - 管理库故障不得拖垮主服务
        result["check_error"] = type(exc).__name__
        return result
    cap_entry = totals["by_capability"].get(str(capability), {})
    result["used_usd"] = float(totals["used_usd"])
    result["used_tokens"] = int(cap_entry.get("tokens", 0) or 0)
    if daily_usd > 0 and float(totals["used_usd"]) >= daily_usd:
        result["allowed"] = False
        result["code"] = "budget_exhausted"
    elif token_limit > 0 and int(cap_entry.get("tokens", 0) or 0) >= token_limit:
        result["allowed"] = False
        result["code"] = "capability_token_limit"
    return result


def record_call(
    capability: str,
    provider_id: str = "",
    *,
    usage: dict[str, Any] | None = None,
    prompt_chars: int = 0,
    output_chars: int = 0,
    db_path: str | Path | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """真实调用结束后累计用量；失败静默（返回 `recorded=false`）。"""
    day = natural_day(now)
    usage = usage if isinstance(usage, dict) else {}
    tokens_in = _int(usage.get("prompt_tokens"))
    if tokens_in is None:
        tokens_in = _int(usage.get("input_tokens"))
    if tokens_in is None:
        tokens_in = _estimate_tokens(prompt_chars)
    tokens_out = _int(usage.get("completion_tokens"))
    if tokens_out is None:
        tokens_out = _int(usage.get("output_tokens"))
    if tokens_out is None:
        tokens_out = _estimate_tokens(output_chars)
    result = {
        "recorded": False,
        "day": day,
        "tokens_in": int(tokens_in or 0),
        "tokens_out": int(tokens_out or 0),
        "cost_usd": estimate_cost(tokens_in or 0, tokens_out or 0),
    }
    try:
        _store(db_path).add(
            day,
            str(capability),
            str(provider_id or "env"),
            tokens_in=result["tokens_in"],
            tokens_out=result["tokens_out"],
            cost_usd=result["cost_usd"],
            calls=1,
            now=now,
        )
        result["recorded"] = True
    except Exception:  # noqa: BLE001 - 记账失败不得影响主服务
        return result
    return result


def _mark_first_denial(
    path: Path, day: str, capability: str, provider_id: str, reason: str, stamp: float
) -> bool:
    """同一 (日, 能力位, provider, 原因) 只返回一次 True；标记落 `admin_runtime`。"""
    key = f"budget_denied:{day}:{capability}:{provider_id}:{reason}"
    conn = sqlite3.connect(str(path), timeout=5.0)
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            "INSERT OR IGNORE INTO admin_runtime(key, value_json, updated_at) VALUES (?, ?, ?)",
            (
                key,
                json.dumps(
                    {"day": day, "capability": capability, "provider_id": provider_id,
                     "reason": reason, "at": stamp},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                float(stamp),
            ),
        )
        first = cursor.rowcount > 0
        conn.execute("COMMIT")
        return first
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


def record_denial(
    decision: dict[str, Any],
    *,
    slot: str = "",
    db_path: str | Path | None = None,
    now: float | None = None,
) -> None:
    """累计拒绝次数；同一 (日, 能力位, provider, 原因) 的首次拒绝写一条审计。"""
    capability = str(decision.get("capability") or "")
    provider_id = str(decision.get("provider_id") or "env")
    day = str(decision.get("day") or natural_day(now))
    reason = str(decision.get("code") or "")
    if not capability:
        return
    path = Path(db_path) if db_path else settings.resolve_db_path()
    stamp = float(now_utc() if now is None else now)
    try:
        _store(db_path).add_denial(day, capability, provider_id, now=now)
    except Exception:  # noqa: BLE001
        return
    if not path.exists():
        return
    try:
        if not _mark_first_denial(path, day, capability, provider_id, reason, stamp):
            return
    except Exception:  # noqa: BLE001
        return
    try:
        detail = {
            "day": day,
            "timezone": TIMEZONE,
            "capability": capability,
            "slot": str(slot or ""),
            "provider_id": provider_id,
            "reason": reason,
            "used_usd": round(float(decision.get("used_usd") or 0.0), 10),
            "limit_usd": float(decision.get("limit_usd") or 0.0),
            "used_tokens": int(decision.get("used_tokens") or 0),
            "token_limit": int(decision.get("token_limit") or 0),
        }
        conn = sqlite3.connect(str(path), timeout=5.0)
        try:
            conn.execute("BEGIN IMMEDIATE")
            audit.append(
                conn,
                who="system:budget",
                action="llm.budget_denied",
                key=capability,
                detail=detail,
                at=stamp,
            )
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - 审计失败不得影响主服务
        return


def summary(
    *,
    db_path: str | Path | None = None,
    snapshot: dict[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """当日“今日已用 / 上限 / 剩余”；供 `GET /api/admin/budget` 与 capabilities 使用。"""
    day = natural_day(now)
    daily_usd, token_map = _limits(snapshot)
    try:
        rows = _store(db_path).rows(day)
        error = ""
    except Exception as exc:  # noqa: BLE001
        rows = []
        error = type(exc).__name__
    totals = _totals(rows)
    capabilities = sorted(set(token_map) | set(totals["by_capability"]))
    tokens: dict[str, dict[str, Any]] = {}
    for capability in capabilities:
        used = int(totals["by_capability"].get(capability, {}).get("tokens", 0) or 0)
        limit = int(token_map.get(capability, 0) or 0)
        tokens[capability] = {
            "used": used,
            "limit": limit,
            "remaining": (max(0, limit - used) if limit > 0 else None),
        }
    remaining_usd = max(0.0, round(daily_usd - totals["used_usd"], 10)) if daily_usd > 0 else None
    return {
        "ok": True,
        "day": day,
        "timezone": TIMEZONE,
        "daily_budget_usd": daily_usd,
        "used_usd": totals["used_usd"],
        "remaining_usd": remaining_usd,
        "used_tokens": totals["used_tokens"],
        "tokens": tokens,
        "calls": totals["calls"],
        "denied": totals["denied"],
        "by_provider": totals["by_provider"],
        "rates": {
            "currency": "USD",
            "per_1k_input_tokens": PER_1K_INPUT_USD,
            "per_1k_output_tokens": PER_1K_OUTPUT_USD,
            "note": "按上游返回 usage 估算；缺失时按 4 字符≈1 token 估算",
        },
        "enabled": {
            "daily_budget": daily_usd > 0,
            "per_capability_tokens": bool(
                [cap for cap, limit in token_map.items() if limit > 0]
            ),
        },
        "error": error,
    }
