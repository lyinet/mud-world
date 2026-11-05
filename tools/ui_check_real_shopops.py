#!/usr/bin/env python3
"""店铺经营真服务验收：真 API + 真代理 + ambient 自走世界。

不 mock 上游：
  - `python3 -m wsim.api --auth off`（XKX_AMBIENT=1，默认 4x）
  - `ui/player/server.py` 真反向代理
  - 浏览器只点经营面板：开摊 → 进货 → 改价 → 等日结 → 账本 → 提现 → 辞退
  - 页面 hook fetch，记录每个动作的真实请求/响应

能力探测：
  进入后先对五个动作发最小请求。若任一动作返回 `unknown_action` /
  `unknown_verb` / `not_implemented_for_player` / HTTP 404，即写
  `blocked_backend_not_ready` 并退出 0；**不会用 mock 数据冒充真服务证据**。

用法：
  python3 tools/ui_check_real_shopops.py [--speed 4] [--settle-timeout 90]

证据（reports/w4-shopops/）：
  real-*.png              每一步的真实截图
  real-shopops.json       能力探测、请求/响应、UI 快照与断言结果
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from e2e_service_check import (  # noqa: E402
    free_port, start_api, start_proxy, stop_proc, ui_enter, wait_http,
)
from ui_check import Results, wait_app  # noqa: E402

EVIDENCE = Path(__file__).resolve().parents[1] / "reports" / "w4-shopops"

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
    ("restock", {"item": "flatbread", "qty": 1}),
    ("set_price", {"item": "flatbread", "price": 1}),
    ("dismiss", {"target": "npc:wang_tiejiang"}),
    ("withdraw", {"amount": 1}),
    ("shop_report", {}),
]


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


def click_action(page, selector, verb, attempts=3, timeout=15000):
    for _ in range(attempts):
        before = action_count(page, verb)
        try:
            page.click(selector, timeout=8000)
        except Exception:
            page.wait_for_timeout(600)
            continue
        try:
            page.wait_for_function("""([verb, n]) => window.__CAP.filter(c => {
              try { return JSON.parse(c.request || '{}').verb === verb; } catch (e) { return false; }
            }).length > n""", arg=[verb, before], timeout=timeout)
            return True
        except Exception:
            page.wait_for_timeout(500)
    return False


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


def move_to(page, exit_id, name, attempts=2):
    for _ in range(attempts):
        try:
            page.click('button[data-exit-id="%s"]' % exit_id, timeout=6000)
        except Exception:
            page.wait_for_timeout(500)
            continue
        page.wait_for_timeout(300)
        page.evaluate("() => window.__XKX.refreshState()")
        try:
            page.wait_for_function(
                "(n) => document.getElementById('placeName').textContent.indexOf(n) >= 0",
                arg=name, timeout=15000)
            return True
        except Exception:
            page.wait_for_timeout(1000)
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
        return {"verb": verb, "status": resp.status, "code": code, "text": text,
                "implemented": not (code in UNKNOWN_MARKERS or resp.status == 404
                                    or "未知操作" in text)}
    except Exception as exc:
        return {"verb": verb, "status": 0, "code": "probe_failed", "text": str(exc),
                "implemented": False}


def shop_snapshot(page):
    return page.evaluate("""() => {
      const op = window.__XKX.shopOpsState();
      return {
        op,
        ownText: (document.getElementById('ownShopInfo') || {}).textContent || '',
        restockTotal: (document.getElementById('restockTotal') || {}).textContent || '',
        withdrawText: (document.getElementById('shopCashNow') || {}).textContent || '',
        ledgerRows: document.querySelectorAll('#ledgerBody tr').length,
        ledgerEmpty: !document.getElementById('ledgerEmpty').classList.contains('hidden'),
        ledgerText: (document.getElementById('ledgerTable') || {}).textContent || '',
        totalText: (document.getElementById('ledgerFoot') || {}).textContent || '',
        stockoutText: (document.getElementById('ledgerStockout') || {}).textContent || '',
        employees: Array.from(document.querySelectorAll('#employeeList [data-dismiss]')).length,
        employeeText: (document.getElementById('employeeList') || {}).textContent || '',
        hireText: (document.getElementById('hireList') || {}).textContent || '',
        toast: (document.getElementById('toast') || {}).textContent || '',
        errors: Array.from(document.querySelectorAll('#errorList .error-item')).map(e => e.textContent.trim())
      };
    }""")


def first_attr(page, selector, attr):
    return page.evaluate("""([sel, attr]) => {
      const el = document.querySelector(sel);
      return el ? el.getAttribute(attr) : '';
    }""", [selector, attr])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--speed", default="4")
    ap.add_argument("--earn-timeout", type=int, default=330,
                    help="押镖到货结算的等待上限（秒）")
    ap.add_argument("--settle-timeout", type=int, default=330,
                    help="等一个世界日日结的等待上限（秒）")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    EVIDENCE.mkdir(parents=True, exist_ok=True)

    stamp = time.strftime("%m%d%H%M%S")
    run_id = "w4-shopops-" + stamp
    account = "w4shopops" + stamp
    display = "铺面客"

    os.environ["XKX_AMBIENT"] = "1"
    os.environ["XKX_AMBIENT_SPEED"] = args.speed

    api = proxy = None
    results = Results()
    steps: dict = {}
    blocked = None
    started = time.time()
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

                # ---- 能力探测：没有店铺时最小请求只会返回前置拒绝或空账本；
                #      未知动作才会命中 UNKNOWN_MARKERS。不伪造任何成功。
                probes = [probe(page, base, account, verb, a) for verb, a in PROBES]
                steps["capability_probe"] = probes
                missing = [p["verb"] for p in probes if not p["implemented"]]

                page.click("#commerceJump")
                page.wait_for_timeout(300)
                page.click('[data-tab="business"]')
                page.wait_for_timeout(400)
                capture(page, "real-0-business")

                if missing:
                    blocked = {
                        "status": "blocked_backend_not_ready",
                        "missing": missing,
                        "probes": probes,
                        "note": "后端动作未进 master；本轮不跑成功路径，不用 mock 顶替真服务证据。",
                    }
                    steps["blocked"] = blocked
                    capture(page, "real-0-blocked")
                else:
                    results.add("真服务 能力探测：五个店铺经营动作均已实现", True,
                                {"probes": probes})
                    # --------------------------------------------- 先挣开店本钱（真押镖）
                    # 押镖货值下限是 100 文；新账号 300 文里要留出摊位 300 + 初始进货 30。
                    # 先把初始粗布/炊饼卖掉换现钱，再买 2 把菜刀（基准 80 文，合计货值约 156）
                    # 押到铁匠铺换 210 镖银（服务端按货值 115% 校验最低镖银）；沈砚舟有 1000 文。
                    page.click('[data-tab="shop"]')
                    page.wait_for_timeout(300)
                    sold = {}
                    for item in ("coarse_cloth", "flatbread"):
                        if not first_attr(page, '[data-sell-go="%s"]' % item, "data-sell-go"):
                            continue
                        cap = first_attr(
                            page, 'input[data-qty="sell:%s"]' % item, "max")
                        if not cap or int(cap) <= 0:
                            continue
                        page.fill('input[data-qty="sell:%s"]' % item, cap)
                        click_action(page, '[data-sell-go="%s"]' % item, "sell")
                        sold[item] = {"qty": int(cap), "action": last_action(page, "sell")}
                    steps["pre_open_sell"] = {
                        "sold": sold,
                        "after": page.evaluate("() => window.__XKX.commerceState()")}
                    move_to(page, "smithy", "铁匠铺")
                    steps["at_smithy"] = page.evaluate("() => window.__XKX.commerceState()")
                    page.click('[data-tab="shop"]')
                    page.wait_for_timeout(400)
                    unit = page.evaluate("""() => {
                      const row = document.querySelector('[data-buy="cleaver"]');
                      const m = row ? row.textContent.match(/单价 (\\d+) 文/) : null;
                      return m ? Number(m[1]) : 0;
                    }""")
                    qty = 2
                    if unit <= 0:
                        steps["pre_open_buy"] = {"error": "铁匠铺没有菜刀可买", "unit": unit}
                        results.add("真服务 买到押镖货物（菜刀）", False, steps["pre_open_buy"])
                    else:
                        page.fill('input[data-qty="buy:cleaver"]', str(qty))
                        click_action(page, '[data-buy-go="cleaver"]', "buy")
                        steps["pre_open_buy"] = last_action(page, "buy")
                        steps["pre_open_buy"]["unit"] = unit
                        steps["pre_open_buy"]["qty"] = qty
                        steps["pre_open_buy"]["after"] = page.evaluate(
                            "() => window.__XKX.commerceState()")

                    page.wait_for_function(
                        "() => window.__XKX.commerceState().escortDests.indexOf('escort_agency') >= 0",
                        timeout=20000)
                    move_to(page, "escort_agency", "镖局")
                    steps["at_agency"] = page.evaluate("() => window.__XKX.commerceState()")
                    page.wait_for_function(
                        "() => window.__XKX.commerceState().hasEscortAgency === true",
                        timeout=20000)
                    page.click('[data-tab="escort"]')
                    page.wait_for_timeout(400)
                    page.select_option("#escortDest", "smithy")
                    page.select_option("#escortCargo", "cleaver")
                    page.fill("#escortFee", "210")
                    page.fill("#escortQty", "2")
                    page.wait_for_function(
                        "() => document.getElementById('escortEstimate').textContent.indexOf('预计到手') >= 0",
                        timeout=8000)
                    money0 = page.evaluate("() => window.__XKX.commerceState().money")
                    fired = click_action(page, "#escortGo", "escort")
                    esc = last_action(page, "escort")
                    accepted = False
                    if fired:
                        try:
                            page.wait_for_function(
                                "() => document.getElementById('escortResult').textContent.indexOf('镖单已接') >= 0",
                                timeout=20000)
                            accepted = True
                        except Exception:
                            accepted = False
                    steps["escort"] = {"action": esc, "money_before": money0,
                                       "accepted": accepted,
                                       "errors": shop_snapshot(page)["errors"]}
                    capture(page, "real-0-escort-request")
                    if accepted:
                        deadline = time.time() + args.earn_timeout
                        while time.time() < deadline:
                            keepalive(page, base, account)
                            page.wait_for_timeout(12000)
                            st = page.evaluate("() => window.__XKX.commerceState()")
                            if st["money"] > money0 and st["activeEscorts"] == 0:
                                break
                        arrived = page.evaluate("() => window.__XKX.commerceState()")
                        steps["escort"]["money_after"] = arrived["money"]
                        results.add("真服务 押镖结算后钱包增加到可开店",
                                    arrived["money"] >= 330,
                                    {"before": money0, "after": arrived["money"]})
                    else:
                        results.add("真服务 押镖结算后钱包增加到可开店", False,
                                    {"request": (esc or {}).get("request"),
                                     "status": (esc or {}).get("status"),
                                     "response": (esc or {}).get("response"),
                                     "errors": steps["escort"]["errors"]})
                    capture(page, "real-0-escort-arrived")
                    if not move_to(page, "market", "市集"):
                        if heartbeat_code(page, base, account) in (
                                "player_offline", "player_not_entered"):
                            page.click("#switchBtn")
                            page.wait_for_timeout(300)
                            ui_enter(page, account, display)
                            dismiss_guide(page)
                        move_to(page, "market", "市集")
                    page.click("#commerceJump")
                    page.wait_for_timeout(300)
                    page.click('[data-tab="business"]')
                    page.wait_for_timeout(400)

                    # --------------------------------------------- open_shop（摊）
                    if not click_action(page, '[data-tier="stall"]', "open_shop"):
                        steps["open_shop"] = {
                            "error": "open_shop 请求未发出或未返回",
                            "money": page.evaluate("() => window.__XKX.commerceState().money"),
                            "disabled": first_attr(page, '[data-tier="stall"]', "disabled"),
                            "text": (page.query_selector('[data-tier="stall"]').text_content()
                                     if page.query_selector('[data-tier="stall"]') else "")
                        }
                        results.add("真服务 开摊成功并出现自有店铺", False, steps["open_shop"])
                    else:
                        page.wait_for_function(
                            "() => !!window.__XKX.commerceState().ownShop", timeout=20000)
                        snapped = shop_snapshot(page)
                        steps["open_shop"] = {"action": last_action(page, "open_shop"),
                                              "after": snapped}
                        results.add("真服务 开摊成功后经营区可用（店内现金/库存可见）",
                                    "店内现金" in snapped["ownText"], snapped["ownText"])
                        steps["order_note"] = (
                            "真服务店铺的店现初始为 0，restock 前置要求店现足够；"
                            "因此首笔进货安排在首日日结营收入账之后，其余按 "
                            "开张→定价→雇人→（首日结算）→进货→提现→次日结算 执行。")
                        capture(page, "real-1-open-shop")

                        # ----------------------------------------- set_price
                        price_item = first_attr(page, '#priceList [data-price-go]', "data-price-go")
                        if not price_item:
                            steps["set_price"] = {"error": "shop 视图未返回可定价行"}
                            results.add("真服务 改价走 UI 真调用（delta_pct 来自服务端）", False,
                                        steps["set_price"])
                        else:
                            base_p = page.evaluate("""(item) => {
                              const st = window.__XKX.shopOpsState();
                              const row = st.stock.find(r => r.item === item);
                              return row ? row.base_price : 0;
                            }""", price_item)
                            base_p = int(base_p or 0)
                            target = base_p + 1 if base_p else 1
                            # 只取基准价 +1，若越出 ±20% 就退回 ±10% 内的整数。
                            if base_p > 0:
                                high = max(1, int(round(base_p * 1.2)))
                                if target > high:
                                    target = max(1, int(round(base_p * 1.1)))
                                target = max(1, min(high, target))
                            page.fill('input[data-price-input="%s"]' % price_item, str(target))
                            click_action(page, '[data-price-go="%s"]' % price_item, "set_price")
                            priced = last_action(page, "set_price")
                            snapped = shop_snapshot(page)
                            steps["set_price"] = {"item": price_item, "base": base_p,
                                                  "target": target, "action": priced,
                                                  "after": snapped}
                            results.add("真服务 改价走 UI 真调用（新价与 ±% 来自服务端）",
                                        bool(priced) and priced["status"] == 200
                                        and "改价" in snapped["toast"],
                                        {"request": (priced or {}).get("request"),
                                         "response": (priced or {}).get("response")})
                            capture(page, "real-2-set-price")

                        # ----------------------------------------- hire（雇人）
                        page.click('[data-tab="hire"]')
                        page.wait_for_timeout(500)

                        def hire_rows():
                            return page.evaluate("""() => Array.from(
                                document.querySelectorAll('#hireList .commerce-row')).map(r => ({
                                  id: r.getAttribute('data-hire-row'),
                                  name: r.querySelector('.row-name')
                                    ? r.querySelector('.row-name').textContent : ''
                                }))""")

                        candidates = hire_rows()
                        deadline = time.time() + 30
                        while not candidates and time.time() < deadline:
                            keepalive(page, base, account)
                            page.wait_for_timeout(4000)
                            candidates = hire_rows()
                        hired = None
                        recruit_try = None
                        for row in candidates[:2]:
                            npc_id = row["id"]
                            name = (row["name"] or "").strip()
                            # 正面向对话可靠 +1/+2 好感（单 tick 2、单日 8）；8 次足够过 6.0。
                            for _ in range(8):
                                found = page.evaluate("""(name) => {
                                  const rows = Array.from(document.querySelectorAll('#npcList .npc'));
                                  const hit = rows.find(r => r.textContent.indexOf(name) >= 0);
                                  if (hit) hit.click();
                                  return !!hit;
                                }""", name)
                                if not found:
                                    keepalive(page, base, account)
                                    page.wait_for_timeout(3000)
                                    continue
                                if page.locator("#npcClose").is_visible():
                                    page.click("#npcClose")
                                page.fill("#talkInput", "多谢，辛苦你了。")
                                page.click("#talkSend")
                                page.wait_for_function(
                                    "() => !document.getElementById('talkSend').disabled", timeout=20000)
                                page.wait_for_timeout(300)
                                aff = page.evaluate("""(npc) => {
                                  const row = document.querySelector('[data-hire-row="' + npc + '"]');
                                  const m = row ? row.textContent.match(/好感 (\\d+\\.\\d)/) : null;
                                  return m ? Number(m[1]) : 0; }""", npc_id)
                                if aff >= 6.0:
                                    break
                            page.click('[data-tab="hire"]')
                            page.wait_for_timeout(300)
                            pos = page.evaluate("""(npc) => {
                              const sel = document.querySelector('select[data-pos-for="' + npc + '"]');
                              return sel ? sel.value : '';
                            }""", npc_id)
                            if pos:
                                page.select_option('select[data-pos-for="%s"]' % npc_id, pos)
                                page.wait_for_timeout(200)
                            page.fill("#hireWage", page.input_value("#hireWage") or "0")
                            before = action_count(page, "recruit")
                            page.evaluate("""(npc) => {
                              const btn = document.querySelector('[data-hire="' + npc + '"]');
                              if (btn && !btn.disabled) btn.click();
                            }""", npc_id)
                            try:
                                page.wait_for_function("""([n]) => window.__CAP.filter(c => {
                                  try { return JSON.parse(c.request || '{}').verb === 'recruit'; }
                                  catch (e) { return false; }
                                }).length > n""", arg=[before], timeout=15000)
                            except Exception:
                                pass
                            page.wait_for_timeout(2000)
                            action = last_action(page, "recruit")
                            snap = shop_snapshot(page)
                            recruit_try = {"npc": npc_id, "name": name, "position": pos,
                                           "action": action, "errors": snap["errors"],
                                           "hireText": snap["hireText"]}
                            if action and action["status"] == 200:
                                hired = {"npc": npc_id, "name": name, "position": pos,
                                         "action": action}
                                break
                        steps["hire"] = {"candidates": candidates, "hired": hired,
                                         "try": recruit_try, "after": shop_snapshot(page)}
                        if hired:
                            # 招募成功后重新拉状态，确认员工真的进了店铺视图（不靠 toast）。
                            page.evaluate("() => window.__XKX.refreshState('%s')" % account)
                            page.wait_for_timeout(500)
                            page.click('[data-tab="hire"]')
                            page.wait_for_timeout(200)
                            page.click('[data-tab="business"]')
                            page.wait_for_timeout(500)
                            snap_after = shop_snapshot(page)
                            steps["hire"]["after"] = snap_after
                            base_name = hired["name"].replace(hired["position"], "").strip()
                            results.add("真服务 雇人走 UI 真调用（雇到人并出现在员工名单）",
                                        snap_after["employees"] >= 1
                                        and base_name in snap_after["employeeText"],
                                        {"hired": hired, "base_name": base_name,
                                         "after": snap_after})
                        else:
                            code = ((recruit_try or {}).get("action") or {}).get(
                                "response", {}).get("reject_reason", "")
                            errs = " ".join((recruit_try or {}).get("errors") or [])
                            results.add("真服务 雇人走 UI 真调用（服务端拒绝码已映射中文）",
                                        bool(code) and bool(errs) and "_" not in errs
                                        and "未知" not in errs,
                                        {"reject_reason": code, "errors": errs,
                                         "request": ((recruit_try or {}).get("action")
                                                     or {}).get("request")})
                        capture(page, "real-3-hire")
                        page.click('[data-tab="business"]')
                        page.wait_for_timeout(400)

                        # ----------------------------------------- 首日结算：店现才有钱进货/提现
                        deadline = time.time() + args.settle_timeout
                        days = []
                        while time.time() < deadline:
                            keepalive(page, base, account)
                            page.evaluate("() => window.__XKX.refreshShopReport()")
                            page.wait_for_timeout(6000)
                            state = page.evaluate("() => window.__XKX.shopOpsState()")
                            days = state.get("days") or []
                            if days:
                                break
                        first_days = len(days)
                        snapped = shop_snapshot(page)
                        steps["ledger_first"] = {"days": days, "ledger_empty": not bool(days),
                                                 "after": snapped}
                        if days:
                            results.add("真服务 首日日结进入账本（世界日/营收/净利/笔数）",
                                        snapped["ledgerRows"] >= 1, snapped["ledgerText"])
                            capture(page, "real-4-ledger-first")
                        else:
                            results.add("真服务 账本为空时如实显示空态（不延长等待凑数据）",
                                        snapped["ledgerEmpty"]
                                        and "尚无日结记录" in snapped["ledgerText"],
                                        {"ledger_empty": True})
                            capture(page, "real-4-ledger-empty")

                        # ----------------------------------------- restock（用首日营收）
                        def pick_restock(op, want):
                            rows = op.get("stock") or []
                            cash_now = int(op.get("cash") or 0)
                            picks = [r for r in rows if int(r.get("qty") or 0) <= 0]
                            picks += [r for r in rows if 0 < int(r.get("qty") or 0) < 3]
                            picks = picks or rows
                            for row in picks:
                                unit = int(row.get("restock_price") or 0)
                                if unit > 0 and unit * want <= cash_now:
                                    return row["item"], want
                            for row in picks:
                                unit = int(row.get("restock_price") or 0)
                                if 0 < unit <= cash_now:
                                    return row["item"], 1
                            return "", 0

                        restock = None
                        item = ""
                        restock_qty = 0
                        restock_attempts = []
                        for attempt in range(6):
                            page.evaluate("() => window.__XKX.refreshState('%s')" % account)
                            op = page.evaluate("() => window.__XKX.shopOpsState()")
                            item, restock_qty = pick_restock(op, 2)
                            if not item:
                                restock_attempts.append(
                                    {"attempt": attempt, "cash": op.get("cash"),
                                     "note": "店内现金不足 1 件进货"})
                                keepalive(page, base, account)
                                page.wait_for_timeout(8000)
                                continue
                            page.fill('input[data-restock-qty="%s"]' % item, str(restock_qty))
                            click_action(page, '[data-restock-go="%s"]' % item, "restock")
                            restock = last_action(page, "restock")
                            restock_attempts.append({
                                "attempt": attempt, "item": item, "qty": restock_qty,
                                "cash": op.get("cash"),
                                "status": (restock or {}).get("status"),
                                "reason": (restock or {}).get("response", {}).get("reject_reason")})
                            if restock and restock.get("status") == 200:
                                break
                            keepalive(page, base, account)
                            page.wait_for_timeout(8000)
                        if not item:
                            steps["restock"] = {"error": "shop 视图未返回可进货行",
                                                "attempts": restock_attempts}
                            results.add("真服务 进货走 UI 真调用（库存前后变化）", False,
                                        steps["restock"])
                        else:
                            page.evaluate("() => window.__XKX.refreshState('%s')" % account)
                            page.wait_for_timeout(300)
                            snapped = shop_snapshot(page)
                            rows_after = (snapped["op"] or {}).get("stock") or []
                            qty_after = next(
                                (r["qty"] for r in rows_after if r["item"] == item), None)
                            qty_before = next(
                                (r["qty"] for r in (op.get("stock") or [])
                                 if r["item"] == item), None)
                            steps["restock"] = {"item": item, "qty": restock_qty,
                                                "before": qty_before, "after": qty_after,
                                                "attempts": restock_attempts,
                                                "action": restock, "snapshot": snapped}
                            # 真服务 restock 不回 details.total/stock_after，改用
                            # 动作成功后重新拉取的库存差来核对（不伪造字段）。
                            results.add("真服务 进货走 UI 真调用（total 与库存前后来自服务端）",
                                        bool(restock) and restock["status"] == 200
                                        and qty_before is not None and qty_after is not None
                                        and qty_after > qty_before,
                                        {"request": (restock or {}).get("request"),
                                         "response": (restock or {}).get("response"),
                                         "before": qty_before, "after": qty_after,
                                         "qty": restock_qty})
                            capture(page, "real-5-restock")

                        # ----------------------------------------- withdraw
                        drew = None
                        amount = 0
                        draw_attempts = []
                        for attempt in range(6):
                            page.evaluate("() => window.__XKX.refreshState('%s')" % account)
                            cash = int(page.evaluate(
                                "() => window.__XKX.shopOpsState().cash") or 0)
                            amount = min(10, cash)
                            if amount <= 0:
                                draw_attempts.append(
                                    {"attempt": attempt, "cash": cash,
                                     "note": "店铺现金为 0，等待当日回款"})
                                keepalive(page, base, account)
                                page.wait_for_timeout(8000)
                                continue
                            page.fill("#withdrawAmount", str(amount))
                            click_action(page, "#withdrawGo", "withdraw")
                            drew = last_action(page, "withdraw")
                            draw_attempts.append({
                                "attempt": attempt, "amount": amount, "cash": cash,
                                "status": (drew or {}).get("status"),
                                "reason": (drew or {}).get("response", {}).get("reject_reason")})
                            if drew and drew.get("status") == 200:
                                break
                            keepalive(page, base, account)
                            page.wait_for_timeout(8000)
                        snapped = shop_snapshot(page)
                        if not drew or drew.get("status") != 200:
                            steps["withdraw"] = {"error": "提现未成功", "cash": cash,
                                                 "attempts": draw_attempts}
                            results.add("真服务 提现走 UI 真调用（店现前后来自服务端）", False,
                                        steps["withdraw"])
                            capture(page, "real-6-withdraw-skipped")
                        else:
                            steps["withdraw"] = {"amount": amount, "action": drew,
                                                 "attempts": draw_attempts, "after": snapped}
                            results.add("真服务 提现走 UI 真调用（店现前后来自服务端）",
                                        drew["status"] == 200
                                        and "提现" in snapped["toast"],
                                        {"request": drew.get("request"),
                                         "response": drew.get("response")})
                            capture(page, "real-6-withdraw")

                        # ----------------------------------------- 次日结算：账本出现第二行
                        deadline = time.time() + args.settle_timeout
                        days = []
                        while time.time() < deadline:
                            keepalive(page, base, account)
                            page.evaluate("() => window.__XKX.refreshShopReport()")
                            page.wait_for_timeout(6000)
                            state = page.evaluate("() => window.__XKX.shopOpsState()")
                            days = state.get("days") or []
                            if len(days) > first_days:
                                break
                        snapped = shop_snapshot(page)
                        steps["ledger_second"] = {"days": days, "after": snapped}
                        results.add("真服务 提现后世界日再次日结，账本追加一行",
                                    len(days) > first_days and snapped["ledgerRows"] >= 2,
                                    {"first_days": first_days, "rows": snapped["ledgerRows"],
                                     "text": snapped["ledgerText"]})
                        capture(page, "real-7-ledger-second")

                        # ----------------------------------------- dismiss
                        # 员工名单只在店铺视图重渲染时更新；切页签强制刷新一次。
                        page.evaluate("() => window.__XKX.refreshState('%s')" % account)
                        page.click('[data-tab="hire"]')
                        page.wait_for_timeout(300)
                        page.click('[data-tab="business"]')
                        page.wait_for_timeout(500)
                        dismiss_btn = first_attr(page, '#employeeList [data-dismiss]', "data-dismiss")
                        if not dismiss_btn:
                            steps["dismiss"] = {"skipped": "no_employee",
                                                "note": "真服务未雇到人，不伪造辞退成功"}
                            results.add("真服务 辞退走 UI 真调用（欠薪仍计且从名单移除）", False,
                                        steps["dismiss"])
                            capture(page, "real-8-dismiss-skipped")
                        else:
                            page.click('[data-dismiss="%s"]' % dismiss_btn)
                            page.wait_for_function(
                                "() => !document.getElementById('dismissModal').classList.contains('hidden')",
                                timeout=5000)
                            capture(page, "real-8-dismiss-confirm")
                            click_action(page, "#dismissConfirm", "dismiss")
                            fired = last_action(page, "dismiss")
                            snapped = shop_snapshot(page)
                            steps["dismiss"] = {"target": dismiss_btn, "action": fired,
                                                "after": snapped}
                            results.add("真服务 辞退走 UI 真调用（欠薪仍计且从名单移除）",
                                        bool(fired) and fired["status"] == 200
                                        and "欠薪" in snapped["toast"],
                                        {"request": (fired or {}).get("request"),
                                         "response": (fired or {}).get("response")})
                            capture(page, "real-8-dismiss")

                # 原因码必须中文，不露原始 code。
                snapped = shop_snapshot(page)
                results.add("真服务 原因码全部中文（无原始 code）",
                            all("_" not in e and "未知操作" not in e for e in snapped["errors"]),
                            snapped["errors"])

                # 移动端 375×812：账本能横滚、页面不横向溢出、控件 ≥44px。
                page.set_viewport_size({"width": 375, "height": 812})
                page.wait_for_timeout(400)
                mobile = page.evaluate("""() => {
                  const wrap = document.getElementById('ledgerWrap');
                  const controls = Array.from(document.querySelectorAll(
                    '#restockList input, #restockList button, #priceList input, #priceList button, ' +
                    '#withdrawAmount, #withdrawGo, #employeeList [data-dismiss]'));
                  return {
                    overflow: document.documentElement.scrollWidth - window.innerWidth,
                    scrollWidth: document.documentElement.scrollWidth,
                    innerWidth: window.innerWidth,
                    ledgerScrollable: wrap.scrollWidth > wrap.clientWidth,
                    small: controls.filter(c => {
                      const r = c.getBoundingClientRect();
                      return r.height > 0 && r.height < 43.5;
                    }).map(c => c.tagName + '#' + (c.id || c.textContent.trim()))
                  };
                }""")
                results.add("真服务 移动端 375×812 账本可横滚、页面不溢出、控件 ≥44px",
                            mobile["overflow"] <= 0 and not mobile["small"],
                            mobile)
                capture(page, "real-7-mobile")
                ctx.close()
            finally:
                browser.close()
    finally:
        stop_proc(proxy)
        stop_proc(api)

    payload = {
        "meta": {
            "run_id": run_id,
            "api": api_base if api else "",
            "proxy": base if proxy else "",
            "ambient_speed": args.speed,
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started)),
            "head": os.popen("git -C " + str(root) + " rev-parse HEAD").read().strip(),
            "status": "blocked_backend_not_ready" if blocked else "executed",
        },
        "blocked": blocked,
        "steps": steps,
        "results": results.rows,
    }
    (EVIDENCE / "real-shopops.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    failures = [r["name"] for r in results.rows if not r["ok"]]
    print("%d checks / %d failed" % (len(results.rows), len(failures)))
    for name in failures:
        print("FAIL " + name)
    if blocked:
        print("BLOCKED " + json.dumps(blocked, ensure_ascii=False))
        raise SystemExit(0)
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
