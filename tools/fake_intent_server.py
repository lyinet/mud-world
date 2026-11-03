"""假意图理解端点：模拟 Xing4.0-29B-Intent 的返回结构。

覆盖四类用例：参数齐全、参数缺失、中文数量词、否定句、改口（多轮）。
只用于本地测试，不联网、不需要 key。

启动：
  python tools/fake_intent_server.py --port 3426
"""
from __future__ import annotations

import argparse
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

CATEGORY_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("还债", ("还债", "还钱", "清债")),
    ("交任务", ("交任务", "交付任务")),
    ("接任务", ("接任务", "领任务")),
    ("护送", ("护送", "押镖")),
    ("开宗立派", ("开宗", "立派")),
    ("开店", ("开店", "盘个铺子")),
    ("入门派", ("入门派", "加入门派", "拜入门派")),
    ("婚配", ("成亲", "婚配", "娶", "嫁")),
    ("拜师", ("拜师", "求师")),
    ("出售", ("卖掉", "出售", "卖出")),
    ("购买", ("买", "购买")),
    ("交易", ("交易", "交换", "换货")),
    ("修炼", ("修炼", "练功", "打坐")),
    ("吃饭", ("吃", "喝", "填肚子")),
    ("上工", ("上工", "做工", "干活", "开工")),
    ("对话", ("闲聊", "聊", "说话", "谈谈")),
    ("去某地", ("去", "前往", "走到", "赶往")),
]

NEGATION = ("不去", "别去", "不要", "不买", "不卖", "取消", "算了", "不干")


def _extract(user: str, start: str, end: str) -> str:
    if start not in user:
        return ""
    body = user.split(start, 1)[1]
    if end and end in body:
        body = body.split(end, 1)[0]
    return body.strip()


def _context(user: str) -> dict[str, Any]:
    raw = _extract(user, "【当前上下文】", "")
    raw = raw.split("\n【", 1)[0].strip()
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def _history(user: str) -> list[str]:
    raw = _extract(user, "【前几轮对话】", "【用户/上层说的话】")
    return [line[2:].strip() for line in raw.splitlines() if line.startswith("- ")]


def _catalog(user: str) -> dict[str, dict[str, list[str]]]:
    """从 prompt 的「每个意图要填哪些字段」反解类别与必填/选填。"""
    out: dict[str, dict[str, list[str]]] = {}
    for line in user.splitlines():
        m = re.match(r"- (\S+)（([a-z_]+)）：必填 (.*?)；选填 (.*)$", line.strip())
        if not m:
            continue
        cat, verb, req, opt = m.groups()
        split = lambda s: [] if s in ("无", "") else [x.strip() for x in s.split("、") if x.strip()]
        out[cat] = {"verb": [verb], "required": split(req), "optional": split(opt)}
    return out


def _category(utterance: str) -> str:
    for cat, words in CATEGORY_HINTS:
        if any(w in utterance for w in words):
            return cat
    return "unknown"


def _quantity(text: str) -> str:
    m = re.search(r"[零一二两三四五六七八九十0-9]+[瓶碗份个包袋坛壶]", text)
    return m.group(0) if m else ""


def _target(ctx: dict[str, Any], npc: str) -> str:
    for row in ctx.get("nearby_npcs") or []:
        rid = str(row.get("id") or "")
        if rid and rid != npc and not rid.startswith("player:"):
            return rid
    return ""


def _args(cat: str, utterance: str, ctx: dict[str, Any]) -> dict[str, Any]:
    npc = str(ctx.get("npc") or "")
    if cat == "去某地":
        m = re.search(r"(?:去|前往|走到|赶往)([^，。,.；;的]+)", utterance)
        dest = m.group(1).strip() if m else ""
        # P3-C：去掉“看看/走走/逛逛/一趟”等口语尾缀，避免把尾缀当地名。
        dest = re.sub(r"[看走逛溜]一?[看走逛溜]?$", "", dest).strip()
        if dest in ("一趟", "一下", "那边", "这边", "那里", "这里") or dest.startswith("一"):
            dest = ""
        reach = [r for r in (ctx.get("reachable_locations") or []) if isinstance(r, dict)]
        for row in reach:
            rid = str(row.get("id") or "")
            name = str(row.get("name") or "")
            if dest and (dest == rid and name):
                dest = name
                break
        return {"地点": dest} if dest else {}
    if cat == "吃饭":
        item = ""
        for name in ("水", "干粮", "饭", "茶", "酒", "药"):
            if name in utterance:
                item = name
                break
        args: dict[str, Any] = {}
        if item:
            args["物品"] = item
        qty = _quantity(utterance)
        if qty:
            args["数量"] = qty
        return args
    if cat == "对话":
        target = _target(ctx, npc)
        args = {"说的话": utterance}
        if target:
            args["对象"] = target
            args["回复"] = "嗯，知道了。"
        return args
    if cat in ("上工", "修炼"):
        hours = re.search(r"([零一二两三四五六七八九十0-9]+)个?时辰", utterance)
        if cat == "上工":
            return {"at": str(ctx.get("location") or ""), **({"hours": hours.group(1)} if hours else {})}
        return {"技能": "基本吐纳", **({"时辰": hours.group(1)} if hours else {})}
    if cat == "交易":
        return {"对象": _target(ctx, npc)}
    if cat == "拜师":
        return {"对象": _target(ctx, npc), "天数": "30"}
    if cat == "入门派":
        return {"对象": _target(ctx, npc)}
    if cat == "婚配":
        return {"对象": _target(ctx, npc)}
    return {}


def _retracted(user: str) -> dict[str, Any] | None:
    """改口：以最近一条 JSON 历史为底稿，只覆盖本次说到的字段。"""
    last: dict[str, Any] | None = None
    for line in _history(user):
        try:
            obj = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(obj, dict) and obj.get("category"):
            last = obj
    if last is None:
        return None
    new = dict(last.get("args") or {})
    m = re.search(r"改成([^，。,.；;]+)", user)
    if m:
        value = m.group(1).strip()
        if last.get("category") == "去某地":
            new["地点"] = value
        elif last.get("category") == "对话":
            new["说的话"] = value
        else:
            new["地点"] = value
    return {"category": last["category"], "args": new}


def respond(user: str) -> dict[str, Any]:
    utterance = _extract(user, "【用户/上层说的话】", "【系统支持哪些意图】")
    ctx = _context(user)
    lower = utterance.lower()
    if any(w in utterance for w in NEGATION) or lower.startswith("不"):
        return {"category": "none", "args": {}}
    if "改口" in utterance or "改成" in utterance:
        retract = _retracted(user)
        if retract is not None:
            return retract
    cat = _category(utterance)
    if cat == "unknown":
        return {"category": "unknown", "args": {}}
    catalog = _catalog(user)
    required = (catalog.get(cat) or {}).get("required") or []
    args = _args(cat, utterance, ctx)
    missing = [f for f in required if not args.get(_alias_key(f))]
    if missing:
        return {"needs_clarification": missing}
    return {"category": cat, "args": args}


_KEY_ALIAS = {
    "destination": "地点", "target": "对象", "item": "物品", "qty": "数量",
    "message": "说的话", "skill": "技能", "hours": "时辰", "duration_days": "天数",
}


def _alias_key(field: str) -> str:
    return _KEY_ALIAS.get(field, field)


class Handler(BaseHTTPRequestHandler):
    server_version = "FakeIntent/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        if getattr(self.server, "verbose", False):
            super().log_message(fmt, *args)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length).decode("utf-8", "replace")
        try:
            payload = json.loads(body)
        except ValueError:
            payload = {}
        messages = payload.get("messages") or []
        user = str(messages[-1].get("content", "")) if messages else ""
        result = respond(user)
        content = json.dumps(result, ensure_ascii=False, sort_keys=True)
        with self.server.lock:  # type: ignore[attr-defined]
            self.server.calls += 1  # type: ignore[attr-defined]
            self.server.last = content  # type: ignore[attr-defined]
        out = {
            "id": "fake-intent",
            "object": "chat.completion",
            "model": str(payload.get("model") or "Xing4.0-29B-Intent"),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
            # 上游可能不返回 usage；本桩默认不返回，验证上游缺 usage 时的记账路径。
        }
        if getattr(self.server, "with_usage", False):
            out["usage"] = {"prompt_tokens": len(user) // 4, "completion_tokens": len(content) // 4}
        raw = json.dumps(out, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class FakeIntentServer:
    """可嵌入测试的进程内假意图端点。"""

    def __init__(self, port: int = 0, seed: int = 42, with_usage: bool = False):
        self._httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self._httpd.daemon_threads = True
        self._httpd.lock = threading.Lock()  # type: ignore[attr-defined]
        self._httpd.calls = 0  # type: ignore[attr-defined]
        self._httpd.last = ""  # type: ignore[attr-defined]
        self._httpd.verbose = False  # type: ignore[attr-defined]
        self._httpd.with_usage = with_usage  # type: ignore[attr-defined]
        self.seed = seed
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return int(self._httpd.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def start(self) -> "FakeIntentServer":
        if self._thread is None:
            self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def __enter__(self) -> "FakeIntentServer":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3426)
    parser.add_argument("--with-usage", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.calls = 0  # type: ignore[attr-defined]
    server.last = ""  # type: ignore[attr-defined]
    server.lock = threading.Lock()  # type: ignore[attr-defined]
    server.verbose = args.verbose  # type: ignore[attr-defined]
    server.with_usage = args.with_usage  # type: ignore[attr-defined]
    print(f"fake intent server on http://{args.host}:{args.port}/v1")
    server.serve_forever()


if __name__ == "__main__":
    main()
