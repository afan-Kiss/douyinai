"""169B WS inner layout — 8-byte class header + 161-byte session crypto body."""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

from pigeon_protocol.ws_inner_buckets import BUCKET_INNER_FP, INIT_SYNC_INNER_FP

INNER_LEN = 169
HEADER_LEN = 8
BODY_LEN = 161
BODY_OFFSET = 8

# Reference headers from one harvested session (NOT universal — full 169B is session-scoped).
EMPIRICAL_CLASS_HEADERS: dict[str, tuple[int, int]] = {
    "E": (708405261, 3740781305),
    "F": (65237018, 2624923661),
    "G": (1710902552, 1427094119),
    "INIT_SYNC": (INIT_SYNC_INNER_FP["le32_0"], INIT_SYNC_INNER_FP["le32_4"]),
}


@dataclass(frozen=True)
class InnerLayout:
    class_name: str
    header: tuple[int, int]
    body: bytes

    @property
    def header_bytes(self) -> bytes:
        return struct.pack("<II", self.header[0], self.header[1])

    @property
    def inner_bytes(self) -> bytes:
        if len(self.body) != BODY_LEN:
            raise ValueError(f"body must be {BODY_LEN} bytes, got {len(self.body)}")
        return self.header_bytes + self.body

    @property
    def body_sha256_prefix(self) -> str:
        return hashlib.sha256(self.body).hexdigest()[:16]


def class_header(class_name: str) -> tuple[int, int] | None:
    """Reference LE32 header from a prior harvest — use session cache for live sends."""
    if class_name in BUCKET_INNER_FP:
        fp = BUCKET_INNER_FP[class_name]
        return fp["le32_0"], fp["le32_4"]
    return EMPIRICAL_CLASS_HEADERS.get(class_name)


def split_inner(inner: bytes) -> InnerLayout:
    if len(inner) != INNER_LEN:
        raise ValueError(f"expected {INNER_LEN} bytes")
    hdr = (
        int.from_bytes(inner[0:4], "little"),
        int.from_bytes(inner[4:8], "little"),
    )
    return InnerLayout(class_name="?", header=hdr, body=inner[8:])


def assemble_inner(class_name: str, body: bytes) -> bytes:
    """Combine known class header + 161-byte session body."""
    hdr = class_header(class_name)
    if not hdr:
        raise ValueError(f"unknown class header: {class_name}")
    if len(body) != BODY_LEN:
        raise ValueError(f"body must be {BODY_LEN} bytes")
    return struct.pack("<II", hdr[0], hdr[1]) + body


def layout_report() -> dict:
    rows = []
    for name in ("A", "B", "C", "D", "E", "F", "G", "INIT_SYNC"):
        hdr = class_header(name)
        if hdr:
            rows.append({"class": name, "header_hex": struct.pack("<II", hdr[0], hdr[1]).hex()})
    return {
        "structure": f"{HEADER_LEN}B session header + {BODY_LEN}B session crypto body (full {INNER_LEN}B is per-login)",
        "formula": "inner(session, class) = opaque 169B constant per class within session",
        "classes": rows,
        "re_note": "header+body are session-scoped; re-login needs CDP harvest or bundle re-export",
    }
