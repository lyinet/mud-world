"""内置 AES-256-GCM 兜底实现的标准向量测试（NIST AES-GCM 用例）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.admin import _aesgcm  # noqa: E402

ZERO_KEY = b"\x00" * 32
ZERO_IV = b"\x00" * 12


def test_sbox_known_values() -> None:
    assert _aesgcm.SBOX[0x00] == 0x63
    assert _aesgcm.SBOX[0x01] == 0x7C
    assert _aesgcm.SBOX[0x53] == 0xED
    assert _aesgcm.SBOX[0xFF] == 0x16


def test_empty_plaintext_vector() -> None:
    out = _aesgcm.encrypt(ZERO_KEY, ZERO_IV, b"")
    assert out.hex() == "530f8afbc74536b9a963b4f1c4cb738b"


def test_zero_block_vector() -> None:
    out = _aesgcm.encrypt(ZERO_KEY, ZERO_IV, b"\x00" * 16)
    assert out.hex() == (
        "cea7403d4d606b6e074ec5d3baf39d18"
        "d0d1c8a799996bf0265b98b5d48ab919"
    )


def test_nist_aad_vector() -> None:
    key = bytes.fromhex("feffe9928665731c6d6a8f9467308308feffe9928665731c6d6a8f9467308308")
    iv = bytes.fromhex("cafebabefacedbaddecaf888")
    plain = bytes.fromhex(
        "d9313225f88406e5a55909c5aff5269a86a7a9531534f7da2e4c303d8a318a72"
        "1c3c0c95956809532fcf0e2449a6b525b16aedf5aa0de657ba637b39"
    )
    aad = bytes.fromhex("feedfacedeadbeeffeedfacedeadbeefabaddad2")
    out = _aesgcm.encrypt(key, iv, plain, aad)
    assert out.hex() == (
        "522dc1f099567d07f47f37a32a84427d643a8cdcbfe5c0c97598a2bd2555d1aa"
        "8cb08e48590dbb3da7b08b1056828838c5f61e6393ba7a0abcc9f662"
        "76fc6ece0f4e1768cddf8853bb2d551b"
    )
    assert _aesgcm.decrypt(key, iv, out, aad) == plain


def test_tamper_rejected() -> None:
    key = bytes.fromhex("00" * 32)
    iv = bytes.fromhex("00" * 12)
    out = bytearray(_aesgcm.encrypt(key, iv, b"hello"))
    out[-1] ^= 0x01
    with pytest.raises(ValueError):
        _aesgcm.decrypt(key, iv, bytes(out))
