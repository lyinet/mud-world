#!/usr/bin/env python3
"""本地假 LLM 端点（OpenAI 兼容，CI 用，不联网）。

用途：给出 `/v1/chat/completions` 的确定性响应，让 P3-A 的 Provider 接线、
主循环三种决策时机、预算降级与端到端 replay 可以在无密钥、无外网的环境下验收。

特性：
- 标准库实现（http.server），无第三方依赖；
- 按 seed + 请求序号稳定生成合法 `{"actions":[...]}`；
- 可配置：延迟、失败率（HTTP 500）、畸形 JSON、超长输出（>4 动作）；
- 从请求里的「当前状态：」JSON 读取 exits / skills，只生成 move + cultivate，
  保证动作大概率可落地（CI 不因假模型产生大量拒绝）。

启动：
  python3 tools/fake_llm_server.py --port 0 --seed 42
  # 输出 PORT=NNNNN 与 FAKE_LLM_BASE_URL=http://127.0.0.1:NNNNN/v1
  LLM_BASE_URL=http://127.0.0.1:NNNNN/v1 LLM_API_KEY=fake LLM_MODEL=fake \
    python3 sim/run_headless.py --days 2 --enable-llm
"""
from __future__ import annotations

import argparse
import json
import random
import threading
import time
import zlib
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


@dataclass
class FakeLLMConfig:
    seed: int = 42
    delay_s: float = 0.0
    fail_rate: float = 0.0
    bad_json_rate: float = 0.0
    long_output: bool = False
    mode: str = "actions"  # actions=旧 JSON 动作；decision=自然语言意图（P3-B 三槽）
    counter: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


def _state_summary(user: str) -> dict[str, Any]:
    """从 Prompt.user 中提取「当前状态：」后的 JSON；失败返回 {}。"""
    marker = "当前状态：\n"
    if marker not in user:
        return {}
    try:
        value = json.loads(user.split(marker, 1)[1])
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _generate_actions(summary: dict[str, Any], rng: random.Random, long_output: bool) -> list[dict[str, Any]]:
    exits = [str(x) for x in (summary.get("exits") or []) if str(x)]
    skills = sorted(str(k) for k in (summary.get("skills") or {}).keys())
    skill = skills[0] if skills else "craft"
    if long_output:
        return [
            {"verb": "cultivate", "args": {"skill": skill, "hours": 1}, "reason": f"假端点长输出第{i + 1}条"}
            for i in range(8)
        ]
    actions: list[dict[str, Any]] = []
    if exits and rng.random() < 0.6:
        actions.append(
            {
                "verb": "move",
                "args": {"to": rng.choice(exits)},
                "reason": "假端点：去附近地点打探消息",
            }
        )
    count = rng.choice([1, 2])
    for _ in range(count):
        actions.append(
            {
                "verb": "cultivate",
                "args": {"skill": skill, "hours": 1},
                "reason": "假端点：静心修炼，稳住内息",
            }
        )
    # P3-B：模型只允许社交动作。给一个同地点可见对象，确保有合法社交动作可落地。
    self_id = str(summary.get("npc") or "")
    nearby = [
        x for x in (summary.get("nearby_npcs") or [])
        if isinstance(x, dict)
        and str(x.get("id") or "")
        and str(x.get("id")) != self_id
        and not str(x.get("id")).startswith("player:")
    ]
    if nearby:
        target = str(nearby[0].get("id") or "")
        if target:
            actions.append(
                {
                    "verb": "talk",
                    "args": {"target": target, "message": "近来可好？", "reply": "尚可，你呢？"},
                    "reason": "假端点：与附近的人闲聊两句",
                }
            )
    return actions


def _response_content(cfg: FakeLLMConfig, body: dict[str, Any], index: int) -> str:
    messages = body.get("messages") or []
    user = ""
    if isinstance(messages, list):
        for msg in messages:
            if isinstance(msg, dict) and msg.get("role") == "user":
                user = str(msg.get("content") or "")
    summary = _state_summary(user)
    salt = zlib.crc32(user.encode("utf-8")) & 0xFFFFFFFF
    rng = random.Random((cfg.seed << 16) ^ index ^ salt)
    if cfg.bad_json_rate > 0 and rng.random() < cfg.bad_json_rate:
        return '{"actions":[{"verb":'
    if cfg.mode == "decision":
        raw_goal = summary.get("goal")
        goal = raw_goal.get("id") if isinstance(raw_goal, dict) else raw_goal
        nearby = [
            n for n in (summary.get("nearby_npcs") or [])
            if isinstance(n, dict) and str(n.get("id") or "")
            and str(n.get("id")) != str(summary.get("npc") or "")
            and not str(n.get("id")).startswith("player:")
        ]
        npc = str(summary.get("npc") or "")
        # 每隔几个 NPC 给一句规则域意图（move），用于验证“模型不得替换生存动作”的丢弃原因分布。
        rotate_move = bool(npc) and (sum(ord(ch) for ch in npc) % 7 == 0)
        if rotate_move:
            intent = "去集市看看"
        elif nearby:
            intent = f"找{nearby[0].get('name') or nearby[0].get('id')}闲聊几句"
        else:
            exits = [str(x) for x in (summary.get("exits") or []) if str(x)]
            intent = f"去{exits[0] if exits else '集市'}看看"
        return json.dumps({"goal": goal, "intent": intent}, ensure_ascii=False, sort_keys=True)
    actions = _generate_actions(summary, rng, cfg.long_output)
    return json.dumps({"actions": actions}, ensure_ascii=False, sort_keys=True)


def _completion_body(cfg: FakeLLMConfig, body: dict[str, Any], content: str, index: int) -> dict[str, Any]:
    model = str(body.get("model") or "fake-llm")
    prompt_tokens = max(1, sum(len(str(m.get("content") or "")) for m in (body.get("messages") or [])) // 4)
    completion_tokens = max(1, len(content) // 4)
    return {
        "id": f"chatcmpl-fake-{index}",
        "object": "chat.completion",
        "created": 1_700_000_000 + index,
        "model": model,
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


class _Handler(BaseHTTPRequestHandler):
    server_version = "wsim-fake-llm/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # 静默，避免污染 CI 输出
        return

    def _send(self, status: int, payload: bytes, ctype: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:  # noqa: N802
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send(404, b'{"error":"not_found"}')
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, TypeError):
            self._send(400, b'{"error":"bad_request"}')
            return
        cfg: FakeLLMConfig = self.server.cfg  # type: ignore[attr-defined]
        with cfg.lock:
            index = cfg.counter
            cfg.counter += 1
        if cfg.delay_s > 0:
            time.sleep(cfg.delay_s)
        rng = random.Random((cfg.seed << 16) ^ index ^ 0x9E3779B9)
        if cfg.fail_rate > 0 and rng.random() < cfg.fail_rate:
            self._send(500, b'{"error":{"message":"fake_injected_failure"}}')
            return
        content = _response_content(cfg, body if isinstance(body, dict) else {}, index)
        payload = json.dumps(
            _completion_body(cfg, body if isinstance(body, dict) else {}, content, index),
            ensure_ascii=False,
        ).encode("utf-8")
        self._send(200, payload)


class FakeLLMServer:
    """可嵌入测试/framework_check 的进程内假端点。"""

    def __init__(
        self,
        port: int = 0,
        seed: int = 42,
        delay_s: float = 0.0,
        fail_rate: float = 0.0,
        bad_json_rate: float = 0.0,
        long_output: bool = False,
        mode: str = "actions",
    ):
        self._httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
        self._httpd.daemon_threads = True
        self._httpd.cfg = FakeLLMConfig(  # type: ignore[attr-defined]
            seed=seed,
            delay_s=delay_s,
            fail_rate=fail_rate,
            bad_json_rate=bad_json_rate,
            long_output=long_output,
            mode=mode,
        )
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return int(self._httpd.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def start(self) -> "FakeLLMServer":
        if self._thread is None:
            self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def serve_forever(self) -> None:
        self._httpd.serve_forever()

    def __enter__(self) -> "FakeLLMServer":
        return self.start()

    def __exit__(self, *_exc: Any) -> None:
        self.stop()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=0, help="0 表示随机端口")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--delay", type=float, default=0.0, help="每次响应前延迟秒数")
    p.add_argument("--fail-rate", type=float, default=0.0, help="注入 HTTP 500 的比例")
    p.add_argument("--bad-json-rate", type=float, default=0.0, help="返回截断 JSON 的比例")
    p.add_argument("--long-output", action="store_true", help="返回 8 个动作（触发 too_many_actions）")
    p.add_argument("--mode", choices=("actions", "decision"), default="actions")
    args = p.parse_args()
    srv = FakeLLMServer(
        port=args.port,
        seed=args.seed,
        delay_s=args.delay,
        fail_rate=args.fail_rate,
        bad_json_rate=args.bad_json_rate,
        long_output=args.long_output,
        mode=args.mode,
    )
    print(f"PORT={srv.port}", flush=True)
    print(f"FAKE_LLM_BASE_URL={srv.base_url}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
