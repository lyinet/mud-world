"""异步对话队列：玩家发话先入队，NPC 回应由工作线程写回，不阻塞 tick。"""
from __future__ import annotations

import hashlib
import json
import queue
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .budget import BudgetGuard
from .cost import CostTelemetry
from .provider import ProviderClient

FALLBACK_BUDGET = "今日对话额度用尽，请明天再来。"
FALLBACK_ERROR = "（对方似乎没听清，稍后再试。）"


@dataclass
class DialogueRequest:
    npc: str
    player_text: str
    day: int
    tick: int
    persona: str = ""
    state_summary: dict[str, Any] = field(default_factory=dict)
    request_id: str = ""


@dataclass
class DialogueReply:
    request_id: str
    npc: str
    text: str
    ok: bool
    fallback: bool
    latency_s: float


class AsyncDialogueQueue:
    """单工作线程 + 队列；submit 立即返回，tick 循环不需要等模型。"""

    def __init__(
        self,
        client: ProviderClient,
        budget: BudgetGuard | None = None,
        log_dir: Path | str | None = None,
        fallback_budget: str = FALLBACK_BUDGET,
        fallback_error: str = FALLBACK_ERROR,
        telemetry: CostTelemetry | None = None,
        tier: str = "active",
    ):
        self.client = client
        self.budget = budget
        self.fallback_budget = fallback_budget
        self.fallback_error = fallback_error
        self.telemetry = telemetry
        self.tier = tier
        self.log_path = Path(log_dir) / "dialogue.jsonl" if log_dir else None
        self._queue: queue.Queue[DialogueRequest | None] = queue.Queue()
        self._replies: list[DialogueReply] = []
        self._lock = threading.Lock()
        # reply_sync 由多个动作线程调用，单飞保护避免同一 NPC 并发打爆网关。
        self._sync_lock = threading.Lock()
        self._closed = False
        self._sync_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dialogue-sync")
        self._sync_stats: dict[str, Any] = {
            "calls": 0, "ok": 0, "fallback": 0,
            "fallback_by_reason": {},
            "latencies": [],
            "input_tokens": 0, "output_tokens": 0, "estimated_cost": 0.0,
        }
        self._worker = threading.Thread(target=self._run, name="dialogue-worker", daemon=True)
        self._worker.start()
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def submit(
        self,
        npc: str,
        player_text: str,
        *,
        day: int,
        tick: int,
        persona: str = "",
        state_summary: dict[str, Any] | None = None,
    ) -> str:
        request_id = f"dlg-{uuid.uuid4().hex[:10]}"
        self._queue.put(
            DialogueRequest(
                npc=npc,
                player_text=str(player_text),
                day=int(day),
                tick=int(tick),
                persona=persona,
                state_summary=dict(state_summary or {}),
                request_id=request_id,
            )
        )
        return request_id

    def _run(self) -> None:
        while True:
            req = self._queue.get()
            if req is None:
                self._queue.task_done()
                return
            try:
                reply = self._answer(req)
                with self._lock:
                    self._replies.append(reply)
                if self.log_path is not None:
                    with self.log_path.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(reply.__dict__, ensure_ascii=False, sort_keys=True) + "\n")
            finally:
                self._queue.task_done()

    def _answer(self, req: DialogueRequest) -> DialogueReply:
        if self.budget is not None:
            ok, _reason = self.budget.check_dialogue(req.day)
            if not ok:
                return DialogueReply(req.request_id, req.npc, self.fallback_budget, True, True, 0.0)
            self.budget.record_dialogue(req.day)
        system = (
            "你在扮演多 NPC 世界中的一个角色，只用第一人称简短回应玩家。"
            + (f"\n角色设定：{req.persona}" if req.persona else "")
        )
        user = json.dumps(
            {"player": req.player_text, "state": req.state_summary},
            ensure_ascii=False,
            sort_keys=True,
        )
        started = time.perf_counter()
        try:
            text = self.client.complete(system, user)
            return DialogueReply(
                req.request_id, req.npc, str(text)[:800], True, False,
                round(time.perf_counter() - started, 4),
            )
        except Exception:
            return DialogueReply(
                req.request_id, req.npc, self.fallback_error, False, True,
                round(time.perf_counter() - started, 4),
            )

    def reply_sync(
        self,
        *,
        npc: str,
        player_text: str,
        day: int,
        tick: int,
        persona: str = "",
        state_summary: dict[str, Any] | None = None,
        timeout_s: float = 8.0,
    ) -> DialogueReply:
        """同步返回一句 NPC 台词；超时/预算/错误时 fallback=True，由调用方保留模板回复。"""
        request_id = f"dlg-{uuid.uuid4().hex[:10]}"
        summary = dict(state_summary or {})
        with self._lock:
            self._sync_stats["calls"] += 1
        if self.budget is not None:
            ok, reason = self.budget.check_dialogue(int(day))
            if not ok:
                return self._finish_sync(
                    request_id, npc, "", False, True, 0.0, tick, "", "", 0, 0, reason, day
                )
            self.budget.record_dialogue(int(day))
        system = (
            "你在扮演一个武侠世界中的 NPC，只能用第一人称回答，保持人设，"
            "不要解释、不要复述问题、不要出戏；最多 80 个汉字，可带一句动作描写。"
        )
        user = json.dumps(
            {
                "npc": npc,
                "persona": persona,
                "state": summary,
                "message": str(player_text),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        started = time.perf_counter()
        text = ""
        fallback = False
        reason = ""
        usage: dict[str, Any] = {}
        with self._sync_lock:
            try:
                def _call() -> tuple[str, dict[str, Any]]:
                    out = self.client.complete(system, user)
                    return out, dict(self.client.last_usage or {})

                future: Future = self._sync_executor.submit(_call)
                text, usage = future.result(timeout=timeout_s)
                text = str(text).strip()
            except FutureTimeout:
                fallback, reason = True, "timeout"
            except Exception as exc:  # noqa: BLE001 - 对话失败必须降级，不影响动作落地
                fallback, reason = True, f"error:{type(exc).__name__}"
        latency = round(time.perf_counter() - started, 4)
        in_tok = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0) or None
        out_tok = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0) or None
        return self._finish_sync(
            request_id, npc, text, not fallback and bool(text), fallback,
            latency, tick, system, user, in_tok, out_tok, reason, day
        )

    def _finish_sync(
        self,
        request_id: str,
        npc: str,
        text: str,
        ok: bool,
        fallback: bool,
        latency_s: float,
        tick: int,
        system: str,
        user: str,
        input_tokens: int | None,
        output_tokens: int | None,
        reason: str,
        day: int,
    ) -> DialogueReply:
        if not ok:
            text = self.fallback_budget if reason == "dialogue_budget_exceeded" else self.fallback_error
        rec = None
        if self.telemetry is not None and reason != "dialogue_budget_exceeded":
            rec = self.telemetry.record(
                tick=int(tick), npc=npc, tier=self.tier,
                prompt_text=system + "\n" + user, output_text=text,
                model=self.client.config.model, slot="dialogue", calls=1,
                fallback=fallback, error=reason,
                input_tokens=input_tokens, output_tokens=output_tokens,
                latency_s=latency_s,
            )
        if self.budget is not None and rec is not None:
            self.budget.record(
                int(day),
                int(rec.get("input_tokens") or 0),
                int(rec.get("output_tokens") or 0),
                float(rec.get("estimated_cost") or 0.0),
            )
        reply = DialogueReply(request_id, npc, str(text)[:800], ok, fallback, latency_s)
        with self._lock:
            self._sync_stats["ok" if ok else "fallback"] += 1
            self._sync_stats["latencies"].append(float(latency_s))
            if not ok:
                by = self._sync_stats["fallback_by_reason"]
                by[reason or "unknown"] = by.get(reason or "unknown", 0) + 1
            if rec is not None:
                self._sync_stats["input_tokens"] += int(rec.get("input_tokens") or 0)
                self._sync_stats["output_tokens"] += int(rec.get("output_tokens") or 0)
                self._sync_stats["estimated_cost"] += float(rec.get("estimated_cost") or 0.0)
        self._log({
            "request_id": request_id, "npc": npc, "tick": int(tick), "day": int(day),
            "text": reply.text, "ok": ok, "fallback": fallback,
            "fallback_reason": reason, "latency_s": latency_s,
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "prompt_sha256": hashlib.sha256((system + "\n" + user).encode("utf-8")).hexdigest(),
            "prompt_text": (system + "\n" + user)[:2000],
        })
        return reply

    def _log(self, payload: dict[str, Any]) -> None:
        if self.log_path is None:
            return
        with self._lock:
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    def poll(self) -> list[DialogueReply]:
        with self._lock:
            out = list(self._replies)
            self._replies.clear()
            return out

    def drain(self, timeout_s: float = 5.0) -> list[DialogueReply]:
        self._queue.join()
        return self.poll()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            latencies = sorted(self._sync_stats["latencies"])

            def quantile(q: float) -> float:
                if not latencies:
                    return 0.0
                idx = min(len(latencies) - 1, max(0, int(round(q * (len(latencies) - 1)))))
                return round(latencies[idx], 3)

            return {
                "pending": self._queue.qsize(),
                "closed": self._closed,
                "fallback_budget": self.fallback_budget,
                "fallback_error": self.fallback_error,
                "calls": int(self._sync_stats["calls"]),
                "ok": int(self._sync_stats["ok"]),
                "fallback": int(self._sync_stats["fallback"]),
                "fallback_by_reason": dict(self._sync_stats["fallback_by_reason"]),
                "p50_latency_s": quantile(0.50),
                "p95_latency_s": quantile(0.95),
                "input_tokens": int(self._sync_stats["input_tokens"]),
                "output_tokens": int(self._sync_stats["output_tokens"]),
                "estimated_cost": round(float(self._sync_stats["estimated_cost"]), 6),
            }

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._queue.put(None)
        self._worker.join(timeout=2.0)
        self._sync_executor.shutdown(wait=False, cancel_futures=True)
