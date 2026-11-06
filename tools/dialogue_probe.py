#!/usr/bin/env python3
"""NPC 对话多样性探针：真实账号 5 NPC × 3 轮，保存请求/响应原文并计算指标。

用法：
    python3 tools/dialogue_probe.py --account qa_dialogue_01 --display 问话人 \
        --out reports/npc-dialogue-variety-before.json

只读玩家 API，不写世界状态；每次 talk 前重新 enter，避免 presence 超时。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

MSG = {
    1: "你好，今日可好？",
    2: "幸会，近来可好？",
    3: "听说镇上最近有新鲜事？",
}
ROUND_INTENT = {1: "greeting", 2: "greeting", 3: "gossip"}
NPC_IDS = ("duan_wudou", "jin_mancang", "liu_sanniang", "meng_jiuniang", "ruan_qingfu")


def _post(base: str, path: str, payload: dict, token: str = "") -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base + path, data=data, method="POST",
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        return {"http_error": exc.code, "body": body}


def _get(base: str, path: str, token: str) -> dict:
    req = urllib.request.Request(base + path, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _auth(base: str, account: str, password: str) -> str:
    _post(base, "/api/auth/register", {"username": account, "password": password})
    out = _post(base, "/api/auth/login", {"username": account, "password": password})
    token = str(out.get("token") or "")
    if not token:
        raise SystemExit(f"login failed: {out}")
    return token


def _enter(base: str, token: str, account: str, display: str, archetype: str = "游侠") -> dict:
    return _post(base, "/api/player/enter", {
        "account_id": account, "display_name": display, "archetype": archetype,
    }, token)


def _lcs_len(strings: list[str]) -> int:
    """所有字符串的最长公共子串长度（两两归约）。"""
    if not strings:
        return 0
    common = strings[0]
    for other in strings[1:]:
        best = ""
        for i in range(len(common)):
            for j in range(i + 1, len(common) + 1):
                if common[i:j] in other and len(common[i:j]) > len(best):
                    best = common[i:j]
        common = best
        if not common:
            return 0
    return len(common)


def _metrics(rows: list[dict]) -> dict:
    replies = [r["reply"] for r in rows]
    unique = len(set(replies))
    same_npc_consecutive = 0
    for npc in {r["npc"] for r in rows}:
        chain = [r["reply"] for r in rows if r["npc"] == npc]
        same_npc_consecutive += sum(1 for a, b in zip(chain, chain[1:]) if a == b)
    same_intent_cross = 0
    intent_unique: dict[str, int] = {}
    intent_lcs: dict[str, int] = {}
    for rnd in (1, 2, 3):
        group = [r["reply"] for r in rows if r["round"] == rnd]
        intent = next(r["intent"] for r in rows if r["round"] == rnd)
        intent_unique[intent] = len(set(group))
        intent_lcs[intent] = _lcs_len(group)
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                same_intent_cross += int(group[i] == group[j])
    model_rows = sum(1 for r in rows if r.get("source") == "model")
    template_rows = sum(1 for r in rows if r.get("source") == "template")
    return {
        "replies": len(replies),
        "unique_replies": unique,
        "unique_rate": round(unique / len(replies), 4) if replies else 0.0,
        "same_npc_consecutive_identical": same_npc_consecutive,
        "same_intent_cross_npc_identical": same_intent_cross,
        "longest_common_substring_global": _lcs_len(replies),
        "longest_common_substring_by_intent": intent_lcs,
        "longest_common_substring_by_intent_max": max(intent_lcs.values() or [0]),
        "unique_replies_by_intent": intent_unique,
        "template_hit": template_rows,
        "template_hit_rate": round(template_rows / len(rows), 4) if rows else 0.0,
        "model_generated": model_rows,
        "model_generated_rate": round(model_rows / len(rows), 4) if rows else 0.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8031")
    ap.add_argument("--account", default="qa_dialogue_01")
    ap.add_argument("--password", default="qa-secret-2026")
    ap.add_argument("--display", default="问话人")
    ap.add_argument("--out", default="reports/npc-dialogue-variety.json")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    token = _auth(base, args.account, args.password)
    enter = _enter(base, token, args.account, args.display)
    if not enter.get("ok") and enter.get("reject_reason") != "already_online":
        raise SystemExit(f"enter failed: {enter}")
    state = _get(base, "/api/player/state", token)
    nearby = {str(n.get("id")) for n in (state.get("nearby") or [])}
    missing = [n for n in NPC_IDS if n not in nearby]
    if missing:
        raise SystemExit(f"NPC 不在当前地点: {missing}; nearby={sorted(nearby)}")

    rows: list[dict] = []
    seq = 0
    for npc in NPC_IDS:
        for rnd in (1, 2, 3):
            seq += 1
            # presence 会超时，每轮前重新进入（幂等）。
            _enter(base, token, args.account, args.display)
            payload = {
                "action": "talk", "target": npc, "message": MSG[rnd],
                "request_id": f"{args.account}-probe-{seq}",
            }
            resp = _post(base, "/api/player/action", payload, token)
            details = resp.get("details") or {}
            reply = str(details.get("reply") or "").strip()
            rows.append({
                "seq": seq, "npc": npc, "round": rnd, "intent": ROUND_INTENT[rnd],
                "request": payload, "reply": reply,
                "source": str(details.get("dialogue_source") or "template"),
                "ok": bool(resp.get("ok")), "status": resp.get("status"),
                "reject_reason": resp.get("reject_reason"), "tick": resp.get("tick"),
                "event_seq": resp.get("event_seq"), "intent_id": resp.get("intent_id"),
                "response": resp,
            })
            print(f"[{npc} r{rnd}] {reply}")

    out = {
        "run_probe": {"base": base, "account": args.account, "npc_ids": list(NPC_IDS)},
        "metrics": _metrics(rows),
        "rows": rows,
    }
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out["metrics"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
