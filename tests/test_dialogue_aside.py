"""对话旁路隔离测试：模型台词不进世界，删旁路不影响世界。"""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

from wsim.agent import dialogue_runtime
from wsim.agent.dialogue_async import AsyncDialogueQueue
from wsim.api.session import WorldSession
from wsim.world.hash import event_hash, state_hash

ROOT = Path(__file__).resolve().parents[1]
MODEL_TEXT = "模型配音今日尚可，街口风大，早些回。"


class _FakeDialogueClient:
    def __init__(self):
        self.config = SimpleNamespace(model="fake/dialogue-model")
        self.last_usage = {}
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        return MODEL_TEXT


def _attach_fake_dialogue(monkeypatch, run_dir: Path) -> AsyncDialogueQueue:
    client = _FakeDialogueClient()
    queue = AsyncDialogueQueue(client, log_dir=run_dir, defer_calls=True)
    monkeypatch.setenv("XKX_DIALOGUE_MODEL", "1")
    monkeypatch.setattr(
        dialogue_runtime, "build_queue", lambda root, run_dir: queue
    )
    return queue


def _run_talk(tmp_path: Path, run_id: str, monkeypatch) -> WorldSession:
    run_dir = tmp_path / "runs" / run_id
    _attach_fake_dialogue(monkeypatch, run_dir)
    session = WorldSession(ROOT, run_id=run_id, seed=42, run_dir=run_dir)
    pid = "player:qa_test"
    enter = session.action(
        "qa_test", "enter", {"account_id": "qa_test", "display_name": "测试甲", "archetype": "游侠"}
    )
    assert enter["ok"], enter
    npc = next(
        e.id
        for e in session.state.entities.values()
        if not e.id.startswith("player:")
        and e.status == "alive"
        and e.location == session.state.entities[pid].location
    )
    message = "你好，今日可好？"
    reply = session.action("qa_test", "talk", {"target": npc, "message": message})
    assert reply["ok"], reply
    session.finalize_dialogue(reply, {"target": npc, "message": message})
    # 配音已改为后台交付：响应先回模板，这里等旁路落盘后再断言。
    seq = int(reply.get("event_seq") or 0)
    deadline = time.time() + 5
    while time.time() < deadline:
        if session.dialogue_aside.get(seq, npc):
            break
        time.sleep(0.02)
    session._test_dialogue = {  # noqa: SLF001 - 仅测试断言用
        "npc": npc,
        "message": message,
        "reply": reply,
    }
    return session


def _hashes(session: WorldSession) -> tuple[str, str]:
    return state_hash(session.state), event_hash(session.store.read_all())


def _template_reply(session: WorldSession, event_seq: int | None = None) -> str:
    for event in session.store.read_all():
        if event_seq is not None and int(event.seq) != int(event_seq):
            continue
        for eff in event.effects or []:
            data = eff.get("data") or {}
            if eff.get("op") == "memory_add" and data.get("source") == "talk:npc":
                return str(data.get("text") or "").removeprefix("我答：")
    return ""


def test_same_seed_worlds_identical_and_model_text_stays_out(tmp_path, monkeypatch):
    first = _run_talk(tmp_path / "a", "aside-a", monkeypatch)
    second = _run_talk(tmp_path / "b", "aside-b", monkeypatch)

    assert _hashes(first) == _hashes(second)

    meta = first._test_dialogue  # noqa: SLF001
    details = meta["reply"]["details"]
    assert details["dialogue_source"] == "template"
    assert details["dialogue_pending"] is True
    assert details["reply"] != MODEL_TEXT

    template = _template_reply(first, int(meta["reply"]["event_seq"]))
    assert template
    assert MODEL_TEXT not in template
    event_blob = json.dumps([e.effects for e in first.store.read_all()], ensure_ascii=False)
    assert MODEL_TEXT not in event_blob

    aside = first.dialogue_aside.get(int(meta["reply"]["event_seq"]), meta["npc"])
    assert aside == MODEL_TEXT
    fetched = first.messages("qa_test", 0, since_seq=int(meta["reply"]["event_seq"]) - 1)
    row = next(r for r in fetched["messages"] if r["seq"] == int(meta["reply"]["event_seq"]))
    assert row["dialogue_source"] == "model" and row["text"] == MODEL_TEXT
    rows = [json.loads(line) for line in first.dialogue_aside.path.read_text(encoding="utf-8").splitlines()]
    assert rows and rows[-1]["model"] == "fake/dialogue-model"


def test_deleting_aside_falls_back_to_template(tmp_path, monkeypatch):
    session = _run_talk(tmp_path, "aside-delete", monkeypatch)
    before = _hashes(session)
    meta = session._test_dialogue  # noqa: SLF001

    session.dialogue_aside.path.unlink()
    assert not session.dialogue_aside.path.exists()

    # 重新打开同一 run：世界照常回放，事件里的模板台词不变。
    reopened = WorldSession(ROOT, run_id="aside-delete", seed=42, run_dir=session.run_dir)
    after = _hashes(reopened)
    assert after == before

    rows = reopened.messages("qa_test", 0)["messages"]
    row = next(r for r in rows if r["seq"] == int(meta["reply"]["event_seq"]))
    assert row["text"] == _template_reply(reopened, int(meta["reply"]["event_seq"]))
    assert row["dialogue_source"] == "template"


class _SlowDialogueClient(_FakeDialogueClient):
    def complete(self, system: str, user: str) -> str:
        time.sleep(1.5)
        return MODEL_TEXT


def test_timeout_falls_back_template_and_action_succeeds(tmp_path, monkeypatch):
    run_dir = tmp_path / "runs" / "aside-timeout"
    queue = AsyncDialogueQueue(_SlowDialogueClient(), log_dir=run_dir, defer_calls=True)
    monkeypatch.setenv("XKX_DIALOGUE_MODEL", "1")
    monkeypatch.setenv("XKX_DIALOGUE_TIMEOUT_S", "0.3")
    monkeypatch.setattr(
        dialogue_runtime, "build_queue", lambda root, run_dir: queue
    )
    session = WorldSession(ROOT, run_id="aside-timeout", seed=42, run_dir=run_dir)
    session.action(
        "qa_test",
        "enter",
        {"account_id": "qa_test", "display_name": "测试甲", "archetype": "游侠"},
    )
    pid = "player:qa_test"
    npc = next(
        e.id
        for e in session.state.entities.values()
        if not e.id.startswith("player:")
        and e.status == "alive"
        and e.location == session.state.entities[pid].location
    )
    args = {"target": npc, "message": "你好，今日可好？"}
    reply = session.action("qa_test", "talk", args)
    assert reply["ok"]
    session.finalize_dialogue(reply, args)
    assert reply["details"]["dialogue_source"] == "template"
    assert reply["details"]["reply"] != MODEL_TEXT
    deadline = time.time() + 4
    while time.time() < deadline and int(queue.stats()["calls"]) < 1:
        time.sleep(0.02)
    while time.time() < deadline and int(queue.stats()["fallback"]) < 1:
        time.sleep(0.02)
    assert not session.dialogue_aside.path.exists()
    stats = queue.stats()
    assert stats["calls"] == 1 and stats["fallback"] == 1
    assert stats["fallback_by_reason"].get("timeout") == 1
    assert stats["timeouts"] == 1
