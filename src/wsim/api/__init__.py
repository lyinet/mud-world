"""玩家 HTTP API（P3-E）：标准库薄服务，只做 JSON 进出，不做页面。"""
from .server import serve
from .session import WorldSession

__all__ = ["WorldSession", "serve"]
