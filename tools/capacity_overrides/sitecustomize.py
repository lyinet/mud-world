"""测量专用注入：把区域席位上限临时抬到 XKX_BENCH_MAX_SEATS。

只用于容量实验（例如 300 席档位）。不进生产路径：仅当环境变量
XKX_BENCH_MAX_SEATS 存在时才生效，默认出厂上限仍是 200 席 / 60000 赋能。
"""
from __future__ import annotations

import importlib.abc
import importlib.machinery
import os
import sys

_CAP = int(os.environ.get("XKX_BENCH_MAX_SEATS") or 0)
_TARGET = "wsim.actions.player"


class _WrapLoader(importlib.abc.Loader):
    def __init__(self, inner: importlib.abc.Loader, cap: int) -> None:
        self._inner = inner
        self._cap = cap

    def create_module(self, spec):  # type: ignore[no-untyped-def]
        return self._inner.create_module(spec)

    def exec_module(self, module):  # type: ignore[no-untyped-def]
        self._inner.exec_module(module)
        module.MAX_REGION_PLAYERS = self._cap
        module.ENDOWMENT_TOTAL_CAP = self._cap * 300


class _Patcher(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):  # type: ignore[no-untyped-def]
        if fullname != _TARGET:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or spec.loader is None:
            return None
        spec.loader = _WrapLoader(spec.loader, _CAP)
        return spec


if _CAP:
    sys.meta_path.insert(0, _Patcher())
