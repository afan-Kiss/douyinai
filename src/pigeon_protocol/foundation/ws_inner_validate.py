"""169B inner validation — session headers vary (not always 0x231a magic)."""
from __future__ import annotations

import math
from collections import Counter

from pigeon_protocol.foundation.ws_inner_proto import extract_edbx_ticket

INNER_LEN = 169
HEADER_LEN = 8
BODY_LEN = 161
MIN_BODY_ENTROPY = 6.0


def body_entropy(body: bytes) -> float:
    if not body:
        return 0.0
    counts = Counter(body)
    n = len(body)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def is_valid_inner_bytes(inner: bytes) -> bool:
    if len(inner) != INNER_LEN:
        return False
    if inner[:4] == b"edbX":
        return bool(extract_edbx_ticket(inner))
    hdr0 = int.from_bytes(inner[0:4], "little")
    hdr1 = int.from_bytes(inner[4:8], "little")
    if hdr0 == 0 and hdr1 == 0:
        return False
    body = inner[8:]
    if len(body) != BODY_LEN:
        return False
    return body_entropy(body) >= MIN_BODY_ENTROPY


def parse_inner_hex(inner_hex: str) -> bytes | None:
    if not inner_hex or len(inner_hex) != INNER_LEN * 2:
        return None
    try:
        inner = bytes.fromhex(inner_hex)
    except ValueError:
        return None
    return inner if is_valid_inner_bytes(inner) else None
