"""管理控制台后端（独立进程，默认 127.0.0.1:8031）。

只通过 `service.sqlite` 的 admin_* 表与主服务交换配置；不挂玩家端口，
不改 `wsim/api/**`。入口：`python -m wsim.admin`。
"""
from __future__ import annotations

__version__ = "1.0.0"
