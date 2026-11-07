"""MCP 审计日志。

确定性约束：不读墙钟。`time` 使用虚拟 tick + 单调 audit_seq，保证回放可复现。

容量约束（W1 生产审计修复）：

- 落盘按大小轮转：默认 100MB、保留 5 份，可用 `XKX_AUDIT_MAX_BYTES` /
  `XKX_AUDIT_BACKUPS` 覆盖；轮转由后台线程执行，写入路径只做 append + 计数，
  不等待改名/删除。
- 活动文件使用长驻 fd（`os.open` + `O_APPEND` + `os.write`），不再每条记录
  open/close 一次。soak 实测：`close()` 偶发 100–400ms 的系统级停顿，4 次/写请求
  经 session 写锁放大成 write.max 1277ms；长驻 fd 后请求路径只剩一次 write。
- 进程内缓存 `records` 有上限（`XKX_AUDIT_MEMORY_MAX`，默认 4096 条），
  只保留最近记录；完整历史仍以文件为准。
"""
from __future__ import annotations

import json
import os
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_MAX_BYTES = 100 * 1024 * 1024
DEFAULT_BACKUPS = 5
DEFAULT_MEMORY_MAX = 4096


def env_int(name: str, default: int) -> int:
    """读取非负整数环境变量；非法值回落到默认值。"""
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class AuditRecord:
    seq: int          # 审计序号（网关内单调）
    tick: int         # 虚拟时间（1 tick = 10 min）
    caller: str
    permission: str
    method: str
    params_digest: str
    ok: bool
    result: dict[str, Any]
    error: str = ""
    identity_source: str = "server_map"

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "tick": self.tick,
            "caller": self.caller,
            "permission": self.permission,
            "method": self.method,
            "params_digest": self.params_digest,
            "ok": self.ok,
            "result": self.result,
            "error": self.error,
            "identity_source": self.identity_source,
        }


class AuditLog:
    def __init__(
        self,
        path: Path | None = None,
        *,
        max_bytes: int | None = None,
        backups: int | None = None,
        memory_max: int | None = None,
    ):
        self.path = Path(path) if path is not None else None
        self.max_bytes = (
            env_int("XKX_AUDIT_MAX_BYTES", DEFAULT_MAX_BYTES) if max_bytes is None else max(0, int(max_bytes))
        )
        self.backups = (
            env_int("XKX_AUDIT_BACKUPS", DEFAULT_BACKUPS) if backups is None else max(0, int(backups))
        )
        self.memory_max = (
            env_int("XKX_AUDIT_MEMORY_MAX", DEFAULT_MEMORY_MAX)
            if memory_max is None
            else max(0, int(memory_max))
        )
        self.records: deque[AuditRecord] = deque(maxlen=self.memory_max)
        self.rotation_count = 0
        self._lock = threading.Lock()
        self._rotate_lock = threading.Lock()
        self._rotate_thread: threading.Thread | None = None
        self._rotating = False
        self._written = 0
        self._fd: int | None = None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                self._written = self.path.stat().st_size
            except FileNotFoundError:
                self._written = 0

    # ------------------------------------------------------------------ write
    def append(self, record: AuditRecord) -> None:
        self.records.append(record)
        if self.path is None:
            return
        data = (
            json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True) + "\n"
        ).encode("utf-8")
        size = len(data)
        with self._lock:
            fd = self._ensure_fd()
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("audit write returned 0 bytes")
                view = view[written:]
            self._written += size
            rotate = (
                self.max_bytes > 0
                and self.backups > 0
                and self._written >= self.max_bytes
                and not self._rotating
            )
            if rotate:
                # 轮转交给后台线程：写入路径不等待改名/删除。
                self._rotating = True
                thread = threading.Thread(
                    target=self._rotate_worker, name="audit-rotate", daemon=True
                )
                self._rotate_thread = thread
                thread.start()

    def _ensure_fd(self) -> int:
        """活动文件的常驻 fd；调用方必须持有 `_lock`。"""
        if self._fd is None:
            assert self.path is not None
            self._fd = os.open(
                self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644
            )
        return self._fd

    def _close_fd(self) -> None:
        """调用方必须持有 `_lock`；轮转改名前后必须断开旧 inode。"""
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

    def _rotate_worker(self) -> None:
        try:
            self.rotate_now()
        finally:
            with self._lock:
                self._rotating = False

    def rotate_now(self) -> int:
        """同步轮转一次（测试/运维用；写路径永远走后台线程）。

        返回轮转后存在的备份文件数。`path` 不存在时返回 0。
        """
        if self.path is None or self.max_bytes <= 0 or self.backups <= 0:
            return 0
        with self._rotate_lock:
            # 改名期间持有写锁：先把长驻 fd 从旧 inode 上摘下来，
            # 否则后续 append 会写进已改名为 .1 的备份文件。
            with self._lock:
                self._close_fd()
                for idx in range(self.backups, 0, -1):
                    src = self._backup_path(idx)
                    if not src.exists():
                        continue
                    if idx == self.backups:
                        src.unlink()
                    else:
                        os.replace(src, self._backup_path(idx + 1))
                if self.path.exists():
                    os.replace(self.path, self._backup_path(1))
                self._written = 0
            self.rotation_count += 1
            return sum(1 for i in range(1, self.backups + 1) if self._backup_path(i).exists())

    def wait_rotation(self, timeout: float = 5.0) -> bool:
        """等待当前后台轮转结束；无轮转任务时立即返回 True。"""
        thread = self._rotate_thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def _backup_path(self, index: int) -> Path:
        assert self.path is not None
        return self.path.with_name(f"{self.path.name}.{index}")

    # ------------------------------------------------------------------- read
    def read_all(self) -> list[dict[str, Any]]:
        if self.path is not None and self.path.exists():
            return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line]
        return [r.to_dict() for r in self.records]
