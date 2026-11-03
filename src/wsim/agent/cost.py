"""成本遥测（P3 预备）：每次推理记录 token 估算与按档位单价估算的成本。

不联网、不读墙钟；成本只是估算值，用于后续与真实账单对账。
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

DEFAULT_COST = {
    "currency": "USD",
    "per_1k_input_tokens": 0.0015,
    "per_1k_output_tokens": 0.0060,
    "tier_multiplier": {"silent": 0.25, "background": 0.5, "active": 1.0, "spotlight": 2.0},
}


@dataclass(frozen=True)
class CostRates:
    currency: str
    per_1k_input_tokens: float
    per_1k_output_tokens: float
    tier_multiplier: dict[str, float]

    def cost(self, tier: str, input_tokens: int, output_tokens: int) -> float:
        mult = float(self.tier_multiplier.get(tier, 1.0))
        raw = (
            input_tokens / 1000.0 * self.per_1k_input_tokens
            + output_tokens / 1000.0 * self.per_1k_output_tokens
        )
        return round(raw * mult, 10)


def load_cost_rates(root: Path | str) -> CostRates:
    path = Path(root) / "config" / "llm.yaml"
    raw: dict[str, Any] = {}
    if yaml is not None and path.is_file():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cost = (raw.get("cost") or {}) if isinstance(raw, dict) else {}
    merged = dict(DEFAULT_COST)
    merged.update({k: v for k, v in cost.items() if v is not None})
    multipliers = dict(DEFAULT_COST["tier_multiplier"])
    multipliers.update(cost.get("tier_multiplier") or {})
    return CostRates(
        currency=str(merged["currency"]),
        per_1k_input_tokens=float(merged["per_1k_input_tokens"]),
        per_1k_output_tokens=float(merged["per_1k_output_tokens"]),
        tier_multiplier=multipliers,
    )


def estimate_tokens(text: str) -> int:
    """粗略估算：4 个字符 ≈ 1 token；空文本按 1 计。"""
    return max(1, int(math.ceil(len(text) / 4.0)))


@dataclass
class CostTelemetry:
    rates: CostRates
    records: list[dict[str, Any]] = field(default_factory=list)
    schema_rejects: dict[str, int] = field(default_factory=dict)
    fallbacks: int = 0

    def record(
        self,
        *,
        tick: int,
        npc: str,
        tier: str,
        prompt_text: str,
        output_text: str,
        model: str,
        slot: str = "decision",
        calls: int = 1,
        schema_reject: bool = False,
        fallback: bool = False,
        error: str = "",
        persona_tokens: int = 0,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        latency_s: float = 0.0,
        usage_source: str = "reported",
        estimate_missing: bool = True,
    ) -> dict[str, Any]:
        # 优先使用网关返回的实测 usage；缺失时按槽位策略处理：
        # decision/dialogue 允许字符估算（usage_source=estimated）；
        # intent 槽上游可能不返回 usage，按要求记 null，避免把估算当账单。
        tokens_in = (
            int(input_tokens) if input_tokens is not None
            else (estimate_tokens(prompt_text) if estimate_missing else None)
        )
        tokens_out = (
            int(output_tokens) if output_tokens is not None
            else (estimate_tokens(output_text) if estimate_missing else None)
        )
        cost = (
            self.rates.cost(tier, tokens_in, tokens_out)
            if tokens_in is not None and tokens_out is not None
            else None
        )
        rec = {
            "tick": tick,
            "npc": npc,
            "tier": tier,
            "model": model,
            "slot": str(slot),
            "calls": calls,
            "input_tokens": tokens_in,
            "output_tokens": tokens_out,
            "usage_source": str(usage_source),
            "persona_tokens": int(persona_tokens),
            # 原始输出保留（截断）用于审计与适配器回放；密钥不在其中。
            "output_text": str(output_text)[:2000],
            "latency_s": round(float(latency_s), 4),
            "estimated_cost": cost,
            "currency": self.rates.currency,
            "schema_reject": bool(schema_reject),
            "fallback": bool(fallback),
            "error": error,
        }
        self.records.append(rec)
        if fallback:
            self.fallbacks += 1
        return rec

    def reject(self, code: str) -> None:
        self.schema_rejects[code] = self.schema_rejects.get(code, 0) + 1

    def summary(self) -> dict[str, Any]:
        by_tier: dict[str, dict[str, Any]] = {}
        by_slot: dict[str, dict[str, Any]] = {}
        total_cost = 0.0
        total_in = 0
        total_out = 0
        total_persona = 0
        token_missing_calls = 0
        for rec in self.records:
            tier = rec["tier"]
            slot = str(rec.get("slot") or "decision")
            bucket = by_tier.setdefault(
                tier,
                {
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "persona_tokens": 0,
                    "estimated_cost": 0.0,
                    "schema_rejects": 0,
                    "token_missing_calls": 0,
                },
            )
            bucket["calls"] += rec["calls"]
            if rec["input_tokens"] is None or rec["output_tokens"] is None:
                bucket["token_missing_calls"] += rec["calls"]
                token_missing_calls += rec["calls"]
            else:
                bucket["input_tokens"] += rec["input_tokens"]
                bucket["output_tokens"] += rec["output_tokens"]
                total_in += rec["input_tokens"]
                total_out += rec["output_tokens"]
            bucket["persona_tokens"] += rec.get("persona_tokens", 0)
            total_persona += rec.get("persona_tokens", 0)
            if rec["estimated_cost"] is not None:
                bucket["estimated_cost"] = round(bucket["estimated_cost"] + rec["estimated_cost"], 10)
                total_cost += rec["estimated_cost"]
            if rec["schema_reject"]:
                bucket["schema_rejects"] += 1
            sbucket = by_slot.setdefault(
                slot,
                {
                    "calls": 0, "input_tokens": 0, "output_tokens": 0,
                    "estimated_cost": 0.0, "schema_rejects": 0,
                    "per_1k_input_tokens": self.rates.per_1k_input_tokens,
                    "per_1k_output_tokens": self.rates.per_1k_output_tokens,
                    "models": [], "token_missing_calls": 0,
                    "usage_sources": [],
                },
            )
            sbucket["calls"] += rec["calls"]
            if rec["input_tokens"] is None or rec["output_tokens"] is None:
                sbucket["token_missing_calls"] += rec["calls"]
            else:
                sbucket["input_tokens"] += rec["input_tokens"]
                sbucket["output_tokens"] += rec["output_tokens"]
            if rec["estimated_cost"] is not None:
                sbucket["estimated_cost"] = round(sbucket["estimated_cost"] + rec["estimated_cost"], 10)
            if rec["schema_reject"]:
                sbucket["schema_rejects"] += 1
            if rec["model"] not in sbucket["models"]:
                sbucket["models"].append(rec["model"])
            if rec["usage_source"] not in sbucket["usage_sources"]:
                sbucket["usage_sources"].append(rec["usage_source"])
        return {
            "currency": self.rates.currency,
            "calls": sum(r["calls"] for r in self.records),
            "input_tokens": total_in,
            "output_tokens": total_out,
            "token_missing_calls": token_missing_calls,
            "persona_tokens": total_persona,
            "estimated_cost": round(total_cost, 10),
            "by_slot": by_slot,
            "by_tier": by_tier,
            "schema_rejects": dict(sorted(self.schema_rejects.items())),
            "schema_reject_total": sum(self.schema_rejects.values()),
            "fallbacks": self.fallbacks,
            "rates": {
                "per_1k_input_tokens": self.rates.per_1k_input_tokens,
                "per_1k_output_tokens": self.rates.per_1k_output_tokens,
                "tier_multiplier": self.rates.tier_multiplier,
            },
        }

    def to_jsonl(self) -> str:
        return "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in self.records) + (
            "\n" if self.records else ""
        )
