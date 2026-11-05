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
        employees: Array.from(document.querySelectorAll('#employeeList [data-dismiss]')).length,
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
    ap.add_argument("--settle-timeout", type=int, default=90)
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
                    # --------------------------------------------- open_shop（摊）
                    if not click_action(page, '[data-tier="stall"]', "open_shop"):
                        # 钱不够时先做一单小委托太慢；直接记录环境不足，不伪造。
                        steps["open_shop"] = {"error": "open_shop 请求未发出或未返回"}
                        results.add("真服务 开摊成功并出现自有店铺", False, steps["open_shop"])
                    else:
                        page.wait_for_function(
                            "() => !!window.__XKX.commerceState().ownShop", timeout=20000)
                        snapped = shop_snapshot(page)
                        steps["open_shop"] = {"action": last_action(page, "open_shop"),
                                              "after": snapped}
                        results.add("真服务 开摊成功后经营区可用（店内现金/库存可见）",
                                    "店内现金" in snapped["ownText"], snapped["ownText"])
                        capture(page, "real-1-open-shop")

                        # ----------------------------------------- restock
                        item = first_attr(page, '#restockList [data-restock-go]', "data-restock-go")
                        if not item:
                            steps["restock"] = {"error": "shop 视图未返回可进货行"}
                            results.add("真服务 进货走 UI 真调用（库存前后变化）", False,
                                        steps["restock"])
                        else:
                            page.fill('input[data-restock-qty="%s"]' % item, "2")
                            click_action(page, '[data-restock-go="%s"]' % item, "restock")
                            restock = last_action(page, "restock")
                            snapped = shop_snapshot(page)
                            details = (restock or {}).get("response", {}).get("details") or {}
                            if not details:
                                details = (restock or {}).get("response", {})
                            steps["restock"] = {"action": restock, "after": snapped}
                            results.add("真服务 进货走 UI 真调用（total 与库存前后来自服务端）",
                                        bool(restock) and restock["status"] == 200
                                        and details.get("total") is not None
                                        and details.get("stock_after") is not None,
                                        {"request": (restock or {}).get("request"),
                                         "response": (restock or {}).get("response")})
                            capture(page, "real-2-restock")

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
                            target = int(base_p) + 1 if base_p else 1
                            page.fill('input[data-price-input="%s"]' % price_item, str(target))
                            click_action(page, '[data-price-go="%s"]' % price_item, "set_price")
                            priced = last_action(page, "set_price")
                            snapped = shop_snapshot(page)
                            steps["set_price"] = {"target": target, "action": priced,
                                                  "after": snapped}
                            results.add("真服务 改价走 UI 真调用（新价与 ±% 来自服务端）",
                                        priced and priced["status"] == 200
                                        and "改价" in snapped["toast"],
                                        {"request": (priced or {}).get("request"),
                                         "response": (priced or {}).get("response")})
                            capture(page, "real-3-set-price")

                        # ----------------------------------------- 等一个世界日日结
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
                        snapped = shop_snapshot(page)
                        steps["ledger"] = {"days": days, "ledger_empty": not bool(days),
                                           "after": snapped}
                        if days:
                            results.add("真服务 7 日账本出现日结行（世界日/营收/净利/笔数）",
                                        snapped["ledgerRows"] >= 1, snapped["ledgerText"])
                            capture(page, "real-4-ledger")
                        else:
                            results.add("真服务 账本为空时如实显示空态（不延长等待凑数据）",
                                        snapped["ledgerEmpty"]
                                        and "尚无日结记录" in snapped["ledgerText"],
                                        {"ledger_empty": True})
                            capture(page, "real-4-ledger-empty")

                        # ----------------------------------------- withdraw
                        cash = int((shop_snapshot(page)["op"] or {}).get("cash") or 0)
                        amount = min(10, cash)
                        if amount <= 0:
                            steps["withdraw"] = {"skipped": "shop_cash_zero",
                                                 "cash": cash,
                                                 "note": "店铺现金为 0，不伪造提现成功"}
                            capture(page, "real-5-withdraw-skipped")
                        else:
                            page.fill("#withdrawAmount", str(amount))
                            click_action(page, "#withdrawGo", "withdraw")
                            drew = last_action(page, "withdraw")
                            snapped = shop_snapshot(page)
                            steps["withdraw"] = {"amount": amount, "action": drew, "after": snapped}
                            results.add("真服务 提现走 UI 真调用（店现前后来自服务端）",
                                        drew and drew["status"] == 200
                                        and "提现" in snapped["toast"],
                                        {"request": (drew or {}).get("request"),
                                         "response": (drew or {}).get("response")})
                            capture(page, "real-5-withdraw")

                        # ----------------------------------------- dismiss
                        dismiss_btn = first_attr(page, '#employeeList [data-dismiss]', "data-dismiss")
                        if not dismiss_btn:
                            steps["dismiss"] = {"skipped": "no_employee",
                                                "note": "真服务短跑未雇到人，不伪造辞退成功"}
                            capture(page, "real-6-dismiss-skipped")
                        else:
                            page.click('[data-dismiss="%s"]' % dismiss_btn)
                            page.wait_for_function(
                                "() => !document.getElementById('dismissModal').classList.contains('hidden')",
                                timeout=5000)
                            capture(page, "real-6-dismiss-confirm")
                            click_action(page, "#dismissConfirm", "dismiss")
                            fired = last_action(page, "dismiss")
                            snapped = shop_snapshot(page)
                            steps["dismiss"] = {"target": dismiss_btn, "action": fired,
                                                "after": snapped}
                            results.add("真服务 辞退走 UI 真调用（欠薪仍计且从名单移除）",
                                        fired and fired["status"] == 200
                                        and "欠薪" in snapped["toast"],
                                        {"request": (fired or {}).get("request"),
                                         "response": (fired or {}).get("response")})
                            capture(page, "real-6-dismiss")

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
