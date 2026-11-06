"""text_guard 接入 dialogue 真实路径的集成回归。"""
from __future__ import annotations

import json
import re
from pathlib import Path

from wsim.agent.dialogue_async import AsyncDialogueQueue
from wsim.agent.prompt import DIALOGUE_SYSTEM_PROMPT
from wsim.agent.provider import Completion, ProviderClient, ProviderConfig
from wsim.api import WorldSession

ROOT = Path(__file__).resolve().parents[1]
NPC = "duan_wudou"
NPC_NAME = "段五斗"
BANNED = re.compile(r"\*|【|】|[（(][^）)]{1,40}[）)]")


def _cfg() -> ProviderConfig:
    return ProviderConfig(
        base_url="http://127.0.0.1:1/v1", key="k", model="m", offline=False, max_retries=0
    )


def _transport(_cfg, _system, _user):
    return Completion(
        text="**拍了拍油亮围裙** 段五斗咧嘴一笑：“兄弟，肉新鲜，要肥要瘦？”",
        usage={"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
    )


def test_async_queue_sanitizes_before_return_and_persists_state(tmp_path):
    queue = AsyncDialogueQueue(ProviderClient(_cfg(), transport=_transport), log_dir=tmp_path)
    queue.submit(NPC, "在吗？", day=0, tick=10, state_summary={"name": NPC_NAME})
    replies = queue.drain()
    queue.close()

    assert len(replies) == 1
    reply = replies[0]
    assert reply.ok and not reply.fallback
    assert reply.after_guard is True
    assert "markdown_action" in reply.guard_flags
    assert "third_person" in reply.guard_flags
    assert not BANNED.search(reply.text)
    assert not reply.text.startswith(NPC_NAME)
    assert reply.text.startswith("我拍了拍油亮围裙。我咧嘴一笑：")
    state = json.loads((tmp_path / "dialogue_guard_state.json").read_text(encoding="utf-8"))
    assert state["window"] == 10
    assert state["history"][NPC] == [[]]  # 该条没有命中固定短语


def test_async_queue_rewrites_second_slogan_within_window(tmp_path):
    texts = iter(["包在我身上！", "这事包在我身上！"])

    def transport(_cfg, _system, _user):
        return Completion(text=next(texts), usage={"prompt_tokens": 2, "completion_tokens": 2})

    queue = AsyncDialogueQueue(ProviderClient(_cfg(), transport=transport), log_dir=tmp_path)
    queue.submit(NPC, "赊账？", day=0, tick=10, state_summary={"name": NPC_NAME})
    queue.submit(NPC, "何时清账？", day=0, tick=20, state_summary={"name": NPC_NAME})
    replies = queue.drain()
    queue.close()

    assert len(replies) == 2
    assert "包在我身上" in replies[0].text
    assert "包在我身上" not in replies[1].text
    assert replies[1].repeated_phrase == "包在我身上"
    assert replies[1].after_guard is True
    state = json.loads((tmp_path / "dialogue_guard_state.json").read_text(encoding="utf-8"))
    assert state["history"][NPC]


def test_reply_sync_uses_dialogue_prompt_and_guards_output(tmp_path):
    def transport(_cfg, _system, _user):
        return Completion(text="【拍案】兄弟，刀认人，不认话！")

    queue = AsyncDialogueQueue(ProviderClient(_cfg(), transport=transport), log_dir=tmp_path)
    reply = queue.reply_sync(
        npc=NPC,
        player_text="刀准吗？",
        day=0,
        tick=10,
        persona="段五斗，肉铺屠户。",
        state_summary={"name": NPC_NAME},
    )
    queue.close()

    assert reply.ok
    assert reply.after_guard is True
    assert "cjk_bracket" in reply.guard_flags
    assert not BANNED.search(reply.text)
    assert reply.text.startswith("我拍案。")
    log = [
        json.loads(line)
        for line in (tmp_path / "dialogue.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert log[-1]["prompt_text"].startswith(DIALOGUE_SYSTEM_PROMPT)


def test_world_session_talk_path_returns_guarded_text(tmp_path):
    def transport(_cfg, _system, _user):
        return Completion(text="段五斗把砍刀往案板上一按：“兄弟，要肥要瘦？”")

    queue = AsyncDialogueQueue(
        ProviderClient(_cfg(), transport=transport),
        log_dir=tmp_path / "run",
        defer_calls=True,
    )
    session = WorldSession(
        ROOT,
        run_id="guard-integration",
        seed=42,
        npc_limit=12,
        tick_step=6,
        run_dir=tmp_path / "run",
    )
    session.dialogue_queue = queue
    session.registry.get("talk").dialogue = queue
    try:
        enter = session.perform(
            "u_demo", "enter", {"account_id": "u_demo", "display_name": "陈捕快"}
        )
        assert enter["ok"]
        args = {"target": NPC, "message": "肉怎么卖？"}
        res = session.perform("u_demo", "talk", args)
        assert res["ok"]
        # 唯一模型路径：事务提交后调用 finalize_dialogue。
        session.finalize_dialogue(res, args)
        memories = session.state.memories.get(NPC) or []
        answers = [m["text"] for m in memories if m.get("source") == "talk:npc"]
        assert answers
        latest = answers[-1]
        assert not BANNED.search(latest)
        assert not latest.startswith(NPC_NAME)
        # 红线：模型台词不落状态/事件；memory 里必须是确定性模板。
        assert "兄弟，要肥要瘦" not in latest
        assert latest.startswith("我答：")
        log = [
            line for line in (tmp_path / "run" / "dialogue.jsonl").read_text(
                encoding="utf-8"
            ).splitlines() if line.strip()
        ]
        assert "兄弟，要肥要瘦" in log[-1]

        # 模型台词写入唯一旁路，并只覆盖本次 HTTP 响应的 details.reply。
        aside = session.dialogue_aside.get(int(res["event_seq"]), NPC)
        assert aside and "兄弟，要肥要瘦" in aside
        assert res["details"]["dialogue_source"] == "model"
        assert "兄弟，要肥要瘦" in res["details"]["reply"]
        assert latest.startswith("我答：")  # 状态里的模板未变
    finally:
        queue.close()
        session.close()
