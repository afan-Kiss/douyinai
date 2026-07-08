"""Pure-Python a_bogus for bdms 1.0.1.20 (Feige / pigeon aid=1383 pageId=30026)."""
from __future__ import annotations

import hashlib
import json
import random
import time
from pathlib import Path
from typing import Any

from pigeon_protocol.foundation.bdms_sm3 import SM3
from pigeon_protocol.foundation.bdms_custom_b64 import rc4 as rc4_encrypt

# From jsvmp string pool indices 255/253 + fn#150 disasm
CHAR_S4 = "Dkdpgh2ZmsQB80/MfvV36XI1R45-WUAlEixNLwoqYTOPuzKFjJnry79HbGcaStCe"
CHAR_S3 = "ckdp1h4ZKsUB80/Mfvw36XIgR25+WQAlEi7NLboqYTOPuzmFjJnryx9HVGDaStCe"

BIG_ARRAY = [
    121, 243, 55, 234, 103, 36, 47, 228, 30, 231, 106, 6, 115, 95, 78, 101, 250, 207, 198, 50,
    139, 227, 220, 105, 97, 143, 34, 28, 194, 215, 18, 100, 159, 160, 43, 8, 169, 217, 180, 120,
    247, 45, 90, 11, 27, 197, 46, 3, 84, 72, 5, 68, 62, 56, 221, 75, 144, 79, 73, 161,
    178, 81, 64, 187, 134, 117, 186, 118, 16, 241, 130, 71, 89, 147, 122, 129, 65, 40, 88, 150,
    110, 219, 199, 255, 181, 254, 48, 4, 195, 248, 208, 32, 116, 167, 69, 201, 17, 124, 125, 104,
    96, 83, 80, 127, 236, 108, 154, 126, 204, 15, 20, 135, 112, 158, 13, 1, 188, 164, 210, 237,
    222, 98, 212, 77, 253, 42, 170, 202, 26, 22, 29, 182, 251, 10, 173, 152, 58, 138, 54, 141,
    185, 33, 157, 31, 252, 132, 233, 235, 102, 196, 191, 223, 240, 148, 39, 123, 92, 82, 128, 109,
    57, 24, 38, 113, 209, 245, 2, 119, 153, 229, 189, 214, 230, 174, 232, 63, 52, 205, 86, 140,
    66, 175, 111, 171, 246, 133, 238, 193, 99, 60, 74, 91, 225, 51, 76, 37, 145, 211, 166, 151,
    213, 206, 0, 200, 244, 176, 218, 44, 184, 172, 49, 216, 93, 168, 53, 21, 183, 41, 67, 85,
    224, 155, 226, 242, 87, 177, 146, 70, 190, 12, 162, 19, 137, 114, 25, 165, 163, 192, 23, 59,
    9, 94, 179, 107, 35, 7, 142, 131, 239, 203, 149, 136, 61, 249, 14, 156,
]

SORT_INDEX = [
    18, 20, 52, 26, 30, 34, 58, 38, 40, 53, 42, 21, 27, 54, 55, 31, 35, 57, 39, 41, 43, 22, 28,
    32, 60, 36, 23, 29, 33, 37, 44, 45, 59, 46, 47, 48, 49, 50, 24, 25, 65, 66, 70, 71,
]
SORT_INDEX_2 = [
    18, 20, 26, 30, 34, 38, 40, 42, 21, 27, 31, 35, 39, 41, 43, 22, 28, 32, 36, 23, 29, 33, 37,
    44, 45, 46, 47, 48, 49, 50, 24, 25, 52, 53, 54, 55, 57, 58, 59, 60, 65, 66, 70, 71,
]

FEIGE_AID = 1383
FEIGE_PAGE_ID = 30026
FEIGE_SALT = "dhzx"
FEIGE_VERSION = "1.0.1.20"


def _js_shift_right(val: int, n: int) -> int:
    return (val % 0x100000000) >> n


def _sm3_bytes(data: str | bytes) -> list[int]:
    sm3 = SM3()
    if isinstance(data, str):
        sm3.update(data)
    else:
        sm3.update(data.decode("latin-1"))
    return list(sm3.digest())


def _double_sm3_array(text: str, *, salt: str = FEIGE_SALT) -> list[int]:
    inner = _sm3_bytes(text + salt)
    return _sm3_bytes(bytes(inner))


def _rc4_encrypt(key: bytes, plaintext: str) -> bytes:
    return rc4_encrypt(plaintext.encode("latin-1"), key)


def _base64_custom(data: str, alphabet: str) -> str:
    out: list[str] = []
    for i in range(0, len(data), 3):
        if i + 2 < len(data):
            n = (ord(data[i]) << 16) | (ord(data[i + 1]) << 8) | ord(data[i + 2])
        elif i + 1 < len(data):
            n = (ord(data[i]) << 16) | (ord(data[i + 1]) << 8)
        else:
            n = ord(data[i]) << 16
        for j, mask in zip(range(18, -1, -6), (0xFC0000, 0x03F000, 0x0FC0, 0x3F)):
            if j == 6 and i + 1 >= len(data):
                break
            if j == 0 and i + 2 >= len(data):
                break
            out.append(alphabet[(n & mask) >> j])
    out.append("=" * ((4 - len(out) % 4) % 4))
    return "".join(out)


def _transform_bytes(values: list[int]) -> str:
    arr = BIG_ARRAY[:]
    s = "".join(chr(v & 0xFF) for v in values)
    result: list[str] = []
    index_b = arr[1]
    initial = 0
    for index, ch in enumerate(s):
        if index == 0:
            initial = arr[index_b]
            sum_i = (index_b + initial) % len(arr)
            arr[1] = initial
            arr[index_b] = index_b
        else:
            sum_i = (initial + value_e) % len(arr)
        fv = arr[sum_i]
        result.append(chr(ord(ch) ^ fv))
        value_e = arr[(index + 2) % len(arr)]
        sum_i = (index_b + value_e) % len(arr)
        initial = arr[sum_i]
        arr[sum_i] = arr[(index + 2) % len(arr)]
        arr[(index + 2) % len(arr)] = initial
        index_b = sum_i
    return "".join(result)


def _random_prefix(length: int = 3) -> str:
    parts: list[str] = []
    for _ in range(length):
        rd = int(random.random() * 10000)
        parts.extend(
            [
                chr(((rd & 255) & 170) | 1),
                chr(((rd & 255) & 85) | 2),
                chr((_js_shift_right(rd, 8) & 170) | 5),
                chr((_js_shift_right(rd, 8) & 85) | 40),
            ]
        )
    return "".join(parts)


def _load_fp_json() -> dict[str, Any]:
    p = Path(__file__).resolve().parents[3] / "analysis" / "browser_fingerprint.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _canvas_webgl_hashes(fp: dict[str, Any]) -> tuple[str, str]:
    """Match jsdom bdms mock: md5(canvasData)[:8] used for canvas + webgl slots."""
    canvas = str(fp.get("canvasData") or "")
    h = hashlib.md5(canvas.encode()).hexdigest()[:8]
    webgl = fp.get("webgl") or {}
    wr = f"{webgl.get('vendor', '')}{webgl.get('renderer', '')}"
    h2 = hashlib.md5(wr.encode()).hexdigest()[:8] if wr else h
    # Feige jsdom canvas mock → bdms reads identical canvas hash twice
    return h, h2 if h2 != h else h


def _browser_fp_from_json() -> str:
    fp = _load_fp_json()
    if not fp:
        return "853|817|877|902|0|0|853|817|1920|1080|1920|1040|853|817|24|24|Win32|8283be6b|8283be6b"
    if fp.get("browser_fp"):
        return str(fp["browser_fp"])
    inner = fp.get("inner") or {}
    screen = fp.get("screen") or {}
    iw, ih = inner.get("w", 853), inner.get("h", 817)
    sw, sh = screen.get("w", 1920), screen.get("h", 1080)
    cd = screen.get("cd", 24)
    base = f"{iw}|{ih}|{iw + 24}|{ih + 85}|0|0|{iw}|{ih}|{sw}|{sh}|{sw}|{sh - 40}|{iw}|{ih}|{cd}|{cd}|Win32"
    c1, c2 = _canvas_webgl_hashes(fp)
    return f"{base}|{c1}|{c2}"


class FeigeABogus:
    """Generate a_bogus matching bdms fn#103→fn#150 pipeline (no browser)."""

    def __init__(
        self,
        *,
        user_agent: str = "",
        browser_fp: str = "",
        aid: int = FEIGE_AID,
        page_id: int = FEIGE_PAGE_ID,
        salt: str = FEIGE_SALT,
        options: tuple[int, int, int] | None = None,
        ua_key: bytes | None = None,
    ) -> None:
        self.aid = aid
        self.page_id = page_id
        self.salt = salt
        # fn#103 CALL pe(..., 1, 0, 8) for GET-style backstage
        self.options = options or (1, 0, 8)
        self.ua_key = ua_key or bytes([0, 1, 8])
        self.user_agent = user_agent or _load_fp_json().get("ua") or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
        )
        self.browser_fp = browser_fp or _browser_fp_from_json()

    def sign_query(self, query: str, body: str = "") -> str:
        """Return a_bogus for URL search string (no leading ?)."""
        start = int(time.time() * 1000)
        array1 = _double_sm3_array(query, salt=self.salt)
        array2 = _double_sm3_array(body, salt=self.salt) if body else _double_sm3_array("", salt=self.salt)
        ua_b64 = _base64_custom(
            _rc4_encrypt(self.ua_key, self.user_agent).decode("latin-1"),
            CHAR_S3,
        )
        array3 = _sm3_bytes(ua_b64)
        end = int(time.time() * 1000)

        o0, o1, o2 = self.options
        ab: dict[int, Any] = {
            8: 3,
            15: {
                "aid": self.aid,
                "pageId": self.page_id,
                "boe": False,
                "ddrt": 8.5,
                "paths": ["^/backstage/"],
                "track": {"mode": 0, "delay": 300, "paths": []},
                "dump": True,
                "rpU": "",
            },
            18: 44,
            19: [1, 0, 1, 0, 1],
            66: 0,
            69: 0,
            70: 0,
            71: 0,
        }
        ab[20] = (start >> 24) & 255
        ab[21] = (start >> 16) & 255
        ab[22] = (start >> 8) & 255
        ab[23] = start & 255
        ab[24] = int(start / 2**32) & 255
        ab[25] = int(start / 2**40) & 255
        ab[26] = (o0 >> 24) & 255
        ab[27] = (o0 >> 16) & 255
        ab[28] = (o0 >> 8) & 255
        ab[29] = o0 & 255
        ab[30] = int(o1 / 256) & 255
        ab[31] = o1 % 256
        ab[32] = (o1 >> 24) & 255
        ab[33] = (o1 >> 16) & 255
        ab[34] = (o2 >> 24) & 255
        ab[35] = (o2 >> 16) & 255
        ab[36] = (o2 >> 8) & 255
        ab[37] = o2 & 255
        ab[38] = array1[21]
        ab[39] = array1[22]
        ab[40] = array2[21]
        ab[41] = array2[22]
        ab[42] = array3[23]
        ab[43] = array3[24]
        ab[44] = (end >> 24) & 255
        ab[45] = (end >> 16) & 255
        ab[46] = (end >> 8) & 255
        ab[47] = end & 255
        ab[48] = ab[8]
        ab[49] = int(end / 2**32) & 255
        ab[50] = int(end / 2**40) & 255
        ab[51] = (self.page_id >> 24) & 255
        ab[52] = (self.page_id >> 16) & 255
        ab[53] = (self.page_id >> 8) & 255
        ab[54] = self.page_id & 255
        ab[55] = self.page_id
        ab[56] = self.aid
        ab[57] = self.aid & 255
        ab[58] = (self.aid >> 8) & 255
        ab[59] = (self.aid >> 16) & 255
        ab[60] = (self.aid >> 24) & 255
        ab[64] = len(self.browser_fp)
        ab[65] = len(self.browser_fp)

        sorted_vals = [ab.get(i, 0) for i in SORT_INDEX]
        fp_arr = [ord(c) for c in self.browser_fp]
        xor_v = ab.get(SORT_INDEX_2[0], 0)
        for idx in range(len(SORT_INDEX_2) - 1):
            xor_v ^= ab.get(SORT_INDEX_2[idx + 1], 0)
        sorted_vals.extend(fp_arr)
        sorted_vals.append(xor_v)

        payload = _random_prefix() + _transform_bytes(sorted_vals)
        return _base64_custom(payload, CHAR_S4)


def feige_abogus_for_session(session: Any | None = None) -> "FeigeABogus":
    """Build signer aligned with live session UA + verifyFp cookie."""
    ua = ""
    browser_fp = ""
    if session is not None:
        ua = str(getattr(session, "user_agent", "") or "")
        fp_json = _load_fp_json()
        if fp_json.get("browser_fp"):
            browser_fp = str(fp_json["browser_fp"])
        else:
            browser_fp = _browser_fp_from_json()
        # When session verifyFp differs from static analysis fp, keep canvas slots but
        # prefer session-bound UA (a_bogus couples UA + fp).
        s_v = str((getattr(session, "cookies", None) or {}).get("s_v_web_id") or "")
        if s_v and fp_json.get("s_v_web_id") and s_v != fp_json.get("s_v_web_id"):
            browser_fp = _browser_fp_from_json()
    return FeigeABogus(user_agent=ua, browser_fp=browser_fp)


def sign_url_query(
    unsigned_url: str,
    *,
    method: str = "GET",
    body: str = "",
    session: Any | None = None,
) -> tuple[str, str]:
    """Append msToken/verifyFp then a_bogus — matches bdms fn#107 hook order."""
    from pigeon_protocol.foundation.bdms_tokens import backstage_query_tokens
    from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

    parsed = urlparse(unsigned_url)
    pairs = list(parse_qsl(parsed.query, keep_blank_values=True))
    existing = {k for k, _ in pairs}
    for key, val in backstage_query_tokens(session).items():
        if val and key not in existing:
            pairs.append((key, val))
            existing.add(key)
    query = urlencode(pairs)
    signer = feige_abogus_for_session(session)
    bogus = signer.sign_query(query, body if method.upper() == "POST" else "")
    pairs.append(("a_bogus", bogus))
    signed = urlunparse(parsed._replace(query=urlencode(pairs)))
    return signed, bogus
