"""W1 存档回放：重启后位置/钱包/物品/任务/成长线/NPC 关系逐项 0 丢失。"""
from __future__ import annotations

from pathlib import Path

from wsim.api.session import WorldSession

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = "u_test"


def _snapshot(session: WorldSession) -> dict:
    state = session.player_state(ACCOUNT)
    assert state["ok"] is True, state
    player = state["player"]
    growth = state["growth"]
    return {
        "tick": state["tick"],
        "location": player["location"],
        "wallet_money": player["wallet"]["money"],
        "items": tuple(sorted((row["item"], row["qty"]) for row in player["wallet"]["items"])),
        "quests": tuple(sorted((qid, session.state.quests[qid].status) for qid in player["active_quests"])),
        "skills": dict(player["skills"]),
        "reputation": growth["reputation"]["value"],
        "assets": growth["assets"]["value"],
        "relations": tuple(
            (row["target"], row["kind"], row["affection"], row["trust"])
            for row in player["relations"]
        ),
    }


def test_restart_replays_all_six_categories(tmp_path):
    run_dir = tmp_path / "run"
    first = WorldSession(ROOT, run_id="w1-persist", run_dir=run_dir, seed=42, seed_accounts=[ACCOUNT])
    try:
        assert first.replayed is False
        assert first.perform(ACCOUNT, "enter", {"display_name": "测试甲", "archetype": "游侠"})["ok"]
        board = first.player_state(ACCOUNT)["quest_board"]["rows"]
        acceptable = [row["id"] for row in board if row["acceptable"]]
        assert acceptable
        assert first.perform(ACCOUNT, "quest/accept", {"quest": acceptable[0]})["ok"]
        npcs = [row["id"] for row in first.player_state(ACCOUNT)["present_npcs"]]
        assert first.perform(ACCOUNT, "talk", {"target": npcs[0], "message": "你好。"})["ok"]
        assert first.perform(ACCOUNT, "talk", {"target": npcs[1], "message": "今日生意如何？"})["ok"]
        assert first.perform(ACCOUNT, "move", {"to": "academy"})["ok"]
        assert first.perform(ACCOUNT, "work", {"hours": 8})["ok"]
        assert first.perform(ACCOUNT, "cultivate", {"hours": 2})["ok"]

        before = _snapshot(first)
        events_before = first.store.count()
        assert before["location"] == "academy"
        assert before["wallet_money"] > 300  # 工作收入已落盘，不是创世初始 300
        assert before["quests"] and before["skills"]["镖师"] != 0.35
        assert before["relations"] and before["reputation"] >= 0.0
    finally:
        first.close()

    second = WorldSession(ROOT, run_id="w1-persist", run_dir=run_dir, seed=42, seed_accounts=[ACCOUNT])
    try:
        assert second.replayed is True
        assert second.store.count() == events_before, "回放不得重新创世/重复发事件"
        after = _snapshot(second)
        assert after["tick"] == before["tick"]
        assert after["location"] == before["location"]
        assert after["wallet_money"] == before["wallet_money"]
        assert after["items"] == before["items"]
        assert after["quests"] == before["quests"]
        assert after["skills"] == before["skills"]
        assert after["reputation"] == before["reputation"]
        assert after["assets"] == before["assets"]
        assert after["relations"] == before["relations"]

        # 回放后仍可继续写：tick 从存档继续，且不会被 stale_tick 拦下。
        tick_before = second.state.tick
        assert second.perform(ACCOUNT, "move", {"to": "market"})["ok"]
        assert second.state.tick >= tick_before
    finally:
        second.close()
