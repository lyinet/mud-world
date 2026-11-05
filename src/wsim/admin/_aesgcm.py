"""AES-256-GCM 的最小纯 Python 实现（仅在 `cryptography` 不可用时兜底）。

只用 12 字节 nonce、无 AAD；接口与 `cryptography` 的 AESGCM 兼容：
`encrypt(nonce, plaintext, None) -> ciphertext||tag`、`decrypt(...) -> plaintext`。

安全说明：纯 Python 实现不是常数时间；生产环境应优先使用 `cryptography`。
本模块存在的意义是：仓库当前 venv 未安装 `cryptography` 时，管理服务仍能用
真 AES-256-GCM 而不是明文或自造流密码。
"""
from __future__ import annotations

import hmac
from typing import Iterable


def _xtime(a: int) -> int:
    a <<= 1
    if a & 0x100:
        a = (a ^ 0x11B) & 0xFF
    return a


def _gmul(a: int, b: int) -> int:
    result = 0
    for _ in range(8):
        if b & 1:
            result ^= a
        b >>= 1
        a = _xtime(a)
    return result


def _rotl8(value: int, count: int) -> int:
    return ((value << count) | (value >> (8 - count))) & 0xFF


def _build_sbox() -> list[int]:
    sbox = [0] * 256
    p = 1
    q = 1
    while True:
        # p <- p * 3 in GF(2^8)
        p = p ^ (p << 1) ^ (0x1B if p & 0x80 else 0)
        p &= 0xFF
        # q <- q / 3 in GF(2^8)
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        x = (q ^ _rotl8(q, 1) ^ _rotl8(q, 2) ^ _rotl8(q, 3) ^ _rotl8(q, 4)) & 0xFF
        sbox[p] = x ^ 0x63
        if p == 1:
            break
    sbox[0] = 0x63
    return sbox


SBOX = _build_sbox()
RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40)
_NK = 8
_NR = 14


def _rot_word(word: int) -> int:
    return ((word << 8) | (word >> 24)) & 0xFFFFFFFF


def _sub_word(word: int) -> int:
    return (
        (SBOX[(word >> 24) & 0xFF] << 24)
        | (SBOX[(word >> 16) & 0xFF] << 16)
        | (SBOX[(word >> 8) & 0xFF] << 8)
        | SBOX[word & 0xFF]
    )


def expand_key(key: bytes) -> list[int]:
    if len(key) != 32:
        raise ValueError("AES-256 需要 32 字节密钥")
    words = [int.from_bytes(key[4 * i : 4 * i + 4], "big") for i in range(_NK)]
    for i in range(_NK, 4 * (_NR + 1)):
        temp = words[i - 1]
        if i % _NK == 0:
            temp = _sub_word(_rot_word(temp)) ^ (RCON[i // _NK - 1] << 24)
        elif i % _NK == 4:
            temp = _sub_word(temp)
        words.append(words[i - _NK] ^ temp)
    return words


def _add_round_key(state: list[int], words: list[int], rnd: int) -> None:
    for col in range(4):
        word = words[4 * rnd + col]
        state[4 * col] ^= (word >> 24) & 0xFF
        state[4 * col + 1] ^= (word >> 16) & 0xFF
        state[4 * col + 2] ^= (word >> 8) & 0xFF
        state[4 * col + 3] ^= word & 0xFF


def _shift_rows(state: list[int]) -> list[int]:
    out = [0] * 16
    for col in range(4):
        for row in range(4):
            out[4 * col + row] = state[4 * ((col + row) % 4) + row]
    return out


def _mix_columns(state: list[int]) -> None:
    for col in range(4):
        a0, a1, a2, a3 = state[4 * col : 4 * col + 4]
        total = a0 ^ a1 ^ a2 ^ a3
        b0 = a0 ^ total ^ _xtime(a0 ^ a1)
        b1 = a1 ^ total ^ _xtime(a1 ^ a2)
        b2 = a2 ^ total ^ _xtime(a2 ^ a3)
        b3 = a3 ^ total ^ _xtime(a3 ^ a0)
        state[4 * col : 4 * col + 4] = [b0, b1, b2, b3]


def encrypt_block(words: list[int], block: bytes) -> bytes:
    if len(block) != 16:
        raise ValueError("block 必须 16 字节")
    state = list(block)
    _add_round_key(state, words, 0)
    for rnd in range(1, _NR):
        state = [_sbox_byte(value) for value in state]
        state = _shift_rows(state)
        _mix_columns(state)
        _add_round_key(state, words, rnd)
    state = [_sbox_byte(value) for value in state]
    state = _shift_rows(state)
    _add_round_key(state, words, _NR)
    return bytes(state)


def _sbox_byte(value: int) -> int:
    return SBOX[value & 0xFF]


_R = 0xE1 << 120


def _gf_mul(x: int, y: int) -> int:
    z = 0
    v = y
    for i in range(128):
        if (x >> (127 - i)) & 1:
            z ^= v
        if v & 1:
            v = (v >> 1) ^ _R
        else:
            v >>= 1
    return z


def _ghash(h: int, chunks: Iterable[bytes]) -> int:
    y = 0
    for chunk in chunks:
        for offset in range(0, len(chunk), 16):
            block = chunk[offset : offset + 16]
            y = _gf_mul(y ^ int.from_bytes(block, "big"), h)
    return y


def _pad16(data: bytes) -> bytes:
    if len(data) % 16 == 0:
        return b""
    return b"\x00" * (16 - len(data) % 16)


def _ctr(words: list[int], j0: bytes, data: bytes) -> bytes:
    prefix = j0[:12]
    counter = int.from_bytes(j0[12:], "big")
    out = bytearray()
    for offset in range(0, len(data), 16):
        # GCM 的计数器从 inc32(J0) 开始，即首块用 J0+1。
        block = prefix + ((counter + 1 + offset // 16) & 0xFFFFFFFF).to_bytes(4, "big")
        stream = encrypt_block(words, block)
        chunk = data[offset : offset + 16]
        out.extend(bytes(a ^ b for a, b in zip(chunk, stream)))
    return bytes(out)


def _tag(words: list[int], h: int, j0: bytes, aad: bytes, ciphertext: bytes) -> bytes:
    lengths = (len(aad) * 8).to_bytes(8, "big") + (len(ciphertext) * 8).to_bytes(8, "big")
    ghash = _ghash(h, [aad + _pad16(aad), ciphertext + _pad16(ciphertext), lengths])
    mask = int.from_bytes(encrypt_block(words, j0), "big")
    return (mask ^ ghash).to_bytes(16, "big")


def encrypt(key: bytes, nonce: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    if len(nonce) != 12:
        raise ValueError("nonce 必须 12 字节")
    words = expand_key(key)
    h = int.from_bytes(encrypt_block(words, b"\x00" * 16), "big")
    j0 = nonce + b"\x00\x00\x00\x01"
    ciphertext = _ctr(words, j0, plaintext)
    return ciphertext + _tag(words, h, j0, aad, ciphertext)


def decrypt(key: bytes, nonce: bytes, data: bytes, aad: bytes = b"") -> bytes:
    if len(nonce) != 12 or len(data) < 16:
        raise ValueError("nonce 或密文长度非法")
    ciphertext, tag = data[:-16], data[-16:]
    words = expand_key(key)
    h = int.from_bytes(encrypt_block(words, b"\x00" * 16), "big")
    j0 = nonce + b"\x00\x00\x00\x01"
    expected = _tag(words, h, j0, aad, ciphertext)
    if not hmac.compare_digest(expected, tag):
        raise ValueError("gcm_tag_mismatch")
    return _ctr(words, j0, ciphertext)
