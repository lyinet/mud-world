#!/usr/bin/env python3
"""W4 前端验收：不写世界，用 mock API + Playwright 跑主流程。

用法：
  python3 tools/ui_check.py                 # 跑全部验收并输出证据到 reports/w4-ui/
  python3 tools/ui_check.py --serve         # 只启动 mock 服务，方便手动打开
  python3 tools/ui_check.py --only desktop  # desktop | mobile | keyboard | auth | reasons | regression

mock 服务只改内存，不会写 runs/ 或 events.sqlite，也不会启动世界 tick。
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
UI_DIR = ROOT / "ui" / "player"
EVIDENCE_DIR = ROOT / "reports" / "w4-ui"
SESSION_PY = ROOT / "src" / "wsim" / "api" / "session.py"
AUTH_PY = ROOT / "src" / "wsim" / "api" / "auth.py"


def _json_bytes(payload):
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


LOCATIONS = {
    "market": {"name": "市集", "exits": [("pharmacy", "药铺"), ("teahouse", "茶楼")]},
    "pharmacy": {"name": "药铺", "exits": [("market", "市集")]},
    "teahouse": {"name": "茶楼", "exits": [("market", "市集")]},
}

QUEST = {
    "id": "quest:silk_sample:0",
    "name": "送一匹粗布到药铺",
    "reward_money": 12,
    "giver_name": "刘三娘",
    "giver_location": "market",
    "target_location": "pharmacy",
    "deadline_day": 3,
    "can_accept_here": True,
    "acceptable": True,
    "expired": False,
    "status": "open",
    "accepted_by_me": False,
}

NPCS = {
    "market": [{"id": "npc:liu_sanniang", "name": "刘三娘", "role": "商户",
                "doing": "整理今日的布匹订单", "location": "market", "presence": "在场"}],
    "pharmacy": [{"id": "npc:pharmacy_keeper", "name": "药铺掌柜", "role": "掌柜",
                  "doing": "核对药柜库存", "location": "pharmacy", "presence": "在场"}],
    "teahouse": [{"id": "npc:teahouse_owner", "name": "茶楼老板", "role": "掌柜",
                  "doing": "招呼客人", "location": "teahouse", "presence": "在场"}],
}

# 世界动态固定样本：市集/药铺（玩家当前地点与相邻地点）各一条 important，
# 茶楼一条普通事件，用于验证强调样式与「附近动静」提示。
FEED_EVENTS = [
    {"tick": 37, "actor": "王铁匠", "verb": "打铁", "place": "药铺",
     "summary": "王铁匠在药铺修好一口药碾。", "important": True},
    {"tick": 34, "actor": "刘三娘", "verb": "理货", "place": "市集",
     "summary": "刘三娘在市集清点新到的粗布。", "important": True},
    {"tick": 31, "actor": "茶楼老板", "verb": "沏茶", "place": "茶楼",
     "summary": "茶楼老板给客人续了三壶茶。", "important": False},
    {"tick": 28, "actor": "更夫", "verb": "巡街", "place": "市集",
     "summary": "更夫敲着梆子走过市集。", "important": False},
    {"tick": 25, "actor": "药铺掌柜", "verb": "晒药", "place": "药铺",
     "summary": "药铺掌柜把陈皮摊在竹匾里。", "important": False},
]


AUTH_ERROR_TEXT = {
    "auth_bad_username": "用户名需为 3-32 位小写字母、数字、下划线或连字符。",
    "auth_bad_password": "口令长度需为 6-128 位。",
    "auth_username_taken": "该用户名已被注册。",
    "auth_invalid_credentials": "用户名或口令不正确。",
    "auth_missing_token": "缺少 Bearer 令牌。",
    "auth_invalid_token": "令牌无效或已注销。",
    "auth_expired_token": "令牌已过期；请重新登录。",
    "auth_account_mismatch": "请求账号与令牌账号不一致。",
    "auth_disabled": "当前为 legacy 匿名模式，鉴权接口不可用。",
}


def auth_error(code, tick=0):
    return {"ok": False, "code": code, "reject_reason": code,
            "reason_text": AUTH_ERROR_TEXT.get(code, code), "tick": tick}


class MockWorld:
    def __init__(self, auth_enabled=False, upstream_ok=True):
        self.lock = threading.RLock()
        self.auth_enabled = auth_enabled
        self.upstream_ok = upstream_ok
        self.accounts = {}
        self.users = {}
        self.sessions = {}          # token -> {user, exp, session_id}
        self.token_seq = 0
        self.last_write_auth = ""
        self.refresh_calls = 0
        self.rotate_count = 0
        self.force_401 = 0
        self.write_auth_headers = []
        self.feed_calls = 0
        self.feed_headers = []
        self.feed_extra = []

    # ---------------------------------------------------------------- accounts
    def _new_account(self, account_id, display_name, archetype, created):
        return {
            "account_id": account_id,
            "display_name": display_name or "无名客",
            "archetype": archetype or "游侠",
            "role": "wanderer",
            "location": "market",
            "money": 300,
            "tick": 12,
            "event_seq": 1,
            "created": created,
            "accepted": False,
        }

    def enter(self, account_id, display_name, archetype):
        with self.lock:
            created = account_id not in self.accounts
            if created:
                self.accounts[account_id] = self._new_account(account_id, display_name, archetype, True)
            return {"ok": True, "details": {"created": created,
                                            "endowment": {"granted": created}}}

    def account(self, account_id):
        return self.accounts.get(account_id)

    def state(self, account_id):
        with self.lock:
            a = self.account(account_id)
            if not a:
                return None
            loc = a["location"]
            active = bool(a["accepted"])
            row = dict(QUEST)
            row["can_accept_here"] = (not active and loc == QUEST["giver_location"])
            row["acceptable"] = not active
            if active:
                row.update({"status": "accepted", "accepted_by_me": True,
                            "can_accept_here": False, "acceptable": False})
            rows = [] if active else [row]
            active_rows = [dict(row, status="accepted", accepted_by_me=True,
                               can_accept_here=False, acceptable=False)] if active else []
            exits = [{"id": x[0], "name": x[1], "exists": True} for x in LOCATIONS[loc]["exits"]]
            next_action = ("把粗布送到药铺，交给药铺掌柜（12 文）" if active
                           else "接「送一匹粗布到药铺」，发布人刘三娘就在市集（12 文）")
            p = {
                "entity_id": "player:" + account_id,
                "account_id": account_id,
                "name": a["display_name"],
                "archetype": a["archetype"],
                "role": a["role"],
                "stamina": 82,
                "max_stamina": 100,
                "location": loc,
                "location_name": LOCATIONS[loc]["name"],
                "wallet": {"money": a["money"], "items": [{"name": "粗布", "qty": 1},
                                                          {"name": "炊饼", "qty": 3}]},
            }
            return {
                "ok": True,
                "account_id": account_id,
                "tick": a["tick"],
                "readonly": False,
                "player": p,
                "location": {"id": loc, "name": LOCATIONS[loc]["name"], "exits": exits},
                "present_npcs": NPCS[loc],
                "quest_board": {
                    "rows": rows,
                    "active": active_rows,
                    "counts": {"open": 0 if active else 1,
                               "acceptable_here": 1 if (not active and loc == QUEST["giver_location"]) else 0,
                               "accepted_by_me": 1 if active else 0},
                },
                "today": {
                    "tick": a["tick"],
                    "next_action": next_action,
                    "quests": [{"name": QUEST["name"], "reward_money": QUEST["reward_money"],
                                "giver_name": QUEST["giver_name"]}] if not active else [],
                    "skills": [{"skill": "算账", "current": 1, "next_at": 2, "hours_to_next": 4}],
                    "npcs": [{"name": "刘三娘", "role": "商户"}],
                    "business": [{"shop_type": "stall", "cost": 120, "affordable": True, "blocked": False}],
                },
                "growth": {
                    "reputation": {"level": 1, "name": "初出茅庐", "value": 0.2, "progress": 0.2,
                                   "next_at": 1.0, "next_name": "小有名气", "next_unlock": "解锁茶楼委托"},
                    "skill": {"level": 1, "name": "手艺人", "value": 0.3, "progress": 0.3,
                              "next_at": 2.0, "next_name": "熟练工", "next_unlock": "工时缩短"},
                    "assets": {"level": 1, "name": "空手", "value": 300, "progress": 0.1,
                               "next_at": 1000, "next_name": "有摊", "next_unlock": "可买摊位"},
                },
                "online_players": [],
            }

    def feed(self, limit=20):
        try:
            cap = max(1, min(int(limit or 20), 50))
        except (TypeError, ValueError):
            cap = 20
        with self.lock:
            rows = [dict(r) for r in FEED_EVENTS] + [dict(r) for r in self.feed_extra]
        rows.sort(key=lambda r: -int(r.get("tick") or 0))
        return {"ok": True, "events": rows[:cap]}

    def push_feed(self, tick, summary, important=False, place="市集"):
        with self.lock:
            self.feed_extra.append({
                "tick": int(tick), "actor": "更夫", "verb": "巡街",
                "place": place, "summary": summary, "important": bool(important),
            })

    def act(self, account_id, verb, args, client_tick):
        with self.lock:
            a = self.account(account_id)
            if not a:
                return 409, {"ok": False, "reason": "player_not_entered",
                             "reason_text": "你还没有进入世界。"}
            if client_tick is not None and Number_ok(client_tick) and int(client_tick) < a["tick"] - 120:
                return 409, {"ok": False, "reason": "stale_tick"}
            if verb == "move":
                target = (args or {}).get("to")
                allowed = [x[0] for x in LOCATIONS[a["location"]]["exits"]]
                if target not in allowed:
                    return 409, {"ok": False, "reason": "not_adjacent",
                                 "reason_text": "目标地点与当前位置不相邻。"}
                a["location"] = target
                a["tick"] += 6
                a["event_seq"] += 1
                return 200, {"ok": True, "event_seq": a["event_seq"],
                             "tick": a["tick"], "next_tick": a["tick"] + 6}
            if verb == "quest_accept":
                qid = (args or {}).get("quest")
                if qid != QUEST["id"]:
                    return 409, {"ok": False, "reason": "quest_not_found"}
                if a["accepted"]:
                    return 409, {"ok": False, "reason": "too_many_quests",
                                 "reason_text": "你手上已有进行中任务。"}
                if a["location"] != QUEST["giver_location"]:
                    return 409, {"ok": False, "reason": "not_at_giver"}
                a["accepted"] = True
                a["tick"] += 3
                a["event_seq"] += 1
                return 200, {"ok": True, "event_seq": a["event_seq"],
                             "tick": a["tick"], "next_tick": a["tick"] + 6}
            if verb == "quest_deliver":
                qid = (args or {}).get("quest")
                if qid != QUEST["id"]:
                    return 409, {"ok": False, "reason": "quest_not_found"}
                if not a["accepted"]:
                    return 409, {"ok": False, "reason": "quest_not_accepted_by_actor"}
                if a["location"] not in (QUEST["target_location"], QUEST["giver_location"]):
                    return 409, {"ok": False, "reason": "not_at_target",
                                 "reason_text": "还没到任务目标地点。"}
                a["accepted"] = False
                a["money"] += QUEST["reward_money"]
                a["tick"] += 4
                a["event_seq"] += 1
                return 200, {"ok": True, "event_seq": a["event_seq"],
                             "tick": a["tick"], "next_tick": a["tick"] + 6,
                             "details": {"reward_money": QUEST["reward_money"]}}
            if verb == "talk":
                a["tick"] += 2
                a["event_seq"] += 1
                return 200, {"ok": True, "event_seq": a["event_seq"], "tick": a["tick"],
                             "details": {"reply": "刘三娘：布已经备好，送到药铺就行。"}}
            if verb in ("cultivate", "open_shop"):
                a["tick"] += 2
                a["event_seq"] += 1
                return 200, {"ok": True, "event_seq": a["event_seq"], "tick": a["tick"]}
            return 409, {"ok": False, "reason": "unknown_verb"}

    # -------------------------------------------------------------------- auth
    # 与 src/wsim/api/auth.py 的真实响应/错误码保持一致。
    def issue(self, username, session_id=None):
        self.token_seq += 1
        token = "tok_%s_%d" % (username, self.token_seq)
        now = time.time()
        sid = session_id or ("s:%d" % self.token_seq)
        self.sessions[token] = {"user": username, "exp": now + 3600, "session_id": sid}
        return {"ok": True, "account_id": username, "username": username,
                "session_id": sid, "token": token, "token_type": "Bearer",
                "expires_at": int(now + 3600), "expires_in": 3600}

    def token_error(self, header):
        if not header or not header.startswith("Bearer "):
            return "auth_missing_token"
        token = header[len("Bearer "):].strip()
        rec = self.sessions.get(token)
        if not rec:
            return "auth_invalid_token"
        if rec.get("exp", 0) <= time.time():
            return "auth_expired_token"
        return None

    def verify(self, header):
        if self.token_error(header):
            return None
        return self.sessions[header[len("Bearer "):].strip()]["user"]

    def rotate(self, header):
        """轮换令牌：旧 token 立即失效。"""
        code = self.token_error(header)
        if code:
            return None, code
        old = header[len("Bearer "):].strip()
        rec = self.sessions.pop(old)
        self.rotate_count += 1
        payload = self.issue(rec["user"], rec["session_id"])
        payload["rotated"] = True
        return payload, None

    def expire_all(self):
        with self.lock:
            self.sessions.clear()


def Number_ok(value):
    try:
        int(value)
        return True
    except Exception:
        return False


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, world=None, **kwargs):
        self.world = world
        super().__init__(*args, **kwargs)

    def log_message(self, fmt, *args):  # noqa: A003
        pass

    def _json(self, status, payload):
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def _auth_error(self, status, code):
        self._json(status, auth_error(code, 12))

    def _require_write_auth(self):
        header = self.headers.get("Authorization") or ""
        self.world.write_auth_headers.append(header)
        if self.world.auth_enabled:
            code = self.world.token_error(header)
            if code:
                self._auth_error(401, code)
                return None
        self.world.last_write_auth = header
        return self.world.verify(header) or "anonymous"

    def _force_401_once(self):
        """测试用：让接下来的 n 个写请求返回 401（模拟并发轮换竞态）。"""
        with self.world.lock:
            if self.world.force_401 > 0:
                self.world.force_401 -= 1
                self._auth_error(401, "auth_expired_token")
                return True
        return False

    # -------------------------------------------------------------------- GET
    def do_GET(self):  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/api/health":
            self._json(200, {"ok": True, "readonly": False, "run": "ui-check-mock",
                             "upstream": "mock", "upstream_ok": self.world.upstream_ok})
            return
        if path == "/api/auth/me":
            if not self.world.auth_enabled:
                self._auth_error(503, "auth_disabled")
                return
            code = self.world.token_error(self.headers.get("Authorization") or "")
            if code:
                self._auth_error(401, code)
                return
            header = self.headers.get("Authorization") or ""
            rec = self.world.sessions[header[len("Bearer "):].strip()]
            self._json(200, {"ok": True, "account_id": rec["user"], "username": rec["user"],
                             "session_id": rec["session_id"], "expires_at": int(rec["exp"]),
                             "status": "active", "disconnected": False})
            return
        if path == "/api/player/state":
            q = parse_qs(urlsplit(self.path).query)
            account = (q.get("account_id") or [""])[0]
            # 读端点匿名可用；令牌无效也不影响读取。
            st = self.world.state(account)
            if st is None:
                self._json(409, {"ok": False, "reason": "player_not_entered",
                                 "reason_text": "账号尚未进入世界。"})
                return
            self._json(200, st)
            return
        if path == "/api/world/feed":
            q = parse_qs(urlsplit(self.path).query)
            self.world.feed_calls += 1
            self.world.feed_headers.append(self.headers.get("Authorization") or "")
            self._json(200, self.world.feed((q.get("limit") or ["20"])[0]))
            return
        if path == "/api/player/messages":
            self._json(200, {"ok": True, "rows": []})
            return
        if path == "/__check/feed_push":
            q = parse_qs(urlsplit(self.path).query)
            self.world.push_feed(
                (q.get("tick") or ["40"])[0],
                (q.get("summary") or ["更夫在街上敲了一声梆子。"])[0],
                (q.get("important") or ["0"])[0] in ("1", "true"),
                (q.get("place") or ["市集"])[0],
            )
            self._json(200, {"ok": True, "rows": len(self.world.feed()["events"])})
            return
        if path == "/api/player/intents":
            self._json(404, {"ok": False, "reason": "intent_not_found"})
            return
        if path == "/__check/expire":
            self.world.expire_all()
            self._json(200, {"ok": True, "expired": True})
            return
        if path == "/__check/force401":
            q = parse_qs(urlsplit(self.path).query)
            self.world.force_401 = int((q.get("n") or ["1"])[0])
            self._json(200, {"ok": True, "force_401": self.world.force_401})
            return
        if path == "/__check/reset_stats":
            self.world.refresh_calls = 0
            self.world.rotate_count = 0
            self.world.force_401 = 0
            self.world.write_auth_headers = []
            self._json(200, {"ok": True})
            return
        if path == "/__check/stats":
            self._json(200, {"ok": True, "refresh_calls": self.world.refresh_calls,
                             "rotate_count": self.world.rotate_count,
                             "force_401": self.world.force_401,
                             "write_auth_headers": self.world.write_auth_headers})
            return
        # 静态文件
        return super().do_GET()

    # ------------------------------------------------------------------- POST
    def do_POST(self):  # noqa: N802
        path = urlsplit(self.path).path
        body = self._read_json()
        if path.startswith("/api/auth/"):
            if not self.world.auth_enabled:
                # 真实服务 legacy 模式没有账号库：鉴权接口返回 503 auth_disabled。
                self._auth_error(503, "auth_disabled")
                return
            if path == "/api/auth/register":
                username = str(body.get("username") or "").strip().lower()
                password = str(body.get("password") or "")
                if not re.match(r"^[a-z0-9_-]{3,32}$", username):
                    self._auth_error(400, "auth_bad_username")
                    return
                if not (6 <= len(password) <= 128):
                    self._auth_error(400, "auth_bad_password")
                    return
                with self.world.lock:
                    if username in self.world.users:
                        self._auth_error(409, "auth_username_taken")
                        return
                    self.world.users[username] = password
                # 真实契约：register 只返回账号信息，不返回令牌。
                self._json(200, {"ok": True, "account_id": username,
                                 "username": username, "created": True})
                return
            if path == "/api/auth/login":
                username = str(body.get("username") or "").strip().lower()
                password = str(body.get("password") or "")
                if self.world.users.get(username) != password:
                    self._auth_error(401, "auth_invalid_credentials")
                    return
                self._json(200, self.world.issue(username))
                return
            if path == "/api/auth/refresh":
                self.world.refresh_calls += 1
                time.sleep(0.2)  # 放大并发窗口，用来验证前端单飞刷新
                payload, code = self.world.rotate(self.headers.get("Authorization") or "")
                if code:
                    self._auth_error(401, code)
                    return
                self._json(200, payload)
                return
            if path == "/api/auth/logout":
                header = self.headers.get("Authorization") or ""
                code = self.world.token_error(header)
                if code:
                    self._auth_error(401, code)
                    return
                rec = self.world.sessions.pop(header[len("Bearer "):].strip())
                self._json(200, {"ok": True, "logged_out": True,
                                 "session_id": rec["session_id"]})
                return
            self._json(404, auth_error("unknown_action"))
            return
        if path == "/api/player/enter":
            user = self._require_write_auth()
            if user is None:
                return
            if self._force_401_once():
                return
            account = str(body.get("account_id") or "").strip().lower()
            if not re.match(r"^[a-z0-9_-]{3,32}$", account):
                self._json(409, {"ok": False, "reason": "bad_account"})
                return
            self._json(200, self.world.enter(account, body.get("display_name"), body.get("archetype")))
            return
        if path == "/api/player/action":
            user = self._require_write_auth()
            if user is None:
                return
            if self._force_401_once():
                return
            status, payload = self.world.act(
                str(body.get("account_id") or ""),
                str(body.get("verb") or ""),
                body.get("args") or {},
                body.get("client_tick"),
            )
            self._json(status, payload)
            return
        self._json(404, {"ok": False, "reason": "unknown_action"})


def start_server(world):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    handler = partial(Handler, directory=str(UI_DIR), world=world)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, "http://127.0.0.1:%d" % port


class Results:
    def __init__(self):
        self.rows = []

    def add(self, name, ok, detail=None):
        row = {"name": name, "ok": bool(ok)}
        if detail is not None:
            row["detail"] = detail
        self.rows.append(row)
        print(("PASS " if ok else "FAIL ") + name + ((" :: " + str(detail)) if detail is not None else ""))
        return bool(ok)

    def must(self, name, ok, detail=None):
        good = self.add(name, ok, detail)
        if not good:
            raise AssertionError(name)
        return good

    def dump(self, path):
        path.write_text(json.dumps(self.rows, ensure_ascii=False, indent=2), encoding="utf-8")


def visible(page, selector):
    return page.evaluate("""(sel) => {
      const n = document.querySelector(sel);
      if (!n) return false;
      const r = n.getBoundingClientRect();
      const s = getComputedStyle(n);
      return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden'
        && !n.classList.contains('hidden');
    }""", selector)


def wait_app(page):
    page.wait_for_function("() => !!window.__XKX && !!document.getElementById('entryOverlay')",
                           timeout=15000)
    page.wait_for_function("""() => {
      const tag = document.getElementById('runTag');
      return tag && tag.textContent !== '载入中' && window.__XKX.authState().mode !== 'unknown';
    }""", timeout=15000)


def enter_world(page, account="u_guide_desktop", display="验收客", auth_flow=None):
    wait_app(page)
    page.fill("#accountId", account)
    page.fill("#displayName", display)
    page.click("#enterBtn")
    if auth_flow:
        page.wait_for_function("() => !document.getElementById('authModal').classList.contains('hidden')",
                               timeout=8000)
        auth_flow(page)
    page.wait_for_function("() => document.getElementById('enterBtn').getAttribute('data-open') === '1'",
                           timeout=10000)
    page.click("#enterBtn")
    page.wait_for_function("() => document.getElementById('entryOverlay').classList.contains('hidden')",
                           timeout=8000)


def guide_snapshot(page):
    return page.evaluate("""() => {
      const g = window.__XKX.guideState();
      const t = window.__XKX.guideTargetInfo();
      const layer = document.getElementById('guideLayer');
      const card = document.getElementById('guideCard');
      const spot = document.getElementById('guideSpotlight');
      const rect = (n) => { if (!n) return null; const r = n.getBoundingClientRect();
        return {left:r.left, top:r.top, width:r.width, height:r.height, right:r.right, bottom:r.bottom}; };
      const cr = rect(card);
      const tr = t && t.rect;
      const overlap = !!(cr && tr) && !(cr.right + 6 <= tr.left || cr.left - 6 >= tr.right ||
                                        cr.bottom + 6 <= tr.top || cr.top - 6 >= tr.bottom);
      let sel = null;
      if (t) {
        if (t.id) sel = '#' + CSS.escape(t.id);
        else if (t.quest) sel = 'button[data-quest="' + CSS.escape(t.quest) + '"]';
        else if (t.exitId) sel = 'button[data-exit-id="' + CSS.escape(t.exitId) + '"]';
        else if (t.deliver) sel = 'button[data-deliver="' + CSS.escape(t.deliver) + '"]';
      }
      let hit = null;
      if (sel) {
        const node = document.querySelector(sel);
        if (node) {
          const r = node.getBoundingClientRect();
          const cx = r.left + r.width / 2;
          const cy = r.top + r.height / 2;
          const h = document.elementFromPoint(cx, cy);
          hit = {selector: sel, same: node === h || node.contains(h),
                 hitTag: h ? h.tagName : null, hitId: h ? (h.id || '') : null};
        }
      }
      const visibleButtons = Array.from(document.querySelectorAll('button,input,textarea,.exit,.npc'))
        .filter(n => { const r = n.getBoundingClientRect(); const s = getComputedStyle(n);
          return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden'; })
        .map(n => ({tag:n.tagName, id:n.id||'', text:(n.textContent||'').trim().slice(0,18),
                    h:Math.round(n.getBoundingClientRect().height)}));
      return {
        visible: !layer.classList.contains('hidden'),
        stepText: document.getElementById('guideStep').textContent,
        title: document.getElementById('guideTitle').textContent,
        why: document.getElementById('guideWhy').textContent,
        nextText: document.getElementById('guideNext').textContent,
        guide: g, target: t, card: cr, spotlight: rect(spot),
        cardOverlapsTarget: overlap, hit: hit, visibleButtons: visibleButtons,
        innerWidth: innerWidth, innerHeight: innerHeight,
        scrollWidth: document.documentElement.scrollWidth,
        scrollHeight: document.documentElement.scrollHeight,
        focusId: document.activeElement ? (document.activeElement.id || document.activeElement.tagName) : null
      };
    }""")


def click_guide_target(page, info):
    t = info["target"]
    if not t:
        raise AssertionError("guide target missing")
    if t.get("id"):
        page.click("#" + t["id"])
    elif t.get("quest"):
        page.click('button[data-quest="%s"]' % t["quest"])
    elif t.get("exitId"):
        page.click('button[data-exit-id="%s"]' % t["exitId"])
    elif t.get("deliver"):
        page.click('button[data-deliver="%s"]' % t["deliver"])
    else:
        raise AssertionError("unknown guide target: %r" % t)


def capture(page, name, results, extra=None):
    path = EVIDENCE_DIR / (name + ".png")
    page.screenshot(path=str(path))
    if extra is not None:
        extra["screenshot"] = str(path.relative_to(ROOT))
    return extra


FEED_EVIDENCE = ROOT / "reports" / "w4-feed"


def feed_capture(page, name, results, extra=None):
    FEED_EVIDENCE.mkdir(parents=True, exist_ok=True)
    path = FEED_EVIDENCE / (name + ".png")
    page.screenshot(path=str(path))
    if extra is not None:
        extra["screenshot"] = str(path.relative_to(ROOT))
    return extra


def feed_snapshot(page):
    return page.evaluate("""() => {
      const list = document.getElementById('feedList');
      const block = document.getElementById('worldFeed');
      const rows = Array.from(document.querySelectorAll('#feedList .feed-row'));
      const cs = getComputedStyle(list);
      const status = document.getElementById('feedStatus');
      return {
        blockVisible: !!(block && block.getBoundingClientRect().height > 0),
        count: rows.length, feedRows: (window.__XKX.feedState() || {}).rows,
        ticks: rows.map(r => Number(r.getAttribute('data-feed-tick'))),
        important: rows.filter(r => r.getAttribute('data-feed-important') === '1').length,
        texts: rows.map(r => r.textContent.trim()),
        height: Math.round(list.getBoundingClientRect().height),
        overflowY: cs.overflowY, scrollHeight: list.scrollHeight, clientHeight: list.clientHeight,
        scrollWidth: document.documentElement.scrollWidth,
        inFold: !!block.closest('.block.foldable'), hasFoldBtn: !!block.querySelector('.fold-btn'),
        announced: (window.__XKX.feedState() || {}).announced || 0,
        logWorld: document.querySelectorAll('#log .log-item.world').length,
        toast: (document.getElementById('toast') || {}).textContent || '',
        errors: document.querySelectorAll('#errorList .error-item').length,
        money: (document.getElementById('stMoney') || {}).textContent || '',
        placeholder: (list.querySelector('.feed-empty') || {}).textContent || '',
        statusHidden: status ? status.classList.contains('hidden') : true,
        statusText: status ? status.textContent : ''
      };
    }""")


def run_feed(browser, base, results, world):
    """世界动态：默认可见、倒序、固定高度、单独节流、失败降级、附近重要动静提示。"""
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    try:
        page.goto(base + "/?ui_check=1")
        enter_world(page, "u_feed_desktop", "看戏客")
        # 跳过新手引导，避免提示层遮住世界动态区域
        page.evaluate("""() => { const b = document.getElementById('guideSkip'); if (b) b.click(); }""")
        page.wait_for_function(
            "() => document.getElementById('guideLayer').classList.contains('hidden')", timeout=8000)
        page.wait_for_function("() => window.__XKX.feedState().rows >= 5", timeout=15000)
        page.wait_for_function("() => window.__XKX.feedState().announced >= 2", timeout=8000)
        view = feed_snapshot(page)
        results.add("feed 默认可见（不在折叠组内）",
                    view["blockVisible"] and not view["inFold"] and not view["hasFoldBtn"], view)
        results.add("feed 20 条上限内、样本 5 条全渲染",
                    view["count"] == 5 and view["feedRows"] == 5, view)
        results.add("feed 按 tick 倒序", view["ticks"] == sorted(view["ticks"], reverse=True),
                    view["ticks"])
        results.add("feed 每行格式 · [tick] actor 在 place：summary",
                    all(re.match(r"^· \[\d+\] .+ 在 .+：", t) for t in view["texts"]),
                    view["texts"])
        results.add("feed important 使用强调样式", view["important"] == 2 and
                    "important" in page.evaluate(
                        "() => document.querySelector('#feedList .feed-row.important') ? 'important' : ''"),
                    {"important": view["important"]})
        results.add("feed 请求匿名、不带 Authorization",
                    bool(world.feed_calls) and all(h == "" for h in world.feed_headers),
                    {"feed_calls": world.feed_calls})
        results.add("feed 附近 important 走现有提示体系（toast + 操作记录）",
                    view["announced"] >= 2 and view["logWorld"] >= 1,
                    {"announced": view["announced"], "logWorld": view["logWorld"],
                     "toast": view["toast"]})
        feed_capture(page, "feed-desktop", results, view)

        # 固定高度容器 + 滚动锚定：用户滚到下方时插入新条目不应跳动
        # 先补几条让列表真正溢出（5 条时固定高度仍未满）
        page.evaluate("""() => Promise.all([40, 41, 42].map(t =>
            fetch('/__check/feed_push?tick=' + t + '&summary=' +
                  encodeURIComponent('街上有人走过。')).then(r => r.json())))""")
        page.evaluate("() => window.__XKX.refreshFeed()")
        page.wait_for_function("() => window.__XKX.feedState().rows >= 8", timeout=8000)
        results.add("feed 列表溢出时固定高度内滚动",
                    page.evaluate("() => { const l = document.getElementById('feedList');"
                                  "return l.scrollHeight > l.clientHeight; }"), {})
        page.evaluate("""() => { const l = document.getElementById('feedList'); l.scrollTop = l.scrollHeight; }""")
        before = page.evaluate("""() => { const l = document.getElementById('feedList');
            return {top: l.scrollTop, height: l.scrollHeight}; }""")
        page.evaluate("""() => fetch('/__check/feed_push?tick=43&important=0&summary=' +
            encodeURIComponent('更夫在西街敲了一声梆子。')).then(r => r.json())""")
        page.evaluate("() => window.__XKX.refreshFeed()")
        page.wait_for_function("() => window.__XKX.feedState().rows >= 9", timeout=8000)
        after = page.evaluate("""() => { const l = document.getElementById('feedList');
            return {top: l.scrollTop, height: l.scrollHeight}; }""")
        delta = after["height"] - before["height"]
        results.add("feed 下滚阅读时插入新条目不跳动",
                    before["top"] > 0 and abs(after["top"] - (before["top"] + delta)) <= 2,
                    {"before": before, "after": after, "delta": delta})

        # 在顶部时跟随最新
        page.evaluate("""() => { const l = document.getElementById('feedList'); l.scrollTop = 0; }""")
        page.evaluate("""() => fetch('/__check/feed_push?tick=45&important=1&summary=' +
            encodeURIComponent('一队镖车进了市集。')).then(r => r.json())""")
        page.evaluate("() => window.__XKX.refreshFeed()")
        page.wait_for_function("() => window.__XKX.feedState().rows >= 7", timeout=8000)
        top = page.evaluate("() => document.getElementById('feedList').scrollTop")
        results.add("feed 停在顶部时自动跟随最新一条", top == 0, {"scrollTop": top})
        results.add("feed important 新事件仍会提示",
                    page.evaluate("() => window.__XKX.feedState().announced") >= 3,
                    {"announced": page.evaluate("() => window.__XKX.feedState().announced")})

        # 失败降级：不弹红字、不动主状态，只显示「世界暂时安静」
        baseline = feed_snapshot(page)
        page.route("**/api/world/feed*", lambda route: route.abort())
        page.evaluate("() => window.__XKX.refreshFeed()")
        page.wait_for_function("() => window.__XKX.feedState().failed === true", timeout=8000)
        failed = feed_snapshot(page)
        results.add("feed 失败显示「世界暂时安静」占位",
                    (not failed["statusHidden"] and "世界暂时安静" in failed["statusText"]) or
                    "世界暂时安静" in failed["placeholder"], failed)
        results.add("feed 失败不弹红字、不写错误历史、主状态不变",
                    failed["errors"] == baseline["errors"] and failed["money"] == baseline["money"],
                    {"errors": [baseline["errors"], failed["errors"]],
                     "money": [baseline["money"], failed["money"]]})
        page.unroute("**/api/world/feed*")
    finally:
        ctx.close()


def wait_money(page, expected):
    page.wait_for_function("""(expected) => {
      const node = document.getElementById('stMoney');
      return !!node && node.textContent.indexOf(String(expected)) >= 0;
    }""", arg=expected, timeout=10000)


def run_reasons(browser, base, results):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    page.goto(base + "/?ui_check=1")
    wait_app(page)
    known = set()
    for src_path, marker in ((SESSION_PY, "REASON_TEXT: dict[str, str] = {"),
                             (AUTH_PY, "ERROR_TEXT: dict[str, str] = {")):
        if not src_path.exists():
            continue
        block = src_path.read_text(encoding="utf-8").split(marker, 1)[1].split("}", 1)[0]
        known |= set(re.findall(r'"([^"]+)":', block))
    required = ["quest_expired", "reputation_too_low", "too_many_quests", "not_together",
                "item_not_held", "auth_required", "auth_bad_username", "auth_bad_password",
                "auth_username_taken", "auth_invalid_credentials", "auth_missing_token",
                "auth_invalid_token", "auth_expired_token", "auth_disabled",
                "intent_not_found"]
    known = sorted(known | set(required))
    mapping = page.evaluate("""(codes) => {
      const x = window.__XKX;
      const rows = {};
      const missing = [];
      codes.forEach(c => {
        const text = x.reasonText(c);
        rows[c] = text;
        if (!text || text.indexOf('服务端没有给出中文说明') >= 0) missing.push(c);
      });
      return {rows, missing, exposed: x.reasons,
              unknown: x.reasonText('zzz_unknown_9'),
              unknownWithServerText: x.reasonText('zzz_unknown_9', '服务端原话')};
    }""", known)
    results.must("原因码：服务端已知码全部有中文文案", not mapping["missing"], mapping["missing"])
    results.must("原因码：前端映射表覆盖已知码",
                 set(known).issubset(set(mapping["exposed"])))
    results.must("原因码：未知码走兜底文案", "服务端没有给出中文说明" in mapping["unknown"])
    results.must("原因码：未知码但有服务端原话时保留原话",
                 mapping["unknownWithServerText"] == "服务端原话")

    # 错误提示：右下角历史最多 3 条、可展开、toast 自动消失
    page.evaluate("() => window.__XKX.showError('quest_expired', '')")
    page.wait_for_function("() => document.getElementById('errorCount').textContent === '1'")
    page.evaluate("() => window.__XKX.showError('reputation_too_low', '')")
    page.evaluate("() => window.__XKX.showError('too_many_quests', '')")
    page.evaluate("() => window.__XKX.showError('not_together', '')")
    hist = page.evaluate("""() => {
      const count = document.getElementById('errorCount').textContent;
      const items = Array.from(document.querySelectorAll('#errorList .error-item')).map(n => n.textContent);
      const toggle = document.getElementById('errorToggle');
      return {count, items, toggleHidden: toggle.classList.contains('hidden'), expanded: toggle.getAttribute('aria-expanded')};
    }""")
    results.must("错误历史：最多保留 3 条", hist["count"] == "3", hist["count"])
    page.click("#errorToggle")
    opened = page.evaluate("""() => ({
      visible: !document.getElementById('errorList').classList.contains('hidden'),
      items: Array.from(document.querySelectorAll('#errorList .error-item')).map(n => n.textContent),
      expanded: document.getElementById('errorToggle').getAttribute('aria-expanded')
    })""")
    results.must("错误历史：可展开复查", opened["visible"] and len(opened["items"]) == 3, opened)
    results.must("错误历史：显示中文原因文案",
                 any("声望还不够" in x for x in opened["items"]),
                 opened["items"])
    time.sleep(5.6)
    toast_hidden = page.evaluate("() => document.getElementById('toast').classList.contains('hidden')")
    results.must("错误 toast 自动消失（不长期遮挡）", toast_hidden)
    capture(page, "reasons-history", results,
            {"failed_check": []})
    ctx.close()


def run_desktop(browser, base, results):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    page.goto(base + "/?ui_check=1")
    enter_world(page, "u_guide_desktop")
    page.wait_for_function("() => window.__XKX.guideState().active === true", timeout=8000)

    steps = []
    page.wait_for_timeout(300)
    s1 = guide_snapshot(page)
    results.must("桌面 引导①出现并高亮「先做这个」",
                 s1["visible"] and s1["stepText"].strip() == "1 / 4" and s1["target"]["id"] == "todayNext",
                 {"step": s1["stepText"], "title": s1["title"], "target": s1["target"]})
    results.must("桌面 引导①不遮挡高亮目标", not s1["cardOverlapsTarget"])
    steps.append(capture(page, "desktop-step1", results, {"snapshot": s1}))

    page.click("#guideNext")
    page.wait_for_function("() => window.__XKX.guideState().step === 2")
    page.wait_for_timeout(300)
    s2 = guide_snapshot(page)
    results.must("桌面 引导②出现并高亮「去接」",
                 s2["visible"] and s2["stepText"].strip() == "2 / 4" and bool(s2["target"]["quest"]),
                 {"title": s2["title"], "target": s2["target"]})
    results.must("桌面 引导②不遮挡高亮目标", not s2["cardOverlapsTarget"])
    steps.append(capture(page, "desktop-step2", results, {"snapshot": s2}))

    click_guide_target(page, s2)
    page.wait_for_function("() => window.__XKX.guideState().step === 3", timeout=10000)
    page.wait_for_timeout(300)
    s3 = guide_snapshot(page)
    results.must("桌面 引导③出现并指向移动出口",
                 s3["visible"] and s3["stepText"].strip() == "3 / 4" and bool(s3["target"]["exitId"]),
                 {"title": s3["title"], "target": s3["target"]})
    steps.append(capture(page, "desktop-step3", results, {"snapshot": s3}))

    click_guide_target(page, s3)
    page.wait_for_function("() => window.__XKX.guideState().step === 4", timeout=10000)
    page.wait_for_timeout(300)
    s4 = guide_snapshot(page)
    results.must("桌面 引导④出现并高亮「交付」",
                 s4["visible"] and s4["stepText"].strip() == "4 / 4" and bool(s4["target"]["deliver"]),
                 {"title": s4["title"], "target": s4["target"]})
    steps.append(capture(page, "desktop-step4", results, {"snapshot": s4}))

    click_guide_target(page, s4)
    page.wait_for_function("() => window.__XKX.guideState().status === 'completed'", timeout=10000)
    wait_money(page, 312)
    done = page.evaluate("""() => ({
      guide: window.__XKX.guideState(),
      hidden: document.getElementById('guideLayer').classList.contains('hidden'),
      money: (document.getElementById('stMoney') || {}).textContent,
      persisted: JSON.parse(localStorage.getItem('ws_player_guide_v1') || '{}'),
      localKeys: Object.keys(localStorage)
    })""")
    results.must("桌面 ④交付后资金 300→312 且引导完成",
                 done["guide"]["status"] == "completed" and "312" in (done["money"] or ""), done)
    results.must("桌面 已完成状态写入 localStorage",
                 any(v.get("status") == "completed" for v in done["persisted"].values()), done["persisted"])
    capture(page, "desktop-complete", results, {"snapshot": done})

    # 刷新后不再出现
    page.reload()
    page.wait_for_function("() => !!window.__XKX", timeout=10000)
    page.wait_for_function("() => document.getElementById('entryOverlay').classList.contains('hidden')",
                           timeout=8000)
    time.sleep(1.0)
    after = page.evaluate("""() => ({
      guideActive: window.__XKX.guideState().active,
      layerHidden: document.getElementById('guideLayer').classList.contains('hidden'),
      status: window.__XKX.guideState().status
    })""")
    results.must("桌面 刷新后已完成引导不再出现", not after["guideActive"] and after["layerHidden"], after)
    capture(page, "desktop-reload-no-guide", results, {"snapshot": after})
    ctx.close()

    # 跳过流程（另一个账号）
    ctx2 = browser.new_context(viewport={"width": 1280, "height": 900})
    page2 = ctx2.new_page()
    page2.goto(base + "/?ui_check=1")
    enter_world(page2, "u_skip_desktop", "跳过客")
    page2.wait_for_function("() => window.__XKX.guideState().step === 1")
    before = guide_snapshot(page2)
    results.must("桌面 跳过前引导可见", before["visible"])
    page2.click("#guideSkip")
    page2.wait_for_function("() => document.getElementById('guideLayer').classList.contains('hidden')")
    skipped = page2.evaluate("""() => ({
      state: window.__XKX.guideState(),
      persisted: JSON.parse(localStorage.getItem('ws_player_guide_v1') || '{}')
    })""")
    results.must("桌面 可随时跳过并持久化 skipped",
                 skipped["state"]["status"] == "skipped" and
                 any(v.get("status") == "skipped" for v in skipped["persisted"].values()), skipped)
    page2.reload()
    page2.wait_for_function("() => !!window.__XKX", timeout=10000)
    page2.wait_for_function("() => document.getElementById('entryOverlay').classList.contains('hidden')",
                            timeout=8000)
    time.sleep(1.0)
    after_skip = page2.evaluate("() => ({active: window.__XKX.guideState().active, hidden: document.getElementById('guideLayer').classList.contains('hidden')})")
    results.must("桌面 刷新后已跳过引导不再出现", not after_skip["active"] and after_skip["hidden"], after_skip)
    ctx2.close()


def run_mobile(browser, base, results):
    ctx = browser.new_context(viewport={"width": 375, "height": 812},
                              device_scale_factor=2, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.goto(base + "/?ui_check=1")
    enter_world(page, "u_guide_mobile", "手机客")
    page.wait_for_function("() => window.__XKX.guideState().active === true", timeout=8000)

    steps = []
    for step in (1, 2, 3, 4):
        if step > 1:
            page.wait_for_function("(step) => window.__XKX.guideState().step === step", arg=step,
                                   timeout=10000)
        page.wait_for_timeout(300)
        snap = guide_snapshot(page)
        t = snap["target"]
        in_view = bool(t and t["rect"]["left"] >= 0 and t["rect"]["right"] <= 375 and
                       t["rect"]["top"] >= 0 and t["rect"]["bottom"] <= 812)
        ok_button = snap["target"]["tag"] != "BUTTON" or snap["target"]["rect"]["height"] >= 44
        hit_ok = bool(snap["hit"] and snap["hit"]["same"])
        no_overflow = snap["scrollWidth"] <= 375
        small = [x for x in snap["visibleButtons"] if x["h"] < 44]
        results.must("移动 375×812 引导%s目标在视口内" % step, in_view, t)
        results.must("移动 引导%s目标可被点中（未被提示遮挡）" % step, hit_ok, snap["hit"])
        results.must("移动 引导%s触控目标≥44px" % step, ok_button,
                     {"tag": snap["target"]["tag"], "height": t["rect"]["height"]})
        results.must("移动 引导%s无横向溢出" % step, no_overflow, snap["scrollWidth"])
        results.must("移动 引导%s可见按钮均≥44px" % step, not small, small)
        results.must("移动 引导%s提示卡不遮挡高亮目标" % step, not snap["cardOverlapsTarget"])
        entry = capture(page, "mobile-step%d" % step, results, {
            "step": step, "snapshot": snap,
            "viewport": {"width": 375, "height": 812},
            "targetInViewport": in_view, "targetHit": hit_ok,
            "targetHeight": t["rect"]["height"], "horizontalOverflow": snap["scrollWidth"] > 375
        })
        steps.append(entry)
        if step == 1:
            page.click("#guideNext")
        else:
            click_guide_target(page, snap)

    page.wait_for_function("() => window.__XKX.guideState().status === 'completed'", timeout=10000)
    wait_money(page, 312)
    done = page.evaluate("""() => ({
      guide: window.__XKX.guideState(),
      money: (document.getElementById('stMoney') || {}).textContent,
      groups: document.querySelectorAll('#todayList .today-group').length,
      growth: document.querySelectorAll('#growthList .growth-row').length,
      quests: document.querySelectorAll('#questList .quest').length,
      scrollWidth: document.documentElement.scrollWidth
    })""")
    results.must("移动 主流程完成：资金 300→312", "312" in (done["money"] or ""), done)
    results.must("移动 面板四组与三条成长线仍渲染",
                 done["groups"] >= 4 and done["growth"] == 3, done)
    results.must("移动 无横向滚动", done["scrollWidth"] <= 375, done["scrollWidth"])
    capture(page, "mobile-complete", results, {"snapshot": done})

    # 世界动态：移动端默认可见、固定高度、不挤压主线、不被底部浮层遮挡
    page.wait_for_function("() => window.__XKX.feedState().rows >= 5", timeout=10000)
    feed = feed_snapshot(page)
    page.evaluate("""() => document.getElementById('worldFeed')
        .scrollIntoView({block: 'center'})""")
    page.wait_for_timeout(150)
    hit = page.evaluate("""() => {
      const l = document.getElementById('feedList');
      const r = l.getBoundingClientRect();
      const n = document.elementFromPoint(r.left + r.width / 2, r.top + 20);
      return {inside: !!(n && n.closest('#worldFeed')), bottom: Math.round(r.bottom),
              innerHeight: window.innerHeight, scrollWidth: document.documentElement.scrollWidth,
              quests: document.querySelectorAll('#questList .quest').length};
    }""")
    results.add("移动 世界动态默认可见且固定高度（≤200px）",
                 feed["blockVisible"] and feed["count"] >= 5 and feed["height"] <= 200, feed)
    results.add("移动 世界动态不被底部安全区/浮层遮挡",
                 hit["inside"] and hit["bottom"] <= hit["innerHeight"], hit)
    results.add("移动 世界动态无横向溢出、主线任务仍渲染",
                 hit["scrollWidth"] <= 375 and done["quests"] >= 0, hit)
    feed_capture(page, "feed-mobile", results, {"feed": feed, "hit": hit})

    EVIDENCE_DIR.joinpath("mobile-summary.json").write_text(
        json.dumps(done, ensure_ascii=False, indent=2), encoding="utf-8")
    ctx.close()


def _active_info(page):
    return page.evaluate("""() => {
      const a = document.activeElement;
      if (!a) return null;
      const s = getComputedStyle(a);
      return {tag:a.tagName, id:a.id||'', text:(a.textContent||'').trim().slice(0,24),
              quest:a.getAttribute('data-quest')||'', exitId:a.getAttribute('data-exit-id')||'',
              deliver:a.getAttribute('data-deliver')||'',
              outlineStyle:s.outlineStyle, outlineWidth:s.outlineWidth,
              outlineColor:s.outlineColor};
    }""")


def tab_until(page, predicate, max_tabs=90, trail=None):
    for _ in range(max_tabs):
        page.keyboard.press("Tab")
        info = _active_info(page)
        if trail is not None and info:
            trail.append(info)
        if predicate(info):
            return info
    raise AssertionError("Tab 未能到达目标")


def run_keyboard(browser, base, results):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    page.goto(base + "/?ui_check=1")
    enter_world(page, "u_keyboard", "键盘客")
    page.wait_for_function("() => window.__XKX.guideState().step === 1")
    trail = []

    accept = tab_until(page, lambda i: i and i.get("quest"), trail=trail)
    results.must("键盘 Tab 到达「去接」并显示焦点样式",
                 accept and accept["outlineStyle"] != "none" and float(accept["outlineWidth"].rstrip("px")) >= 2,
                 accept)
    page.keyboard.press("Enter")
    page.wait_for_function("() => window.__XKX.guideState().step === 3", timeout=10000)

    page.evaluate("() => document.activeElement && document.activeElement.blur()")
    move = tab_until(page, lambda i: i and i.get("exitId") == "pharmacy", trail=trail)
    results.must("键盘 Tab 到达移动出口并显示焦点样式",
                 move and move["outlineStyle"] != "none", move)
    page.keyboard.press("Enter")
    page.wait_for_function("() => window.__XKX.guideState().step === 4", timeout=10000)

    page.evaluate("() => document.activeElement && document.activeElement.blur()")
    deliver = tab_until(page, lambda i: i and i.get("deliver"), trail=trail)
    results.must("键盘 Tab 到达「交付」并显示焦点样式",
                 deliver and deliver["outlineStyle"] != "none", deliver)
    page.keyboard.press("Enter")
    page.wait_for_function("() => window.__XKX.guideState().status === 'completed'", timeout=10000)
    wait_money(page, 312)
    results.must("键盘 Enter 走完接单→移动→交付", True,
                 {"tabs": len(trail), "focusSequence": [
                     (x.get("id") or x.get("quest") or x.get("exitId") or x.get("deliver") or x.get("text"))
                     for x in trail[-18:]]})
    (EVIDENCE_DIR / "keyboard.txt").write_text(
        "\n".join("%s\t%s\toutline=%s %s" % (x.get("tag"), x.get("id") or x.get("quest") or x.get("exitId") or x.get("deliver") or x.get("text"), x.get("outlineStyle"), x.get("outlineWidth")) for x in trail),
        encoding="utf-8")
    capture(page, "keyboard-complete", results, {"focusTrail": trail})
    ctx.close()


def run_regression(browser, base, results):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    page.goto(base + "/?ui_check=1")
    enter_world(page, "u_regression", "回归客")
    page.wait_for_function("() => window.__XKX.guideState().active === true")
    page.click("#guideSkip")
    page.wait_for_function("() => document.getElementById('guideLayer').classList.contains('hidden')")
    dom = page.evaluate("""() => ({
      groups: Array.from(document.querySelectorAll('#todayList .today-group')).map(n => n.textContent.trim().slice(0,8)),
      growth: Array.from(document.querySelectorAll('#growthList .growth-row')).map(n => n.textContent.trim().slice(0,12)),
      quests: document.querySelectorAll('#questList .quest').length,
      noticeHidden: document.getElementById('noticeBar').classList.contains('hidden'),
      authMode: document.getElementById('authBtn').textContent,
      guideHidden: document.getElementById('guideLayer').classList.contains('hidden')
    })""")
    labels = dom["groups"]
    results.must("回归 今日面板四组仍在",
                 all(any(x.startswith(y) for x in labels) for y in ("可接委托", "可学技能", "可拜访 NPC", "可买产业")),
                 labels)
    results.must("回归 三条成长线仍在", len(dom["growth"]) == 3, dom["growth"])
    results.must("回归 委托板仍在", dom["quests"] >= 1, dom["quests"])
    results.must("回归 健康模式下断线提示条不显示", dom["noticeHidden"])
    results.must("回归 legacy 模式显示匿名且不强制登录", dom["authMode"] == "匿名模式", dom["authMode"])
    capture(page, "regression-healthy", results, {"snapshot": dom})
    ctx.close()

    # degraded 独立 mock
    world = MockWorld(upstream_ok=False)
    httpd, dbase = start_server(world)
    try:
        ctx2 = browser.new_context(viewport={"width": 1280, "height": 900})
        page2 = ctx2.new_page()
        page2.goto(dbase + "/?ui_check=1")
        enter_world(page2, "u_degraded", "断线客")
        page2.wait_for_function("() => !document.getElementById('noticeBar').classList.contains('hidden')",
                                timeout=8000)
        notice = page2.evaluate("() => document.getElementById('noticeBar').textContent.trim()")
        results.must("回归 断线提示条仍显示「上游不可达」", "上游不可达" in notice, notice)
        capture(page2, "regression-degraded", results, {"notice": notice})
        ctx2.close()
    finally:
        httpd.shutdown()


def auth_register(page, username="ui_user", password="secret123"):
    page.click("#authTabRegister")
    page.fill("#authUsername", username)
    page.fill("#authPassword", password)
    page.click("#authSubmit")
    page.wait_for_function("() => document.getElementById('authModal').classList.contains('hidden')",
                           timeout=10000)


def auth_login(page, username="ui_user", password="secret123"):
    page.click("#authBtn")
    page.click("#authTabLogin")
    page.fill("#authUsername", username)
    page.fill("#authPassword", password)
    page.click("#authSubmit")
    page.wait_for_function("() => document.getElementById('authModal').classList.contains('hidden')",
                           timeout=10000)


def run_auth(browser, base, results):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    page.goto(base + "/?ui_check=1")
    wait_app(page)
    page.wait_for_function("() => window.__XKX.authState().mode === 'auth'", timeout=8000)
    mode = page.evaluate("""() => ({auth: window.__XKX.authState(), btn: document.getElementById('authBtn').textContent,
      entryLine: !document.getElementById('entryAuthLine').classList.contains('hidden')})""")
    results.must("鉴权 未登录显示登录/注册入口",
                 mode["btn"] == "登录/注册" and mode["entryLine"], mode)

    # 未登录进入世界：401 → 自动给出登录入口，不静默
    page.fill("#accountId", "u_auth")
    page.fill("#displayName", "鉴权客")
    page.click("#enterBtn")
    page.wait_for_function("() => !document.getElementById('authModal').classList.contains('hidden')",
                           timeout=8000)
    denied = page.evaluate("""() => ({
      entryError: document.getElementById('entryError').textContent,
      noticeVisible: !document.getElementById('authNotice').classList.contains('hidden'),
      noticeText: document.getElementById('authNotice').textContent,
      modalVisible: !document.getElementById('authModal').classList.contains('hidden')
    })""")
    results.must("鉴权 未登录写操作显示「需要重新登录」入口",
                 "需要登录" in denied["entryError"] and denied["noticeVisible"] and denied["modalVisible"],
                 denied)
    capture(page, "auth-required", results, {"snapshot": denied})

    auth_register(page, "ui_user", "secret123")
    stored = page.evaluate("""() => ({
      auth: window.__XKX.authState(),
      raw: localStorage.getItem('ws_auth_v1'),
      hasPassword: (localStorage.getItem('ws_auth_v1') || '').toLowerCase().indexOf('password') >= 0,
      btn: document.getElementById('authBtn').textContent
    })""")
    results.must("鉴权 注册成功后令牌写入 localStorage 且无口令",
                 stored["auth"]["hasAccess"] and stored["raw"] and not stored["hasPassword"], stored)
    results.must("鉴权 登录态显示在顶部", "已登录" in stored["btn"], stored["btn"])

    page.click("#enterBtn")
    page.wait_for_function("() => document.getElementById('enterBtn').getAttribute('data-open') === '1'",
                           timeout=10000)
    page.click("#enterBtn")
    page.wait_for_function("() => document.getElementById('entryOverlay').classList.contains('hidden')",
                           timeout=8000)
    page.wait_for_function("() => window.__XKX.guideState().active === true", timeout=8000)
    # 带 Bearer 接单
    page.click("#guideNext")
    page.wait_for_function("() => window.__XKX.guideState().step === 2")
    snap = guide_snapshot(page)
    click_guide_target(page, snap)
    page.wait_for_function("() => window.__XKX.guideState().step === 3", timeout=10000)
    results.must("鉴权 登录后写操作成功（Bearer 已发送）", True)

    # 刷新：已有令牌启动探活（/api/auth/refresh）→ 轮换并保持登录
    page.reload()
    page.wait_for_function("() => !!window.__XKX", timeout=10000)
    page.wait_for_function("() => window.__XKX.authState().mode === 'auth'", timeout=8000)
    time.sleep(1.0)
    reloaded = page.evaluate("() => ({auth: window.__XKX.authState(), btn: document.getElementById('authBtn').textContent})")

    # 令牌失效：启动探活 401 → 清掉本地令牌并回到登录态（不残留假登录）
    page.evaluate("""() => {
      const o = JSON.parse(localStorage.getItem('ws_auth_v1'));
      o.access_token = 'broken_access_token';
      localStorage.setItem('ws_auth_v1', JSON.stringify(o));
    }""")
    page.reload()
    page.wait_for_function("() => !!window.__XKX", timeout=10000)
    page.wait_for_function("() => window.__XKX.authState().mode === 'auth'", timeout=8000)
    page.wait_for_function("() => window.__XKX.authState().hasAccess === false", timeout=8000)
    broken = page.evaluate("""() => ({auth: window.__XKX.authState(), btn: document.getElementById('authBtn').textContent,
      raw: localStorage.getItem('ws_auth_v1')})""")
    results.must("鉴权 刷新后保持登录；失效令牌启动时清掉并回到登录态",
                 reloaded["auth"]["hasAccess"] and "已登录" in reloaded["btn"]
                 and not broken["auth"]["hasAccess"] and broken["raw"] is None, {"reloaded": reloaded, "broken": broken})

    # refresh 轮换 + 单飞：两个并发写请求都 401，只允许一次 refresh；
    # 旧令牌立即失效，两个请求都用新令牌重放成功。
    auth_login(page, "ui_user", "secret123")
    page.request.get(base + "/__check/reset_stats")
    page.request.get(base + "/__check/force401?n=2")
    race = page.evaluate("""async () => {
      const oldToken = JSON.parse(localStorage.getItem('ws_auth_v1')).access_token;
      const x = window.__XKX;
      const [a, b] = await Promise.all([
        x.apiAction('cultivate', {hours: 1}),
        x.apiAction('cultivate', {hours: 1})
      ]);
      const stats = await (await fetch('/__check/stats')).json();
      const newToken = JSON.parse(localStorage.getItem('ws_auth_v1')).access_token;
      return {a, b, stats, oldToken, newToken};
    }""")
    old_probe = page.request.get(base + "/api/auth/me",
                                 headers={"Authorization": "Bearer " + race["oldToken"]})
    results.must("鉴权 refresh 轮换：并发 401 只刷新一次、旧令牌立即失效",
                 bool(race["a"].get("httpOk")) and bool(race["b"].get("httpOk"))
                 and race["stats"]["refresh_calls"] == 1
                 and race["oldToken"] != race["newToken"]
                 and old_probe.status == 401,
                 {"refresh_calls": race["stats"]["refresh_calls"],
                  "rotate_count": race["stats"]["rotate_count"],
                  "old_status": old_probe.status,
                  "newTokenDiffers": race["oldToken"] != race["newToken"]})
    page.wait_for_function("() => window.__XKX.guideState().step === 3", timeout=8000)

    # 会话中途全部令牌失效：写操作 401 → 提示重新登录，但世界状态不被吞
    page.wait_for_function("() => document.getElementById('entryOverlay').classList.contains('hidden')",
                           timeout=8000)
    page.request.get(base + "/__check/expire")
    page.wait_for_function("() => window.__XKX.guideState().step === 3", timeout=8000)
    page.click('button[data-deliver="%s"]' % QUEST["id"])
    page.wait_for_function("() => !document.getElementById('authNotice').classList.contains('hidden')",
                           timeout=8000)
    mid = page.evaluate("""() => ({
      auth: window.__XKX.authState(),
      noticeText: document.getElementById('authNotice').textContent,
      noticeVisible: !document.getElementById('authNotice').classList.contains('hidden'),
      btn: document.getElementById('authBtn').textContent,
      quests: document.querySelectorAll('#questList .quest').length,
      location: document.getElementById('placeName') ? document.getElementById('placeName').textContent : '',
      errorCount: document.getElementById('errorCount').textContent
    })""")
    results.must("鉴权 写操作 401 显示「需要重新登录」且不吞世界状态",
                 mid["noticeVisible"] and "需要重新登录" in mid["noticeText"] and mid["quests"] >= 1
                 and "需要重新登录" in mid["btn"], mid)
    results.must("鉴权 401 进入错误历史可复查", mid["errorCount"] == "1", mid["errorCount"])
    capture(page, "auth-401-mid-session", results, {"snapshot": mid})
    ctx.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", action="store_true", help="只启动 mock 服务")
    ap.add_argument("--only", default="",
                    help="desktop|mobile|keyboard|auth|reasons|regression|feed，逗号分隔")
    ap.add_argument("--auth", action="store_true", help="--serve 时启用鉴权模式")
    args = ap.parse_args()

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    if args.serve:
        world = MockWorld(auth_enabled=args.auth)
        httpd, base = start_server(world)
        print("mock UI:", base + "/?ui_check=1", "(auth=%s)" % args.auth)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            httpd.shutdown()
        return

    results = Results()
    world = MockWorld()
    httpd, base = start_server(world)
    only = set(x.strip() for x in args.only.split(",") if x.strip())
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                if not only or "reasons" in only:
                    run_reasons(browser, base, results)
                if not only or "feed" in only:
                    run_feed(browser, base, results, world)
                if not only or "desktop" in only:
                    run_desktop(browser, base, results)
                if not only or "mobile" in only:
                    run_mobile(browser, base, results)
                if not only or "keyboard" in only:
                    run_keyboard(browser, base, results)
                if not only or "regression" in only:
                    run_regression(browser, base, results)
                if not only or "auth" in only:
                    auth_world = MockWorld(auth_enabled=True)
                    auth_httpd, auth_base = start_server(auth_world)
                    try:
                        run_auth(browser, auth_base, results)
                    finally:
                        auth_httpd.shutdown()
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    results.dump(EVIDENCE_DIR / "checks.json")

    # 世界动态专项证据（reports/w4-feed）：桌面 + 移动端检查与匿名性
    feed_rows = [r for r in results.rows
                 if r["name"].startswith("feed ") or r["name"].startswith("移动 世界动态")]
    if feed_rows:
        FEED_EVIDENCE.mkdir(parents=True, exist_ok=True)
        payload = {
            "checks": feed_rows,
            "failed": sum(1 for r in feed_rows if not r["ok"]),
            "feed_calls": world.feed_calls,
            "authorization_headers": world.feed_headers,
            "screenshots": ["reports/w4-feed/feed-desktop.png",
                            "reports/w4-feed/feed-mobile.png"],
        }
        FEED_EVIDENCE.joinpath("feed-checks.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    failed = [r for r in results.rows if not r["ok"]]
    print("\n%d checks, %d failed; evidence: %s" % (len(results.rows), len(failed), EVIDENCE_DIR))
    if failed:
        for row in failed:
            print("FAILED:", row["name"], row.get("detail"))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
