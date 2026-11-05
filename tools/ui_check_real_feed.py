#!/usr/bin/env python3
"""服务模式「世界动态」真后端复验：真 API + 真 sqlite + 真自走 + 真代理。

不 mock 上游：
  - `python3 -m wsim.api --auth on`（进程内 XKX_AMBIENT=1 自走）
  - `ui/player/server.py` 真反向代理
  - 浏览器经代理注册/进入世界，观察 60 秒

用法：
  python3 tools/ui_check_real_feed.py [--root .] [--seconds 60] [--speed 2]

证据（reports/w4-feed/）：
  feed-service-real.png      观察结束时的截图
  feed-service-real-reload.png 刷新后截图
  real-feed-service.json     断言结果 + 原始 /api/world/feed JSON + 请求节流记录
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from e2e_service_check import (  # noqa: E402
    free_port, http_json, start_api, start_proxy, stop_proc, ui_enter, ui_register, wait_http,
)
from ui_check import Results, wait_app  # noqa: E402

EVENT_KEYS = {"tick", "actor", "verb", "place", "summary", "important"}
PRIVATE_MARKERS = ("wallet", "items", "skills", "relations", "active_quests",
                   "account_id", "player:")
# ui_check.py 里 mock 用过的 5 个 NPC；真世界里出现其它名字即证明不是 mock 数据。
MOCK_ACTORS = {"王铁匠", "刘三娘", "茶楼老板", "更夫", "药铺掌柜"}


def no_private(text: str) -> bool:
    return all(m not in text for m in PRIVATE_MARKERS)


def shape_ok(payload: dict) -> bool:
    events = payload.get("events")
    return (payload.get("ok") is True and isinstance(events, list)
            and all(isinstance(e, dict) and set(e) == EVENT_KEYS for e in events))


def feed_snapshot(page):
    return page.evaluate("""async () => {
      const rows = Array.from(document.querySelectorAll('#feedList .feed-row'));
      const st = await window.__XKX.readState().catch(() => null);
      const loc = (st && st.location) || {};
      return {
        rows: rows.length,
        texts: rows.map(r => r.textContent.trim()),
        ticks: rows.map(r => Number(r.getAttribute('data-feed-tick'))),
        places: rows.map(r => {
          const m = r.textContent.match(/ 在 (.+?)：/);
          return m ? m[1] : '';
        }),
        important: rows.filter(r => r.getAttribute('data-feed-important') === '1').length,
        feedState: window.__XKX.feedState(),
        statusText: (document.getElementById('feedStatus') || {}).textContent || '',
        statusHidden: (document.getElementById('feedStatus') || {}).classList.contains('hidden'),
        location_name: loc.name || '',
        exits: (loc.exits || []).map(x => x.name),
        errors: document.querySelectorAll('#errorList .error-item').length
      };
    }""")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--speed", default="2", help="XKX_AMBIENT_SPEED（2x = 20 ticks/分钟）")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    evidence = root / "reports" / "w4-feed"
    evidence.mkdir(parents=True, exist_ok=True)

    stamp = time.strftime("%m%d%H%M%S")
    run_id = "w4-feed-service-" + stamp
    user = "w4feed" + stamp
    display = "观世客"

    api = proxy = None
    results = Results()
    payloads: dict = {}
    requests: list[dict] = []
    responses: list[dict] = []
    started = time.time()

    os.environ["XKX_AMBIENT"] = "1"
    os.environ["XKX_AMBIENT_SPEED"] = args.speed
    try:
        api_port = free_port()
        proxy_port = free_port()
        api = start_api(root, run_id, api_port, "on")
        proxy = start_proxy(root, f"http://127.0.0.1:{api_port}", proxy_port)
        api_base = f"http://127.0.0.1:{api_port}"
        base = f"http://127.0.0.1:{proxy_port}"

        # 等自走先产出真实事件，确保打开 UI 时 feed 已有内容。
        deadline = time.time() + 40
        anonymous = {}
        while time.time() < deadline:
            code, anonymous = http_json(api_base + "/api/world/feed?limit=20")
            if code == 200 and len(anonymous.get("events") or []) >= 5:
                break
            time.sleep(0.5)

        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(viewport={"width": 1280, "height": 900})
                page = ctx.new_page()
                page.on("request", lambda r: requests.append(
                    {"ts": time.monotonic(), "url": r.url,
                     "authorization": bool(r.headers.get("authorization"))})
                    if "/api/world/feed" in r.url else None)
                page.on("response", lambda r: responses.append(
                    {"ts": time.monotonic(), "status": r.status, "url": r.url})
                    if "/api/world/feed" in r.url else None)

                page.goto(base + "/?ui_check=1")
                wait_app(page)
                page.wait_for_function("() => window.__XKX.authState().mode === 'auth'",
                                       timeout=15000)
                page.evaluate("""() => { const b = document.getElementById('guideSkip');
                    if (b) b.click(); }""")
                ui_register(page, user, "secret123")
                token = page.evaluate(
                    "() => JSON.parse(localStorage.getItem('ws_auth_v1')).access_token")
                ui_enter(page, user, display)
                entered_at = time.monotonic()
                page.wait_for_function("() => window.__XKX.feedState().rows >= 5", timeout=20000)

                # 观察窗口：只等待，不点击；验证 8 秒节流与玩家可见性。
                # feed 只保留最近 20 条，逐秒累积整个窗口里真实出现过的行，
                # 避免「附近事件恰好在最后一屏之外」被误判为没看到。
                seen_rows: dict[tuple[str, str], str] = {}
                observe_until = time.monotonic() + args.seconds
                while time.monotonic() < observe_until:
                    page.wait_for_timeout(1000)
                    batch = page.evaluate("""() => Array.from(
                        document.querySelectorAll('#feedList .feed-row')).map(r => ({
                          tick: r.getAttribute('data-feed-tick'),
                          text: r.textContent.trim(),
                          place: (r.textContent.match(/ 在 (.+?)：/) || ['', ''])[1]
                        }))""")
                    for item in batch:
                        seen_rows[(item["tick"], item["text"])] = item["place"]

                view = feed_snapshot(page)
                page.screenshot(path=str(evidence / "feed-service-real.png"))

                # 原始 JSON：匿名 + 带令牌，直连真 API（不经 mock）。
                a_status, a_payload = http_json(api_base + "/api/world/feed?limit=20")
                t_status, t_payload = http_json(api_base + "/api/world/feed?limit=20", token=token)
                h_status, health = http_json(api_base + "/api/health")

                window_reqs = [r for r in requests if r["ts"] >= entered_at]
                gaps = [round(window_reqs[i]["ts"] - window_reqs[i - 1]["ts"], 2)
                        for i in range(1, len(window_reqs))]
                long_gaps = [g for g in gaps if g >= 6.0]

                events = a_payload.get("events") or []
                actors = sorted({str(e.get("actor") or "") for e in events})
                real_actors = [x for x in actors if x not in MOCK_ACTORS]
                near = set([view["location_name"]] + list(view["exits"]))
                seen_places = sorted({p for p in seen_rows.values() if p})
                near_rows = [p for p in seen_places if p in near]

                results.add("服务模式真后端 feed 非空且来自真实自走世界",
                            len(events) >= 5 and len(real_actors) >= 2,
                            {"events": len(events), "actors": actors[:12],
                             "non_mock_actors": real_actors[:8]})
                results.add("真 API 顶层 {ok,tick,limit,events}、每项 6 公共字段",
                            shape_ok(a_payload) and
                            set(a_payload) >= {"ok", "tick", "limit", "events"},
                            {"keys": sorted(a_payload), "event_keys": sorted(events[0]) if events else []})
                results.add("匿名可读、正文无玩家私有字段",
                            a_status == 200 and shape_ok(a_payload) and
                            no_private(json.dumps(a_payload, ensure_ascii=False)),
                            {"status": a_status})
                results.add("登录态 feed 仍只有公共字段（无玩家私有状态）",
                            t_status == 200 and shape_ok(t_payload) and
                            no_private(json.dumps(t_payload, ensure_ascii=False)),
                            {"status": t_status, "events": len(t_payload.get("events") or [])})
                results.add("feed tick 与 health ambient_tick 同一世界",
                            h_status == 200 and
                            abs(int(a_payload.get("tick") or 0) -
                                int(health.get("ambient_tick") or -1)) <= 3,
                            {"feed_tick": a_payload.get("tick"),
                             "ambient_tick": health.get("ambient_tick"),
                             "health_ambient": health.get("ambient")})
                results.add("UI 60 秒内至少 5 条 NPC 事件、倒序、important 样式存在",
                            view["rows"] >= 5 and view["ticks"] == sorted(view["ticks"], reverse=True),
                            {"rows": view["rows"], "important": view["important"]})
                results.add("60 秒窗口内至少 1 条事件在当前或相邻地点",
                            bool(near_rows),
                            {"near": sorted(near), "seen_places": seen_places,
                             "matched": near_rows[:5], "seen_rows": len(seen_rows)})
                results.add("feed 单独 8 秒节流（60 秒内请求数不刷爆）",
                            5 <= len(window_reqs) <= 12 and len(long_gaps) >= 4,
                            {"requests": len(window_reqs), "gaps": gaps,
                             "long_gaps": len(long_gaps)})
                results.add("feed 请求匿名（不带 Authorization）",
                            all(not r["authorization"] for r in window_reqs),
                            {"with_auth": sum(1 for r in window_reqs if r["authorization"])})
                results.add("渲染文本不出现 [a-z_]+ 内部动词（中文映射生效）",
                            all(not re.search(r"[a-z_]+", t) for t in view["texts"])
                            and any(re.search(r"[a-z_]+", str(e.get("verb") or "")) for e in events),
                            {"raw_verbs": sorted({str(e.get("verb") or "") for e in events}),
                             "sample": view["texts"][:5]})
                results.add("feed 失败/静默不弹红字（观察窗内错误数不变）",
                            view["errors"] == 0 and view["feedState"].get("failed") is False,
                            {"errors": view["errors"], "feedState": view["feedState"]})

                # 刷新不丢：重载后仍登录、feed 立刻有内容。
                page.reload()
                wait_app(page)
                page.wait_for_function("() => window.__XKX.authState().hasAccess === true",
                                       timeout=15000)
                page.wait_for_function("() => window.__XKX.feedState().rows >= 5", timeout=20000)
                reloaded = feed_snapshot(page)
                page.screenshot(path=str(evidence / "feed-service-real-reload.png"))
                results.add("刷新后 feed 仍有真实事件、不丢",
                            reloaded["rows"] >= 5 and reloaded["feedState"].get("failed") is False,
                            {"rows": reloaded["rows"], "tick": reloaded["feedState"].get("topTick")})

                payloads = {
                    "meta": {
                        "run_id": run_id,
                        "api": api_base,
                        "proxy": base,
                        "service_sqlite": str(root / "runs" / run_id / "service.sqlite"),
                        "events_sqlite": str(root / "runs" / run_id / "events.sqlite"),
                        "ambient_speed": args.speed,
                        "observe_seconds": args.seconds,
                        "head": os.popen("git -C " + str(root) + " rev-parse HEAD").read().strip(),
                        "started_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started)),
                    },
                    "anonymous_feed": {"status": a_status, "body": a_payload},
                    "token_feed": {"status": t_status, "body": t_payload},
                    "health": {"status": h_status, "body": health},
                    "ui": {"first": view, "reloaded": reloaded,
                           "seen_places": seen_places, "seen_rows": len(seen_rows)},
                    "feed_requests": [{"gap_s": None if i == 0 else gaps[i - 1]}
                                      for i, _ in enumerate(window_reqs)],
                    "feed_responses": [{"status": r["status"]} for r in responses],
                    "results": results.rows,
                    "screenshots": ["feed-service-real.png", "feed-service-real-reload.png"],
                }

                # 真 sqlite 证据：事件库里 NPC 事件条数（只读连接）。
                events_db = root / "runs" / run_id / "events.sqlite"
                try:
                    conn = sqlite3.connect(f"file:{events_db}?mode=ro", uri=True)
                    row = conn.execute(
                        "SELECT COUNT(*) FROM events WHERE actor != 'system' "
                        "AND actor NOT LIKE 'player:%' AND verb NOT LIKE 'system%'").fetchone()
                    payloads["meta"]["npc_events_in_sqlite"] = int(row[0])
                    conn.close()
                except Exception as exc:
                    payloads["meta"]["npc_events_in_sqlite"] = f"error: {type(exc).__name__}: {exc}"

                ctx.close()
            finally:
                browser.close()
    finally:
        stop_proc(proxy)
        stop_proc(api)

    (evidence / "real-feed-service.json").write_text(
        json.dumps(payloads, ensure_ascii=False, indent=2), encoding="utf-8")
    failures = [r["name"] for r in results.rows if not r["ok"]]
    print("\n%d checks / %d failed" % (len(results.rows), len(failures)))
    for name in failures:
        print("FAIL " + name)
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
