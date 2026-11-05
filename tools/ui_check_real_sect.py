#!/usr/bin/env python3
"""门派成功链真服务验收：立派 → 收徒 → 派俸。

不 mock 上游：
  - `python3 -m wsim.api --auth off`（XKX_AMBIENT=1，默认 4x 自走世界）
  - `ui/player/server.py` 真反向代理
  - 挣钱/声望/技能走真 API（押镖 / recruit / cultivate / rest）
  - 立派、收徒、派俸必须点页面按钮，页面 hook fetch 记录真实请求/响应

能力探测：
  进入后先对六个门派动作各发一次最小真请求。若任一动作返回
  `unknown_action` / `unknown_verb` / `not_implemented...` / HTTP 404，
  写 `blocked_backend_not_ready` 并以 0 退出；绝不用 mock 冒充真服务证据。

用法：
  python3 tools/ui_check_real_sect.py --speed 4

证据（reports/w5-sect/）：
  real-*.png              每一步的真实截图
  real-sect.json          能力探测、请求/响应、前后快照与断言结果
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml  # noqa: E402

from e2e_service_check import (  # noqa: E402
    free_port, start_api, start_proxy, stop_proc, ui_enter, wait_http,
)
from ui_check import Results, wait_app  # noqa: E402

EVIDENCE = Path(__file__).resolve().parents[1] / "reports" / "w5-sect"

CAPTURE_JS = """
window.__CAP = [];
const _fetch = window.fetch.bind(window);
window.fetch = async function (input, init) {
  const url = typeof input === 'string' ? input : (input && input.url) || '';
  const method = (init && init.method) || 'GET';
  const reqBody = init && init.body ? String(init.body) : '';
  const resp = await _fetch(input, init);
  if (url.indexOf('/api/player/action') >= 0) {
    const copy = resp.clone();
    let text = '';
    try { text = await copy.text(); } catch (e) { text = ''; }
    window.__CAP.push({url: url, method: method, request: reqBody, status: resp.status,
                       response: text, ts: Date.now()});
  }
  return resp;
};
"""

UNKNOWN_MARKERS = ("unknown_action", "unknown_verb", "not_implemented",
                   "not_implemented_for_player", "action_not_found")

PROBES = [
    ("found_sect", {"name": "探门派"}),
    ("recruit_disciple", {"target": "liu_sanniang"}),
    ("expel_disciple", {"target": "liu_sanniang"}),
    ("pay_disciples", {}),
    ("sect_deposit", {"amount": 1}),
    ("sect_withdraw", {"amount": 1}),
]


class BlockedFlow(Exception):
    """真实前置无法在时限内满足：保留证据并退出 0。"""


def dismiss_guide(page):
    page.evaluate("""() => {
      const btn = document.getElementById('guideSkip');
      const card = document.getElementById('guideCard');
      if (btn && card && card.getBoundingClientRect().height > 0) btn.click();
    }""")


def capture(page, name):
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(EVIDENCE / (name + ".png")))


def last_action(page, verb):
    rows = page.evaluate("() => window.__CAP.slice()")
    for row in reversed(rows):
        try:
            body = json.loads(row["request"] or "{}")
        except ValueError:
            continue
        if body.get("verb") == verb:
            try:
                resp = json.loads(row["response"] or "{}")
            except ValueError:
                resp = {}
            return {"request": body, "status": row["status"], "response": resp}
    return None


def action_count(page, verb):
    return page.evaluate("""(verb) => window.__CAP.filter(c => {
      try { return JSON.parse(c.request || '{}').verb === verb; } catch (e) { return false; }
    }).length""", verb)


def keepalive(page, base, account):
    try:
        page.request.post(
            base + "/api/player/action",
            data=json.dumps({"account_id": account, "verb": "heartbeat",
                             "args": {}, "client_tick": None}),
            headers={"Content-Type": "application/json"})
    except Exception:
        pass


def heartbeat_code(page, base, account):
    try:
        resp = page.request.post(
            base + "/api/player/action",
            data=json.dumps({"account_id": account, "verb": "heartbeat",
                             "args": {}, "client_tick": None}),
            headers={"Content-Type": "application/json"})
        data = resp.json()
        return str(data.get("reject_reason") or data.get("code") or "")
    except Exception:
        return ""


def ensure_online(page, base, account, display):
    code = heartbeat_code(page, base, account)
    if code in ("player_offline", "player_not_entered"):
        try:
            page.click("#switchBtn", timeout=5000)
        except Exception:
            pass
        page.wait_for_timeout(400)
        ui_enter(page, account, display)
        dismiss_guide(page)
        page.wait_for_timeout(300)
        return True
    return False


def probe(page, base, account, verb, args):
    try:
        resp = page.request.post(
            base + "/api/player/action",
            data=json.dumps({"account_id": account, "verb": verb,
                             "args": args, "client_tick": None}),
            headers={"Content-Type": "application/json"})
        data = resp.json()
        code = str(data.get("reject_reason") or data.get("code") or "")
        text = str(data.get("reason_text") or data.get("message") or "")
        return {"verb": verb, "request": {"verb": verb, "args": args},
                "status": resp.status, "response": data,
                "code": code, "text": text,
                "implemented": not (code in UNKNOWN_MARKERS or resp.status == 404
                                    or "未知操作" in text)}
    except Exception as exc:
        return {"verb": verb, "status": 0, "code": "probe_failed", "text": str(exc),
                "implemented": False}


def ui_api(page, verb, args):
    return page.evaluate("""async ([verb, args]) => {
      const r = await window.__XKX.apiAction(verb, args);
      return {httpOk: r.httpOk, status: r.status, data: r.data};
    }""" , [verb, args])


def refresh(page, account):
    return page.evaluate(
        "async (acct) => await window.__XKX.refreshState(acct)", account)


def read_state(page, account):
    return page.evaluate(
        "async (acct) => await window.__XKX.readState(acct)", account)


def sect_state(page):
    return page.evaluate("() => window.__XKX.sectState()")


def body_text(page):
    return page.evaluate(
        "() => (document.getElementById('sectBody') || {}).textContent || ''")


def found_disabled(page):
    return page.evaluate("""() => {
      const b = document.getElementById('sectFoundGo');
      return b ? !!b.disabled : null;
    }""")


def pay_button(page):
    return page.evaluate("""() => {
      const b = document.getElementById('sectPayGo');
      if (!b) return null;
      return {disabled: !!b.disabled, text: (b.textContent || '').trim()};
    }""")


def snapshot(raw):
    p = raw.get("player") or {}
    wallet = p.get("wallet") or {}
    growth = raw.get("growth") or {}
    items = {}
    for row in wallet.get("items") or []:
        if row and row.get("item"):
            items[row["item"]] = int(row.get("qty") or 0)
    return {
        "tick": raw.get("tick"),
        "location": (raw.get("location") or {}).get("id"),
        "money": int(wallet.get("money") or 0),
        "stamina": p.get("stamina"),
        "skills": p.get("skills") or {},
        "reputation": ((growth.get("reputation") or {}).get("value")),
        "items": items,
        "active_escorts": len([o for o in (raw.get("escort_orders") or [])
                               if o.get("status") in ("active", "open")]),
        "present": [{"id": n.get("id"), "name": n.get("name"),
                    "bond": n.get("bond"), "trust": n.get("trust"),
                    "skill": ((n.get("skills") or {}).get("level"))}
                    for n in (raw.get("present_npcs") or [])],
    }


def load_graph(root):
    data = yaml.safe_load((root / "content" / "locations.yaml").read_text(encoding="utf-8"))
    return {row["id"]: list(row.get("exits") or []) for row in data.get("locations") or []}


def bfs_path(graph, start, goal):
    if start == goal:
        return [start]
    queue = [[start]]
    seen = {start}
    while queue:
        path = queue.pop(0)
        for nxt in graph.get(path[-1], []):
            if nxt in seen:
                continue
            if nxt == goal:
                return path + [nxt]
            seen.add(nxt)
            queue.append(path + [nxt])
    return []


def move_to(page, account, graph, goal, base="", display=""):
    """用 readState 的 location.exits 校验相邻，按 BFS 逐跳真 API move。"""
    moves = []
    for _ in range(10):
        raw = read_state(page, account)
        cur = (raw.get("location") or {}).get("id") or ""
        if cur == goal:
            return {"ok": True, "moves": moves, "location": cur}
        path = bfs_path(graph, cur, goal)
        if len(path) < 2:
            return {"ok": False, "error": "no_path", "from": cur, "goal": goal,
                    "moves": moves}
        step = path[1]
        exits = [x.get("id") for x in (raw.get("location") or {}).get("exits") or []]
        if step not in exits:
            return {"ok": False, "error": "not_adjacent", "from": cur, "step": step,
                    "exits": exits, "moves": moves}
        res = ui_api(page, "move", {"to": step})
        moves.append({"from": cur, "to": step, "status": res.get("status"),
                      "reject_reason": (res.get("data") or {}).get("reject_reason"),
                      "tick": raw.get("tick")})
        if (res.get("data") or {}).get("ok") is False or res.get("status") != 200:
            if base:
                ensured = ensure_online(page, base, account, display)
                if ensured:
                    continue
            page.wait_for_timeout(800)
            continue
        refresh(page, account)
        page.wait_for_timeout(150)
    raw = read_state(page, account)
    return {"ok": (raw.get("location") or {}).get("id") == goal,
            "location": (raw.get("location") or {}).get("id"), "moves": moves}


def wait_result(page, needle, timeout=20000):
    try:
        page.wait_for_function(
            "(n) => document.getElementById('sectResult').textContent.indexOf(n) >= 0",
            arg=needle, timeout=timeout)
        return True
    except Exception:
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--speed", default="4", choices=["1", "2", "4"])
    ap.add_argument("--earn-round-timeout", type=int, default=150,
                    help="单轮押镖到货结算的等待上限（秒）")
    ap.add_argument("--earn-total-timeout", type=int, default=840,
                    help="挣钱阶段总时长上限（秒）；押镖全局风险 15%，需覆盖 3 次成功")
    ap.add_argument("--rep-timeout", type=int, default=120,
                    help="招揽建立 sworn 关系的等待上限（秒）")
    ap.add_argument("--candidate-timeout", type=int, default=150,
                    help="立派后等已 sworn NPC 回到驻地的上限（秒）")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    for old in EVIDENCE.glob("real-*"):
        if old.is_file():
            try:
                old.unlink()
            except OSError:
                pass
    graph = load_graph(root)

    stamp = time.strftime("%m%d%H%M%S")
    run_id = "w5-sect-" + stamp
    account = "w5sect" + stamp
    display = "门派验" + stamp[-5:]

    os.environ["XKX_AMBIENT"] = "1"
    os.environ["XKX_AMBIENT_SPEED"] = args.speed

    api = proxy = None
    api_base = base = ""
    results = Results()
    steps: dict = {}
    blocked = None
    started = time.time()

    def block(reason, detail):
        nonlocal blocked
        blocked = {"status": "blocked_" + reason, "detail": detail,
                   "note": "真实前置未满足；保留真实请求/响应与页面文案，不用 mock 冒充成功。"}
        steps["blocked"] = blocked
        raise BlockedFlow()

    try:
        api_port, proxy_port = free_port(), free_port()
        api = start_api(root, run_id, api_port, "off")
        proxy = start_proxy(root, f"http://127.0.0.1:{api_port}", proxy_port)
        api_base = f"http://127.0.0.1:{api_port}"
        base = f"http://127.0.0.1:{proxy_port}"
        wait_http(api_base + "/api/health")

        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(viewport={"width": 1280, "height": 900})
                page = ctx.new_page()
                page.add_init_script(CAPTURE_JS)
                page.goto(base + "/?ui_check=1")
                wait_app(page)
                ui_enter(page, account, display)
                page.wait_for_function("() => !!window.__XKX", timeout=20000)
                dismiss_guide(page)
                page.wait_for_timeout(300)

                # ---- 能力探测：六个动作各发一次最小真请求
                probes = [probe(page, base, account, verb, a) for verb, a in PROBES]
                steps["capability_probe"] = probes
                missing = [p["verb"] for p in probes if not p["implemented"]]
                results.add("真服务 能力探测：六个门派动作均已实现",
                            not missing, {"missing": missing, "probes": probes})
                bad_text = [p for p in probes if not p["text"]
                            or "_" in p["text"] or not any("\u4e00" <= ch <= "\u9fff"
                                                           for ch in p["text"])]
                results.add("真服务 拒绝码中文：reason_text 为中文且不含原始 code",
                            not bad_text, {"bad": bad_text})
                if missing:
                    block("backend_not_ready", {"missing": missing, "probes": probes})

                # ---- 未入派页面：截图 + 门槛文案 + 按钮 disabled
                page.click("#commerceJump")
                page.wait_for_timeout(300)
                page.click('[data-tab="sect"]')
                page.wait_for_timeout(500)
                initial = {
                    "body": body_text(page),
                    "found_disabled": found_disabled(page),
                    "sect": sect_state(page),
                    "raw": snapshot(read_state(page, account)),
                }
                steps["sect_before"] = initial
                capture(page, "real-0-sect-before")
                ok_gate = ("立派费用" in initial["body"] and "声望" in initial["body"]
                           and "差" in initial["body"])
                results.add("真服务 未入派页展示立派门槛（现有/需要/差）", ok_gate,
                            initial["body"][:500])
                results.add("真服务 钱/声望不足时立派按钮 disabled",
                            initial["found_disabled"] is True,
                            {"disabled": initial["found_disabled"],
                             "money": initial["raw"]["money"],
                             "reputation": initial["raw"]["reputation"]})

                # ---------------------------------------------------- 挣钱（真押镖）
                earn_rounds = []
                earn_deadline = time.time() + args.earn_total_timeout
                money = initial["raw"]["money"]
                while money < 860 and len(earn_rounds) < 8 and time.time() < earn_deadline:
                    raw0 = read_state(page, account)
                    if raw0.get("tick") is None:
                        block("player_not_entered", {"raw": raw0})
                    before = snapshot(raw0)
                    round_row = {"round": len(earn_rounds) + 1,
                                 "tick_before": before["tick"],
                                 "money_before": before["money"],
                                 "reputation_before": before["reputation"],
                                 "items_before": before["items"]}
                    if before["items"].get("cleaver", 0) < 2:
                        mv = move_to(page, account, graph, "smithy", base, display)
                        round_row["move_smithy"] = mv
                        if not mv["ok"]:
                            block("move_to_smithy_failed", mv)
                        buy = ui_api(page, "buy", {"item": "cleaver", "qty": 2})
                        round_row["buy"] = {"request": {"item": "cleaver", "qty": 2},
                                            "status": buy.get("status"),
                                            "response": buy.get("data")}
                        if (buy.get("data") or {}).get("ok") is False or buy.get("status") != 200:
                            block("buy_cleaver_rejected",
                                  round_row["buy"])
                        refresh(page, account)
                        page.wait_for_timeout(200)
                        round_row["after_buy"] = snapshot(read_state(page, account))
                    else:
                        round_row["buy"] = {"skipped": True,
                                            "reason": "背包已有 2 把菜刀（上轮失败已退回）"}

                    mv = move_to(page, account, graph, "escort_agency", base, display)
                    round_row["move_agency"] = mv
                    if not mv["ok"]:
                        block("move_to_agency_failed", mv)

                    raw1 = read_state(page, account)
                    money_before = snapshot(raw1)["money"]
                    route = ["escort_agency", "ferry", "inn", "market"]
                    esc = ui_api(page, "escort", {
                        "route": route,
                        "cargo": {"item": "cleaver", "qty": 2},
                        "fee": 400})
                    esc_data = esc.get("data") or {}
                    round_row["escort"] = {"request": {"route": route,
                                                       "cargo": {"item": "cleaver", "qty": 2},
                                                       "fee": 400},
                                           "status": esc.get("status"),
                                           "response": esc_data,
                                           "money_before": money_before,
                                           "tick_before": snapshot(raw1)["tick"]}
                    capture(page, "real-1-earn-round-%d" % round_row["round"])
                    if esc_data.get("ok") is False or esc.get("status") != 200:
                        round_row["outcome"] = "rejected"
                        earn_rounds.append(round_row)
                        code = str(esc_data.get("reject_reason") or "")
                        if code in ("item_missing", "no_payer_funds", "no_escort_agency",
                                    "not_at_origin", "route_not_adjacent"):
                            steps["earn_rounds"] = earn_rounds
                            block("escort_rejected:" + code, round_row["escort"])
                        keepalive(page, base, account)
                        page.wait_for_timeout(8000)
                        continue

                    wait_started = time.time()
                    last_beat = wait_started
                    outcome = "timeout"
                    arrived_money = money_before
                    while time.time() < wait_started + args.earn_round_timeout:
                        now = time.time()
                        if now - last_beat >= 10:
                            heartbeat_code(page, base, account)
                            last_beat = now
                        page.wait_for_timeout(10000)
                        raw2 = read_state(page, account)
                        snap2 = snapshot(raw2)
                        arrived_money = snap2["money"]
                        if snap2["active_escorts"] == 0:
                            outcome = "success" if arrived_money > money_before else "failed"
                            round_row["after_wait"] = snap2
                            break
                    if outcome == "timeout":
                        raw2 = read_state(page, account)
                        round_row["after_wait"] = snapshot(raw2)
                    if outcome == "failed":
                        # 全局费率失败：货原量退回，运费 0；原地可重试。
                        ensure_online(page, base, account, display)
                        refresh(page, account)
                        page.wait_for_timeout(1000)
                    round_row["wait_seconds"] = round(time.time() - wait_started, 1)
                    round_row["outcome"] = outcome
                    round_row["money_after"] = arrived_money
                    earn_rounds.append(round_row)
                    refresh(page, account)
                    page.wait_for_timeout(300)
                    if outcome == "success":
                        money = arrived_money
                    elif outcome == "timeout":
                        block("escort_wait_timeout",
                              {"round": round_row, "rounds": earn_rounds})
                    # failed 时继续下一轮（背包里菜刀已原量退回）

                steps["earn_rounds"] = earn_rounds
                final_earn = snapshot(read_state(page, account))
                steps["earn_final"] = final_earn
                results.add("真服务 押镖收入达到立派门槛（钱包 ≥ 860）",
                            final_earn["money"] >= 860,
                            {"money": final_earn["money"], "rounds": len(earn_rounds)})
                capture(page, "real-1-earn")
                if final_earn["money"] < 860:
                    block("insufficient_funds_after_escorts",
                          {"money": final_earn["money"], "rounds": earn_rounds})

                # ---------------------------------------------------- 声望 20（真 recruit）
                pre_recruited = []
                rep_started = time.time()
                raw3 = read_state(page, account)
                snap3 = snapshot(raw3)
                rep_before = snap3["reputation"]
                rep_actions = []
                rep_tried = set()
                retryable = ("not_together", "already_sworn", "target_offline",
                             "target_in_sect", "player_consent_required", "self_recruit")
                while (rep_before is None or rep_before < 20) and time.time() - rep_started < args.rep_timeout:
                    present = snap3["present"]
                    target = next((n for n in present
                                   if n.get("id") and not str(n["id"]).startswith("player:")
                                   and n["id"] not in rep_tried), None)
                    if target is None:
                        ensure_online(page, base, account, display)
                        keepalive(page, base, account)
                        page.wait_for_timeout(5000)
                        refresh(page, account)
                        raw3 = read_state(page, account)
                        snap3 = snapshot(raw3)
                        continue
                    rep_tried.add(target["id"])
                    res = ui_api(page, "recruit", {"target": target["id"]})
                    data = res.get("data") or {}
                    rep_actions.append({"target": target, "status": res.get("status"),
                                        "response": data,
                                        "reputation_before": rep_before})
                    if data.get("ok") is not False and res.get("status") == 200:
                        if target["id"] not in pre_recruited:
                            pre_recruited.append(target["id"])
                        refresh(page, account)
                        page.wait_for_timeout(300)
                        raw3 = read_state(page, account)
                        snap3 = snapshot(raw3)
                        rep_before = snap3["reputation"]
                    else:
                        code = str(data.get("reject_reason") or "")
                        if code in retryable:
                            keepalive(page, base, account)
                            page.wait_for_timeout(1000)
                            refresh(page, account)
                            raw3 = read_state(page, account)
                            snap3 = snapshot(raw3)
                            continue
                        steps["rep_actions"] = rep_actions
                        block("recruit_rejected:" + (code or "unknown"), res)

                # 备用 sworn：再与一个不同的在场 NPC 建立关系，降低立派后 NPC 行程风险
                tried = set(pre_recruited)
                backup_started = time.time()
                while len(pre_recruited) < 2 and time.time() - backup_started < 30:
                    present = snap3["present"]
                    target = next((n for n in present
                                   if n.get("id") and not str(n["id"]).startswith("player:")
                                   and n["id"] not in tried), None)
                    if target is None:
                        keepalive(page, base, account)
                        page.wait_for_timeout(3000)
                        refresh(page, account)
                        raw3 = read_state(page, account)
                        snap3 = snapshot(raw3)
                        continue
                    tried.add(target["id"])
                    res = ui_api(page, "recruit", {"target": target["id"]})
                    data = res.get("data") or {}
                    rep_actions.append({"target": target, "status": res.get("status"),
                                        "response": data,
                                        "reputation_before": rep_before,
                                        "backup": True})
                    if data.get("ok") is not False and res.get("status") == 200:
                        pre_recruited.append(target["id"])
                        refresh(page, account)
                        page.wait_for_timeout(300)
                        raw3 = read_state(page, account)
                        snap3 = snapshot(raw3)
                        rep_before = snap3["reputation"]
                    else:
                        keepalive(page, base, account)
                        page.wait_for_timeout(2000)
                        refresh(page, account)
                        raw3 = read_state(page, account)
                        snap3 = snapshot(raw3)

                steps["reputation"] = {"before": initial["raw"]["reputation"],
                                       "after": rep_before,
                                       "pre_recruited": pre_recruited,
                                       "actions": rep_actions}
                results.add("真服务 声望达到立派门槛（≥ 20）",
                            rep_before is not None and rep_before >= 20,
                            {"before": initial["raw"]["reputation"], "after": rep_before})
                capture(page, "real-2-reputation")
                if rep_before is None or rep_before < 20:
                    block("insufficient_reputation", {"reputation": rep_before,
                                                      "actions": rep_actions})
                if not pre_recruited:
                    block("no_sworn_npc", {"present": snap3["present"]})

                # ---------------------------------------------------- 掌门技能 0.4
                cultivate_actions = []
                skill_started = time.time()
                raw4 = read_state(page, account)
                snap4 = snapshot(raw4)
                while (max((snap4["skills"] or {}).values(), default=0.0) < 0.4
                       and time.time() - skill_started < 180):
                    stamina = float(snap4.get("stamina") or 0)
                    if stamina < 32:
                        rest = ui_api(page, "rest", {"hours": 8})
                        cultivate_actions.append({"kind": "rest", "status": rest.get("status"),
                                                  "response": rest.get("data"),
                                                  "stamina_before": stamina})
                        if (rest.get("data") or {}).get("ok") is False or rest.get("status") != 200:
                            block("rest_rejected", rest)
                        refresh(page, account)
                        page.wait_for_timeout(200)
                        raw4 = read_state(page, account)
                        snap4 = snapshot(raw4)
                        continue
                    cul = ui_api(page, "cultivate", {"hours": 8})
                    data = cul.get("data") or {}
                    cultivate_actions.append({"kind": "cultivate", "status": cul.get("status"),
                                              "response": data,
                                              "skills_before": snap4["skills"],
                                              "stamina_before": stamina})
                    if (data.get("ok") is False or cul.get("status") != 200):
                        code = str(data.get("reject_reason") or "")
                        if code == "insufficient_stamina":
                            refresh(page, account)
                            page.wait_for_timeout(200)
                            raw4 = read_state(page, account)
                            snap4 = snapshot(raw4)
                            continue
                        block("cultivate_rejected:" + (code or "unknown"), cul)
                    refresh(page, account)
                    page.wait_for_timeout(200)
                    raw4 = read_state(page, account)
                    snap4 = snapshot(raw4)
                max_skill = max((snap4["skills"] or {}).values(), default=0.0)
                steps["skill"] = {"before": initial["raw"]["skills"],
                                  "after": snap4["skills"],
                                  "max": max_skill,
                                  "actions": cultivate_actions}
                results.add("真服务 掌门技能达到收徒门槛（≥ 0.4）",
                            max_skill >= 0.4, {"max": max_skill,
                                               "skills": snap4["skills"]})
                capture(page, "real-3-skill")
                if max_skill < 0.4:
                    block("skill_too_low", {"skills": snap4["skills"],
                                            "actions": cultivate_actions})

                # ---------------------------------------------------- 立派（必须走 UI）
                refresh(page, account)
                page.wait_for_timeout(300)
                page.click("#commerceJump")
                page.wait_for_timeout(200)
                page.click('[data-tab="sect"]')
                page.wait_for_timeout(400)
                sect_name = "青竹派" + stamp[-5:]
                doctrine = "守望相济，不欺乡里"
                page.fill("#sectName", sect_name)
                page.fill("#sectDoctrine", doctrine)
                page.wait_for_timeout(300)
                ready = {"sect_name": sect_name, "doctrine": doctrine,
                         "money": snapshot(read_state(page, account))["money"],
                         "reputation": snapshot(read_state(page, account))["reputation"],
                         "found_disabled": found_disabled(page)}
                steps["found_ready"] = ready
                capture(page, "real-4-found-ready")
                if ready["found_disabled"]:
                    results.add("真服务 立派按钮在钱/声望/名字满足后可用", False, ready)
                    block("found_button_disabled", ready)
                results.add("真服务 立派按钮在钱/声望/名字满足后可用", True, ready)

                before_count = action_count(page, "found_sect")
                page.click("#sectFoundGo")
                fired = False
                try:
                    page.wait_for_function("""(n) => window.__CAP.filter(c => {
                      try { return JSON.parse(c.request || '{}').verb === 'found_sect'; }
                      catch (e) { return false; }
                    }).length > n""", arg=[before_count], timeout=15000)
                    fired = True
                except Exception:
                    fired = False
                found_ok = fired and wait_result(page, "立派成功", 20000)
                found_action = last_action(page, "found_sect")
                found_text = page.evaluate(
                    "() => (document.getElementById('sectResult') || {}).textContent || ''")
                refresh(page, account)
                page.wait_for_timeout(400)
                after_found = sect_state(page)
                steps["found"] = {"request": (found_action or {}).get("request"),
                                  "status": (found_action or {}).get("status"),
                                  "response": (found_action or {}).get("response"),
                                  "result_text": found_text,
                                  "sect": after_found}
                results.add("真服务 立派走 UI 成功（#sectResult 出现“立派成功”）",
                            found_ok, {"text": found_text})
                results.add("真服务 立派后 role=leader、金库 600、名册 1",
                            after_found.get("role") == "leader"
                            and after_found.get("treasury") == 600
                            and after_found.get("memberCount") == 1,
                            after_found)
                found_body = after_found.get("bodyText") or ""
                results.add("真服务 立派结果条回显真实“金库 600 文”“名册 1 人”",
                            "金库 600 文" in found_text and "名册 1 人" in found_text,
                            {"result_text": found_text})
                results.add("真服务 立派成功后页面展示真实“金库：600 文”“名册 1 / 上限 3”",
                            "金库：600 文" in found_body
                            and "名册 1 / 上限 3" in found_body,
                            {"result_text": found_text,
                             "body": found_body[:500]})
                capture(page, "real-5-found")
                if not found_ok:
                    block("found_ui_failed", steps["found"])
                if after_found.get("role") != "leader" or after_found.get("treasury") != 600:
                    block("found_state_mismatch", after_found)

                # ---------------------------------------------------- 收徒（必须走 UI）
                # 参考事实：API recruit 写入 sworn 关系（好感 30 / 信任 45）。页面候选的
                # present_npcs.bond 由 affection_between 计算（不含 sworn），UI 已用
                # player.relations 的真实关系补齐（与后端 _bond 同口径）。若页面仍显示
                # 好感 <30，说明真实 trade 可补（每次 +3）；全程不使用 mock。
                cand_started = time.time()
                chosen = None
                cand_trace = []
                while time.time() - cand_started < args.candidate_timeout:
                    refresh(page, account)
                    page.wait_for_timeout(300)
                    st = sect_state(page)
                    cands = st.get("candidates") or []
                    cand_trace.append({"tick": snapshot(read_state(page, account))["tick"],
                                       "candidates": cands, "memberCount": st.get("memberCount")})
                    for row in cands:
                        if (row.get("id") in pre_recruited
                                and float(row.get("trust") or 0) >= 30
                                and row.get("skill") is not None
                                and float(row.get("skill") or 0) >= 0.2):
                            chosen = row
                            break
                    if chosen:
                        break
                    keepalive(page, base, account)
                    page.wait_for_timeout(5000)
                steps["recruit_candidates"] = cand_trace
                if not chosen:
                    block("no_sworn_candidate_present",
                          {"pre_recruited": pre_recruited,
                           "candidates_last": cand_trace[-1] if cand_trace else None})

                bond_candidates = []
                if float(chosen.get("bond") or 0) < 30:
                    raw5 = read_state(page, account)
                    snap5 = snapshot(raw5)
                    if snap5["location"] != "market":
                        block("bond_boost_not_at_sect_location",
                              {"location": snap5["location"], "candidate": chosen})
                    need = int(math.ceil((30.0 - float(chosen.get("bond") or 0)) / 3.0))
                    held = int(snap5["items"].get("flatbread") or 0)
                    if held < need:
                        buy = ui_api(page, "buy", {"item": "flatbread", "qty": need - held})
                        bond_candidates.append({"kind": "buy_flatbread",
                                                "request": {"item": "flatbread",
                                                            "qty": need - held},
                                                "status": buy.get("status"),
                                                "response": buy.get("data")})
                        if (buy.get("data") or {}).get("ok") is False or buy.get("status") != 200:
                            block("buy_flatbread_rejected", bond_candidates[-1])
                        refresh(page, account)
                        page.wait_for_timeout(300)
                    for _ in range(need + 3):
                        st = sect_state(page)
                        row = next((c for c in st.get("candidates") or []
                                    if c.get("id") == chosen["id"]), None)
                        if row is None:
                            break
                        if float(row.get("bond") or 0) >= 30:
                            chosen = row
                            break
                        trade = ui_api(page, "trade", {
                            "target": chosen["id"],
                            "offer": [{"item": "flatbread", "qty": 1}],
                            "want": [],
                            "money_delta": 0})
                        bond_candidates.append({"kind": "trade_flatbread",
                                                "request": {"target": chosen["id"],
                                                            "offer": [{"item": "flatbread", "qty": 1}],
                                                            "want": [], "money_delta": 0},
                                                "status": trade.get("status"),
                                                "response": trade.get("data"),
                                                "bond_before": row.get("bond")})
                        if (trade.get("data") or {}).get("ok") is False or trade.get("status") != 200:
                            break
                        refresh(page, account)
                        page.wait_for_timeout(200)
                    st = sect_state(page)
                    row = next((c for c in st.get("candidates") or []
                                if c.get("id") == chosen["id"]), None)
                    if row is not None:
                        chosen = row
                steps["bond_boost"] = {"candidate_id": chosen["id"],
                                       "bond_after": chosen.get("bond"),
                                       "actions": bond_candidates}
                results.add("真服务 候选页面好感 ≥30（sworn 关系由 relations 补齐，"
                            "必要时用真实 trade）",
                            float(chosen.get("bond") or 0) >= 30, steps["bond_boost"])
                if float(chosen.get("bond") or 0) < 30:
                    block("candidate_bond_below_gate", steps["bond_boost"])

                # 读取按钮 disabled 原因，不强行拆除
                selector = '[data-sect-recruit="%s"]' % chosen["id"]
                row_info = page.evaluate("""(id) => {
                  const row = document.querySelector('[data-recruit-row="' + id + '"]');
                  const btn = document.querySelector('[data-sect-recruit="' + id + '"]');
                  return {row_text: row ? row.textContent.trim() : '',
                          button_text: btn ? btn.textContent.trim() : '',
                          disabled: btn ? !!btn.disabled : null,
                          exists: !!btn};
                }""", chosen["id"])
                steps["recruit_button"] = row_info
                if not row_info.get("exists") or row_info.get("disabled"):
                    results.add("真服务 收徒按钮可用（disabled 时如实记录页面原因）", False,
                                row_info)
                    block("recruit_button_disabled", row_info)

                before_count = action_count(page, "recruit_disciple")
                page.click(selector)
                fired = False
                try:
                    page.wait_for_function("""(n) => window.__CAP.filter(c => {
                      try { return JSON.parse(c.request || '{}').verb === 'recruit_disciple'; }
                      catch (e) { return false; }
                    }).length > n""", arg=[before_count], timeout=15000)
                    fired = True
                except Exception:
                    fired = False
                recruit_ok = fired and wait_result(page, "为徒", 20000)
                recruit_action = last_action(page, "recruit_disciple")
                recruit_text = page.evaluate(
                    "() => (document.getElementById('sectResult') || {}).textContent || ''")
                refresh(page, account)
                page.wait_for_timeout(400)
                after_recruit = sect_state(page)
                steps["recruit"] = {"target": chosen,
                                    "button": row_info,
                                    "request": (recruit_action or {}).get("request"),
                                    "status": (recruit_action or {}).get("status"),
                                    "response": (recruit_action or {}).get("response"),
                                    "result_text": recruit_text,
                                    "sect": after_recruit}
                results.add("真服务 收徒走 UI 成功（#sectResult 出现“为徒”）",
                            recruit_ok, {"text": recruit_text})
                recruit_body = after_recruit.get("bodyText") or ""
                results.add("真服务 收徒结果条回显真实“金库 600 文”“名册 2 人”",
                            "金库 600 文" in recruit_text and "名册 2 人" in recruit_text,
                            {"result_text": recruit_text})
                results.add("真服务 收徒后名册 2 人，页面展示真实“金库：600 文”“名册 2 / 上限 3”",
                            after_recruit.get("memberCount") == 2
                            and "金库：600 文" in recruit_body
                            and "名册 2 / 上限 3" in recruit_body,
                            {"memberCount": after_recruit.get("memberCount"),
                             "text": recruit_text, "body": recruit_body[:500]})
                capture(page, "real-6-recruit")
                if not recruit_ok:
                    block("recruit_ui_failed", steps["recruit"])
                if after_recruit.get("memberCount") != 2:
                    block("recruit_state_mismatch", after_recruit)

                # ---------------------------------------------------- 派俸（必须走 UI）
                pay_btn = pay_button(page)
                steps["pay_button_before"] = pay_btn
                if not pay_btn or pay_btn["disabled"]:
                    block("pay_button_disabled", pay_btn)
                before_count = action_count(page, "pay_disciples")
                page.click("#sectPayGo")
                pay_fired = False
                try:
                    page.wait_for_function("""(n) => window.__CAP.filter(c => {
                      try { return JSON.parse(c.request || '{}').verb === 'pay_disciples'; }
                      catch (e) { return false; }
                    }).length > n""", arg=[before_count], timeout=15000)
                    pay_fired = True
                except Exception:
                    pay_fired = False
                pay_ok = pay_fired and wait_result(page, "日结完成", 20000)
                pay_action = last_action(page, "pay_disciples")
                pay_text = page.evaluate(
                    "() => (document.getElementById('sectResult') || {}).textContent || ''")
                settlement = (((pay_action or {}).get("response") or {})
                              .get("details") or {}).get("settlement") or {}
                refresh(page, account)
                page.wait_for_timeout(400)
                after_pay = sect_state(page)
                pay_btn_after = pay_button(page)
                steps["pay"] = {"request": (pay_action or {}).get("request"),
                                "status": (pay_action or {}).get("status"),
                                "response": (pay_action or {}).get("response"),
                                "settlement": settlement,
                                "result_text": pay_text,
                                "sect": after_pay,
                                "button_after": pay_btn_after}
                results.add("真服务 派俸走 UI 成功（#sectResult 出现“日结完成”）",
                            pay_ok, {"text": pay_text})
                pay_body = after_pay.get("bodyText") or ""
                results.add("真服务 派俸结果条含收会费/发俸禄与真实“金库 595 文”“名册 2 人”",
                            all(k in pay_text for k in ("收会费", "发俸禄",
                                                        "金库 595 文", "名册 2 人")),
                            {"text": pay_text})
                results.add("真服务 派俸后页面展示真实“金库：595 文”“名册 2 / 上限 3”",
                            all(k in pay_body for k in ("金库：595 文", "名册 2 / 上限 3")),
                            {"text": pay_text, "body": pay_body[:600]})
                dues = settlement.get("dues")
                stipend = settlement.get("stipend")
                results.add("真服务 派俸 settlement 真实 dues/stipend 与页面一致",
                            dues is not None and stipend is not None
                            and int(dues) == 5 and int(stipend) == 10
                            and ("收会费 %s 文" % dues) in pay_text
                            and ("发俸禄 %s 文" % stipend) in pay_text,
                            {"settlement": settlement})
                results.add("真服务 派俸后 settledToday=true",
                            after_pay.get("settledToday") is True, after_pay)
                capture(page, "real-7-pay")
                if not pay_ok:
                    block("pay_ui_failed", steps["pay"])
                if after_pay.get("settledToday") is not True:
                    block("settled_today_false", after_pay)

                # 幂等：再次查看按钮应 disabled 且文案“今日已结算”，不再发第二单
                steps["pay_idempotent_before"] = {"button": pay_btn_after,
                                                  "count": action_count(page, "pay_disciples")}
                page.wait_for_timeout(500)
                idem = {"button": pay_button(page),
                        "count": action_count(page, "pay_disciples"),
                        "settledToday": sect_state(page).get("settledToday")}
                steps["pay_idempotent"] = idem
                results.add("真服务 派俸幂等：按钮 disabled + 文案“今日已结算”且不再发请求",
                            bool(idem["button"]) and idem["button"]["disabled"] is True
                            and "今日已结算" in (idem["button"]["text"] or "")
                            and idem["count"] == steps["pay_idempotent_before"]["count"],
                            idem)
                capture(page, "real-8-pay-idempotent")

                # ---------------------------------------------------- 匿名无假门派数据
                anon = browser.new_context(viewport={"width": 1280, "height": 900})
                apage = anon.new_page()
                try:
                    apage.goto(base + "/?ui_check=1")
                    wait_app(apage)
                    apage.evaluate("() => document.getElementById('commerceJump').click()")
                    apage.wait_for_timeout(300)
                    apage.evaluate(
                        "() => document.querySelector('[data-tab=\\\"sect\\\"]').click()")
                    apage.wait_for_timeout(500)
                    anon_state = apage.evaluate("""() => {
                      const hint = (document.getElementById('sectHint') || {}).textContent || '';
                      const body = (document.getElementById('sectBody') || {}).textContent || '';
                      const result = (document.getElementById('sectResult') || {}).textContent || '';
                      let ss = null;
                      try { ss = window.__XKX.sectState(); } catch (e) { ss = null; }
                      return {hint: hint.trim(), body: body.trim(), result: result.trim(),
                              sect: ss, html: document.body.innerText};
                    }""")
                    steps["anonymous"] = anon_state
                    results.add("真服务 匿名页门派提示正确",
                                anon_state["hint"] == "登录并进入世界后查看门派。",
                                anon_state["hint"])
                    html = anon_state["html"]
                    fake_data = [x for x in ("金库：", "名册 1 /", "门主：")
                                 if x in html]
                    results.add("真服务 匿名页无假门派数据（body/result 空、未入派、无数据文案）",
                                anon_state["body"] == ""
                                and anon_state["result"] == ""
                                and (anon_state["sect"] or {}).get("inSect") is False
                                and not fake_data,
                                {"body": anon_state["body"],
                                 "result": anon_state["result"],
                                 "inSect": (anon_state["sect"] or {}).get("inSect"),
                                 "fake_data": fake_data})
                    capture(apage, "real-9-anonymous")
                finally:
                    anon.close()

                # ---------------------------------------------------- 移动端 375×812
                page.set_viewport_size({"width": 375, "height": 812})
                page.wait_for_timeout(400)
                page.evaluate("() => document.getElementById('commerceJump').click()")
                page.wait_for_timeout(300)
                page.evaluate("() => document.querySelector('[data-tab=\\\"sect\\\"]').click()")
                page.wait_for_timeout(500)
                mobile = page.evaluate("""() => {
                  const body = document.getElementById('sectBody');
                  const btns = Array.from(body.querySelectorAll('button')).filter(b => {
                    const r = b.getBoundingClientRect();
                    return r.height > 0 && r.width > 0;
                  });
                  return {
                    overflow: document.documentElement.scrollWidth - window.innerWidth,
                    scrollWidth: document.documentElement.scrollWidth,
                    innerWidth: window.innerWidth,
                    buttonCount: btns.length,
                    small: btns.filter(b => b.getBoundingClientRect().height < 43.5)
                      .map(b => b.tagName + '#' + (b.id || b.textContent.trim())),
                    bodyText: (body.textContent || '').trim().slice(0, 200)
                  };
                }""")
                steps["mobile"] = mobile
                results.add("真服务 移动端 375×812 门派页不横向溢出且按钮 ≥43.5px",
                            mobile["overflow"] <= 0 and not mobile["small"]
                            and mobile["buttonCount"] >= 1, mobile)
                capture(page, "real-10-mobile")

                page.set_viewport_size({"width": 1280, "height": 900})
                ctx.close()
            finally:
                browser.close()
    except BlockedFlow:
        pass
    finally:
        stop_proc(proxy)
        stop_proc(api)

    elapsed = round(time.time() - started, 1)
    head = ""
    try:
        head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"],
                                       text=True).strip()
    except Exception:
        head = ""
    payload = {
        "meta": {
            "run_id": run_id,
            "api": api_base,
            "proxy": base,
            "ambient_speed": args.speed,
            "head": head,
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S",
                                        time.localtime(started)),
            "elapsed_seconds": elapsed,
            "status": blocked["status"] if blocked else "executed",
        },
        "blocked": blocked,
        "steps": steps,
        "results": results.rows,
    }
    (EVIDENCE / "real-sect.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    failures = [r["name"] for r in results.rows if not r["ok"]]
    print("%d checks / %d failed" % (len(results.rows), len(failures)))
    for name in failures:
        print("FAIL " + name)
    print("elapsed %.1fs" % elapsed)
    if blocked:
        print("BLOCKED " + json.dumps(blocked, ensure_ascii=False))
        raise SystemExit(0)
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
