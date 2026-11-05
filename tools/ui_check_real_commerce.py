#!/usr/bin/env python3
"""商业与经营面板真服务验收：真 API + 真代理 + 自走世界。

不 mock 上游：
  - `python3 -m wsim.api --auth off`（XKX_AMBIENT=1 自走，加速推进价格/技能/押镖到货）
  - `ui/player/server.py` 真反向代理
  - 浏览器只点商业面板（买 → 吃 → 卖 → 押镖 → 开店 → 雇人），
    同时在页面里 hook `fetch` 记录每个动作的真实请求/响应。

用法：
  python3 tools/ui_check_real_commerce.py [--speed 60] [--warmup-ticks 1200]

证据（reports/w4-commerce/）：
  real-*.png               每一步的真实截图
  real-commerce.json       请求/响应片段 + UI 快照 + 断言结果
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
    free_port, http_json, start_api, start_proxy, stop_proc, ui_enter, wait_http,
)
from ui_check import Results, wait_app  # noqa: E402

EVIDENCE = Path(__file__).resolve().parents[1] / "reports" / "w4-commerce"

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
    """首进账号会弹 4 步引导；跳过它，截图只留商业面板本身。"""
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
    """真服务的写动作按 tick 受理：点击后等这条请求真的出现在抓包里，避免读到旧状态。"""
    for _ in range(attempts):
        before = action_count(page, verb)
        page.click(selector)
        try:
            page.wait_for_function("""([verb, n]) => window.__CAP.filter(c => {
              try { return JSON.parse(c.request || '{}').verb === verb; } catch (e) { return false; }
            }).length > n""", arg=[verb, before], timeout=timeout)
            return True
        except Exception:
            page.wait_for_timeout(500)
    return False


def keepalive(page, base, account):
    """真服务按真实秒判在场：等待期间补心跳，避免押镖到货前被判离线。"""
    try:
        page.request.post(
            base + "/api/player/action",
            data=json.dumps({"account_id": account, "verb": "heartbeat",
                             "args": {}, "client_tick": None}),
            headers={"Content-Type": "application/json"})
    except Exception:
        pass


def move_to(page, exit_id, name, attempts=2):
    """沿相邻出口走一步，并强制拉一次状态，确认地点名真的变了。"""
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


def heartbeat_code(page, base, account):
    """返回心跳的拒绝码；空串表示在线（或本地网络异常）。"""
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


def snapshot(page):
    return page.evaluate("""() => {
      const st = window.__XKX.commerceState();
      const p = window.__XKX.stateSnapshot ? window.__XKX.stateSnapshot() : null;
      return {
        money: st.money, tab: st.tab, own: st.ownShop,
        buyRows: Array.from(document.querySelectorAll('#buyList .commerce-row')).map(r => ({
          item: r.getAttribute('data-buy'), text: r.textContent.trim()})),
        sellRows: Array.from(document.querySelectorAll('#sellList .commerce-row')).map(r => ({
          item: r.getAttribute('data-sell'), text: r.textContent.trim()})),
        tierText: (document.getElementById('tierList') || {}).textContent || '',
        ownText: (document.getElementById('ownShopInfo') || {}).textContent || '',
        employeeText: (document.getElementById('employeeList') || {}).textContent || '',
        hireText: (document.getElementById('hireList') || {}).textContent || '',
        hireHint: (document.getElementById('hireHint') || {}).textContent || '',
        escortText: (document.getElementById('escortEstimate') || {}).textContent || '',
        escortResult: (document.getElementById('escortResult') || {}).textContent || '',
        consumeText: (document.getElementById('consumeResult') || {}).textContent || '',
        soonText: (document.getElementById('soonList') || {}).textContent || '',
        errors: Array.from(document.querySelectorAll('#errorList .error-item')).map(e => e.textContent.trim()),
        deps: p
      };
    }""")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--speed", default="60")
    ap.add_argument("--warmup-ticks", type=int, default=0)
    args = ap.parse_args()
    root = Path(args.root).resolve()
    EVIDENCE.mkdir(parents=True, exist_ok=True)

    stamp = time.strftime("%m%d%H%M%S")
    run_id = "w4-commerce-" + stamp
    account = "w4shop" + stamp
    display = "商户客"

    os.environ["XKX_AMBIENT"] = "1"
    os.environ["XKX_AMBIENT_SPEED"] = args.speed

    api = proxy = None
    results = Results()
    steps: dict = {}
    started = time.time()
    try:
        api_port, proxy_port = free_port(), free_port()
        api = start_api(root, run_id, api_port, "off")
        proxy = start_proxy(root, f"http://127.0.0.1:{api_port}", proxy_port)
        api_base = f"http://127.0.0.1:{api_port}"
        base = f"http://127.0.0.1:{proxy_port}"
        wait_http(api_base + "/api/health")

        # 自走先把世界推过若干天：NPC 修炼出技能（雇工要求技能 >= 0.15），
        # 也让商店价格/库存进入真实变化状态。
        # 注意：scheduler 的 SPEEDS 只到 4.0，拉高 env 也只会取最近的 4x；
        # 默认不再长等，直接进 UI 流程。
        deadline = time.time() + (30 if args.warmup_ticks > 0 else 0)
        warm_tick = -1
        while time.time() < deadline:
            code, payload = http_json(api_base + "/api/health")
            if code == 200:
                warm_tick = int(payload.get("ambient_tick") or 0)
                if warm_tick >= args.warmup_ticks:
                    break
            time.sleep(1.0)

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
                page.wait_for_function("() => !!window.__XKX.commerceState()", timeout=20000)
                dismiss_guide(page)
                page.click("#commerceJump")
                page.wait_for_timeout(300)

                before = snapshot(page)
                steps["entry"] = {"before": before}
                results.add("真服务 商业面板入口可见且展示当前地点可买/可卖",
                            bool(before["buyRows"]) and bool(before["sellRows"]),
                            {"buy": before["buyRows"], "sell": before["sellRows"]})
                capture(page, "real-1-shop")

                # ------------------------------------------------ buy（数量 2）
                page.fill('input[data-qty="buy:flatbread"]', "2")
                click_action(page, '[data-buy-go="flatbread"]', "buy")
                page.wait_for_function("() => window.__XKX.commerceState().money < 300", timeout=15000)
                buy = last_action(page, "buy")
                snap = snapshot(page)
                steps["buy"] = {"action": buy, "after": snap}
                results.add("真服务 buy 走 UI 真调用（数量 2、钱包减少、库存变化）",
                            buy and buy["status"] == 200 and buy["request"]["args"].get("qty") == 2
                            and snap["money"] <= 294,
                            {"request": (buy or {}).get("request"), "status": (buy or {}).get("status"),
                             "money": snap["money"]})
                capture(page, "real-2-buy")

                # ------------------------------------------------ consume（买一份现吃）
                click_action(page, "#consumeBtn", "consume")
                page.wait_for_function(
                    "() => document.getElementById('consumeResult').textContent.indexOf('→') >= 0",
                    timeout=15000)
                eat = last_action(page, "consume")
                snap = snapshot(page)
                steps["consume"] = {"action": eat, "after": snap}
                results.add("真服务 consume 走 UI 真调用并显示体力变化",
                            eat and eat["status"] == 200 and "体力" in snap["consumeText"],
                            {"request": (eat or {}).get("request"), "text": snap["consumeText"]})
                capture(page, "real-3-consume")

                # ------------------------------------------------ sell
                page.fill('input[data-qty="sell:coarse_cloth"]', "1")
                click_action(page, '[data-sell-go="coarse_cloth"]', "sell")
                page.wait_for_function(
                    "() => document.querySelectorAll('#sellList [data-sell=coarse_cloth]').length === 0",
                    timeout=15000)
                sell = last_action(page, "sell")
                snap = snapshot(page)
                steps["sell"] = {"action": sell, "after": snap}
                results.add("真服务 sell 走 UI 真调用（粗布售出后从可卖列表消失）",
                            sell and sell["status"] == 200,
                            {"request": (sell or {}).get("request"), "sellRows": snap["sellRows"]})
                capture(page, "real-4-sell")

                # ------------------------------------------------ 走到镖局（市集 → 铁匠铺 → 镖局）
                move_to(page, "smithy", "铁匠铺")
                page.wait_for_function(
                    "() => window.__XKX.commerceState().escortDests.indexOf('escort_agency') >= 0",
                    timeout=20000)
                move_to(page, "escort_agency", "镖局")
                page.wait_for_function("() => window.__XKX.commerceState().hasEscortAgency === true",
                                       timeout=20000)
                page.click('[data-tab="escort"]')
                page.wait_for_timeout(300)
                snap = snapshot(page)
                results.add("真服务 走到镖局后押镖入口可用（相邻终点来自服务端 exits）",
                            page.evaluate("() => window.__XKX.commerceState().escortDests.length >= 1")
                            and "相邻" in (snap["escortText"] or ""),
                            {"estimate": snap["escortText"],
                             "dests": page.evaluate(
                                 "() => window.__XKX.commerceState().escortDests")})

                # ------------------------------------------------ escort
                page.select_option("#escortDest", "smithy")
                page.select_option("#escortCargo", "flatbread")
                page.fill("#escortFee", "180")
                page.fill("#escortQty", "1")
                page.wait_for_function(
                    "() => document.getElementById('escortEstimate').textContent.indexOf('预计到手') >= 0",
                    timeout=8000)
                estimate = snapshot(page)["escortText"]
                click_action(page, "#escortGo", "escort")
                page.wait_for_function(
                    "() => document.getElementById('escortResult').textContent.indexOf('镖单已接') >= 0",
                    timeout=20000)
                esc = last_action(page, "escort")
                snap = snapshot(page)
                steps["escort"] = {"action": esc, "estimate": estimate, "after": snap}
                results.add("真服务 escort 走 UI 真调用（路线/货物/镖费/到货 tick 均来自服务端）",
                            esc and esc["status"] == 200 and "到货 tick" in snap["escortResult"],
                            {"request": (esc or {}).get("request"), "estimate": estimate,
                             "result": snap["escortResult"]})
                capture(page, "real-5-escort")

                # 等押镖到货结算：钱包 + 净镖银，世界自动把承运人送到终点。
                # 真服务按真实秒判在场，等待期间每 15 秒补一次心跳，避免被 idle 超时踢下线。
                money_before = snap["money"]
                deadline = time.time() + 330
                while time.time() < deadline:
                    keepalive(page, base, account)
                    page.wait_for_timeout(15000)
                    st = page.evaluate("() => window.__XKX.commerceState()")
                    if st["money"] > money_before and st["activeEscorts"] == 0:
                        break
                arrived = snapshot(page)
                steps["escort_arrived"] = {"before_money": money_before, "after": arrived}
                results.add("真服务 押镖到货后钱包增加（结算不是界面自算）",
                            arrived["money"] > money_before and arrived["money"] - money_before >= 100,
                            {"before": money_before, "after": arrived["money"]})
                capture(page, "real-6-escort-arrived")

                # 到货后世界把玩家送到终点（铁匠铺），走回市集开店。
                if not move_to(page, "market", "市集"):
                    if heartbeat_code(page, base, account) in ("player_offline", "player_not_entered"):
                        page.click("#switchBtn")
                        page.wait_for_timeout(300)
                        ui_enter(page, account, display)
                    move_to(page, "market", "市集")
                page.click('[data-tab="business"]')
                page.wait_for_timeout(300)

                # ------------------------------------------------ open_shop（摊）
                click_action(page, '[data-tier="stall"]', "open_shop")
                page.wait_for_function("() => !!window.__XKX.commerceState().ownShop", timeout=20000)
                opened = last_action(page, "open_shop")
                snap = snapshot(page)
                steps["open_shop"] = {"action": opened, "after": snap}
                results.add("真服务 open_shop 走 UI 真调用（余额扣铺面+进货、出现店内现金）",
                            opened and opened["status"] == 200 and "店内现金" in snap["ownText"],
                            {"request": (opened or {}).get("request"), "ownText": snap["ownText"]})
                capture(page, "real-7-open-shop")

                # ------------------------------------------------ hire
                page.click('[data-tab="hire"]')
                page.wait_for_timeout(500)

                def hire_rows():
                    return page.evaluate("""() => Array.from(
                        document.querySelectorAll('#hireList .commerce-row')).map(r => ({
                          id: r.getAttribute('data-hire-row'),
                          name: r.querySelector('.row-name') ? r.querySelector('.row-name').textContent : ''
                        }))""")

                candidates = hire_rows()
                # 高倍速自走时 NPC 会来回走动，等一个本地点在場的可雇对象。
                deadline = time.time() + 40
                while not candidates and time.time() < deadline:
                    keepalive(page, base, account)
                    page.wait_for_timeout(4000)
                    candidates = hire_rows()
                steps["hire_list"] = snapshot(page)["hireText"]
                hired = None
                hired_snap = None
                recruit_try = None
                for row in candidates[:1]:
                    npc_id = row["id"]
                    name = (row["name"] or "").strip()
                    # 先把好感谈到 6.0 以上（服务端 clamp：单 tick 2、单日 8）。
                    for _ in range(5):
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
                        page.fill("#talkInput", "今日生意可好？")
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
                    page.wait_for_timeout(2500)
                    action = last_action(page, "recruit")
                    snap = snapshot(page)
                    recruit_try = {"npc": npc_id, "name": name, "position": pos,
                                   "action": action, "errors": snap["errors"],
                                   "hireText": snap["hireText"]}
                    if action and action["status"] == 200:
                        hired = {"npc": npc_id, "name": name, "position": pos, "action": action}
                        hired_snap = snap
                        break
                steps["hire"] = {"candidates": candidates, "hired": hired, "try": recruit_try,
                                 "after": hired_snap or snapshot(page)}
                if hired:
                    results.add("真服务 recruit 走 UI 真调用（成功雇到人并显示岗位/日薪/到店）",
                                "到店" in (hired_snap or {}).get("employeeText", ""),
                                {"hired": hired,
                                 "employeeText": (hired_snap or {}).get("employeeText")})
                else:
                    # 新世界 NPC 还没练出技能（max_skill < 0.15），真服务会拒绝；
                    # 这里验证 UI 把拒绝码翻成中文、不露原始 code。
                    code = ((recruit_try or {}).get("action") or {}).get("response", {}).get(
                        "reject_reason", "")
                    text = " ".join((recruit_try or {}).get("errors") or [])
                    results.add("真服务 recruit 走 UI 真调用（服务端拒绝码已映射中文）",
                                bool(code) and bool(text) and "skill" not in text
                                and "_" not in text and "未知" not in text,
                                {"reject_reason": code, "errors": text,
                                 "request": ((recruit_try or {}).get("action") or {}).get("request")})
                capture(page, "real-8-hire")

                # 未开放玩法：入口可见、文案明确。
                page.click('[data-tab="soon"]')
                snap = snapshot(page)
                steps["soon"] = snap["soonText"]
                results.add("真服务 未开放玩法显示「该玩法尚未开放」（仅立派/收徒）",
                            "该玩法尚未开放" in snap["soonText"] and "立派" in snap["soonText"]
                            and "收徒" in snap["soonText"] and "结婚" not in snap["soonText"],
                            {"soonText": snap["soonText"]})

                # 最近一次拒绝（若有）必须全是中文，不含原始 code。
                results.add("真服务 原因码全部为中文（无原始 code/未知操作）",
                            all("未知操作" not in e and "_" not in e for e in snap["errors"]),
                            {"errors": snap["errors"]})

                # 移动端 375×812：商业面板不横向溢出、触控 ≥44px。
                page.set_viewport_size({"width": 375, "height": 812})
                page.wait_for_timeout(500)
                page.click("#commerceJump")
                page.wait_for_timeout(300)
                mobile = page.evaluate("""() => {
                  const box = document.getElementById('commerceBlock');
                  const controls = Array.from(box.querySelectorAll('button, input, select'));
                  return {overflow: box.scrollWidth - document.documentElement.clientWidth,
                          small: controls.filter(c => {
                            const r = c.getBoundingClientRect();
                            return r.height > 0 && r.height < 43.5;
                          }).map(c => c.tagName + '#' + c.id),
                          controls: controls.length};
                }""")
                results.add("真服务 移动端 375×812 无横向溢出且触控≥44px",
                            mobile["overflow"] <= 0 and not mobile["small"] and mobile["controls"] > 2,
                            mobile)
                capture(page, "real-9-mobile")
                page.set_viewport_size({"width": 1280, "height": 900})
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
            "warmup_ticks": args.warmup_ticks,
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started)),
            "head": os.popen("git -C " + str(root) + " rev-parse HEAD").read().strip(),
        },
        "steps": steps,
        "results": results.rows,
    }
    (EVIDENCE / "real-commerce.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    failures = [r["name"] for r in results.rows if not r["ok"]]
    print("%d checks / %d failed" % (len(results.rows), len(failures)))
    for name in failures:
        print("FAIL " + name)
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
