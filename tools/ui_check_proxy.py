"""W4 服务模式代理验收：真实 ui/player/server.py + service-mode mock 上游。

与 tools/ui_check.py 的区别：这里启动的是**真的** UI 反向代理进程，
上游按 W1 方案 A 的读契约返回（带令牌=本人完整状态，无令牌=公共摘要，
跨账号 403 auth_account_mismatch，intents 匿名 401），用来证明：

- 代理 GET/POST 都会透传 Authorization；
- 代理保留上游的 401/403 状态码与响应体；
- 经代理登录后 GET state 能拿到 wallet（不是公共摘要），写操作不再 401；
- 前端遇到 403 会用令牌身份重取，不弹红字；401 仍走单飞刷新。

用法（不写世界，只动内存）：
  python3 tools/ui_check_proxy.py

输出：reports/w4-ui/proxy-service.json
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ui_check import (  # noqa: E402
    EVIDENCE_DIR,
    QUEST,
    MockWorld,
    Results,
    auth_error,
    wait_app,
)

ROOT = Path(__file__).resolve().parents[1]
UI_DIR = ROOT / "ui" / "player"
USER = "proxy_user"


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class ServiceMock:
    """service 模式上游：auth_required=True 的读写语义。"""

    def __init__(self):
        self.world = MockWorld(auth_enabled=True)
        self.reads = []
        self.writes = []
        self.lock = threading.RLock()

    def record_read(self, path, requested, identity, status, wallet):
        with self.lock:
            self.reads.append({"path": path, "account": requested, "identity": identity,
                               "status": status, "wallet": wallet})

    def record_write(self, path, header, identity, status):
        with self.lock:
            self.writes.append({"path": path, "auth": bool(header),
                                "identity": identity, "status": status})


class UpstreamHandler(BaseHTTPRequestHandler):
    server_version = "wsim-mock-upstream/1.0"

    def log_message(self, fmt, *args):  # noqa: A003
        pass

    @property
    def mock(self) -> ServiceMock:
        return self.server.mock  # type: ignore[attr-defined]

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _identity(self):
        header = self.headers.get("Authorization") or ""
        if not header:
            return None, None, header
        code = self.mock.world.token_error(header)
        if code:
            return None, code, header
        return self.mock.world.verify(header), None, header

    # -------------------------------------------------------------------- GET
    def do_GET(self):  # noqa: N802
        parsed = urlsplit(self.path)
        path = parsed.path
        q = parse_qs(parsed.query)
        account = str((q.get("account_id") or [""])[0]).strip().lower()
        if path == "/api/service/info":
            self._json(200, {"ok": True, "auth_required": True, "tick": 12,
                             "run": "proxy-check", "profile_version": 1})
            return
        if path == "/api/auth/me":
            identity, err, _ = self._identity()
            if err or not identity:
                self._json(401, auth_error(err or "auth_missing_token"))
                return
            rec = self.mock.world.sessions[self.headers["Authorization"][len("Bearer "):].strip()]
            self._json(200, {"ok": True, "account_id": identity, "username": identity,
                             "session_id": rec["session_id"], "expires_at": int(rec["exp"]),
                             "status": "active", "disconnected": False})
            return
        if path in ("/api/player/state", "/api/state"):
            identity, err, _ = self._identity()
            if err:
                self.mock.record_read(path, account, None, 401, False)
                self._json(401, auth_error(err))
                return
            if identity and account and account != identity:
                self.mock.record_read(path, account, identity, 403, False)
                self._json(403, auth_error("auth_account_mismatch"))
                return
            if identity:
                st = self.mock.world.state(identity)
                if st is None:
                    self.mock.record_read(path, identity, identity, 409, False)
                    self._json(409, {"ok": False, "code": "player_not_entered",
                                     "reject_reason": "player_not_entered",
                                     "reason_text": "账号尚未进入世界。"})
                    return
                self.mock.record_read(path, account or identity, identity, 200, True)
                self._json(200, st)
                return
            # 无令牌：公共摘要白名单，没有 wallet / player，也**没有 profile_version**
            # （与 W1 方案 A 的真实契约一致；探活必须改走 /api/service/info）。
            self.mock.record_read(path, account, None, 200, False)
            self._json(200, {
                "ok": True, "public": True, "tick": 12,
                "account_id": account,
                "summary": {"online_players": 1,
                            "quests": [{"name": QUEST["name"],
                                        "reward_money": QUEST["reward_money"]}]},
            })
            return
        if path == "/api/player/quests":
            identity, err, _ = self._identity()
            if err:
                self._json(401, auth_error(err))
                return
            if identity:
                self._json(200, {"ok": True, "quests": [dict(QUEST)]})
            else:
                self._json(200, {"ok": True, "public": True, "quests": [{"name": QUEST["name"]}]})
            return
        if path == "/api/player/messages":
            identity, err, _ = self._identity()
            if err:
                self._json(401, auth_error(err))
                return
            self._json(200, {"ok": True, "public": identity is None,
                             "messages": [], "next_since_tick": 0})
            return
        if path.startswith("/api/player/intents/"):
            identity, err, _ = self._identity()
            if err:
                self._json(401, auth_error(err))
                return
            if identity is None:
                # 个人动作结果可枚举：服务模式匿名一律 401。
                self._json(401, auth_error("auth_missing_token"))
                return
            self._json(200, {"ok": True, "message": "已完成"})
            return
        self._json(404, {"ok": False, "code": "unknown_action"})

    # ------------------------------------------------------------------- POST
    def do_POST(self):  # noqa: N802
        path = urlsplit(self.path).path
        body = self._body()
        if path.startswith("/api/auth/"):
            return self._auth_route(path, body)
        if path in ("/api/player/enter", "/api/player/action"):
            identity, err, header = self._identity()
            if err or not identity:
                self.mock.record_write(path, header, None, 401)
                self._json(401, auth_error(err or "auth_missing_token"))
                return
            account = str(body.get("account_id") or "").strip().lower()
            if account != identity:
                self.mock.record_write(path, header, identity, 403)
                self._json(403, auth_error("auth_account_mismatch"))
                return
            if path == "/api/player/enter":
                self.mock.record_write(path, header, identity, 200)
                payload = self.mock.world.enter(identity, body.get("display_name"), body.get("archetype"))
                self._json(200, payload)
                return
            status, payload = self.mock.world.act(
                identity, str(body.get("verb") or ""), body.get("args") or {}, body.get("client_tick")
            )
            self.mock.record_write(path, header, identity, status)
            self._json(status, payload)
            return
        self._json(404, {"ok": False, "code": "unknown_action"})

    def _auth_route(self, path: str, body: dict) -> None:
        world = self.mock.world
        if path == "/api/auth/register":
            username = str(body.get("username") or "").strip().lower()
            password = str(body.get("password") or "")
            if not username or not (6 <= len(password) <= 128):
                self._json(400, auth_error("auth_bad_username"))
                return
            with world.lock:
                if username in world.users:
                    self._json(409, auth_error("auth_username_taken"))
                    return
                world.users[username] = password
            self._json(200, {"ok": True, "account_id": username, "username": username, "created": True})
            return
        if path == "/api/auth/login":
            username = str(body.get("username") or "").strip().lower()
            if world.users.get(username) != str(body.get("password") or ""):
                self._json(401, auth_error("auth_invalid_credentials"))
                return
            self._json(200, world.issue(username))
            return
        if path == "/api/auth/refresh":
            payload, code = world.rotate(self.headers.get("Authorization") or "")
            if code:
                self._json(401, auth_error(code))
                return
            self._json(200, payload)
            return
        if path == "/api/auth/logout":
            header = self.headers.get("Authorization") or ""
            code = world.token_error(header)
            if code:
                self._json(401, auth_error(code))
                return
            world.sessions.pop(header[len("Bearer "):].strip(), None)
            self._json(200, {"ok": True, "logged_out": True})
            return
        self._json(404, auth_error("unknown_action"))


def start_upstream(mock: ServiceMock):
    httpd = ThreadingHTTPServer(("127.0.0.1", free_port()), UpstreamHandler)
    httpd.mock = mock  # type: ignore[attr-defined]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, "http://127.0.0.1:%d" % httpd.server_address[1]


def start_proxy(upstream: str, port: int) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, str(UI_DIR / "server.py"),
         "--upstream", upstream, "--port", str(port), "--host", "127.0.0.1"],
        cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/api/health" % port, timeout=1) as resp:
                if resp.status == 200:
                    return proc
        except Exception:
            if proc.poll() is not None:
                raise RuntimeError("UI 代理启动失败：" + (proc.stdout.read().decode("utf-8", "ignore") if proc.stdout else ""))
            time.sleep(0.2)
    raise RuntimeError("UI 代理未就绪")


def ui_login(page, username: str = USER, password: str = "secret123") -> None:
    # 进入页覆盖层会挡住顶部按钮，这里直接触发覆盖层里的登录入口。
    page.evaluate("() => document.getElementById('entryAuthBtn').click()")
    page.click("#authTabRegister")
    page.fill("#authUsername", username)
    page.fill("#authPassword", password)
    page.click("#authSubmit")
    page.wait_for_function("() => document.getElementById('authModal').classList.contains('hidden')",
                           timeout=10000)
    page.wait_for_function("() => window.__XKX.authState().hasAccess === true", timeout=8000)


def run_proxy(browser, base: str, mock: ServiceMock, results: Results) -> dict:
    health = json.loads(urllib.request.urlopen(base + "/api/health", timeout=5).read().decode("utf-8"))
    results.must("服务模式 匿名公共摘要下探活仍成立（/api/health 200、mode=live）",
                 health.get("ok") is True and health.get("mode") == "live"
                 and health.get("upstream_ok") is True,
                 {"mode": health.get("mode"), "upstream_ok": health.get("upstream_ok"),
                  "readonly": health.get("readonly")})

    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    page.goto(base + "/?ui_check=1")
    wait_app(page)
    page.wait_for_function("() => window.__XKX.authState().mode === 'auth'", timeout=10000)
    results.must("服务模式 经代理探活判定为 auth 模式",
                 page.evaluate("() => window.__XKX.authState().mode") == "auth")

    ui_login(page)
    results.must("服务模式 经代理注册+登录拿到令牌",
                 page.evaluate("() => window.__XKX.authState().hasAccess === true"))

    page.fill("#accountId", USER)
    page.fill("#displayName", "代理验客")
    page.click("#enterBtn")
    page.wait_for_function("() => document.getElementById('enterBtn').getAttribute('data-open') === '1'",
                           timeout=10000)
    page.click("#enterBtn")
    page.wait_for_function("() => document.getElementById('entryOverlay').classList.contains('hidden')",
                           timeout=10000)

    own = page.evaluate("""async () => {
      const st = await window.__XKX.readState();
      const w = st && st.player && st.player.wallet;
      return {ok: !!st.ok, account: st.account_id, money: w ? w.money : null,
              items: (w && w.items) ? w.items.length : 0};
    }""")
    results.must("服务模式 经代理登录后 GET state 拿到本人 wallet（非公共摘要）",
                 own["ok"] and own["money"] == 300 and own["items"] >= 1 and own["account"] == USER,
                 own)

    anon = page.evaluate("""async (acct) => {
      const r = await fetch('/api/player/state?account_id=' + encodeURIComponent(acct));
      const body = await r.json();
      return {status: r.status, public: !!body.public,
              hasWallet: !!(body.player && body.player.wallet),
              hasProfileVersion: Object.prototype.hasOwnProperty.call(body, 'profile_version')};
    }""", USER)
    results.must("服务模式 经代理匿名读只拿到公共摘要（且不含 profile_version）",
                 anon["status"] == 200 and anon["public"] and not anon["hasWallet"]
                 and not anon["hasProfileVersion"], anon)

    raw403 = page.evaluate("""async () => {
      const t = JSON.parse(localStorage.getItem('ws_auth_v1') || '{}').access_token;
      const r = await fetch('/api/player/state?account_id=u_other',
        {headers: {Accept: 'application/json', Authorization: 'Bearer ' + t}});
      const body = await r.json();
      return {status: r.status, code: body.code || body.reject_reason};
    }""")
    results.must("服务模式 代理原样透传 403 auth_account_mismatch",
                 raw403["status"] == 403 and raw403["code"] == "auth_account_mismatch", raw403)

    retried = page.evaluate("""async () => {
      const st = await window.__XKX.readState('u_other');
      const w = st && st.player && st.player.wallet;
      return {account: st.account_id, money: w ? w.money : null};
    }""")
    results.must("服务模式 前端 403 用令牌身份重取（不弹红字）",
                 retried["account"] == USER and retried["money"] == 300, retried)

    moved = page.evaluate("() => window.__XKX.apiAction('move', {to: 'pharmacy'})")
    results.must("服务模式 经代理写操作不再 401",
                 bool(moved.get("httpOk")) and moved.get("status") == 200, moved)

    raw401 = page.evaluate("""async (acct) => {
      const r = await fetch('/api/player/action', {method: 'POST',
        headers: {Accept: 'application/json', 'Content-Type': 'application/json'},
        body: JSON.stringify({account_id: acct, verb: 'move', args: {to: 'market'}})});
      const body = await r.json();
      return {status: r.status, code: body.code || body.reject_reason};
    }""", USER)
    results.must("服务模式 代理原样透传写操作 401",
                 raw401["status"] == 401 and raw401["code"] == "auth_missing_token", raw401)

    page.reload()
    wait_app(page)
    page.wait_for_function("() => document.getElementById('entryOverlay').classList.contains('hidden')",
                           timeout=10000)
    after = page.evaluate("""async () => {
      const st = await window.__XKX.readState();
      const w = st && st.player && st.player.wallet;
      return {money: w ? w.money : null, t: window.__XKX.authState()};
    }""")
    results.must("服务模式 刷新后仍按令牌取数（钱包不消失）",
                 after["money"] == 300 and after["t"]["hasAccess"], after)

    ctx.close()
    return {"reads": list(mock.reads), "writes": list(mock.writes)}


def main() -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    mock = ServiceMock()
    upstream_httpd, upstream_base = start_upstream(mock)
    proxy_proc = None
    results = Results()
    stats = {}
    try:
        port = free_port()
        proxy_proc = start_proxy(upstream_base, port)
        base = "http://127.0.0.1:%d" % port
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                stats = run_proxy(browser, base, mock, results)
            finally:
                browser.close()
    finally:
        if proxy_proc is not None:
            proxy_proc.terminate()
            try:
                proxy_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proxy_proc.kill()
        upstream_httpd.shutdown()

    out = {"checks": results.rows, "stats": stats}
    (EVIDENCE_DIR / "proxy-service.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    failed = [r for r in results.rows if not r["ok"]]
    print("\n%d checks, %d failed; evidence: %s"
          % (len(results.rows), len(failed), EVIDENCE_DIR / "proxy-service.json"))
    if failed:
        for row in failed:
            print("FAILED:", row["name"], row.get("detail"))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
