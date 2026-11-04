"""A4 战略层客户端：低频 prompt 调用、schema 校验、录制/回放与成本记账。

- 真实模式复用 ProviderClient（strategy 槽），每次只返回一份 StrategyPlan JSON；
- fake 模式用确定性 transport，不走网络，用于阶段 0 接线验证；
- 回放模式不重复计费、不触发预算硬闸；录制行按 prompt_sha 对齐。
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from ..core.clock import TICKS_PER_DAY
from .budget import BudgetGuard
from .cost import CostTelemetry, estimate_tokens
from .prompt import build_strategy_prompt
from .provider import Completion, ProviderClient, prompt_sha
from ..sim.strategy import StrategyPlan, parse_strategy_plan, validate_plan

STRATEGY_DEFAULT_TOKENS = 512


def _extract_json(text: str) -> dict[str, Any]:
    """从模型输出里取第一个平衡的 JSON 对象；兼容 ```json 围栏与前后废话。"""
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        if raw.endswith("```"):
            raw = raw[:-3]
    start = raw.find("{")
    if start < 0:
        raise ValueError("no_json_object")
    depth = 0
    for idx in range(start, len(raw)):
        ch = raw[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                payload = json.loads(raw[start : idx + 1])
                if not isinstance(payload, dict):
                    raise ValueError("json_not_object")
                return payload
    raise ValueError("unbalanced_json")


def _usage_tokens(usage: dict[str, Any] | None, prompt_text: str, output_text: str) -> tuple[int, int]:
    usage = usage or {}
    tokens_in = usage.get("prompt_tokens")
    tokens_out = usage.get("completion_tokens")
    if tokens_in is None:
        tokens_in = estimate_tokens(prompt_text)
    if tokens_out is None:
        tokens_out = estimate_tokens(output_text)
    return int(tokens_in), int(tokens_out)


def make_fake_strategy_transport():
    """确定性 fake 端点：按 prompt 里的 goal_candidates 生成合法计划。"""
    def _fake(cfg, system: str, user: str) -> Completion:
        marker = "当前状态：\n"
        summary: dict[str, Any] = {}
        if marker in user:
            try:
                summary = json.loads(user.split(marker, 1)[1])
            except (TypeError, ValueError):
                summary = {}
        npc = str(summary.get("npc") or "")
        candidates = [c for c in (summary.get("goal_candidates") or []) if isinstance(c, dict)]
        active = summary.get("active_goal") if isinstance(summary.get("active_goal"), dict) else {}
        active_id = str(active.get("id") or "")
        active_progress = float(active.get("progress") or 0.0)
        ids = [str(c.get("goal_id") or "") for c in candidates if str(c.get("goal_id") or "")]
        primary = active_id if active_id in ids else (ids[0] if ids else None)
        secondary = [gid for gid in ids if gid != primary][:2]
        preserve = [active_id] if active_id in ids and active_progress >= 0.1 else []
        core = [str(c.get("type") or "") for c in candidates if str(c.get("type") or "") in
                ("build_shop", "earn_money", "advance_skill")]
        quota = [core[0]] if core else []
        primary_type = next((str(c.get("type") or "") for c in candidates if c.get("goal_id") == primary), "")
        rationale = f"这三天先把{primary_type or '手头的事'}做扎实，再顾旁的，钱先不亏。"
        payload = {
            "plan_id": f"plan:{npc}:{int(summary.get('tick') or 0)}",
            "primary_goal_id": primary,
            "secondary_goal_ids": secondary,
            "relation_targets": [],
            "budget": {"cash_floor": 40, "max_discretionary_spend": 200, "max_goal_actions_per_day": 2},
            "preserve_goal_ids": preserve,
            "type_quota": quota,
            "confidence": 0.55,
            "rationale": rationale[:80],
        }
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return Completion(
            text=text,
            usage={
                "prompt_tokens": estimate_tokens(system + "\n" + user),
                "completion_tokens": estimate_tokens(text),
            },
        )

    return _fake


class StrategyClient:
    """按 NPC/日界批量调用战略层，返回合法计划并记账。"""

    def __init__(
        self,
        provider: ProviderClient,
        content,
        telemetry: CostTelemetry | None = None,
        *,
        budget: BudgetGuard | None = None,
        concurrency: int = 4,
        record_path: Path | str | None = None,
        replay_mode: bool = False,
        fake: bool = False,
        max_tokens: int = STRATEGY_DEFAULT_TOKENS,
    ):
        self.provider = provider
        self.content = content
        self.telemetry = telemetry
        self.budget = budget
        self.concurrency = max(1, int(concurrency))
        self.record_path = Path(record_path) if record_path else None
        self.replay_mode = bool(replay_mode)
        self.fake = bool(fake)
        self.max_tokens = int(max_tokens)
        self.calls = 0
        self.ok_calls = 0
        self.fallbacks = 0
        self.schema_rejects = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost = 0.0
        self.latencies: list[float] = []
        self.errors: dict[str, int] = {}

    def plan_batch(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """items: [{npc, tick, day, state, recent_events?}]；按输入顺序返回。"""
        prepared: list[dict[str, Any]] = []
        for item in items:
            npc = str(item["npc"])
            tick = int(item.get("tick", 0))
            day = int(item.get("day", tick // TICKS_PER_DAY))
            state = item["state"]
            prompt = build_strategy_prompt(
                self.content, state, npc, tick,
                int(item.get("horizon_days", 3)),
                item.get("recent_events") or (),
            )
            row = {"item": item, "npc": npc, "tick": tick, "day": day, "prompt": prompt}
            prepared.append(row)

        pending = []
        for row in prepared:
            if row["prompt"] is None:
                continue
            if self.budget is not None and not self.replay_mode:
                tier, _reason = self.budget.check(row["day"], "active", row["prompt"].text(), self.max_tokens)
                if tier is None:
                    row["budget_blocked"] = True
                    continue
            pending.append(row)
        if pending:
            results = self.provider.complete_many(
                [(row["prompt"].system, row["prompt"].user) for row in pending],
                concurrency=self.concurrency,
            )
            for row, result in zip(pending, results):
                row["result"] = result

        out: list[dict[str, Any]] = []
        for row in prepared:
            out.append(self._finish(row))
        return out

    def _finish(self, row: dict[str, Any]) -> dict[str, Any]:
        item = row["item"]
        npc = row["npc"]
        tick = row["tick"]
        day = row["day"]
        prompt = row["prompt"]
        state = item["state"]
        horizon_days = int(item.get("horizon_days", 3))
        result = row.get("result") or {}
        error = result.get("error")
        text = str(result.get("text") or "")
        usage = dict(result.get("usage") or {})
        latency = float(result.get("latency_s") or 0.0)
        if latency > 0:
            self.latencies.append(latency)

        if row.get("budget_blocked"):
            self.fallbacks += 1
            self.errors["budget"] = self.errors.get("budget", 0) + 1
            return self._record_result(row, None, ["budget_exceeded"], 0, 0, 0.0, "", error="budget_exceeded")

        if error is not None:
            self.fallbacks += 1
            kind = str(getattr(error, "kind", "") or result.get("error_kind") or "provider_error")
            self.errors[kind] = self.errors.get(kind, 0) + 1
            return self._record_result(row, None, [f"provider_error:{kind}"], 0, 0, 0.0, "", error=kind)

        self.calls += 1
        tokens_in, tokens_out = _usage_tokens(usage, prompt.text(), text)
        if self.replay_mode:
            cost = 0.0
        elif self.telemetry is not None:
            cost = self.telemetry.rates.cost("active", tokens_in, tokens_out)
        else:
            cost = 0.0
        self.input_tokens += tokens_in
        self.output_tokens += tokens_out
        self.cost += float(cost or 0.0)

        try:
            payload = _extract_json(text)
            plan: StrategyPlan = parse_strategy_plan(
                payload, npc=npc, tick=tick, horizon_days=horizon_days, content=self.content
            )
        except ValueError as exc:
            self.schema_rejects += 1
            self.errors[str(exc)] = self.errors.get(str(exc), 0) + 1
            return self._record_result(row, None, [f"schema:{exc}"], tokens_in, tokens_out, cost, text)

        valid, reasons = validate_plan(plan, self.content, state)
        if valid:
            self.ok_calls += 1
        else:
            self.schema_rejects += 1
            for reason in reasons:
                self.errors[reason.split(":", 1)[0]] = self.errors.get(reason.split(":", 1)[0], 0) + 1
        return self._record_result(row, plan, reasons, tokens_in, tokens_out, cost, text, valid=valid)

    def _record_result(
        self,
        row: dict[str, Any],
        plan: StrategyPlan | None,
        reasons: list[str],
        tokens_in: int,
        tokens_out: int,
        cost: float | None,
        text: str,
        *,
        valid: bool = False,
        error: str = "",
    ) -> dict[str, Any]:
        item = row["item"]
        prompt = row["prompt"]
        npc = row["npc"]
        tick = row["tick"]
        day = row["day"]
        if self.record_path is not None and not self.replay_mode:
            self.record_path.parent.mkdir(parents=True, exist_ok=True)
            rec = {
                "prompt_sha": prompt_sha(prompt.system, prompt.user),
                "npc": npc,
                "tick": tick,
                "output": text,
                "usage": {"prompt_tokens": tokens_in, "completion_tokens": tokens_out},
                "status": "ok" if valid else ("error" if error else "invalid"),
            }
            with self.record_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        if self.telemetry is not None and not self.replay_mode and not error:
            self.telemetry.record(
                tick=tick,
                npc=npc,
                tier="active",
                slot="strategy",
                prompt_text=prompt.text(),
                output_text=text,
                model=("fake-strategy" if self.fake else self.provider.config.model),
                calls=1,
                schema_reject=not valid and not error,
                fallback=bool(error),
                error=error,
                input_tokens=tokens_in,
                output_tokens=tokens_out,
                latency_s=float((row.get("result") or {}).get("latency_s") or 0.0),
                thinking_disabled_by=str((row.get("result") or {}).get("thinking_disabled_by") or ""),
                thinking_param_ignored=bool((row.get("result") or {}).get("thinking_param_ignored")),
                usage_source="reported" if (row.get("result") or {}).get("usage") else "estimated",
            )
        if self.budget is not None and not self.replay_mode and not error:
            self.budget.record(day, tokens_in, tokens_out, float(cost or 0.0))
        return {
            "npc": npc,
            "tick": tick,
            "day": day,
            "plan": plan,
            "valid": bool(valid),
            "reasons": list(reasons),
            "error": error,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost": float(cost or 0.0),
            "output": text,
        }

    def summary(self) -> dict[str, Any]:
        n = len(self.latencies)
        p95 = 0.0
        if n:
            values = sorted(self.latencies)
            p95 = round(values[min(n - 1, int(round((n - 1) * 0.95)))], 3)
        return {
            "slot": "strategy",
            "mode": "fake" if self.fake else ("replay" if self.replay_mode else "real"),
            "calls": self.calls,
            "ok_calls": self.ok_calls,
            "schema_rejects": self.schema_rejects,
            "fallbacks": self.fallbacks,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost": round(self.cost, 6),
            "latency_p95_s": p95,
            "errors": dict(sorted(self.errors.items())),
            "record_path": str(self.record_path) if self.record_path else "",
        }
