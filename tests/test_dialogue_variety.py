"""对话多样性回归：同 NPC 逐轮不同、不同 NPC 同题不同、人设台词在场。"""
from __future__ import annotations

from pathlib import Path

from wsim.agent.dialogue import compose_talk, place_names
from wsim.agent.dialogue_async import AsyncDialogueQueue
from wsim.agent.dialogue_runtime import build_queue, enabled
from wsim.agent.provider import Completion, ProviderClient, ProviderConfig
from wsim.api import WorldSession

ROOT = Path(__file__).resolve().parents[1]
NPC_IDS = ("duan_wudou", "jin_mancang", "liu_sanniang", "meng_jiuniang", "ruan_qingfu")
MSGS = ("你好，今日可好？", "幸会，近来可好？", "听说镇上最近有新鲜事？")


class _Content:
    locations = {}


def _persona(npc: str) -> str:
    return (ROOT / "content" / "personas" / f"{npc}.md").read_text(encoding="utf-8")


def test_three_turns_and_five_npcs_are_distinct():
    rows = []
    for npc in NPC_IDS:
        persona = _persona(npc)
        for turn, msg in enumerate(MSGS):
            out = compose_talk(
                persona, npc, npc, "", msg, 1000 + turn,
                turn=turn, places=place_names(_Content),
            )
            rows.append((npc, turn, out["reply"], out["quote"]))
    replies = [r[2] for r in rows]
    assert len(set(replies)) >= 12, f"unique={len(set(replies))}/15"

    for npc in NPC_IDS:
        chain = [r[2] for r in rows if r[0] == npc]
        assert all(a != b for a, b in zip(chain, chain[1:])), (npc, chain)
    for turn in (0, 1, 2):
        group = [r[2] for r in rows if r[1] == turn]
        assert all(a != b for a, b in zip(group, group[1:])), (turn, group)
    for npc, _turn, reply, quote in rows:
        bare = quote.strip("“”").strip()
        assert bare and bare in reply, (npc, quote, reply)


def test_repeated_talks_change_reply_without_new_state_field(tmp_path):
    from wsim.api.session import WorldSession

    session = WorldSession(
        ROOT, run_id="dialogue-variety", seed=42, npc_limit=12,
        tick_step=6, run_dir=tmp_path / "run",
    )
    try:
        enter = session.perform(
            "u_var", "enter", {"account_id": "u_var", "display_name": "问话人"}
        )
        assert enter["ok"]
        replies = []
        for i in range(3):
            res = session.perform(
                "u_var", "talk",
                {"target": "duan_wudou", "message": "你好，今日可好？",
                 "request_id": f"var-{i}"},
            )
            assert res["ok"], res
            replies.append(res["details"]["reply"])
        assert len(set(replies)) == 3, replies
        answers = [
            m for m in session.state.memories["duan_wudou"]
            if m.get("source") == "talk:npc"
        ]
        assert len(answers) == 3
        # 轮次由 memory 推导，不新增 talk_ledger 字段（保证 state_hash 不漂移）。
        assert "turns" not in session.state.talk_ledger["duan_wudou"]
    finally:
        session.close()


def test_dialogue_runtime_supports_admin_and_env_sources(tmp_path, monkeypatch):
    cfg = ProviderConfig(
        base_url="http://127.0.0.1:1/v1", key="k", model="m",
        timeout_s=10, max_retries=1, offline=False, backoff_base_s=0.1,
        max_tokens=512, temperature=0.8, json_mode=False, slot="dialogue",
    )
    import wsim.agent.dialogue_runtime as runtime

    monkeypatch.setattr(runtime, "resolve_admin_provider", lambda _slot: cfg)
    q = build_queue(tmp_path, tmp_path / "run-admin")
    assert q is not None and q.config_source == "admin_registry"
    assert q.client.config.max_retries == 0, "对话槽不得在 provider 层重试"
    q.close()

    monkeypatch.setattr(
        runtime, "resolve_admin_provider",
        lambda _slot: (_ for _ in ()).throw(RuntimeError("no admin db")),
    )
    monkeypatch.setenv("LLM_API_KEY", "local")
    q = build_queue(ROOT, tmp_path / "run-env")
    assert q is not None and q.config_source == "env"
    assert q.client.config.max_retries == 0
    q.close()


def test_dialogue_enabled_flag(monkeypatch):
    monkeypatch.delenv("XKX_DIALOGUE_MODEL", raising=False)
    assert not enabled()
    monkeypatch.setenv("XKX_DIALOGUE_MODEL", "1")
    assert enabled()
    assert ProviderClient is not None  # 保持导入路径可用


def test_talk_never_calls_model_without_defer(tmp_path):
    """唯一模型路径：未标记 defer_calls 的队列在 TalkAction 内不得发起调用。"""
    calls = []

    def transport(_cfg, _system, _user):
        calls.append(1)
        return Completion(text="模型句不应出现")

    queue = AsyncDialogueQueue(
        ProviderClient(
            ProviderConfig(
                base_url="http://127.0.0.1:1/v1", key="k", model="m",
                offline=False, max_retries=0,
            ),
            transport=transport,
        ),
        log_dir=tmp_path,
    )
    session = WorldSession(
        ROOT, run_id="single-path", seed=42, npc_limit=12,
        tick_step=6, run_dir=tmp_path,
    )
    session.registry.get("talk").dialogue = queue
    try:
        assert session.perform(
            "u_demo", "enter", {"account_id": "u_demo", "display_name": "陈捕快"}
        )["ok"]
        res = session.perform(
            "u_demo", "talk", {"target": "duan_wudou", "message": "肉怎么卖？"}
        )
        assert res["ok"]
        assert calls == []
        answers = [
            m["text"] for m in (session.state.memories.get("duan_wudou") or [])
            if m.get("source") == "talk:npc"
        ]
        assert answers and answers[-1].startswith("我答：")
    finally:
        queue.close()
        session.close()
