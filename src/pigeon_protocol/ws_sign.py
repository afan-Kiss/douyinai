"""WS text-frame signature region — reverse engineering helpers."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from pigeon_protocol.parsers.ws_frame_builder import read_varint, write_varint

SIG_MARKER_AFTER_CLIENT = b"s:client_message_id"
SIG_FIELD_PREFIX = b"\xe8\x07\x3a"
SIG_BLOB_LENGTH = 226


@dataclass
class SignatureRegion:
    field_tag_pos: int
    length_pos: int
    blob_start: int
    blob_end: int
    blob: bytes
    client_message_id: str
    dollar_suffix_start: int
    dollar_suffix: bytes


def extract_client_message_id(raw: bytes) -> str:
    m = re.search(
        rb"s:client_message_id\x12\$([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
        raw,
    )
    return m.group(1).decode() if m else ""


def locate_signature_region(raw: bytes) -> SignatureRegion | None:
    anchor = raw.find(SIG_MARKER_AFTER_CLIENT)
    if anchor < 0:
        return None
    tag_pos = raw.find(SIG_FIELD_PREFIX, anchor)
    if tag_pos < 0:
        return None
    length, blob_start = read_varint(raw, tag_pos + 3)
    if length != SIG_BLOB_LENGTH:
        return None
    blob_end = blob_start + length
    blob = bytes(raw[blob_start:blob_end])
    cid = extract_client_message_id(raw)
    suffix_start = blob_end
    suffix = b""
    m = re.match(rb"B\$[0-9a-f-]{36}", raw[blob_end : blob_end + 40])
    if m:
        suffix = m.group(0)
        suffix_start = blob_end
    elif raw[blob_end : blob_end + 1] == b"$":
        end = raw.find(b"p", blob_end + 1)
        if end > blob_end:
            suffix = raw[blob_end:end]
    return SignatureRegion(
        field_tag_pos=tag_pos,
        length_pos=tag_pos + 3,
        blob_start=blob_start,
        blob_end=blob_end,
        blob=blob,
        client_message_id=cid,
        dollar_suffix_start=suffix_start,
        dollar_suffix=suffix,
    )


def patch_client_message_id(raw: bytearray, new_id: str) -> None:
    old = extract_client_message_id(raw)
    if not old or len(new_id) != 36:
        return
    old_pat = f"s:client_message_id\x12${old}".encode()
    new_pat = f"s:client_message_id\x12${new_id}".encode()
    idx = raw.find(old_pat)
    if idx >= 0:
        raw[idx : idx + len(old_pat)] = new_pat
    old_dollar = f"B${old}".encode()
    new_dollar = f"B${new_id}".encode()
    idx2 = raw.find(old_dollar)
    if idx2 >= 0 and len(old_dollar) == len(new_dollar):
        raw[idx2 : idx2 + len(old_dollar)] = new_dollar


def rebuild_dollar_suffix(raw: bytearray, region: SignatureRegion, new_id: str) -> None:
    """Patch trailing B$uuid suffix after 226-byte signature blob."""
    new_suffix = f"B${new_id}".encode()
    start = region.blob_end
    old = raw[start : start + len(new_suffix)]
    if len(old) == len(new_suffix):
        raw[start : start + len(new_suffix)] = new_suffix


def copy_signature_from_template(
    target: bytearray,
    template: bytes,
    *,
    new_client_id: str | None = None,
) -> bool:
    """Copy 226-byte signature blob from a signed template frame."""
    src = locate_signature_region(template)
    dst = locate_signature_region(target)
    if not src or not dst:
        return False
    if len(src.blob) != len(dst.blob):
        return False
    target[dst.blob_start : dst.blob_end] = src.blob
    cid = new_client_id or str(uuid.uuid4())
    patch_client_message_id(target, cid)
    rebuild_dollar_suffix(target, dst, cid)
    return True
