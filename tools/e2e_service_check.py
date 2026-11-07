"""p3k 真服务端到端验收：真 wsim.api（--auth on/off + 真 service.sqlite）+ 真 UI 代理。

与 tools/ui_check_proxy.py 不同，这里**没有 mock**：上游是冻结包自带的
`python3 -m wsim.api`，账号令牌落在 run 目录的真实 sqlite 里；浏览器走真实
代理（ui/player/server.py）完成注册、刷新、401 重放、原因码与玩家闭环。

只读验证：不改任何产品代码；会在 runs/ 下新建一次性 run 目录作为证据。

用法：
  python3 tools/e2e_service_check.py [--root .]

输出：
  reports/w4-e2e/e2e-service.json     逐项结果
  reports/w4-e2e/*.png                必要截图
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ui_check import Results, wait_app  # noqa: E402

EVIDENCE = Path(__file__).resolve().parents[1] / "reports" / "w4-e2e"


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def http_json(url: str, method: str = "GET", body: dict | None = None,
              token: str | None = None, timeout: float = 10.0):
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status), json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        return int(exc.code), json.loads(exc.read().decode("utf-8") or "{}")


def wait_http(url: str, timeout: float = 40.0) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            code, payload = http_json(url, timeout=1.0)
            if code == 200:
                return payload
        except Exception as exc:  # 端口未就绪
            last = exc
        time.sleep(0.2)
    raise RuntimeError(f"等待 {url} 超时：{last}")


def start_api(root: Path, run_id: str, port: int, auth: str) -> subprocess.Popen:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src")
    proc = subprocess.Popen(
        [sys.executable, "-m", "wsim.api", "--run", run_id,
         "--host", "127.0.0.1", "--port", str(port), "--auth", auth],
        cwd=str(root), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    wait_http(f"http://127.0.0.1:{port}/api/service/info")
    return proc


def start_proxy(root: Path, upstream: str, port: int) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, str(root / "ui" / "player" / "server.py"),
         "--upstream", upstream, "--port", str(port), "--host", "127.0.0.1"],
        cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    wait_http(f"http://127.0.0.1:{port}/api/health")
    return proc


def stop_proc(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()


def read_state(page, account: str | None = None) -> dict:
    return page.evaluate(
        """async (acct) => {
            const st = await window.__XKX.readState(acct);
            const p = st.player || {};
            const w = p.wallet || {};
            return {tick: st.tick, location: (st.location || {}).id,
                    location_name: (st.location || {}).name, money: w.money,
                    items: (w.items || []).length,
                    skills: Object.keys(p.skills || {}).length,
                    relations: (p.relations || []).length,
                    active: ((st.quest_board || {}).active || []).map(q => q.id),
                    keys: Object.keys(p).sort()};
        }""", account,
    )


def wait_state(page, predicate_js: str, arg=None, timeout: float = 12000) -> dict:
    page.wait_for_function(predicate_js, arg=arg, timeout=timeout)
    return read_state(page)


def error_rows(page) -> list[str]:
    return page.evaluate(
        """() => Array.from(document.querySelectorAll('#errorList .error-item'))
                   .map(x => x.innerText.trim())"""
    )


def ui_register(page, username: str, password: str) -> None:
    page.evaluate("() => document.getElementById('entryAuthBtn').click()")
    page.click("#authTabRegister")
    page.fill("#authUsername", username)
    page.fill("#authPassword", password)
    page.click("#authSubmit")
    page.wait_for_function("() => document.getElementById('authModal').classList.contains('hidden')",
                           timeout=15000)
    page.wait_for_function("() => window.__XKX.authState().hasAccess === true", timeout=10000)


def ui_enter(page, username: str, display: str) -> None:
    # 117e656 起已登录态 #accountId 被 syncEntryAccountLock 锁为只读（值已是令牌账号），
    # page.fill 撞只读会超时；仅在可编辑时填入，已锁定时沿用自动回填的值。
    if not page.evaluate("() => document.getElementById('accountId').readOnly"):
        page.fill("#accountId", username)
    page.fill("#displayName", display)
    page.click("#enterBtn")
    page.wait_for_function("() => document.getElementById('enterBtn').getAttribute('data-open') === '1'",
                           timeout=10000)
    page.click("#enterBtn")
    page.wait_for_function("() => document.getElementById('entryOverlay').classList.contains('hidden')",
                           timeout=15000)


def click_quest(page, quest_id: str, action: str) -> None:
    card = f'[data-quest-card="{quest_id}"]'
    page.wait_for_selector(card, timeout=10000)
    page.locator(card).scroll_into_view_if_needed()
    page.click(f'{card} button[data-{action}]')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args()
    root = Path(args.root).resolve()
    EVIDENCE.mkdir(parents=True, exist_ok=True)

    stamp = time.strftime("%m%d%H%M%S")
    user = "w4e2e_" + stamp
    display = "交付验客"
    service_run = "w4-e2e-" + stamp
    legacy_run = "w4-e2e-legacy-" + stamp

    api = proxy = legacy_api = legacy_proxy = None
    results = Results()
    git_root = Path(__file__).resolve().parents[1]
    meta = {
        "tag": "delivery-p3k",
        "tag_commit": subprocess.check_output(
            ["git", "rev-parse", "delivery-p3k^{commit}"], cwd=str(git_root), text=True).strip(),
        "head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(git_root), text=True).strip(),
        "tree_root": str(root),
        "service_run": service_run,
        "legacy_run": legacy_run,
        "account": user,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    shots = []

    def shot(page, name: str) -> None:
        path = EVIDENCE / name
        page.screenshot(path=str(path), full_page=False)
        shots.append(name)

    try:
        api_port = free_port()
        proxy_port = free_port()
        api = start_api(root, service_run, api_port, "on")
        proxy = start_proxy(root, f"http://127.0.0.1:{api_port}", proxy_port)
        base = f"http://127.0.0.1:{proxy_port}"
        meta["service_api"] = f"http://127.0.0.1:{api_port}"
        meta["service_proxy"] = base

        legacy_api_port = free_port()
        legacy_proxy_port = free_port()
        legacy_api = start_api(root, legacy_run, legacy_api_port, "off")
        legacy_proxy = start_proxy(root, f"http://127.0.0.1:{legacy_api_port}", legacy_proxy_port)
        legacy_base = f"http://127.0.0.1:{legacy_proxy_port}"

        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                # ---------------------------------------------------- service
                ctx = browser.new_context(viewport={"width": 1280, "height": 900})
                page = ctx.new_page()
                requests: list[dict] = []
                responses: list[dict] = []

                def on_response(resp):
                    entry = {"status": resp.status, "url": resp.url}
                    # 契约层证据：UI 只展示本地化文案，原始拒绝码仍从真 API 响应体断言。
                    if "/api/player/action" in resp.url and resp.request.method == "POST":
                        try:
                            entry["body"] = resp.json()
                        except Exception:  # 非 JSON / 响应体已不可读
                            entry["body"] = None
                    responses.append(entry)

                page.on("request", lambda r: requests.append(
                    {"method": r.method, "url": r.url}))
                page.on("response", on_response)

                def last_reject(code: str) -> dict:
                    """最近一条 /api/player/action 响应里断言原始 reject_reason。"""
                    for entry in reversed(responses):
                        body = entry.get("body")
                        if isinstance(body, dict) and body.get("reject_reason") == code:
                            return body
                    return {}

                page.goto(base + "/?ui_check=1")
                wait_app(page)
                page.wait_for_function("() => window.__XKX.authState().mode === 'auth'", timeout=15000)
                results.add("服务模式 探活判定 auth（真 /api/service/info）", True, meta)

                ui_register(page, user, "secret123")
                reg = [r for r in requests if r["url"].endswith("/api/auth/register")]
                login = [r for r in requests if r["url"].endswith("/api/auth/login")]
                stored = page.evaluate("() => JSON.parse(localStorage.getItem('ws_auth_v1') || '{}')")
                results.add("1. 注册 → 自动登录", bool(reg) and bool(login)
                             and page.evaluate("() => window.__XKX.authState().hasAccess"),
                             {"register": len(reg), "login": len(login)})
                results.add("1. 令牌真落盘 ws_auth_v1", bool(stored.get("access_token")),
                             {k: stored.get(k) for k in ("username", "expires_at", "has_refresh")})

                ui_enter(page, user, display)
                first = read_state(page)
                results.add("1. 首登读到完整状态（钱包/背包/技能/关系）",
                             first["money"] == 300 and first["items"] >= 1
                             and first["skills"] >= 1 and "relations" in first["keys"],
                             first)

                page.reload()
                wait_app(page)
                page.wait_for_function(
                    "() => window.__XKX.authState().mode === 'auth'"
                    " && window.__XKX.authState().hasAccess === true", timeout=15000)
                page.wait_for_function(
                    "() => document.getElementById('entryOverlay').classList.contains('hidden')",
                    timeout=15000)
                reloaded = read_state(page)
                results.add("1. 刷新后仍登录且仍读完整状态（不是公共摘要）",
                             reloaded["money"] == 300 and reloaded["items"] >= 1
                             and reloaded["skills"] >= 1 and reloaded["location"] == first["location"],
                             reloaded)
                shot(page, "service-reload-state.png")

                # ------------------------------------------------ 玩家闭环
                talk = page.evaluate("""async () => {
                    const r = await window.__XKX.apiAction('talk',
                      {target: 'jin_mancang', message: '最近的委托有什么？'});
                    return {status: r.status, data: r.data};
                }""")
                results.add("闭环 对话（真 API + 真代理）", talk["status"] == 200
                             and bool(talk["data"].get("ok")), talk["data"].get("details"))

                click_quest(page, QUEST_ID, "quest")
                accepted = read_state(page)
                for _ in range(20):
                    accepted = read_state(page)
                    if QUEST_ID in accepted["active"]:
                        break
                    page.wait_for_timeout(300)
                results.add("闭环 接单：任务进入 active", QUEST_ID in accepted["active"], accepted["active"])

                page.click('[data-exit-id="pharmacy"]')
                page.wait_for_function(
                    "() => document.getElementById('placeName').textContent.includes('药铺')",
                    timeout=15000)
                moved = read_state(page)
                results.add("闭环 移动：market → pharmacy", moved["location"] == "pharmacy",
                             {"location": moved["location"]})

                # f77d142 任务过程化：交付前必须真实完成目标动作，否则 quest_step_incomplete。
                # tutorial_silk_sample 的目标动作是与药铺 su_hexiang 当面 trade（零金额）。
                traded = page.evaluate("""async () => {
                    const r = await window.__XKX.apiAction('trade',
                      {target: 'su_hexiang', offer: [], want: [], money_delta: 0});
                    return {status: r.status, ok: !!(r.data && r.data.ok),
                            reject: r.data ? r.data.reject_reason : null};
                }""")
                results.add("闭环 目标动作：药铺 trade 苏合香（交付前置）",
                             traded["status"] == 200 and traded["ok"], traded)

                click_quest(page, QUEST_ID, "deliver")
                after = None
                for _ in range(30):
                    after = read_state(page)
                    if after["money"] > moved["money"]:
                        break
                    page.wait_for_timeout(300)
                results.add("闭环 交付 → 钱包增长 300→312",
                             after["money"] == 312 and QUEST_ID not in after["active"], after)
                shot(page, "service-loop-delivered.png")

                # ------------------------------------------------ 原因码（真服务）
                click_quest(page, LEDGER_ID, "quest")  # 委托人不在药铺
                # UI 只展示本地化文案（见 ui/player/app.js REASON_TEXT），原始码走 API 断言。
                page.wait_for_function(
                    "() => document.getElementById('errorList').innerText"
                    ".includes('你不在委托人所在的地点')",
                    timeout=10000)
                reasons = error_rows(page)
                results.add("原因码 not_at_giver 走真实 API 到 UI 文案",
                             any("不在委托人" in row or "委托人所在" in row for row in reasons),
                             reasons)
                raw = last_reject("not_at_giver")
                results.add("契约层 not_at_giver 原始码（真 /api/player/action 响应体）",
                             raw.get("ok") is False, raw)

                page.click('[data-exit-id="market"]')
                page.wait_for_function(
                    "() => document.getElementById('placeName').textContent.includes('集市')",
                    timeout=15000)
                click_quest(page, LEDGER_ID, "quest")
                for _ in range(10):
                    if LEDGER_ID in read_state(page)["active"]:
                        break
                    page.wait_for_timeout(300)
                # 交付允许在 target 地点**或**发布人所在地；集市是发布人所在地，
                # 所以必须离开集市（回药铺：既非客栈也非集市）才能触发 not_at_target。
                page.click('[data-exit-id="pharmacy"]')
                page.wait_for_function(
                    "() => document.getElementById('placeName').textContent.includes('药铺')",
                    timeout=15000)
                click_quest(page, LEDGER_ID, "deliver")
                page.wait_for_function(
                    "() => document.getElementById('errorList').innerText"
                    ".includes('还没到任务目标地点')",
                    timeout=10000)
                reasons = error_rows(page)
                results.add("原因码 not_at_target 走真实 API 到 UI 文案",
                             any("没到" in row or "目标地点" in row for row in reasons), reasons)
                raw = last_reject("not_at_target")
                results.add("契约层 not_at_target 原始码（真 /api/player/action 响应体）",
                             raw.get("ok") is False, raw)

                # 离线后继续操作：真 409 player_offline
                page.evaluate("""async () => {
                    const t = JSON.parse(localStorage.getItem('ws_auth_v1')).access_token;
                    await fetch('/api/player/leave', {method: 'POST',
                      headers: {Accept: 'application/json', 'Content-Type': 'application/json',
                                Authorization: 'Bearer ' + t},
                      body: JSON.stringify({account_id: window.__XKX.authState().username})});
                }""")
                # f01ebb9 地图即操作：方向按钮整块移除，出口渲染为 button[data-exit-id]，
                # 不再有 #exits 容器；取任一一步可达出口点击以触发 player_offline。
                page.locator('[data-exit-id]').first.click()
                # 32201e4 离线自愈：写动作收到 player_offline 后 UI 自动 enter 恢复并重试一次，
                # 自愈成功时提前返回、不展示错误文案；故不再等 errorList，改为验证 API 确实发出
                # player_offline 且自愈后动作成功（不卡死）。
                recovered = False
                deadline = time.time() + 15
                off_idx = -1
                while time.time() < deadline:
                    for i, e in enumerate(responses):
                        if (e.get("status") == 409 and isinstance(e.get("body"), dict)
                                and e["body"].get("reject_reason") == "player_offline"):
                            off_idx = i
                    if off_idx >= 0 and any(
                            e.get("status") == 200 and "/api/player/action" in e.get("url", "")
                            for e in responses[off_idx + 1:]):
                        recovered = True
                        break
                    page.wait_for_timeout(200)
                raw = last_reject("player_offline")
                results.add("离线自愈：player_offline 后自动 enter 恢复并重试成功（32201e4）",
                             recovered and raw.get("ok") is False,
                             {"off_idx": off_idx, "recovered": recovered})
                results.add("契约层 player_offline 原始码（真 /api/player/action 响应体）",
                             raw.get("ok") is False, raw)
                page.evaluate("() => document.getElementById('errorToggle').click()")
                shot(page, "service-reason-codes.png")

                page.evaluate("""async () => {
                    const t = JSON.parse(localStorage.getItem('ws_auth_v1')).access_token;
                    await fetch('/api/player/enter', {method: 'POST',
                      headers: {Accept: 'application/json', 'Content-Type': 'application/json',
                                Authorization: 'Bearer ' + t},
                      body: JSON.stringify({account_id: window.__XKX.authState().username})});
                }""")
                page.reload()
                wait_app(page)
                page.wait_for_function(
                    "() => window.__XKX.authState().hasAccess === true"
                    " && document.getElementById('entryOverlay').classList.contains('hidden')",
                    timeout=15000)
                # 停掉轮询，保证下面的 401 由我们主动触发（只动测试页，不改产品代码）。
                page.evaluate("""() => {
                    const max = setInterval(() => {}, 1000);
                    for (let i = 1; i <= max; i++) clearInterval(i);
                    clearInterval(max);
                }""")
                before_401 = read_state(page)
                errors_before_race = len(error_rows(page))

                # ------------------------------ 中途 401：并发 + 单飞 + 状态不丢
                token_before = page.evaluate(
                    "() => JSON.parse(localStorage.getItem('ws_auth_v1')).access_token")
                stale = {"left": 2}
                stale_401 = []

                def route_state(route):
                    headers = dict(route.request.headers)
                    if stale["left"] > 0 and headers.get("authorization"):
                        stale["left"] -= 1
                        headers["authorization"] = "Bearer e2e-stale-token"
                        route.continue_(headers=headers)
                        return
                    route.continue_()

                page.route("**/api/player/state*", route_state)
                refresh_before = len([r for r in requests
                                      if r["url"].endswith("/api/auth/refresh")])
                response_before = len(responses)
                reads = page.evaluate("""async (acct) => {
                    const out = await Promise.allSettled([
                        window.__XKX.readState(acct), window.__XKX.readState(acct)
                    ]);
                    return out.map(r => r.status === 'fulfilled'
                        ? {ok: true, tick: r.value.tick, location: r.value.location,
                           money: r.value.player.wallet.money}
                        : {ok: false, err: String(r.reason)});
                }""", user)
                page.wait_for_timeout(800)
                new_responses = responses[response_before:]
                stale_401 = [r for r in new_responses
                             if r["status"] == 401 and "/api/player/state" in r["url"]]
                refresh_now = len([r for r in requests
                                   if r["url"].endswith("/api/auth/refresh")])
                token_after = page.evaluate(
                    "() => JSON.parse(localStorage.getItem('ws_auth_v1')).access_token")
                page.unroute("**/api/player/state*")

                results.add("2. 并发两个 401 只触发一次 refresh（单飞）",
                             len([r for r in reads if r["ok"]]) == 2
                             and refresh_now - refresh_before == 1 and len(stale_401) == 2,
                             {"reads": reads, "refresh": refresh_now - refresh_before,
                              "401": len(stale_401)})
                results.add("2. 轮换后旧令牌被拒、新令牌可用",
                             token_after != token_before
                             and http_json(f"http://127.0.0.1:{api_port}/api/auth/me",
                                           token=token_before)[0] == 401
                             and http_json(f"http://127.0.0.1:{api_port}/api/auth/me",
                                           token=token_after)[0] == 200,
                             {"old_me": 401, "new_me": 200})
                results.add("2. 失败一侧不误清新令牌（登录态保持）",
                             page.evaluate("() => window.__XKX.authState().hasAccess === true")
                             and page.evaluate(
                                 "() => document.getElementById('authNotice').classList.contains('hidden')"),
                             page.evaluate("() => window.__XKX.authState()"))

                after_401 = read_state(page)
                results.add("3. 中途 401 刷新重放：世界状态不丢",
                             after_401["tick"] == before_401["tick"]
                             and after_401["location"] == before_401["location"]
                             and after_401["money"] == before_401["money"]
                             and after_401["active"] == before_401["active"],
                             {"before": before_401, "after": after_401})
                results.add("3. 中途 401 不写错误历史",
                             len(error_rows(page)) == errors_before_race,
                             {"before": errors_before_race, "after": len(error_rows(page))})
                shot(page, "service-mid-401-preserved.png")

                # ------------------------- 403 跨账号读：令牌身份重取、不写红字
                token_now = page.evaluate(
                    "() => JSON.parse(localStorage.getItem('ws_auth_v1')).access_token")
                cross = page.evaluate("""async (ignore) => {
                    const st = await window.__XKX.readState('u_other');
                    return {account: st.account_id, money: st.player.wallet.money};
                }""", user)
                api_403 = http_json(
                    f"http://127.0.0.1:{api_port}/api/player/state?account_id=u_other",
                    token=token_now)
                results.add("5. auth_account_mismatch 真 403 → 前端用令牌身份重取（无红字）",
                             api_403[0] == 403 and api_403[1].get("code") == "auth_account_mismatch"
                             and cross.get("account") == user and cross.get("money") == 312
                             and len(error_rows(page)) == errors_before_race,
                             {"api": {"status": api_403[0], "code": api_403[1].get("code")},
                              "ui": cross})

                # ------------------------------ 令牌真正失效：不清世界快照
                t2 = page.evaluate(
                    "() => JSON.parse(localStorage.getItem('ws_auth_v1')).access_token")
                rented, _ = http_json(f"http://127.0.0.1:{api_port}/api/auth/refresh",
                                      "POST", {}, t2)
                results.add("3. 令牌被轮换走后旧令牌失效（/auth/me 401）",
                             http_json(f"http://127.0.0.1:{api_port}/api/auth/me",
                                       token=t2)[0] == 401, {"rotate": rented})
                try:
                    page.evaluate("""async () => {
                        await window.__XKX.readState();
                    }""")
                except Exception:
                    pass
                page.wait_for_timeout(300)
                hard = page.evaluate("""() => ({
                    money: document.getElementById('stMoney').innerText,
                    place: document.getElementById('placeName').innerText,
                    auth: window.__XKX.authState(),
                    notice: document.getElementById('authNotice').innerText,
                    noticeVisible: !document.getElementById('authNotice').classList.contains('hidden'),
                    errors: Array.from(document.querySelectorAll('#errorList .error-item'))
                              .map(x => x.innerText.trim())
                })""")
                results.add("3. 令牌失效不清世界快照（钱/地点仍在），只提示一次重新登录",
                             hard["money"] != "—" and hard["place"] != "—"
                             and hard["auth"]["hasAccess"] is False
                             and hard["noticeVisible"]
                             and len(hard["errors"]) <= 3,
                             hard)

                ctx.close()

                # ------------------------------------------------------ legacy
                lctx = browser.new_context(viewport={"width": 1280, "height": 900})
                lpage = lctx.new_page()
                lpage.goto(legacy_base + "/?ui_check=1")
                wait_app(lpage)
                lpage.wait_for_function("() => window.__XKX.authState().mode === 'legacy'",
                                        timeout=15000)
                lres = {}
                for path in ("/api/auth/register", "/api/auth/login", "/api/auth/refresh"):
                    code, payload = http_json(f"http://127.0.0.1:{legacy_proxy_port}{path}",
                                              "POST",
                                              {"username": "w4legacy_" + stamp,
                                               "password": "secret123"},
                                              token=None if path != "/api/auth/refresh" else "x")
                    lres[path] = {"status": code, "code": payload.get("code")}
                results.add("4. legacy（--auth off）UI 判定为 legacy",
                             lpage.evaluate("() => window.__XKX.authState().mode") == "legacy",
                             lpage.evaluate("() => window.__XKX.authState()"))
                results.add("4. legacy 注册/登录/刷新均 503 auth_disabled",
                             all(v["status"] == 503 and v["code"] == "auth_disabled"
                                 for v in lres.values()), lres)
                shot(lpage, "legacy-auth-disabled.png")
                lctx.close()
            finally:
                browser.close()
    except Exception as exc:  # 记录异常后仍写出证据，避免“只在终端里失败”
        results.add("执行异常", False, {"error": f"{type(exc).__name__}: {exc}"})
    finally:
        for proc in (proxy, api, legacy_proxy, legacy_api):
            stop_proc(proc)

    payload = {
        "meta": meta,
        "checks": results.rows,
        "screenshots": shots,
        "service_stats": {
            "requests": locals().get("requests", []),
            "responses": locals().get("responses", []),
        },
    }
    (EVIDENCE / "e2e-service.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    failed = [r for r in results.rows if not r["ok"]]
    print("\n%d checks, %d failed; evidence: %s"
          % (len(results.rows), len(failed), EVIDENCE / "e2e-service.json"))
    for row in failed:
        print("FAILED:", row["name"], row.get("detail"))
    if failed:
        raise SystemExit(1)


QUEST_ID = "quest:tutorial_silk_sample:0"
LEDGER_ID = "quest:errand_market_ledger:0"

if __name__ == "__main__":
    main()
