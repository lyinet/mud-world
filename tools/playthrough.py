#!/usr/bin/env python3
"""P3-E 十分钟走查：只通过 HTTP API 完成一条可玩主线，并输出中文叙事。

流程：进入（捕快）→ 移动 3 个地点 → 与 3 个不同 NPC 对话（含 1 次故意找不在场的人）
→ 接任务 → 交付 → 看到钱与关系变化 → 离开 → 重新进入确认状态还在。
验收：守恒误差 0、replay MATCH、事件 seq 连续、所有拒绝都有原因码。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from wsim.api import WorldSession, serve  # noqa: E402
from wsim.audit.conservation import conservation  # noqa: E402
from wsim.core.models import Effect, Event  # noqa: E402
from wsim.world.effects import apply_effects  # noqa: E402
from wsim.world.state import WorldState  # noqa: E402

ACCOUNT = "u_demo"


class Narrative:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def say(self, text: str) -> None:
        self.lines.append(text)
        print(text)

    def write(self, path: Path) -> None:
        path.write_text("\n".join(self.lines).rstrip() + "\n", encoding="utf-8")


class Client:
    def __init__(self, port: int, narrative: Narrative) -> None:
        self.base = f"http://127.0.0.1:{port}"
        self.narrative = narrative
        self.responses: list[dict] = []

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if isinstance(body, dict) and "event_seq" in body:
            self.responses.append(body)
        return body

    def post(self, path: str, payload: dict) -> dict:
        return self._request("POST", path, payload)

    def get(self, path: str) -> dict:
        return self._request("GET", path)


def find_path(content, start: str, end: str) -> list[str]:
    if start == end:
        return [start]
    frontier = [[start]]
    seen = {start}
    while frontier:
        path = frontier.pop(0)
        for nxt in sorted(content.locations[path[-1]].get("exits") or []):
            if nxt in seen:
                continue
            if nxt == end:
                return path + [nxt]
            seen.add(nxt)
            frontier.append(path + [nxt])
    raise RuntimeError(f"没有从 {start} 到 {end} 的路径")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default="p3e-playthrough")
    parser.add_argument("--npcs", type=int, default=0, help="0 = 全量内容包 NPC")
    args = parser.parse_args()

    run_dir = ROOT / "runs" / args.run_id
    if run_dir.exists():
        shutil.rmtree(run_dir)

    # endow=False：走查账号不预置，验证“运行中真实新玩家首次进入”的发放路径。
    session = WorldSession(ROOT, run_id=args.run_id, npc_limit=args.npcs, tick_step=6, endow=False)
    server, port = serve(session)
    narrative = Narrative()
    client = Client(port, narrative)
    checks: dict[str, object] = {}
    failures: list[str] = []

    try:
        narrative.say("# 十分钟走查 · 我是新到任的捕快")
        narrative.say("")
        res = client.post("/api/player/enter", {
            "account_id": ACCOUNT, "display_name": "陈捕快", "archetype": "捕快",
        })
        assert res["ok"], res
        st = res["data"]
        narrative.say(
            f"我以「捕快」身份进了城，站在{st['location']}。"
            f"钱袋里有 {st['wallet']} 文，行囊：{st['inventory']}，体力 {st['stamina']}/{st['max_stamina']}。"
            f"（seq={res['event_seq']} tick={res['tick']}）"
        )
        endow = res["details"]["endowment"]
        if endow["granted"]:
            narrative.say(
                f"这是我第一次进这个世界，领到 {endow['money']} 文 + 配给，"
                f"来源 {endow['money_source']}（独立事件 seq={endow['event_seq']}）。"
            )

        def status() -> dict:
            out = client.get(f"/api/player/status?account_id={ACCOUNT}")
            assert out["ok"], out
            return out

        def move_to(dest: str, label: str) -> None:
            cur = status()
            path = find_path(session.content, cur["location"], dest)
            for nxt in path[1:]:
                name = str((session.content.locations.get(nxt) or {}).get("name") or nxt)
                r = client.post("/api/player/move", {"account_id": ACCOUNT, "to": nxt})
                if not r["ok"]:
                    raise RuntimeError(f"移动失败 {nxt}: {r}")
                narrative.say(
                    f"我沿路走到{nxt}（{name}）。体力 {r['data']['stamina']}/100，"
                    f"seq={r['event_seq']} tick={r['tick']}。"
                )

        def talk(target_id: str, message: str) -> dict:
            r = client.post("/api/player/talk", {
                "account_id": ACCOUNT, "target": target_id, "message": message,
            })
            return r

        def pick_npc(exclude: set[str]) -> dict | None:
            for npc in status()["nearby"]:
                if npc["id"] not in exclude:
                    return npc
            return None

        talked: set[str] = set()

        move_to("inn", "客栈")
        npc = pick_npc(talked)
        if npc:
            r = talk(npc["id"], f"{npc['name']}，初来乍到，跟你打听打听城里的规矩。")
            assert r["ok"], r
            talked.add(npc["id"])
            narrative.say(
                f"我对{npc['name']}说：初来乍到，跟你打听打听城里的规矩。"
                f" 对方答：{r['details'].get('reply','（沉默）')}"
                f"（关系 {r['details'].get('relation')}，seq={r['event_seq']}）"
            )

        move_to("pharmacy", "药铺")
        npc = pick_npc(talked)
        if npc:
            r = talk(npc["id"], f"{npc['name']}，城里最近可有什么案子？")
            assert r["ok"], r
            talked.add(npc["id"])
            narrative.say(
                f"在药铺我问{npc['name']}：城里最近可有什么案子？"
                f" 对方答：{r['details'].get('reply','（沉默）')}"
                f"（关系 {r['details'].get('relation')}，seq={r['event_seq']}）"
            )

        move_to("academy", "书院")
        # 故意找一个不在场的人：柳三娘此刻在集市。
        absent = client.post("/api/player/talk", {
            "account_id": ACCOUNT, "target": "liu_sanniang", "message": "柳娘子，我有一桩事想问你。",
        })
        if absent["ok"]:
            raise RuntimeError("故意找不在场的人却没有被拒绝")
        narrative.say(
            f"我对着空气喊柳三娘，系统拒绝了我：{absent['reject_reason']}——{absent['reason_text']}"
            f"（seq={absent['event_seq']} tick={absent['tick']}）"
        )
        npc = pick_npc(talked)
        if npc:
            r = talk(npc["id"], f"{npc['name']}，读书人可知道近来有什么委托？")
            assert r["ok"], r
            talked.add(npc["id"])
            narrative.say(
                f"我向{npc['name']}请教委托。 对方答：{r['details'].get('reply','（沉默）')}"
                f"（关系 {r['details'].get('relation')}，seq={r['event_seq']}）"
            )

        # 再补一位，确保成功对话的 NPC 至少有 3 个不同的人。
        if len(talked) < 3:
            npc = pick_npc(talked)
            if npc:
                r = talk(npc["id"], f"{npc['name']}，多谢指点。")
                assert r["ok"], r
                talked.add(npc["id"])
                narrative.say(f"我又与{npc['name']}寒暄了几句（seq={r['event_seq']}）。")
        if len(talked) < 3:
            raise RuntimeError(f"成功对话的 NPC 不足 3 人: {talked}")

        move_to("market", "集市")
        quests = client.get(f"/api/player/quests?account_id={ACCOUNT}")
        assert quests["ok"], quests
        candidates = [q for q in quests["open"] if q["acceptable"] and q["category"] == "errand"]
        if not candidates:
            candidates = [q for q in quests["open"] if q["acceptable"]]
        if not candidates:
            raise RuntimeError("任务板上没有可接的任务")
        quest = candidates[0]
        narrative.say(
            f"我在任务板前看到「{quest['id']}」：{quest['giver_name']}委托我去"
            f"{quest['target_location']}，报酬 {quest['reward_money']} 文。"
        )
        accept = client.post("/api/player/quest/accept", {
            "account_id": ACCOUNT, "quest": quest["id"],
        })
        assert accept["ok"], accept
        narrative.say(f"我接下了这单任务（seq={accept['event_seq']} tick={accept['tick']}）。")

        wallet_before = status()["wallet"]
        move_to(quest["target_location"], "交付地点")
        deliver = client.post("/api/player/quest/deliver", {
            "account_id": ACCOUNT, "quest": quest["id"],
        })
        assert deliver["ok"], deliver
        after = deliver["data"]
        wallet_after = after["wallet"]
        rel = next((r for r in after["relations"] if r["target"] == quest["giver"]), None)
        narrative.say(
            f"我把事情办妥，任务交付成功：钱袋从 {wallet_before} 文变成 {wallet_after} 文"
            f"（+{wallet_after - wallet_before}），与{quest['giver_name']}的关系：{rel}。"
            f"（seq={deliver['event_seq']} tick={deliver['tick']}）"
        )

        leave = client.post("/api/player/leave", {"account_id": ACCOUNT})
        assert leave["ok"], leave
        narrative.say(f"我下线离开世界（seq={leave['event_seq']}）。")
        back = client.post("/api/player/enter", {
            "account_id": ACCOUNT, "display_name": "陈捕快", "archetype": "捕快",
        })
        assert back["ok"], back
        final = status()
        narrative.say(
            f"我重新进入世界，仍在{final['location']}，钱袋 {final['wallet']} 文，"
            f"与柳三娘的关系还在：{[r for r in final['relations'] if r['target']=='liu_sanniang']}；"
            f"进行中任务 {final['active_quests']}。"
        )

        # ---------------- 硬验收 ----------------
        events: list[Event] = session.store.read_all()
        seqs = [e.seq for e in events]
        checks["event_count"] = len(events)
        checks["seq_continuous"] = seqs == list(range(1, len(events) + 1))
        checks["seq_last"] = seqs[-1] if seqs else 0
        rejection_responses = [r for r in client.responses if not r["ok"]]
        checks["rejections"] = [
            {"reason": r["reject_reason"], "text": r["reason_text"], "seq": r["event_seq"]}
            for r in rejection_responses
        ]
        checks["every_rejection_has_reason"] = all(
            r["reject_reason"] and r["reason_text"] for r in rejection_responses
        )
        for r in client.responses:
            ev = events[r["event_seq"] - 1]
            if ev.actor != r["player"] or ev.verb != r["verb"]:
                failures.append(f"seq {r['event_seq']} 与响应不匹配: {ev.actor}/{ev.verb}")
        checks["response_seq_verified"] = not failures

        replay = WorldState(locations=session.content.locations)
        for ev in events:
            if ev.outcome == "ok":
                apply_effects(replay, [Effect.from_dict(e) for e in ev.effects])
            replay.tick = ev.tick
        checks["live_hash"] = session.state.hash()
        checks["replay_hash"] = replay.hash()
        checks["replay_match"] = replay.hash() == session.state.hash()
        cons = conservation(session.state)
        checks["conservation_error"] = cons["conservation_error"]
        checks["minted_total"] = cons["minted_total"]
        checks["burned_total"] = cons["burned_total"]

        if not checks["seq_continuous"]:
            failures.append("事件 seq 不连续")
        if not checks["every_rejection_has_reason"]:
            failures.append("存在没有原因码/人话原因的拒绝")
        if not checks["replay_match"]:
            failures.append("replay 哈希不匹配")
        if cons["conservation_error"] != 0:
            failures.append(f"守恒误差 {cons['conservation_error']}")

        narrative.say("")
        narrative.say(
            f"## 验收\n- 事件 {len(events)} 条，seq 1..{checks['seq_last']} 连续：{checks['seq_continuous']}\n"
            f"- 守恒误差：{cons['conservation_error']}\n- replay MATCH：{checks['replay_match']}\n"
            f"- 拒绝：{checks['rejections']}"
        )
        if failures:
            narrative.say("FAIL:\n- " + "\n- ".join(failures))
        else:
            narrative.say("全部硬验收 PASS。")
    finally:
        server.shutdown()
        session.close()

    run_dir.mkdir(parents=True, exist_ok=True)
    narrative.write(run_dir / "narrative.txt")
    report = {
        "run_id": args.run_id,
        "player": ACCOUNT,
        "checks": checks,
        "failures": failures,
        "responses": client.responses,
    }
    (run_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"\n报告：{run_dir / 'report.json'}")
    print(f"叙事：{run_dir / 'narrative.txt'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
