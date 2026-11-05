#!/usr/bin/env python3
"""玩家婚姻闭环验收（续跑）：在已完成“求婚→成婚”的真实世界里继续观察。

前置：同一 run 里玩家已与 liu_sanniang 成婚（marriage since_tick=1026）。
本脚本只走真实 HTTP，不 import wsim；负责：
- 从 events.sqlite 还原前半段证据（拒绝样例 / 提好感 / 求婚与聘礼转移）；
- 观察婚后 3 个世界日的 spouse_time（同地点日常 + 带饭/问候）与配偶专属 talk；
- 重婚拒绝、和离、对账/seq 审计。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

TICKS_PER_DAY = 144
MARRIAGE_VERBS = (
    "marriage_propose",
    "marriage_accept",
    "marriage_reject",
    "marriage",
    "marriage_end",
    "spouse_time",
)


class Driver:
    def __init__(self, base: str, account: str, db_path: Path) -> None:
        self.base = base.rstrip("/")
        self.account = account
        self.db_path = db_path
        self.records: dict[str, Any] = {}
        self.last_action_wall = 0.0

    def _request(self, path: str, body: dict | None = None) -> dict:
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self.base + path,
            data=data,
            headers=headers,
            method="POST" if body is not None else "GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {"ok": False, "reason_text": raw}
            payload.setdefault("http_status", exc.code)
            return payload

    def state(self) -> dict:
        return self._request(f"/api/player/state?account_id={self.account}")

    def action(self, verb: str, args: dict | None = None) -> dict:
        self.last_action_wall = time.time()
        return self._request(
            "/api/player/action",
            {"account_id": self.account, "verb": verb, "args": args or {}},
        )

    def enter(self) -> dict:
        self.last_action_wall = time.time()
        return self._request(
            "/api/player/enter",
            {"account_id": self.account, "display_name": "沈砚", "archetype": "游侠"},
        )

    def events(self, since: int = 0) -> list[dict]:
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT seq,tick,actor,verb,args,effects,outcome,reason,source"
                " FROM events WHERE seq > ? ORDER BY seq",
                (int(since),),
            ).fetchall()
        finally:
            conn.close()
        out = []
        for seq, tick, actor, verb, args, effects, outcome, reason, source in rows:
            out.append(
                {
                    "seq": int(seq),
                    "tick": int(tick),
                    "actor": actor,
                    "verb": verb,
                    "args": json.loads(args),
                    "effects": json.loads(effects),
                    "outcome": outcome,
                    "reason": reason,
                    "source": source,
                }
            )
        return out

    def log(self, step: str, **fields: Any) -> None:
        print(json.dumps({"step": step, **fields}, ensure_ascii=False), flush=True)

    # -------------------------------------------------------------- helpers
    def _wallet(self, st: dict) -> int:
        return int((st.get("player", {}).get("wallet") or {}).get("money") or 0)

    def _items(self, st: dict) -> list[dict]:
        return list((st.get("player", {}).get("wallet") or {}).get("items") or [])

    def _present(self, st: dict, eid: str) -> bool:
        return any(row.get("id") == eid for row in st.get("present_npcs") or [])

    def _keep_online(self) -> None:
        if time.time() - self.last_action_wall > 40.0:
            self.action("heartbeat", {})

    def _wait(self, predicate, timeout_s: float, note: str = "") -> bool:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if predicate():
                return True
            self._keep_online()
            time.sleep(0.5)
        return False


def history_from_db(driver: Driver, married_tick: int) -> dict[str, Any]:
    all_events = driver.events(0)
    rejection_rows = []
    proposal_rows = []
    courtship_days: dict[int, dict[str, Any]] = {}
    login_table: dict[str, int] = {}
    for ev in all_events:
        verb = ev["verb"]
        if verb == "marriage_propose" and ev["outcome"] == "rejected":
            rejection_rows.append(
                {
                    "seq": ev["seq"],
                    "tick": ev["tick"],
                    "actor": ev["actor"],
                    "outcome": ev["outcome"],
                    "reason_code": ev["args"].get("reason"),
                    "args": ev["args"],
                }
            )
        elif verb == "marriage_reject":
            rejection_rows.append(
                {
                    "seq": ev["seq"],
                    "tick": ev["tick"],
                    "actor": ev["actor"],
                    "outcome": ev["outcome"],
                    "reason_code": ev["args"].get("reason_code"),
                    "args": ev["args"],
                }
            )
        elif verb in ("marriage_propose", "marriage_accept", "marriage") and ev["tick"] >= married_tick:
            keep = [
                eff for eff in ev["effects"]
                if eff.get("op") in ("transfer", "item_move", "marry", "marriage_meta")
            ]
            proposal_rows.append(
                {
                    "seq": ev["seq"],
                    "tick": ev["tick"],
                    "actor": ev["actor"],
                    "verb": verb,
                    "outcome": ev["outcome"],
                    "reason": ev["reason"],
                    "args": ev["args"],
                    "effects": keep,
                }
            )
        if verb == "talk" and ev["actor"].startswith("player:") and ev["outcome"] == "ok":
            target = str(ev["args"].get("target") or "")
            day = ev["tick"] // TICKS_PER_DAY
            row = courtship_days.setdefault(
                day, {"day": day, "first_tick": ev["tick"], "talks": 0, "targets": {}}
            )
            row["talks"] = int(row["talks"]) + 1
            row["targets"][target] = int(row["targets"].get(target, 0)) + 1
            for eff in ev["effects"]:
                data = eff.get("data") or {}
                if eff.get("op") == "relation_upsert" and target:
                    key = f"{data.get('a')}|{data.get('b')}"
                    login_table[key] = login_table.get(key, 0) + 1
    return {
        "rejections": rejection_rows,
        "proposal_and_marriage": proposal_rows,
        "courtship_days": [courtship_days[d] for d in sorted(courtship_days)],
        "talk_target_counts": login_table,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8138")
    parser.add_argument("--account", default="pmmain")
    parser.add_argument("--db", default="runs/pm-accept-1/events.sqlite")
    parser.add_argument("--out", default="specs/player-marriage/evidence/acceptance.json")
    parser.add_argument("--target", default="liu_sanniang")
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--day-timeout", type=float, default=900.0)
    args = parser.parse_args(argv)
    driver = Driver(args.base_url, args.account, Path(args.db))
    target = args.target

    entered = driver.enter()
    if not entered.get("ok") and entered.get("reject_reason") != "already_online":
        raise SystemExit(f"enter failed: {entered}")
    driver.log("enter", ok=bool(entered.get("ok")), reject_reason=str(entered.get("reject_reason") or ""))
    st = driver.state()
    marriage = st.get("player", {}).get("marriage") or {}
    if marriage.get("status") != "married" or marriage.get("spouse_id") != target:
        raise SystemExit(f"expected married to {target}, got {marriage}")
    married_tick = int(marriage.get("since_tick") or 0)
    base = {
        "tick": int(st["tick"]),
        "day": int(st["tick"]) // TICKS_PER_DAY,
        "wallet": driver._wallet(st),
        "items": driver._items(st),
        "location": st["player"]["location"],
        "present_npcs": [r["id"] for r in st.get("present_npcs") or []],
        "marriage": marriage,
        "reconciliation": st.get("reconciliation"),
        "note": "服务已重启加载婚后日常修复；前半段（拒绝/提好感/求婚/成婚）从同一 events.sqlite 还原。",
    }
    driver.records["baseline"] = base
    driver.records["history"] = history_from_db(driver, married_tick)
    driver.log("baseline", **{k: base[k] for k in ("tick", "day", "wallet", "marriage", "reconciliation")})

    # 婚后 N 个世界日：同地点日常（spouse_time）+ 配偶专属 talk
    daily: list[dict] = []
    talk_replies: list[dict] = []
    observed_days: set[int] = set()
    last_seq = 0
    deadline = time.time() + args.day_timeout
    while len(observed_days) < args.days:
        if time.time() > deadline:
            raise SystemExit(f"only {sorted(observed_days)} within {args.day_timeout}s")
        for ev in driver.events(last_seq):
            last_seq = max(last_seq, ev["seq"])
            if ev["verb"] != "spouse_time" or ev["outcome"] != "ok":
                continue
            note = ""
            items_moved = []
            for eff in ev["effects"]:
                data = eff.get("data") or {}
                if eff.get("op") == "memory_add" and data.get("source") == "spouse:daily":
                    note = str(data.get("text") or "")
                if eff.get("op") == "item_move":
                    items_moved.append(data)
            day = ev["tick"] // TICKS_PER_DAY
            if day in observed_days:
                continue
            observed_days.add(day)
            row = {
                "day": day,
                "tick": ev["tick"],
                "actor": ev["actor"],
                "reason": ev["reason"],
                "note": note,
                "items_moved": items_moved,
                "seq": ev["seq"],
            }
            daily.append(row)
            driver.log("spouse_time", **row)
            st2 = driver.state()
            if driver._present(st2, target):
                talked = driver.action("talk", {"target": target, "message": "娘子今日可还顺当？"})
                talk_replies.append(
                    {
                        "tick": int(talked.get("tick") or 0),
                        "day": int(talked.get("tick") or 0) // TICKS_PER_DAY,
                        "ok": bool(talked.get("ok")),
                        "reply": str((talked.get("details") or {}).get("reply") or ""),
                    }
                )
                driver.log("talk_spouse", **talk_replies[-1])
        driver._keep_online()
        time.sleep(0.5)
    driver.records["daily"] = daily
    driver.records["talk_replies"] = talk_replies

    # 重婚拒绝
    if not driver._wait(lambda: driver._present(driver.state(), target), 240, "spouse present"):
        raise SystemExit("spouse not present for remarriage check")
    before = driver.state()
    remarry = driver.action("marriage_propose", {"target": target, "gift_money": 0})
    after = driver.state()
    row = {
        "tick": int(remarry.get("tick") or 0),
        "ok": bool(remarry.get("ok")),
        "reject_reason": str(remarry.get("reject_reason") or ""),
        "wallet_before": driver._wallet(before),
        "wallet_after": driver._wallet(after),
        "marriage_after": after["player"].get("marriage"),
    }
    driver.records["already_married"] = row
    driver.log("reject_already_married", **row)
    if row["reject_reason"] != "already_married":
        raise SystemExit(f"already_married sample failed: {row}")

    # 和离
    if not driver._wait(lambda: driver._present(driver.state(), target), 240, "spouse present for divorce"):
        raise SystemExit("spouse not present for divorce")
    before = driver.state()
    wallet_before = driver._wallet(before)
    divorce = driver.action("marriage_end", {})
    after = driver.state()
    div = {
        "tick": int(divorce.get("tick") or 0),
        "ok": bool(divorce.get("ok")),
        "reject_reason": str(divorce.get("reject_reason") or ""),
        "wallet_before": wallet_before,
        "wallet_after": driver._wallet(after),
        "marriage_after": after["player"].get("marriage"),
        "events": [
            {
                "seq": ev["seq"],
                "tick": ev["tick"],
                "actor": ev["actor"],
                "verb": ev["verb"],
                "outcome": ev["outcome"],
                "reason": ev["reason"],
                "effects": [
                    eff for eff in ev["effects"]
                    if eff.get("op") in ("set_field", "relation_upsert", "marriage_meta")
                ],
            }
            for ev in driver.events(0)
            if ev["verb"] == "marriage_end" and ev["seq"] > 0 and ev["tick"] >= int(divorce.get("tick") or 0) - 1
        ],
    }
    driver.records["divorce"] = div
    driver.log("divorce", **{k: div[k] for k in ("tick", "ok", "reject_reason", "wallet_before", "wallet_after", "marriage_after")})
    for ev in div["events"]:
        driver.log("divorce_event", seq=ev["seq"], tick=ev["tick"], actor=ev["actor"], verb=ev["verb"], outcome=ev["outcome"], effects=ev["effects"])

    # 对账 / seq / 事件统计
    final = driver.state()
    all_events = driver.events(0)
    seqs = [ev["seq"] for ev in all_events]
    verbs: dict[str, int] = {}
    for ev in all_events:
        verbs[ev["verb"]] = verbs.get(ev["verb"], 0) + 1
    audit = {
        "tick": int(final["tick"]),
        "wallet": driver._wallet(final),
        "items": driver._items(final),
        "reconciliation": final.get("reconciliation"),
        "spouse_after_divorce": final["player"].get("marriage"),
        "seq_first": seqs[0] if seqs else 0,
        "seq_last": seqs[-1] if seqs else 0,
        "seq_count": len(seqs),
        "seq_contiguous": bool(seqs) and seqs[0] == 1 and seqs == list(range(1, seqs[-1] + 1)),
        "verbs": dict(sorted(verbs.items())),
        "marriage_events": [
            {
                "seq": ev["seq"],
                "tick": ev["tick"],
                "actor": ev["actor"],
                "verb": ev["verb"],
                "outcome": ev["outcome"],
                "reason": ev["reason"],
            }
            for ev in all_events
            if ev["verb"] in MARRIAGE_VERBS
        ],
    }
    driver.records["audit"] = audit
    driver.log("audit", **{k: audit[k] for k in ("tick", "wallet", "reconciliation", "seq_first", "seq_last", "seq_count", "seq_contiguous")})

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(driver.records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[driver] wrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
