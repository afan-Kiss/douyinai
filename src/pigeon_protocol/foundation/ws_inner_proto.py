"""169B inner protobuf / variant classification (edbX init vs encrypted send)."""
from __future__ import annotations

import math
import re
import struct
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Any

MAGIC_EDBX = b"edbX"
INNER_LEN = 169
HEADER_LEN = 8
BODY_LEN = 161


class InnerVariant(str, Enum):
    ENCRYPTED_SEND = "encrypted_send"  # 8B class hdr + 161B ring/AES-GCM body
    EDBX_INIT = "edbx_init"  # 4B edbX + 165B init ticket proto (not send-usable)
    UNKNOWN = "unknown"


@dataclass
class ProtoField:
    offset: int
    field_num: int
    wire: int
    value: Any


def _entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = Counter(data)
    n = len(data)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def read_varint(data: bytes, i: int) -> tuple[int | None, int]:
    val = 0
    shift = 0
    start = i
    while i < len(data):
        b = data[i]
        i += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            return val, i
        shift += 7
        if shift > 63:
            return None, start
    return None, start


def parse_protobuf_wire(data: bytes, *, max_fields: int = 32) -> list[ProtoField]:
    fields: list[ProtoField] = []
    i = 0
    while i < len(data) and len(fields) < max_fields:
        start = i
        tag, i = read_varint(data, i)
        if tag is None or i == start:
            break
        field_num = tag >> 3
        wire = tag & 0x07
        if field_num == 0 or wire > 5:
            break
        try:
            if wire == 0:
                val, i = read_varint(data, i)
                if val is None:
                    break
                fields.append(ProtoField(start, field_num, wire, val))
            elif wire == 1:
                if i + 8 > len(data):
                    break
                fields.append(ProtoField(start, field_num, wire, data[i : i + 8].hex()))
                i += 8
            elif wire == 2:
                ln, i = read_varint(data, i)
                if ln is None or i + ln > len(data):
                    break
                chunk = data[i : i + ln]
                i += ln
                ascii_hint = chunk.decode("utf-8", errors="replace") if ln <= 120 else ""
                fields.append(
                    ProtoField(
                        start,
                        field_num,
                        wire,
                        {"len": ln, "hex": chunk[:48].hex(), "ascii": ascii_hint[:100] or None},
                    )
                )
            elif wire == 5:
                if i + 4 > len(data):
                    break
                fields.append(ProtoField(start, field_num, wire, data[i : i + 4].hex()))
                i += 4
            else:
                break
        except IndexError:
            break
    return fields


def classify_inner(inner: bytes) -> dict[str, Any]:
    if len(inner) != INNER_LEN:
        return {"variant": InnerVariant.UNKNOWN.value, "error": f"expected {INNER_LEN} bytes, got {len(inner)}"}

    if inner[:4] == MAGIC_EDBX:
        payload = inner[4:]
        ticket = extract_edbx_ticket(inner)
        route_ok = bool(ticket and ":pigeon" in ticket)
        return {
            "variant": InnerVariant.EDBX_INIT.value,
            "magic": "edbX",
            "payload_len": len(payload),
            "payload_entropy": round(_entropy(payload), 3),
            "ticket": ticket,
            "send_usable": route_ok,
            "note": (
                "jinritemai ticket inner — protobuf core {f1=0,f2=7,f3=route}; "
                "envelope+trailer session-scoped (pure Python core in ws_inner_edbx)"
            ),
        }

    hdr = inner[:8]
    body = inner[8:]
    body_ent = _entropy(body)
    gcm_layout = aes_gcm_layout(body)
    wire = parse_protobuf_wire(body)
    return {
        "variant": (InnerVariant.ENCRYPTED_SEND if body_ent >= 6.4 else InnerVariant.UNKNOWN).value,
        "header_hex": hdr.hex(),
        "header_le32": struct.unpack("<II", hdr),
        "body_entropy": round(body_ent, 3),
        "body_sha256_prefix": __import__("hashlib").sha256(body).hexdigest()[:16],
        "aes_gcm_layout": gcm_layout,
        "protobuf_wire_body": [
            {"offset": f.offset, "field": f.field_num, "wire": f.wire, "value": f.value} for f in wire
        ],
        "send_usable": body_ent >= 6.4,
        "note": "161B body = ring/AES-GCM output (Rust .node cmd 11327)",
    }


def aes_gcm_layout(body: bytes) -> dict[str, Any]:
    """Hypothesis: 161B = 12B nonce + 133B ciphertext + 16B tag (ring AES-GCM)."""
    if len(body) != BODY_LEN:
        return {"ok": False}
    nonce, ct, tag = body[:12], body[12:-16], body[-16:]
    return {
        "ok": True,
        "nonce_hex": nonce.hex(),
        "ciphertext_len": len(ct),
        "tag_hex": tag.hex(),
        "fits_ring_aead": len(nonce) == 12 and len(tag) == 16 and len(ct) == 133,
    }


def extract_edbx_ticket(inner: bytes) -> str | None:
    if inner[:4] != MAGIC_EDBX:
        return None
    payload = inner[4:]
    m = re.search(rb"[A-Za-z0-9_+/=-]{20,}:[0-9]+::[0-9]+:[0-9]+:pigeon", payload)
    if m:
        return m.group(0).decode("ascii", errors="replace")
    m2 = re.search(rb"MS4w[A-Za-z0-9+/=_-]{8,}", payload)
    if m2:
        return m2.group(0).decode("ascii", errors="replace")
    return None


def corpus_variant_summary(inners: list[bytes]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    tickets: list[str] = []
    for inner in inners:
        info = classify_inner(inner)
        variant = info.get("variant")
        key = variant.value if isinstance(variant, InnerVariant) else str(variant)
        counts[key] += 1
        t = info.get("ticket")
        if t:
            tickets.append(t)
    return {
        "total": len(inners),
        "by_variant": dict(counts),
        "edbx_tickets_sample": tickets[:3],
        "encrypted_count": counts.get(InnerVariant.ENCRYPTED_SEND.value, 0),
        "edbx_count": counts.get(InnerVariant.EDBX_INIT.value, 0),
    }
