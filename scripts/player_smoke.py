#!/usr/bin/env python3
"""玩家闭环冒烟：只走 HTTP，不 import wsim。

依次执行：进入世界 → 查看 300 文与地点 → 移动到相邻地点 → 和在场 NPC 说话
→ 回到集市接一张任务 → 再读一次状态，断言位置/任务/钱包真的还在。

用法：
    python3 scripts/player_smoke.py --base-url http://127.0.0.1:8020 --account u_demo

失败时退出码 1，并把失败步骤和响应原文打到 stderr。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

DEFAULT_ACCOUNT = "u_demo"
DEFAULT_NAME = "陈捕快"
DEFAULT_ARCHETYPE = "捕快"


class SmokeError(RuntimeError):
    pass


def _request(base: str, path: str, body: dict | None = None) -> dict:
    url = base.rstrip("/") + path
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"ok": False, "reason_text": raw}
        payload.setdefault("http_status", exc.code)
        return payload
    except urllib.error.URLError as exc:
        raise SmokeError(f"连不上 {url}: {exc}") from exc


def _get_state(base: str, account: str) -> dict:
    return _request(base, f"/api/player/state?account_id={account}")


def _require(cond: bool, message: str, payload: dict | None = None) -> None:
    if cond:
        return
    detail = json.dumps(payload, ensure_ascii=False, sort_keys=True) if payload else ""
    raise SmokeError(f"{message}\n响应: {detail}")


def _post_action(base: str, account: str, verb: str, args: dict) -> dict:
    return _request(
        base,
        "/api/player/action",
        {"account_id": account, "verb": verb, "args": args},
    )


def run(base: str, account: str, name: str, archetype: str) -> dict:
    print(f"[walk] 1/6 进入世界 account={account}")
    entered = _request(
        base,
        "/api/player/enter",
        {"account_id": account, "display_name": name, "archetype": archetype},
    )
    details = entered.get("details") or {}
    if entered.get("ok") is not True:
        _require(
            entered.get("reject_reason") == "already_online",
            "进入世界失败",
            entered,
        )
    endowment = details.get("endowment") or {}
    if endowment:
        _require(int(endowment.get("money") or 0) == 300, "进入后没有拿到 300 文", entered)
    print(f"[walk]     ok 初始资产={endowment.get('money', '(已在线)')} 文，created={details.get('created')}")

    state = _get_state(base, account)
    _require(state.get("ok") is True, "进入后读取状态失败", state)
    player = state["player"]
    money = int((player.get("wallet") or {}).get("money") or 0)
    location = player["location"]
    location_name = player["location_name"]
    _require(money == 300, f"钱包不是 300 文，而是 {money}", state)
    print(f"[walk] 2/6 查看地点：{location_name}({location})，钱包 {money} 文，在场 NPC {len(state.get('present_npcs') or [])} 个")

    exits = [row for row in state["location"]["exits"] if row.get("exists")]
    _require(bool(exits), "当前地点没有可用出口", state)
    target = exits[0]
    moved = _post_action(base, account, "move", {"to": target["id"]})
    _require(moved.get("ok") is True, f"移动到 {target['id']} 失败", moved)
    after_move = _get_state(base, account)
    after_move_location = after_move["player"]["location"]
    _require(after_move_location != location, "移动后地点没有变化", after_move)
    print(f"[walk] 3/6 移动 {location} -> {target['name']}({target['id']})，tick={moved.get('next_tick')}")

    talk_target = (after_move.get("present_npcs") or [])
    if not talk_target:
        back = _post_action(base, account, "move", {"to": location})
        _require(back.get("ok") is True, f"返回 {location} 失败", back)
        after_back = _get_state(base, account)
        talk_target = after_back.get("present_npcs") or []
        _require(bool(talk_target), "目的地和出生点都没有在场 NPC，无法说话", after_back)
        after_move = after_back
    npc = talk_target[0]
    talked = _post_action(
        base,
        account,
        "talk",
        {"target": npc["id"], "message": "你好，今日集市可有什么新鲜事？"},
    )
    _require(talked.get("ok") is True, f"和 {npc['id']} 说话失败", talked)
    reply = str((talked.get("details") or {}).get("reply") or "").strip()
    _require(bool(reply), f"{npc['id']} 没有给出回复", talked)
    print(f"[walk] 4/6 对话 {npc['name']}({npc['id']})，回复 {len(reply)} 字：{reply[:40]}...")

    state2 = _get_state(base, account)
    if state2["player"]["location"] != location:
        back = _post_action(base, account, "move", {"to": location})
        _require(back.get("ok") is True, f"回到集市 {location} 失败", back)
        state2 = _get_state(base, account)
    _require(state2["player"]["location"] == location, "没有回到集市，无法接集市任务", state2)

    acceptable = [row for row in state2["quest_board"]["rows"] if row.get("acceptable")]
    _require(bool(acceptable), "集市当前没有可接任务", state2)
    quest = acceptable[0]
    accepted = _post_action(base, account, "quest_accept", {"quest": quest["id"]})
    _require(accepted.get("ok") is True, f"接任务 {quest['id']} 失败", accepted)
    print(f"[walk] 5/6 接任务 {quest['name']}({quest['id']})，报酬 {quest.get('reward_money')} 文")

    final = _get_state(base, account)
    _require(final.get("ok") is True, "最终状态读取失败", final)
    final_player = final["player"]
    active = final["quest_board"].get("active") or []
    _require(final_player["location"] == location, "最终位置没有保持", final)
    _require(any(row["id"] == quest["id"] for row in active), "接下的任务没有出现在 active", final)
    _require(int((final_player.get("wallet") or {}).get("money") or 0) == 300, "最终钱包不是 300 文", final)
    print(
        "[walk] 6/6 刷新后状态仍在："
        f"location={final_player['location']} active_quests={len(active)} "
        f"money={final_player['wallet']['money']} tick={final['tick']}"
    )
    print("[walk] PASS")
    return final


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="玩家闭环冒烟（进入/移动/对话/接任务）")
    parser.add_argument("--base-url", default="http://127.0.0.1:8020")
    parser.add_argument("--account", default=DEFAULT_ACCOUNT)
    parser.add_argument("--name", default=DEFAULT_NAME)
    parser.add_argument("--archetype", default=DEFAULT_ARCHETYPE)
    args = parser.parse_args(argv)
    try:
        run(args.base_url, args.account.strip().lower(), args.name, args.archetype)
    except SmokeError as exc:
        print(f"[walk] FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
