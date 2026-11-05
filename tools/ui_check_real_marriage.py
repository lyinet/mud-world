#!/usr/bin/env python3
"""婚姻 UI 真服务验收：真 API + 真代理 + 自走世界（无 mock）。

流程（用户指定的成功路径，全部走真实服务）：
  talk 反复提好感（服务端 ±2/tick、±8/日）→ 求婚成功 → 配偶栏出现 → 和离成功。
浏览器每步截图；页面里 hook `fetch` 记录 `/api/player/action` 的真实请求/响应。

用法：
  python3 tools/ui_check_real_marriage.py [--speed 4] [--target liu_sanniang]
                                            [--deadline-min 50]

证据（reports/w4-marriage/）：
  real-*.png                 每一步的真实截图
  real-marriage.json         请求/响应片段 + UI 快照 + 断言结果 + talk 统计

注意：
- 世界按真实时间推进（1x = 10 tick/分钟，ambient 最高 4x）；好感每日上限 8 点，
  从 0 提到 60 需要跨 8 个世界日（4x 下约 29 分钟），属预期内的真实等待。
- 只读验收：账号/世界数据都落在 runs/ 新目录，不改产品代码。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from e2e_service_check import (  # noqa: E402
    free_port, start_api, start_proxy, stop_proc, ui_enter, wait_http,
)
from ui_check import Results, wait_app  # noqa: E402

EVIDENCE = Path(__file__).resolve().parents[1] / "reports" / "w4-marriage"
TICKS_PER_DAY = 144
PREFERRED = ["liu_sanniang", "meng_jiuniang", "luo_qingxu", "jin_mancang",
             "duan_wudou", "ruan_qingfu", "xing_pojun"]

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


def dismiss_guide(page):
    page.evaluate("""() => {
      const btn = document.getElementById('guideSkip');
      const card = document.getElementById('guideCard');
      if (btn && card && card.getBoundingClientRect().height > 0) btn.click();
    }""")


def shot(page, name):
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(EVIDENCE / (name + ".png")))


def read_state(page):
    return page.evaluate("() => window.__XKX.readState()")


def relation_of(st, target):
    for row in ((st.get("player") or {}).get("relations") or []):
        if row.get("target") == target:
            return row
    return {}


def present_ids(st):
    return [str(n.get("id")) for n in (st.get("present_npcs") or []) if n.get("id")]


def action_rows(page):
    return page.evaluate("() => window.__CAP.slice()")


def last_action(page, verb):
    for row in reversed(action_rows(page)):
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


def keepalive(page, base, account):
    try:
        page.request.post(
            base + "/api/player/action",
            data=json.dumps({"account_id": account, "verb": "heartbeat",
                             "args": {}, "client_tick": None}),
            headers={"Content-Type": "application/json"})
    except Exception:
        pass


def fast_message(target, tick):
    """挑一句带正面词、且在 tick..tick+2 上都被判 +2 的台词。

    服务端 `_sentiment` 用 sha256(f"{npc}|{message}|{tick}")[:2] 的奇偶定幅度；
    预先算好，保证每句话都拿满 +2，避免把单日 8 点额度浪费在 +1 上。
    """
    base = "多谢照应，今日辛苦了。"
    for k in range(0, 80):
        msg = base + ("多谢" * k)
        if len(msg) > 200:
            break
        ok = all(
            int(hashlib.sha256(f"{target}|{msg}|{t}".encode("utf-8")).hexdigest()[:2], 16) % 2 == 0
            for t in range(tick, tick + 3)
        )
        if ok:
            return msg
    return base


def select_target(page, name):
    found = page.evaluate("""(name) => {
      const rows = Array.from(document.querySelectorAll('#npcList .npc'));
      const hit = rows.find(r => r.textContent.indexOf(name) >= 0);
      if (hit) hit.click();
      return !!hit;
    }""", name)
    if found and page.locator("#npcModal").is_visible():
        page.click("#npcClose")
        page.wait_for_selector("#npcModal", state="hidden", timeout=5000)
    return found


def talk_ui(page, name, msg):
    """走对话面板发一句话；返回 doTalk 的结果文本。"""
    select_target(page, name)
    page.fill("#talkInput", msg)
    page.click("#talkSend")
    page.wait_for_function("() => !document.getElementById('talkSend').disabled", timeout=25000)
    page.wait_for_timeout(200)
    return page.evaluate("""() => {
      const box = document.getElementById('talkResult');
      return {text: (box ? box.textContent : '').trim(),
              bad: !!(box && box.classList.contains('bad'))};
    }""")


def wait_target_present(page, base, account, target, deadline):
    while time.time() < deadline:
        keepalive(page, base, account)
        st = read_state(page)
        if target in present_ids(st):
            return st
        page.wait_for_timeout(8000)
    return read_state(page)


def load_adjacency(root):
    """地点邻接表（只读 content/locations.yaml），用于按最短路追人。"""
    try:
        import yaml
        data = yaml.safe_load((root / "content" / "locations.yaml").read_text(encoding="utf-8"))
        rows = data.get("locations") if isinstance(data, dict) else data
        return {str(r["id"]): [str(x) for x in (r.get("exits") or [])] for r in rows}
    except Exception:
        return {}


def route_between(adj, start, dest):
    if start == dest:
        return []
    if start not in adj or dest not in adj:
        return []
    prev = {start: None}
    queue = [start]
    while queue:
        cur = queue.pop(0)
        for nxt in adj.get(cur) or []:
            if nxt in prev:
                continue
            prev[nxt] = cur
            if nxt == dest:
                path = []
                node = dest
                while node and node != start:
                    path.append(node)
                    node = prev[node]
                return list(reversed(path))
            queue.append(nxt)
    return []


def target_location(root, run_id, target):
    """从本次 run 的事件库只读地读 NPC 最近一次落点；读不到就返回空串。

    只是验收工具的“找人”手段：服务端不提供他人位置查询，UI 也只列同地点 NPC。
    """
    db = root / "runs" / run_id / "events.sqlite"
    if not db.exists():
        return ""
    try:
        con = sqlite3.connect(str(db), timeout=2)
        try:
            rows = con.execute(
                "SELECT effects FROM events WHERE effects LIKE '%set_location%' "
                "AND effects LIKE ? ORDER BY seq DESC LIMIT 5", ("%" + target + "%",)
            ).fetchall()
        finally:
            con.close()
        for (effects,) in rows:
            for eff in json.loads(effects or "[]"):
                if str(eff.get("op")) != "set_location":
                    continue
                data = eff.get("data") or {}
                if str(data.get("entity") or "") == target:
                    return str(data.get("to") or "")
    except Exception:
        return ""
    return ""


def move_to(page, adj, dest):
    """按最短路把玩家走到 dest（真实 move 动作，逐跳）。"""
    st = read_state(page)
    cur = str((st.get("location") or {}).get("id") or "")
    path = route_between(adj, cur, dest)
    for nxt in path:
        out = page.evaluate("""async (to) => {
          const res = await window.__XKX.apiAction('move', {to: to});
          await window.__XKX.refreshState();
          return res;
        }""", nxt)
        if not (isinstance(out, dict) and out.get("ok")):
            print("[move] %s -> %s failed: %s" % (cur, nxt, str(out)[:160]), flush=True)
            return read_state(page)
        cur = nxt
    return read_state(page)


def ensure_present(page, base, account, target, root, run_id, adj):
    """目标不在同地点时，只读事件库找到它的落点并走过去；找不到就短暂等待。"""
    st = read_state(page)
    if target in present_ids(st):
        return st
    loc = target_location(root, run_id, target)
    if loc:
        st = move_to(page, adj, loc)
        if target in present_ids(st):
            return st
    keepalive(page, base, account)
    page.wait_for_timeout(6000)
    return read_state(page)


def wait_next_day(page, base, account, day, deadline):
    while time.time() < deadline:
        keepalive(page, base, account)
        st = read_state(page)
        if int(st.get("tick") or 0) // TICKS_PER_DAY > day:
            return st
        page.wait_for_timeout(10000)
    return read_state(page)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--speed", default="4")
    ap.add_argument("--target", default="liu_sanniang")
    ap.add_argument("--deadline-min", type=float, default=50.0)
    ap.add_argument("--smoke", action="store_true", help="只跑 talk 循环，约 90 秒，用于验证选择器")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    EVIDENCE.mkdir(parents=True, exist_ok=True)

    stamp = time.strftime("%m%d%H%M%S")
    run_id = "w4-marriage-real-" + stamp
    account = "w4wed" + stamp
    display = "求婚验客"
    deadline = time.time() + args.deadline_min * 60
    adj = load_adjacency(root)

    os.environ["XKX_AMBIENT"] = "1"
    os.environ["XKX_AMBIENT_SPEED"] = args.speed

    api = proxy = None
    results = Results()
    steps: dict = {}
    talks: list[dict] = []
    started = time.time()
    tgt = args.target
    name = ""
    base = ""
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
                page.wait_for_function("() => !!window.__XKX.marriageState()", timeout=20000)
                dismiss_guide(page)

                st = read_state(page)
                present = present_ids(st)
                for cand in PREFERRED:
                    if cand in present:
                        tgt = cand
                        break
                row = [n for n in st["present_npcs"] if n.get("id") == tgt][0]
                name = row.get("name") or tgt
                steps["start"] = {"tick": st["tick"], "target": tgt, "name": name,
                                  "affection": relation_of(st, tgt).get("affection", 0),
                                  "trust": relation_of(st, tgt).get("trust", 0),
                                  "present": present}
                results.add("真服务 目标 NPC 与玩家同处一地（初始关系为 0）",
                            tgt in present and not relation_of(st, tgt),
                            {"target": tgt, "name": name, "tick": st["tick"]})

                # ------------------------------------------------ talk 提好感
                # 单日上限 8 点：每天 4 句 +2 → 8；跨 8 个世界日到 60+。
                last_day = -1
                while time.time() < deadline:
                    st = read_state(page)
                    aff = float(relation_of(st, tgt).get("affection") or 0)
                    if aff >= 60.0:
                        break
                    if tgt not in present_ids(st):
                        st = ensure_present(page, base, account, tgt, root, run_id, adj)
                        continue
                    tick = int(st.get("tick") or 0)
                    day = tick // TICKS_PER_DAY
                    if last_day >= 0 and day != last_day:
                        print("[day %d] tick %d aff %.1f" % (day, tick, aff), flush=True)
                    last_day = day
                    msg = fast_message(tgt, tick)
                    out = talk_ui(page, name, msg)
                    after = read_state(page)
                    new_aff = float(relation_of(after, tgt).get("affection") or 0)
                    new_tick = int(after.get("tick") or 0)
                    delta = new_aff - aff
                    talks.append({"day": day, "tick_before": tick, "tick_after": new_tick,
                                  "affection_before": aff, "affection_after": new_aff,
                                  "delta": delta, "result": out["text"][:120]})
                    print("[talk] tick %d→%d aff %.1f→%.1f (Δ%.1f) day %d"
                          % (tick, new_tick, aff, new_aff, delta, day), flush=True)
                    if delta > 0:
                        continue
                    if "被拒" in out["text"]:
                        # 目标走开了（not_together）或同 tick 限流：等它回到同地点再试。
                        st = ensure_present(page, base, account, tgt, root, run_id, adj)
                        print("[wait] 对话被拒：%s" % out["text"][:80], flush=True)
                        continue
                    if new_tick == tick:
                        # 同 tick 内第二次发言，服务端按 ±2/tick 截断；稍后重试。
                        page.wait_for_timeout(2500)
                        continue
                    # tick 已前进仍为 0：当日 ±8 额度用尽，等世界日翻篇。
                    st2 = wait_next_day(page, base, account, day, deadline)
                    last_day = int(st2.get("tick") or 0) // TICKS_PER_DAY

                final = read_state(page)
                aff = float(relation_of(final, tgt).get("affection") or 0)
                trust = float(relation_of(final, tgt).get("trust") or 0)
                steps["talk_loop"] = {"talks": talks, "affection": aff, "trust": trust,
                                      "tick": final["tick"],
                                      "sample_action": last_action(page, "talk"),
                                      "seconds": round(time.time() - started, 1)}
                results.add("真服务 talk 把好感提到 60+（真实世界日上限内累计）",
                            aff >= 60.0, {"affection": aff, "trust": trust,
                                          "talks": len(talks), "tick": final["tick"]})
                if args.smoke:
                    print("SMOKE talk_ui OK: %d talks, aff %.1f, tick %d"
                          % (len(talks), aff, final["tick"]), flush=True)
                    return

                # 把 UI 拉到婚姻页签，确认候选列表读到 60+ 好感。
                while (tgt not in present_ids(read_state(page))
                       and time.time() < deadline - 120):
                    ensure_present(page, base, account, tgt, root, run_id, adj)
                page.evaluate("() => window.__XKX.refreshState()")
                page.wait_for_timeout(500)
                page.click("#commerceJump")
                page.wait_for_timeout(300)
                page.click('[data-tab="marriage"]')
                page.wait_for_timeout(500)
                page.wait_for_selector('[data-propose-choose="%s"]' % tgt, timeout=10000)
                page.click('[data-propose-choose="%s"]' % tgt)
                page.wait_for_timeout(300)
                mstate = page.evaluate("() => window.__XKX.marriageState()")
                cand = [c for c in mstate["candidates"] if c["id"] == tgt][0]
                steps["candidates"] = mstate["candidates"]
                results.add("真服务 求婚候选显示好感/信任/同地点",
                            cand["affection"] >= 60 and cand["trust"] >= 30 and cand["sameLocation"],
                            {"target": tgt, "candidate": cand, "text": mstate["estimate"]})
                shot(page, "real-1-talk-60")

                # ------------------------------------------------ 求婚
                page.fill("#proposeMoney", "20")
                gifts = page.evaluate("""() => Array.from(
                  document.querySelectorAll('#proposeItem option')).map(o => o.value)
                  .filter(v => v)""")
                gift = gifts[0] if gifts else ""
                if gift:
                    page.select_option("#proposeItem", gift)
                    page.fill("#proposeQty", "1")
                page.wait_for_timeout(300)
                page.click("#proposeGo")
                page.wait_for_function(
                    "() => document.getElementById('proposeResult').textContent.indexOf('成婚') >= 0",
                    timeout=25000)
                page.wait_for_function(
                    "() => window.__XKX.marriageState().status === 'married'", timeout=15000)
                propose = last_action(page, "marriage_propose")
                mstate = page.evaluate("() => window.__XKX.marriageState()")
                details = ((propose or {}).get("response") or {}).get("details") or {}
                decision = str(details.get("decision") or "")
                steps["propose"] = {"action": propose, "decision": decision,
                                    "marriage": details.get("marriage") or {},
                                    "ui": mstate}
                results.add("真服务 marriage_propose 走 UI 真调用并接受",
                            bool(propose) and propose["status"] == 200 and decision == "accept"
                            and propose["request"]["args"].get("target") == tgt,
                            {"request": (propose or {}).get("request"),
                             "status": (propose or {}).get("status"), "decision": decision})
                results.add("成婚文案为「与 X 成婚，tick N」",
                            ("与 " + name + " 成婚") in mstate["result"] and "tick" in mstate["result"],
                            {"result": mstate["result"]})
                results.add("state.marriage 刷新为 married（配偶/成婚 tick 正确）",
                            mstate["status"] == "married" and mstate["spouseId"] == tgt
                            and mstate["sinceTick"] > 0,
                            {"status": mstate["status"], "spouseId": mstate["spouseId"],
                             "sinceTick": mstate["sinceTick"]})
                shot(page, "real-2-propose-accepted")

                # ------------------------------------------------ 配偶常驻区
                page.wait_for_function(
                    "() => window.__XKX.marriageState().spouseBlockVisible === true",
                    timeout=15000)
                mstate = page.evaluate("() => window.__XKX.marriageState()")
                steps["spouse_block"] = mstate
                results.add("配偶常驻区固定显示「配偶：姓名 · 成婚 tick N」",
                            ("配偶：" + name + " · 成婚 tick") in mstate["spouseInfo"],
                            {"spouseInfo": mstate["spouseInfo"]})
                results.add("配偶当日陪伴有口径（spouse_time 或 nearby 近似）",
                            bool(mstate["spouseNote"]),
                            {"spouseNote": mstate["spouseNote"], "dailyNote": mstate["dailyNote"],
                             "lastDailyTick": mstate["lastDailyTick"]})
                shot(page, "real-3-spouse-block")

                # ------------------------------------------------ 配偶对话标记
                if tgt in present_ids(read_state(page)):
                    head = talk_ui(page, name, "多谢你，今日辛苦了。")
                    page.wait_for_timeout(600)
                    marks = page.evaluate("""() => ({
                      spouseRow: !!document.querySelector('#npcList .npc.spouse'),
                      head: document.getElementById('talkTarget').textContent,
                      headSpouseClass: document.getElementById('talkTarget').classList.contains('spouse'),
                      result: document.getElementById('talkResult').textContent,
                      replySpouse: !!document.querySelector('#talkReplies .reply-item.spouse'),
                      replyText: (document.querySelector('#talkReplies .reply-item.spouse') || {}).textContent || ''
                    })""")
                    steps["spouse_talk"] = {"marks": marks, "talk": head,
                                            "action": last_action(page, "talk")}
                    results.add("配偶对话有「（配偶）」标记与高亮",
                                marks["spouseRow"] and "（配偶）" in marks["head"]
                                and marks["headSpouseClass"] and "（配偶）" in marks["result"],
                                marks)
                    results.add("配偶回应与普通回应对应（reply-item.spouse + 配偶回应）",
                                marks["replySpouse"] and "配偶回应" in marks["replyText"],
                                {"replyText": marks["replyText"][:200]})
                    shot(page, "real-3b-spouse-talk")
                else:
                    results.add("配偶对话有「（配偶）」标记与高亮", False,
                                {"error": "配偶不在同一地点，无法对话", "present": present_ids(read_state(page))})
                    results.add("配偶回应与普通回应对应（reply-item.spouse + 配偶回应）", False,
                                {"error": "配偶不在同一地点"})

                # ------------------------------------------------ 和离（二次确认）
                st = read_state(page)
                while (tgt not in present_ids(st)
                       and time.time() < deadline - 30):
                    st = ensure_present(page, base, account, tgt, root, run_id, adj)
                page.evaluate("() => window.__XKX.refreshState()")
                page.wait_for_timeout(500)
                page.click("#marriageEndBtn")
                page.wait_for_function(
                    "() => !document.getElementById('divorceModal').classList.contains('hidden')",
                    timeout=10000)
                modal = page.evaluate("""() => ({
                  text: document.getElementById('divorceText').textContent,
                  body: document.getElementById('divorceModal').textContent
                })""")
                steps["divorce_confirm"] = modal
                results.add("和离二次确认写清「聘礼不退还」",
                            "聘礼不退还" in modal["body"],
                            {"text": modal["text"]})
                shot(page, "real-4-divorce-confirm")
                page.click("#divorceCancel")
                page.wait_for_function(
                    "() => document.getElementById('divorceModal').classList.contains('hidden')",
                    timeout=5000)
                still = page.evaluate("() => window.__XKX.marriageState()")
                results.add("取消和离不改变婚姻态",
                            still["status"] == "married" and still["spouseBlockVisible"],
                            {"status": still["status"]})

                page.click("#marriageEndBtn")
                page.wait_for_function(
                    "() => !document.getElementById('divorceModal').classList.contains('hidden')",
                    timeout=10000)
                page.click("#divorceConfirm")
                page.wait_for_function(
                    "() => window.__XKX.marriageState().status === 'single'", timeout=25000)
                end = last_action(page, "marriage_end")
                mstate = page.evaluate("() => window.__XKX.marriageState()")
                steps["divorce"] = {"action": end, "ui": mstate}
                results.add("真服务 marriage_end 走 UI 真调用并入离",
                            bool(end) and end["status"] == 200
                            and (end["response"].get("details") or {}).get("decision") == "ended",
                            {"request": (end or {}).get("request"),
                             "status": (end or {}).get("status"),
                             "decision": ((end or {}).get("response") or {}).get("details", {}).get("decision")})
                results.add("和离后回到单身态且配偶栏隐藏",
                            mstate["status"] == "single" and mstate["spouseBlockVisible"] is False
                            and "已与 " + name + " 和离" in mstate["result"],
                            {"status": mstate["status"],
                             "spouseBlockVisible": mstate["spouseBlockVisible"],
                             "result": mstate["result"]})
                shot(page, "real-5-single")

                # 婚姻结果文案里不允许漏出英文原因码（对话结果里的 intent/event 调试串
                # 属于既有 W4 诊断，不在婚姻裁决文案之列）。
                texts = [mstate["result"],
                         (steps.get("divorce_confirm") or {}).get("text", "")]
                leaked = [t for t in texts if re.search(r"[a-z]+_[a-z_]+", t or "")]
                results.add("真服务 婚姻结果文案不含英文原因码",
                            not leaked, {"leaked": leaked, "texts": texts})

                # 全部真实富 action 请求/响应（talk/move/propose/end）留档，便于复核。
                captured = []
                for row in action_rows(page):
                    try:
                        req = json.loads(row.get("request") or "{}")
                    except ValueError:
                        req = row.get("request")
                    try:
                        resp = json.loads(row.get("response") or "{}")
                    except ValueError:
                        resp = row.get("response")
                    captured.append({"verb": (req or {}).get("verb") if isinstance(req, dict) else "",
                                     "request": req, "status": row.get("status"),
                                     "response": resp})
                steps["captured_actions"] = captured

                ctx.close()
            finally:
                browser.close()
    except Exception as exc:  # 记录异常后仍写证据
        results.add("执行异常", False, {"error": f"{type(exc).__name__}: {exc}"})
    finally:
        stop_proc(proxy)
        stop_proc(api)

    payload = {
        "meta": {
            "run_id": run_id,
            "api": base,
            "ambient_speed": args.speed,
            "target": tgt,
            "name": name,
            "account": account,
            "head": os.popen("git -C " + str(root) + " rev-parse HEAD").read().strip(),
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started)),
            "seconds": round(time.time() - started, 1),
        },
        "steps": steps,
        "results": results.rows,
    }
    (EVIDENCE / "real-marriage.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    failures = [r["name"] for r in results.rows if not r["ok"]]
    print("%d checks / %d failed" % (len(results.rows), len(failures)))
    for name_ in failures:
        print("FAIL " + name_)
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
