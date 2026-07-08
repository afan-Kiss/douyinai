"""169B inner blob store — canonical inners per bucket for patch/re-sign RE."""
from __future__ import annotations

import base64
from functools import lru_cache

from pigeon_protocol.ws_sign import locate_signature_region
from pigeon_protocol.ws_sign_bucket import BUCKET_SPECS, bucket_for_text_len
from pigeon_protocol.ws_sign_decode import decode_blob, encode_blob


@lru_cache(maxsize=8)
def canonical_inner_for_bucket(bucket_name: str) -> bytes | None:
    from pigeon_protocol.capture_loader import find_send_template, load_capture, index_send_templates

    spec = next((s for s in BUCKET_SPECS if s.name == bucket_name), None)
    if not spec:
        return None
    info = index_send_templates().get(spec.canonical_len)
    if not info:
        return None
    ev = load_capture(info.path)
    raw = base64.b64decode(str(ev.get("payload") or ""))
    region = locate_signature_region(raw)
    if not region:
        return None
    return decode_blob(region.blob)


def patch_inner_blob(frame: bytearray, inner: bytes) -> bool:
    """Replace 226B ASCII blob region with re-encoded inner (169B)."""
    region = locate_signature_region(bytes(frame))
    if not region or len(inner) != 169:
        return False
    new_blob = encode_blob(inner)
    if len(new_blob) != 226:
        return False
    frame[region.blob_start : region.blob_end] = new_blob
    return True


def inner_for_text(text: str) -> bytes | None:
    bl = len(text.encode("utf-8"))
    spec = bucket_for_text_len(bl)
    if not spec:
        return None
    return canonical_inner_for_bucket(spec.name)
