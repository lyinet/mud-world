"""异步对话队列：玩家发话先入队，NPC 回应由工作线程写回，不阻塞 tick。"""
from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .budget import BudgetGuard
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
    ):
        self.client = client
        self.budget = budget
        self.fallback_budget = fallback_budget
        self.fallback_error = fallback_error
        self.log_path = Path(log_dir) / "dialogue.jsonl" if log_dir else None
        self._queue: queue.Queue[DialogueRequest | None] = queue.Queue()
        self._replies: list[DialogueReply] = []
        self._lock = threading.Lock()
        self._closed = False
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

    def poll(self) -> list[DialogueReply]:
        with self._lock:
            out = list(self._replies)
            self._replies.clear()
            return out

    def drain(self, timeout_s: float = 5.0) -> list[DialogueReply]:
        self._queue.join()
        return self.poll()

    def stats(self) -> dict[str, Any]:
        return {
            "pending": self._queue.qsize(),
            "closed": self._closed,
            "fallback_budget": self.fallback_budget,
            "fallback_error": self.fallback_error,
        }

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._queue.put(None)
        self._worker.join(timeout=2.0)
