"""P4 委托供给 / 过期下架 / 拒绝原因 回归。

- 纯函数：AcceptAction 对过期单返回 quest_expired；FailAction 对 open 过期单先 fail 再 prune。
- 端到端：7 天玩家每天接 2 单，逐日可接数 ≥2、0 拒绝、0 过期单可见；末态摘要做确定性基线。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from wsim.actions.common import wallet
from wsim.actions.quests import QuestAcceptAction, SystemQuestFailAction
from wsim.core.content import Content
from wsim.core.models import Effect, Intent
from wsim.world.effects import apply_effects
from wsim.world.state import Quest, WorldState

ROOT = Path(__file__).resolve().parents[1]
PID = "player:u_supply"

# P4 新玩家基线（2026-11-04，非静默改旧基线）：
# 日界新增委托“先下架过期、再按模板补发”，过期 open 单立即 prune，配额只算近 3 天；
# 因此 7 天可接单量稳定在 7–8 条、0 拒绝。旧 headless golden 不受影响（系统动作只在
# player session 日界触发），另加本组玩家侧基线。GOLDEN_MILESTONE 保持原值。
PLAYER_SUPPLY_GOLDEN = {
    "days": [[1, 8], [2, 7], [3, 7], [4, 7], [5, 7], [6, 7], [7, 7]],
    "accepts": 14,
    "delivered": 14,
    "rejects": 0,
    "digest": "f393218dbdb9977743d0803f593d6f7d60aa2a1ff7a6467d90fe64acec5eefcc",
}


def _mini_content() -> Content:
    c = Content()
    c.locations = {"m": {"id": "m", "name": "集市", "exits": []}}
    c.economy = {}
    c.quests = {}
    return c


def _seed() -> tuple[Content, WorldState]:
    content = _mini_content()
    state = WorldState(locations=content.locations)
    apply_effects(state, [
        Effect("create_account", {"account": wallet(PID)}),
        Effect("create_entity", {
            "id": PID, "name": "阿青", "role": "worker", "location": "m", "home": "m",
            "stamina": 100.0, "skills": {},
        }),
        Effect("create_entity", {
            "id": "n1", "name": "甲", "role": "worker", "location": "m", "home": "m",
            "stamina": 100.0, "skills": {},
        }),
    ])
    return content, state


def _quest(qid: str, *, status: str = "open", deadline: int = 100, acceptor: str | None = None) -> Quest:
    return Quest(
        id=qid, template_id="x", giver="n1", acceptor=acceptor, target_npc="",
        target_location="m", verb="inquiry", args={}, reward_money=9,
        reward_relation=0.0, deadline_tick=deadline, status=status, created_tick=0,
    )


def test_expired_open_quest_returns_quest_expired():
    content, state = _seed()
    state.quests["quest:x:0"] = _quest("quest:x:0", deadline=100)
    reject = QuestAcceptAction(content).preconditions(
        state, Intent(actor=PID, verb="quest_accept", args={"quest": "quest:x:0"}, tick=200)
    )
    assert reject == ["quest_expired"]


def test_active_cap_returns_too_many_quests():
    content, state = _seed()
    for i in range(2):
        qid = f"quest:a:{i}"
        state.quests[qid] = _quest(qid, status="accepted", acceptor=PID, deadline=999)
    state.quests["quest:b:0"] = _quest("quest:b:0", deadline=999)
    reject = QuestAcceptAction(content).preconditions(
        state, Intent(actor=PID, verb="quest_accept", args={"quest": "quest:b:0"}, tick=10)
    )
    assert reject == ["too_many_quests"]


def test_low_reputation_returns_reputation_too_low():
    content, state = _seed()
    content.quests = {"x": {"require_reputation": 3}}
    state.quests["quest:x:0"] = _quest("quest:x:0", deadline=999)
    reject = QuestAcceptAction(content).preconditions(
        state, Intent(actor=PID, verb="quest_accept", args={"quest": "quest:x:0"}, tick=10)
    )
    assert reject == ["reputation_too_low"]


def test_fail_action_prunes_open_expired_immediately():
    content, state = _seed()
    state.quests["quest:x:0"] = _quest("quest:x:0", deadline=100)
    effects = SystemQuestFailAction(content).apply(
        state,
        Intent(actor="system", verb="system_quest_fail", args={"prune_expired": True}, tick=200),
        None,
    )
    ops = [e.op for e in effects]
    assert "quest_fail" in ops
    assert any(e.op == "quest_prune" and e.data["ids"] == ["quest:x:0"] for e in effects)


def test_fail_action_keeps_expired_without_player_day_boundary_flag():
    """NPC 无头世界不传 prune_expired：保留过期记录（沿用 7 天保留期清理），
    事件流与旧基线一致，避免 K1/里程碑基线无谓漂移。"""
    content, state = _seed()
    state.quests["quest:x:0"] = _quest("quest:x:0", deadline=100)
    effects = SystemQuestFailAction(content).apply(
        state, Intent(actor="system", verb="system_quest_fail", args={}, tick=200), None
    )
    ops = [e.op for e in effects]
    assert "quest_fail" in ops
    assert "quest_prune" not in ops


def _route(content: Content, a: str, b: str) -> list[str]:
    if a == b:
        return [a]
    prev: dict[str, str | None] = {a: None}
    queue = [a]
    while queue:
        node = queue.pop(0)
        for nxt in content.locations[node].get("exits", []):
            if nxt not in prev:
                prev[nxt] = node
                queue.append(nxt)
    out: list[str] = []
    cur: str | None = b
    while cur:
        out.append(cur)
        cur = prev[cur]
    return list(reversed(out))


def _run_supply_week(tmp: Path) -> tuple[dict, dict]:
    from wsim.api.session import WorldSession

    ws = WorldSession(ROOT, run_id="sup", seed=42, npc_limit=0, run_dir=tmp)
    ws.perform("u_demo", "enter", {"archetype": "游侠", "display_name": "阿青"})
    pid = "player:u_demo"

    def day() -> int:
        return int(ws.state.tick) // 144 + 1

    def move_to(loc: str) -> bool:
        ent = ws.state.entities[pid]
        if ent.location == loc:
            return True
        for step in _route(ws.content, ent.location, loc)[1:]:
            if not ws.perform("u_demo", "move", {"to": step}).get("ok"):
                return False
        return True

    def complete(qid: str) -> bool:
        q = ws.state.quests[qid]
        tpl = ws.content.quests.get(q.template_id) or {}
        if str(tpl.get("category", "")) == "procurement":
            return False
        target = q.target_location or ws.state.entities[q.giver].location
        if not move_to(target):
            return False
        ok = bool(ws.perform("u_demo", "quest_deliver", {"quest": qid}).get("ok"))
        move_to("market")
        return ok

    days: list[list[int]] = []
    accepts = delivered = rejects = 0
    for _ in range(7):
        rows = [r for r in ws._quest_board_view(pid, "market")["rows"] if r["acceptable"]]
        assert len(rows) >= 2, "每天至少 2 条可接委托"
        days.append([day(), len(rows)])
        for row in rows[:2]:
            res = ws.perform("u_demo", "quest_accept", {"quest": row["id"]})
            assert res.get("ok"), res.get("reason_text")
            accepts += 1
            if complete(row["id"]):
                delivered += 1
        start = day()
        guard = 0
        while day() == start and guard < 200:
            player = ws.player_state("u_demo")["player"]
            if player["stamina"] >= 12:
                ws.perform("u_demo", "work", {"at": "market", "hours": 8})
            elif player["stamina"] >= 8:
                ws.perform("u_demo", "cultivate", {"hours": 2})
            else:
                view = ws.player_state("u_demo")["today"]
                target = view["npcs"][0]["id"] if view["npcs"] else "bai_lusheng"
                ws.perform("u_demo", "talk", {"target": target, "message": "闲话"})
            guard += 1
    # 过期单不得出现在板面：任意 row 的 deadline 都必须晚于当前 tick
    for row in ws._quest_board_view(pid, "market")["rows"]:
        q = ws.state.quests[row["id"]]
        assert q.deadline_tick >= int(ws.state.tick)

    stats = {
        "accepts": accepts,
        "delivered": delivered,
        "rejects": rejects,
    }
    state = ws.player_state("u_demo")
    sig = {
        "stats": stats,
        "days": days,
        "player": {
            "location": state["player"]["location"],
            "money": state["player"]["wallet"]["money"],
            "skills": state["player"]["skills"],
            "growth": {k: v["level"] for k, v in state["growth"].items()},
        },
        "quests": sorted((q.id, q.status) for q in ws.state.quests.values()),
    }
    digest = hashlib.sha256(
        json.dumps(sig, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {"days": days, **stats, "digest": digest}, state


def test_seven_day_player_supply_is_stable(tmp_path):
    got, _ = _run_supply_week(tmp_path / "week")
    drift = {k: (PLAYER_SUPPLY_GOLDEN[k], got[k])
             for k in PLAYER_SUPPLY_GOLDEN if got[k] != PLAYER_SUPPLY_GOLDEN[k]}
    assert not drift, "玩家 7 天委托供给基线漂移：" + json.dumps(drift, ensure_ascii=False)
