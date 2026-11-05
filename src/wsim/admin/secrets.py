"""provider key 的加密存储（AES-256-GCM）。

- 主密钥只来自环境变量 `XKX_SECRET_KEY`（32 字节 base64）；缺失/长度不对抛
  `MasterKeyMissing`，调用方必须返回 503 `master_key_missing`。
- 绝不明文回退：没有主密钥时 provider 只能用环境变量里的 key。
- 掩码规则：`前4****后4`（不足 9 位全星号）；响应/日志/审计只允许掩码。
"""
from __future__ import annotations

import base64
import binascii
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .clock import now_utc

MASTER_KEY_ENV = "XKX_SECRET_KEY"


class MasterKeyMissing(RuntimeError):
    code = "master_key_missing"


def _decode_master(raw: str) -> bytes:
    text = str(raw or "").strip()
    if not text:
        raise MasterKeyMissing("master_key_missing")
    try:
        key = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise MasterKeyMissing("master_key_invalid_base64") from exc
    if len(key) != 32:
        raise MasterKeyMissing("master_key_invalid_length")
    return key


def master_key(env: dict[str, str] | None = None) -> bytes:
    env = env if env is not None else os.environ
    return _decode_master(str(env.get(MASTER_KEY_ENV, "")))


def has_master_key(env: dict[str, str] | None = None) -> bool:
    try:
        master_key(env)
        return True
    except MasterKeyMissing:
        return False


def _crypto_backend():
    """优先用 `cryptography`（常数时间、经过审计）；缺失时用内置 AES-GCM 兜底。"""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        return AESGCM
    except Exception:  # noqa: BLE001 - 缺包不是配置错误，走兜底实现
        return None


BACKEND_NAME = "cryptography" if _crypto_backend() is not None else "builtin-aesgcm"


def encrypt(plaintext: str, env: dict[str, str] | None = None) -> tuple[bytes, bytes]:
    """返回 (ciphertext, nonce)；密文为 `ct||tag`，与 cryptography 格式一致。"""
    key = master_key(env)
    nonce = os.urandom(12)
    plain = str(plaintext).encode("utf-8")
    backend = _crypto_backend()
    if backend is not None:
        ciphertext = backend(key).encrypt(nonce, plain, None)
    else:
        from ._aesgcm import encrypt as _encrypt

        ciphertext = _encrypt(key, nonce, plain)
    return ciphertext, nonce


def decrypt(ciphertext: bytes, nonce: bytes, env: dict[str, str] | None = None) -> str:
    key = master_key(env)
    backend = _crypto_backend()
    try:
        if backend is not None:
            plain = backend(key).decrypt(bytes(nonce), bytes(ciphertext), None)
        else:
            from ._aesgcm import decrypt as _decrypt

            plain = _decrypt(key, bytes(nonce), bytes(ciphertext))
    except Exception as exc:  # noqa: BLE001 - 统一收敛成可审计错误
        raise ValueError("secret_decrypt_failed") from exc
    return plain.decode("utf-8")


def mask_secret(value: Any) -> str:
    text = str(value or "")
    if len(text) < 9:
        return "*" * len(text)
    return f"{text[:4]}****{text[-4:]}"


def _iso_utc(timestamp: Any) -> str | None:
    """0/None 视为“未发生”；否则转 UTC ISO（秒级）。"""
    try:
        value = float(timestamp or 0.0)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SecretStore:
    """admin_secrets 表的读写；调用方保证 schema 已初始化。"""

    def __init__(self, path: str | Path, env: dict[str, str] | None = None):
        self.path = Path(path)
        self.env = env

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.path), timeout=5.0)

    def has(self, secret_id: str) -> bool:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT 1 FROM admin_secrets WHERE id = ?", (str(secret_id),)
            ).fetchone()
        finally:
            conn.close()
        return row is not None

    def put(self, secret_id: str, plaintext: str, who: str = "admin") -> None:
        ciphertext, nonce = encrypt(str(plaintext), self.env)
        now = now_utc()
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO admin_secrets"
                "(id, ciphertext, nonce, created_at, updated_at, last_used_at, updated_by)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET ciphertext = excluded.ciphertext,"
                " nonce = excluded.nonce, updated_at = excluded.updated_at,"
                " updated_by = excluded.updated_by",
                (str(secret_id), ciphertext, nonce, now, now, 0.0, str(who)),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def meta(self, secret_id: str) -> dict[str, Any]:
        """只回掩码与元数据；明文永不外泄。无主密钥时原样抛 `MasterKeyMissing`。"""
        result: dict[str, Any] = {
            "has": False,
            "masked": "",
            "length": 0,
            "created_at": None,
            "updated_at": None,
            "last_used_at": None,
        }
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT ciphertext, nonce, created_at, updated_at, last_used_at"
                " FROM admin_secrets WHERE id = ?",
                (str(secret_id),),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return result
        result["has"] = True
        result["created_at"] = _iso_utc(row[2])
        result["updated_at"] = _iso_utc(row[3])
        result["last_used_at"] = _iso_utc(row[4])
        try:
            plain = decrypt(bytes(row[0]), bytes(row[1]), self.env)
        except MasterKeyMissing:
            raise
        except (ValueError, TypeError, KeyError):
            return result
        result["masked"] = mask_secret(plain)
        result["length"] = len(plain)
        return result

    def touch(self, secret_id: str, at: float | None = None) -> None:
        """更新 last_used_at；不抛异常（无行则忽略）。"""
        try:
            conn = self._conn()
            try:
                conn.execute(
                    "UPDATE admin_secrets SET last_used_at = ? WHERE id = ?",
                    (
                        float(now_utc() if at is None else at),
                        str(secret_id),
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 - 遥测写失败不得影响主调用
            return

    def get(self, secret_id: str) -> str:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT ciphertext, nonce FROM admin_secrets WHERE id = ?", (str(secret_id),)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise KeyError(str(secret_id))
        return decrypt(bytes(row[0]), bytes(row[1]), self.env)

    def masked(self, secret_id: str) -> str:
        try:
            return mask_secret(self.get(secret_id))
        except (KeyError, MasterKeyMissing, ValueError):
            return ""

    def delete(self, secret_id: str) -> None:
        conn = self._conn()
        try:
            conn.execute("DELETE FROM admin_secrets WHERE id = ?", (str(secret_id),))
            conn.commit()
        finally:
            conn.close()

    def ids(self) -> list[str]:
        conn = self._conn()
        try:
            rows = conn.execute("SELECT id FROM admin_secrets ORDER BY id").fetchall()
        finally:
            conn.close()
        return [str(row[0]) for row in rows]
