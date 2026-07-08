"""WS 226B signature blob — decode & reverse helpers (169-byte inner payload)."""
from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass

from pigeon_protocol.ws_sign import SIG_BLOB_LENGTH, locate_signature_region


def decode_blob(blob: bytes) -> bytes:
    """Blob is standard base64 ASCII (226 chars) → 169-byte binary."""
    text = blob.decode("ascii", errors="strict")
    pad = (4 - len(text) % 4) % 4
    return base64.b64decode(text + "=" * pad)


def encode_blob(inner: bytes) -> bytes:
    if len(inner) != 169:
        raise ValueError(f"expected 169 bytes, got {len(inner)}")
    # Official WS frames use 226-char unpadded base64 (decode accepts trailing nibble variants).
    encoded = base64.b64encode(inner)
    if len(encoded) >= 226:
        return encoded[:226]
    return encoded.rstrip(b"=")


@dataclass
class SignatureAnalysis:
    text_byte_length: int
    frame_length: int
    blob_ascii: bytes
    inner: bytes
    inner_hex: str
    client_message_id: str
    md5_text: str


def analyze_frame(raw: bytes, *, text: str = "") -> SignatureAnalysis | None:
    region = locate_signature_region(raw)
    if not region:
        return None
    inner = decode_blob(region.blob)
    bl = len(text.encode("utf-8")) if text else 0
    from pigeon_protocol.ws_sign import extract_client_message_id

    cid = extract_client_message_id(raw)
    return SignatureAnalysis(
        text_byte_length=bl,
        frame_length=len(raw),
        blob_ascii=region.blob,
        inner=inner,
        inner_hex=inner.hex(),
        client_message_id=cid,
        md5_text=hashlib.md5(text.encode("utf-8")).hexdigest() if text else "",
    )


def compare_inners(samples: list[tuple[int, bytes]]) -> dict:
    """Compare decoded 169B payloads keyed by text byte length."""
    if len(samples) < 2:
        return {"error": "need >=2 samples"}
    samples = sorted(samples, key=lambda x: x[0])
    report: dict = {"lengths": [s[0] for s in samples]}
    for i in range(len(samples) - 1):
        a_len, a = samples[i]
        b_len, b = samples[i + 1]
        mn = min(len(a), len(b))
        diff_idx = [j for j in range(mn) if a[j] != b[j]]
        report[f"{a_len}_vs_{b_len}"] = {
            "diff_bytes": len(diff_idx),
            "first_diffs": diff_idx[:20],
            "xor_prefix": bytes(a[j] ^ b[j] for j in diff_idx[:16]).hex() if diff_idx else "",
        }
    return report


def guess_inner_layout(inner: bytes) -> dict:
    """Heuristic field scan on 169B inner signature."""
    out: dict = {}
    # scan for embedded ascii runs
    for m in re.finditer(rb"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", inner):
        out.setdefault("uuids", []).append(m.group(0).decode())
    for m in re.finditer(rb"MS4w[A-Za-z0-9+/=]{8,}", inner):
        out.setdefault("tickets", []).append(m.group(0)[:48].decode())
    # possible length prefix at start
    if len(inner) >= 4:
        from pigeon_protocol.ws_inner_buckets import classify_inner_bucket

        out["le32_0"] = int.from_bytes(inner[0:4], "little")
        out["le32_4"] = int.from_bytes(inner[4:8], "little")
        out["be32_0"] = int.from_bytes(inner[0:4], "big")
        out["inner_bucket"] = classify_inner_bucket(inner)
    out["sha256_prefix"] = hashlib.sha256(inner).hexdigest()[:16]
    return out
