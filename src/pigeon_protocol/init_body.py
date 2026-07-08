"""get_message_by_init protobuf body — varint-safe session patching."""
from __future__ import annotations

from typing import Any

from pigeon_protocol.parsers.ws_frame_builder import read_varint, write_varint

PIGEON_SIGN_MARKER = b"pigeon_sign"
SESSION_DID_MARKER = b"session_did"


def _find_string_after_marker(data: bytes | bytearray, marker: bytes) -> tuple[int, int, int] | None:
    """Return (length_pos, val_start, val_end) for protobuf string after ASCII marker."""
    idx = 0
    while True:
        pos = data.find(marker, idx)
        if pos < 0:
            return None
        scan = pos + len(marker)
        while scan < len(data) and data[scan] not in (0x12, 0x1A):
            scan += 1
        if scan >= len(data):
            return None
        length, val_start = read_varint(data, scan + 1)
        val_end = val_start + length
        if val_end <= len(data):
            return scan + 1, val_start, val_end
        idx = pos + 1


def _fixup_enclosing_length_varints(
    data: bytearray,
    inner_start: int,
    inner_end: int,
    delta: int,
) -> None:
    """Adjust all protobuf length prefixes that wrap [inner_start, inner_end)."""
    if delta == 0:
        return
    pos = 0
    while pos < len(data):
        tag = data[pos]
        wire = tag & 7
        pos += 1
        if wire == 0:
            _, pos = read_varint(data, pos)
        elif wire == 2:
            length_pos = pos
            length, val_start = read_varint(data, pos)
            val_end = val_start + length
            if val_start <= inner_start and val_end >= inner_end:
                new_length = length + delta
                encoded = write_varint(new_length)
                old_len_bytes = val_start - length_pos
                if len(encoded) != old_len_bytes:
                    raise ValueError(
                        f"length varint resize unsupported: {length} -> {new_length}"
                    )
                data[length_pos:val_start] = encoded
            pos = val_end
        else:
            break


def patch_string_after_marker(data: bytearray, marker: bytes, new_value: str) -> bool:
    """Replace length-delimited string after field name marker; fix enclosing lengths."""
    loc = _find_string_after_marker(data, marker)
    if not loc:
        return False
    length_pos, val_start, val_end = loc
    new_bytes = new_value.encode("utf-8")
    encoded_len = write_varint(len(new_bytes))
    old_length_size = val_start - length_pos
    if len(encoded_len) != old_length_size:
        return False
    delta = len(new_bytes) - (val_end - val_start)
    _fixup_enclosing_length_varints(data, val_start, val_end, delta)
    data[val_start:val_end] = new_bytes
    data[length_pos:val_start] = encoded_len
    return True


def patch_top_level_field_string(data: bytearray, field_no: int, new_value: str) -> bool:
    """Patch top-level protobuf field (wire type 2) by field number."""
    pos = 0
    tag_expect = (field_no << 3) | 2
    new_bytes = new_value.encode("ascii")
    while pos < len(data):
        if pos >= len(data):
            break
        tag = data[pos]
        if tag != tag_expect:
            wire = tag & 7
            pos += 1
            if wire == 0:
                _, pos = read_varint(data, pos)
            elif wire == 2:
                ln, pos = read_varint(data, pos)
                pos += ln
            else:
                return False
            continue
        pos += 1
        length_pos = pos
        length, val_start = read_varint(data, pos)
        val_end = val_start + length
        encoded_len = write_varint(len(new_bytes))
        if len(encoded_len) != val_start - length_pos:
            return False
        delta = len(new_bytes) - length
        _fixup_enclosing_length_varints(data, val_start, val_end, delta)
        data[val_start:val_end] = new_bytes
        data[length_pos:val_start] = encoded_len
        return True
    return False


def patch_init_body(body: bytes, session) -> bytes:
    """
    Patch init protobuf for current session without breaking nested lengths.

    Fields: token (4), device_id (9), pigeon_sign (nested), session_did.
    """
    data = bytearray(body)
    cid = str(session.cookies.get("PIGEON_CID") or session.device_id or "")
    if cid:
        patch_string_after_marker(data, SESSION_DID_MARKER, cid)
        patch_top_level_field_string(data, 9, cid)

    token = ""
    for url in reversed(session.ws_urls or []):
        if "ws.fxg.jinritemai.com" not in url:
            continue
        from urllib.parse import parse_qs, urlparse

        token = (parse_qs(urlparse(url).query).get("token") or [""])[0]
        if token:
            break
    if not token:
        token = str(session.query_tokens.get("token") or "")
    if token:
        patch_top_level_field_string(data, 4, token)

    sign = str(session.query_tokens.get("pigeon_sign") or "")
    if sign:
        patch_string_after_marker(data, PIGEON_SIGN_MARKER, sign)

    return bytes(data)


def validate_init_body(body: bytes) -> dict[str, Any]:
    """Quick structural validation — ensure protobuf walk completes."""
    pos = 0
    fields: list[dict[str, Any]] = []
    try:
        while pos < len(body):
            if pos >= len(body):
                break
            tag = body[pos]
            field = tag >> 3
            wire = tag & 7
            pos += 1
            if wire == 0:
                val, pos = read_varint(body, pos)
                fields.append({"field": field, "wire": wire, "varint": val})
            elif wire == 2:
                ln, pos = read_varint(body, pos)
                pos += ln
                fields.append({"field": field, "wire": wire, "len": ln})
            else:
                return {"ok": False, "error": f"bad wire {wire} field {field} at {pos}", "fields": fields}
        return {"ok": True, "fields": fields, "size": len(body)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "fields": fields}
