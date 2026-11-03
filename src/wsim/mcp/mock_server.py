"""本地 mock MCP 服务器。

不接外部服务、不联网。提供：
- `MockMCPServer.handle(request)`：本地函数调用；
- `serve_stdio()`：逐行 JSON 的 stdio 模式，便于以后替换成真正的 MCP transport。
"""
from __future__ import annotations

import json
import sys
from typing import Any

from .gateway import GatewayError, MCPGateway


class MockMCPServer:
    def __init__(self, gateway: MCPGateway):
        self.gateway = gateway

    def tools(self) -> list[dict[str, Any]]:
        return self.gateway.tools()

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        try:
            result = self.gateway.call(
                caller=str(request.get("caller", "unknown")),
                permission=str(request.get("permission", "read")),
                method=str(request.get("method", "")),
                params=dict(request.get("params") or {}),
            )
            return {"ok": True, "result": result}
        except GatewayError as exc:
            return {"ok": False, "error": str(exc)}

    def serve_stdio(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
                resp = self.handle(req)
            except Exception as exc:
                resp = {"ok": False, "error": f"bad_request:{exc}"}
            sys.stdout.write(json.dumps(resp, ensure_ascii=False, sort_keys=True) + "\n")
            sys.stdout.flush()


def main() -> None:  # pragma: no cover - 真实运行入口
    raise SystemExit("mock server 需要由调用方传入 MCPGateway；请使用 MockMCPServer(gateway).serve_stdio()")


if __name__ == "__main__":  # pragma: no cover
    main()
