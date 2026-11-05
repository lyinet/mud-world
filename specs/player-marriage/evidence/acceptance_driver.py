#!/usr/bin/env python3
"""玩家婚姻闭环验收驱动：只走真实 HTTP，不 import wsim。

流程：进入世界 → 四个拒绝样例 → 提高好感（每日对话）→ 求婚（聘礼 50 文 + 炊饼）
→ NPC 接受 → 成婚 → 婚后 3 个世界日观察同地点日常（spouse_time + 配偶专属 talk）
→ 重婚拒绝 → 和离 → 对账/seq 审计。

所有证据写到 --out（JSON），同时逐条打印到 stdout。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

TICKS_PER_DAY = 144


class Driver:
    def __init__(self, base: str, account: str, db_path: Path, target: str = "") -> None:
        self.base = base.rstrip("/")
        self.account = account
        self.db_path = db_path
        self.target = target
        self.records: dict[str, Any] = {"steps": []}
        self.last_event_seq = 0
        self.last_action_wall = 0.0

    # ---------------------------------------------------------------- http
    def _request(self, path: str, body: dict | None = None) -> dict:
        url = self.base + path
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            url, data=data, headers=headers, method="POST" if body is not None else "GET"
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

    def action(self, verb: str, args: dict) -> dict:
        self.last_action_wall = time.time()
        return self._request(
            "/api/player/action",
            {"account_id": self.account, "verb": verb, "args": args},
        )

    def enter(self) -> dict:
        self.last_action_wall = time.time()
        return self._request(
            "/api/player/enter",
            {"account_id": self.account, "display_name": "沈砚", "archetype": "游侠"},
        )

    # ---------------------------------------------------------------- events
    def events(self, since: int = 0) -> list[dict]:
        uri = f"file:{self.db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
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

    def collect(self) -> list[dict]:
        rows = self.events(self.last_event_seq)
        if rows:
            self.last_event_seq = rows[-1]["seq"]
        return rows

    # ---------------------------------------------------------------- helpers
    def log(self, step: str, **fields: Any) -> None:
        row = {"step": step, **fields}
        self.records["steps"].append(row)
        print(json.dumps(row, ensure_ascii=False, sort_keys=False), flush=True)

    def _wallet(self, st: dict) -> int:
        return int((st.get("player", {}).get("wallet") or {}).get("money") or 0)

    def _aff(self, st: dict) -> tuple[float, float]:
        affs: list[float] = []
        trusts: list[float] = []
        for rel in st.get("player", {}).get("relations") or []:
            if rel.get("target") == self.target:
                affs.append(float(rel.get("affection") or 0.0))
                trusts.append(float(rel.get("trust") or 0.0))
        return (max(affs) if affs else 0.0, max(trusts) if trusts else 0.0)

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8138")
    parser.add_argument("--account", default="pmmain")
    parser.add_argument("--db", default="runs/pm-accept-1/events.sqlite")
    parser.add_argument("--out", default="specs/player-marriage/evidence/acceptance.json")
    parser.add_argument("--target", default="")
    parser.add_argument("--max-days", type=int, default=24)
    args = parser.parse_args(argv)
    driver = Driver(args.base_url, args.account, Path(args.db), args.target)

    # 0) 进入世界
    entered = driver.enter()
    if not entered.get("ok"):
        raise SystemExit(f"enter failed: {entered}")
    driver.last_event_seq = driver.events(0)[-1]["seq"] if driver.events(0) else 0
    st = driver.state()
    base = {
        "tick": int(st["tick"]),
        "wallet": driver._wallet(st),
        "location": st["player"]["location"],
        "present_npcs": [r["id"] for r in st.get("present_npcs") or []],
        "reconciliation": st.get("reconciliation"),
        "marriage": st["player"].get("marriage"),
    }
    driver.records["baseline"] = base
    driver.log("enter", tick=base["tick"], wallet=base["wallet"], location=base["location"],
               present_npcs=base["present_npcs"], reconciliation=base["reconciliation"])
    if not driver.target:
        present = base["present_npcs"]
        driver.target = "liu_sanniang" if "liu_sanniang" in present else (present[0] if present else "")
    if not driver.target:
        raise SystemExit("no npc present at spawn")
    driver.records["target"] = driver.target
    driver.log("target", target=driver.target)

    # 1) 拒绝样例（在提高好感之前，保证 bond_too_low 可复现）
    others = [x for x in base["present_npcs"] if x != driver.target and not x.startswith("player:")]
    reject_cases = [
        ("not_enough_money", {"target": driver.target, "gift_money": base["wallet"] + 100}, "not_enough_money"),
        ("gift_missing", {"target": driver.target, "gift_item": "coarse_cloth", "gift_qty": 2}, "gift_missing"),
        ("bond_too_low", {"target": others[0]}, "bond_too_low"),
    ]
    driver.records["rejections"] = []
    for name, payload, expected in reject_cases:
        if name == "bond_too_low":
            if not others:
                continue
            # 先建立 acquaintanceship（默认 trust=40），好感仍低，用于复现 bond_too_low。
            driver.action("talk", {"target": others[0], "message": "多谢关照，改日再来叨扰。"})
        resp = driver.action("marriage_propose", payload)
        after = driver.state()
        other_rel = [
            r for r in (after.get("player", {}).get("relations") or [])
            if r.get("target") == payload.get("target")
        ]
        row = {
            "case": name,
            "tick": int(resp.get("tick") or 0),
            "request": payload,
            "ok": bool(resp.get("ok")),
            "reject_reason": str(resp.get("reject_reason") or ""),
            "expected": expected,
            "wallet_after": driver._wallet(after),
            "marriage_after": after["player"].get("marriage"),
            "target_relations": other_rel,
            "reconciliation_after": after.get("reconciliation"),
        }
        driver.records["rejections"].append(row)
        driver.log("reject_sample", **row)
        if row["reject_reason"] != expected:
            raise SystemExit(f"reject sample {name} got {row['reject_reason']} != {expected}")

    # 2) 提高好感：每日对话（服务端每次对话 +6 tick；每日上限由 TalkAction 控制）
    start_day = int(st["tick"]) // TICKS_PER_DAY
    day = start_day
    talks_today = 0
    days: list[dict] = []
    driver.records["courtship_days"] = days
    last_day_snapshot = None
    while True:
        st = driver.state()
        if not st.get("ok"):
            driver.enter()
            st = driver.state()
        tick = int(st["tick"])
        cur_day = tick // TICKS_PER_DAY
        aff, trust = driver._aff(st)
        if cur_day != day:
            if last_day_snapshot is not None:
                days.append(last_day_snapshot)
                driver.log("courtship_day", **last_day_snapshot)
            last_day_snapshot = {"day": cur_day, "tick": tick, "affection": aff, "trust": trust, "talks": 0}
            day, talks_today = cur_day, 0
        if last_day_snapshot is None:
            last_day_snapshot = {"day": cur_day, "tick": tick, "affection": aff, "trust": trust, "talks": 0}
        if aff >= 60.0 and trust >= 30.0:
            break
        if cur_day - start_day > args.max_days:
            raise SystemExit(f"courtship exceeded {args.max_days} days")
        if talks_today < 6 and driver._present(st, driver.target):
            resp = driver.action(
                "talk",
                {"target": driver.target, "message": f"多谢娘子照拂，今日辛苦您了。第{talks_today + 1}回。"},
            )
            talks_today += 1
            last_day_snapshot["talks"] = talks_today
            last_day_snapshot["affection"], last_day_snapshot["trust"] = driver._aff(driver.state())
            driver.log("talk", tick=int(resp.get("tick") or 0), ok=bool(resp.get("ok")),
                       reject_reason=str(resp.get("reject_reason") or ""),
                       affection=last_day_snapshot["affection"], trust=last_day_snapshot["trust"])
            continue
        driver._keep_online()
        time.sleep(0.5)
    if last_day_snapshot is not None:
        days.append(last_day_snapshot)
        driver.log("courtship_day", **last_day_snapshot)
    driver.collect()  # 丢弃拒绝样例事件，避免混入求婚证据

    # 3) 求婚（聘礼 50 文 + 炊饼一枚），等目标同地点
    if not driver._wait(lambda: driver._present(driver.state(), driver.target), 240, "wait target"):
        raise SystemExit("target never present for proposal")
    before = driver.state()
    wallet_before = driver._wallet(before)
    resp = driver.action(
        "marriage_propose",
        {"target": driver.target, "gift_money": 50, "gift_item": "flatbread", "gift_qty": 1},
    )
    events = driver.collect()
    after = driver.state()
    proposal = {
        "tick": int(resp.get("tick") or 0),
        "next_tick": int(resp.get("next_tick") or 0),
        "ok": bool(resp.get("ok")),
        "reject_reason": str(resp.get("reject_reason") or ""),
        "details": resp.get("details"),
        "wallet_before": wallet_before,
        "wallet_after": driver._wallet(after),
        "marriage": after["player"].get("marriage"),
        "events": [
            {"seq": e["seq"], "tick": e["tick"], "actor": e["actor"], "verb": e["verb"],
             "outcome": e["outcome"], "reason": e["reason"], "args": e["args"],
             "effects": [x for x in e["effects"] if x.get("op") in ("transfer", "item_move", "marry", "marriage_meta")]}
            for e in events if e["verb"].startswith("marriage")
        ],
    }
    driver.records["proposal"] = proposal
    driver.log("propose", **{k: proposal[k] for k in ("tick", "next_tick", "ok", "reject_reason", "wallet_before", "wallet_after", "marriage")})
    for ev in proposal["events"]:
        driver.log("propose_event", seq=ev["seq"], tick=ev["tick"], actor=ev["actor"], verb=ev["verb"], outcome=ev["outcome"], reason=ev["reason"], effects=ev["effects"])
    if not proposal["ok"] or (proposal["marriage"] or {}).get("status") != "married":
        raise SystemExit(f"proposal not accepted: {proposal}")

    married_tick = int((proposal["marriage"] or {}).get("since_tick") or proposal["tick"])
    married_day = married_tick // TICKS_PER_DAY

    # 4) 婚后 3 个世界日：观察同地点日常
    daily: list[dict] = []
    driver.records["daily"] = daily
    observed_days = set()
    seen_seq = driver.last_event_seq
    talk_replies: list[dict] = []
    while len(observed_days) < 3:
        st = driver.state()
        tick = int(st["tick"])
        cur_day = tick // TICKS_PER_DAY
        new_events = driver.collect()
        for e in new_events:
            if e["verb"] != "spouse_time" or e["outcome"] != "ok":
                continue
            note = ""
            for eff in e["effects"]:
                if eff.get("op") == "memory_add":
                    note = str((eff.get("data") or {}).get("text") or "")
            d = e["tick"] // TICKS_PER_DAY
            observed_days.add(d)
            row = {"day": d, "tick": e["tick"], "actor": e["actor"], "outcome": e["outcome"],
                   "reason": e["reason"], "note": note, "seq": e["seq"]}
            daily.append(row)
            driver.log("spouse_time", **row)
            st2 = driver.state()
            if driver._present(st2, driver.target):
                talked = driver.action("talk", {"target": driver.target, "message": "娘子今日可还顺当？"})
                reply = str((talked.get("details") or {}).get("reply") or "")
                talk_replies.append({"tick": int(talked.get("tick") or 0), "day": int(talked.get("tick") or 0) // TICKS_PER_DAY,
                                     "ok": bool(talked.get("ok")), "reply": reply})
                driver.log("talk_spouse", **talk_replies[-1])
        if cur_day > married_day + 6:
            raise SystemExit("no spouse_time in 3 days")
        driver._keep_online()
        time.sleep(0.5)

    # 5) 重婚拒绝（必须在配偶同地点）
    if not driver._wait(lambda: driver._present(driver.state(), driver.target), 240, "wait spouse"):
        raise SystemExit("spouse not present for remarriage check")
    remarry = driver.action("marriage_propose", {"target": driver.target, "gift_money": 0})
    st_after = driver.state()
    row = {"tick": int(remarry.get("tick") or 0), "ok": bool(remarry.get("ok")),
           "reject_reason": str(remarry.get("reject_reason") or ""),
           "wallet_after": driver._wallet(st_after),
           "marriage": st_after["player"].get("marriage")}
    driver.records["already_married"] = row
    driver.log("reject_already_married", **row)
    if row["reject_reason"] != "already_married":
        raise SystemExit(f"already_married sample failed: {row}")

    # 6) 和离
    if not driver._wait(lambda: driver._present(driver.state(), driver.target), 240, "wait spouse"):
        raise SystemExit("spouse not present for divorce")
    before = driver.state()
    wallet_before = driver._wallet(before)
    divorce = driver.action("marriage_end", {})
    events = driver.collect()
    after = driver.state()
    div = {
        "tick": int(divorce.get("tick") or 0),
        "ok": bool(divorce.get("ok")),
        "reject_reason": str(divorce.get("reject_reason") or ""),
        "wallet_before": wallet_before,
        "wallet_after": driver._wallet(after),
        "marriage": after["player"].get("marriage"),
        "events": [
            {"seq": e["seq"], "tick": e["tick"], "actor": e["actor"], "verb": e["verb"],
             "outcome": e["outcome"], "reason": e["reason"],
             "effects": [x for x in e["effects"] if x.get("op") in ("set_field", "relation_upsert", "marriage_meta")]}
            for e in events if e["verb"] in ("marriage_end", "marriage_meta")
        ],
    }
    driver.records["divorce"] = div
    driver.log("divorce", **{k: div[k] for k in ("tick", "ok", "reject_reason", "wallet_before", "wallet_after", "marriage")})
    for ev in div["events"]:
        driver.log("divorce_event", seq=ev["seq"], tick=ev["tick"], actor=ev["actor"], verb=ev["verb"], outcome=ev["outcome"], effects=ev["effects"])

    # 7) 收尾审计：对账 / seq / 事件计数
    final = driver.state()
    all_events = driver.events(0)
    seqs = [e["seq"] for e in all_events]
    verbs: dict[str, int] = {}
    for e in all_events:
        verbs[e["verb"]] = verbs.get(e["verb"], 0) + 1
    marriages = [
        e for e in all_events
        if e["verb"] in ("marriage_propose", "marriage_accept", "marriage_reject", "marriage", "marriage_end", "spouse_time")
    ]
    audit = {
        "tick": int(final["tick"]),
        "wallet": driver._wallet(final),
        "reconciliation": final.get("reconciliation"),
        "seq_first": seqs[0] if seqs else 0,
        "seq_last": seqs[-1] if seqs else 0,
        "seq_count": len(seqs),
        "seq_contiguous": bool(seqs) and seqs[0] == 1 and seqs == list(range(1, seqs[-1] + 1)),
        "verbs": dict(sorted(verbs.items())),
        "marriage_event_count": len(marriages),
        "marriage_events": [
            {"seq": e["seq"], "tick": e["tick"], "actor": e["actor"], "verb": e["verb"],
             "outcome": e["outcome"], "reason": e["reason"]}
            for e in marriages
        ],
        "talk_replies": talk_replies,
        "daily": daily,
    }
    driver.records["audit"] = audit
    driver.log("audit", **{k: audit[k] for k in ("tick", "wallet", "reconciliation", "seq_first", "seq_last", "seq_count", "seq_contiguous", "marriage_event_count")})

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(driver.records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[driver] wrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
