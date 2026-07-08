"""SM3 hash — port of bdms.js `be` class."""
from __future__ import annotations

import struct


def _rotl(x: int, n: int) -> int:
    return ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF


def _tj(j: int) -> int:
    return 0x79CC4519 if j < 16 else 0x7A879D8A


def _ff(j: int, x: int, y: int, z: int) -> int:
    if j < 16:
        return (x ^ y ^ z) & 0xFFFFFFFF
    return ((x & y) | (x & z) | (y & z)) & 0xFFFFFFFF


def _gg(j: int, x: int, y: int, z: int) -> int:
    if j < 16:
        return (x ^ y ^ z) & 0xFFFFFFFF
    return ((x & y) | ((~x) & z)) & 0xFFFFFFFF


def _p0(x: int) -> int:
    return (x ^ _rotl(x, 9) ^ _rotl(x, 17)) & 0xFFFFFFFF


def _p1(x: int) -> int:
    return (x ^ _rotl(x, 15) ^ _rotl(x, 23)) & 0xFFFFFFFF


def _expand(block: bytes) -> tuple[list[int], list[int]]:
    w = [0] * 68
    for i in range(16):
        w[i] = struct.unpack(">I", block[i * 4 : i * 4 + 4])[0]
    for j in range(16, 68):
        w[j] = (
            _p1(w[j - 16] ^ w[j - 9] ^ _rotl(w[j - 3], 15)) ^ _rotl(w[j - 13], 7) ^ w[j - 6]
        ) & 0xFFFFFFFF
    w1 = [(w[j] ^ w[j + 4]) & 0xFFFFFFFF for j in range(64)]
    return w, w1


class SM3:
    """bdms IV (same as be class in bdms.js)."""

    _IV = (1937774191, 1226093241, 388252375, 3666478592, 2842636476, 372324522, 3817729613, 2969243214)

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.reg = list(self._IV)
        self.buf = bytearray()
        self.total = 0

    def update(self, data: bytes | str) -> None:
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.buf.extend(data)
        self.total += len(data)

    def _compress(self, block: bytes) -> None:
        w, w1 = _expand(block)
        a, b, c, d, e, f, g, h = self.reg
        for j in range(64):
            ss1 = _rotl((_rotl(a, 12) + e + _rotl(_tj(j), j % 32)) & 0xFFFFFFFF, 7)
            ss2 = ss1 ^ _rotl(a, 12)
            tt1 = (_ff(j, a, b, c) + d + ss2 + w1[j]) & 0xFFFFFFFF
            tt2 = (_gg(j, e, f, g) + h + ss1 + w[j]) & 0xFFFFFFFF
            d, c, b, a = c, _rotl(b, 9), a, tt1
            h, g, f, e = g, _rotl(f, 19), e, _p0(tt2)
        self.reg = [(self.reg[i] ^ v) & 0xFFFFFFFF for i, v in enumerate([a, b, c, d, e, f, g, h])]

    def digest(self) -> bytes:
        buf = bytes(self.buf)
        bit_len = self.total * 8
        buf += b"\x80"
        while (len(buf) % 64) != 56:
            buf += b"\x00"
        buf += struct.pack(">Q", bit_len)
        self.buf.clear()
        for i in range(0, len(buf), 64):
            self._compress(buf[i : i + 64])
        out = b"".join(struct.pack(">I", x) for x in self.reg)
        self.reset()
        return out

    def hexdigest(self) -> str:
        return self.digest().hex()

    def sum_hex(self, text: str) -> str:
        self.reset()
        self.update(text)
        return self.hexdigest()


def sm3_hex(text: str) -> str:
    return SM3().sum_hex(text)


def double_sm3_query(query: str) -> bytes:
    """SM3(SM3(query + 'bds')) → 32 bytes (bdms 1.0.1.20 pattern)."""
    inner = SM3().sum_hex(query + "bds")
    outer = SM3().sum_hex(bytes.fromhex(inner).decode("latin-1"))
    return bytes.fromhex(outer)
