"""Extract edbX send seeds (8B trailer) from get_message_by_init field 6."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pigeon_protocol.foundation.ws_inner_edbx import (
    PREFIX_XOR_MASK,
    decode_ts_us_from_prefix,
    derive_prefix_from_ts_us,
)

FIELD6 = 6
PIGEON_MARK = b":pigeon"
TRAILER_LEN = 8
TRAILER_SLOT_LEN = 0x10  # bytes after f21 fixed64 trailing 0x6a
F21_FIXED64_TAG = 0xA9  # field 21 wire 1 (fixed64)
PREFIX8_LEN = 8  # legacy alias length for session_nonce
# Nested layout after :pigeon in init field 6 (conversation template):
#   field 21 / len 3  → 9c 0c 08
#   field 21 / fixed64 → session_nonce (8B, ends with 0x6a)
#   field 13 / len 16 → trailer[0:8] + padding
NESTED_F21_TAG = bytes([0xAA, 0x03])


@dataclass
class EdbxInitSeed:
    trailer: bytes
    offset: int
    pigeon_dist: int
    session_nonce: bytes
    route_hint: str
    nested_f21_hex: str = ""
    score: int = 0

    @property
    def prefix8(self) -> bytes:
        """Legacy alias — session-static f21 fixed64 (NOT live send prefix)."""
        return self.session_nonce

    def to_dict(self) -> dict[str, Any]:
        return {
            "trailer_hex": self.trailer.hex(),
            "offset": self.offset,
            "pigeon_dist": self.pigeon_dist,
            "score": self.score,
            "session_nonce_hex": self.session_nonce.hex(),
            "prefix8_hex": self.session_nonce.hex(),
            "nested_f21_hex": self.nested_f21_hex,
            "route_hint": self.route_hint,
        }


def extract_init_field_bytes(raw: bytes, field_num: int) -> bytes:
    """Return first length-delimited init field payload."""
    if not raw:
        return b""
    i = 0
    while i < len(raw):
        tag = raw[i]
        fn = tag >> 3
        wire = tag & 0x07
        i += 1
        if wire == 2:
            if i >= len(raw):
                break
            ln = raw[i]
            i += 1
            if ln & 0x80:
                if i >= len(raw):
                    break
                ln = (ln & 0x7F) | (raw[i] << 7)
                i += 1
            chunk = raw[i : i + ln]
            i += ln
            if fn == field_num:
                return chunk
        elif wire == 0:
            while i < len(raw):
                if not (raw[i] & 0x80):
                    i += 1
                    break
                i += 1
        else:
            break
    return b""


def _route_hint(before: bytes) -> str:
    if PIGEON_MARK not in before:
        return ""
    tail = before[before.rfind(PIGEON_MARK) - 80 : before.rfind(PIGEON_MARK) + len(PIGEON_MARK)]
    try:
        text = tail.decode("ascii", errors="ignore")
    except UnicodeDecodeError:
        return ""
    for token in text.split():
        if ":pigeon" in token:
            return token.split(":pigeon")[0] + ":pigeon"
    idx = text.rfind("AQ")
    if idx >= 0:
        return text[idx:]
    return text[-64:]


def _validate_trailer_slot(blob: bytes, k: int) -> bool:
    """k points to 0x6a (last byte of f21 fixed64); k+1 is len 0x10."""
    if k < 8 or k + 2 + TRAILER_LEN > len(blob):
        return False
    if blob[k + 1] != TRAILER_SLOT_LEN:
        return False
    if blob[k - 8] != F21_FIXED64_TAG:
        return False
    return True


def _session_nonce_before(blob: bytes, k: int) -> bytes:
    """8B f21 fixed64 (tag 0xa9 at k-8, value k-7..k inclusive)."""
    if k >= 7:
        return blob[k - 7 : k + 1]
    return b""


def _nested_f21_payload(blob: bytes, k: int) -> bytes:
    """3B f21 sub-message payload (9c0c08) before fixed64 tag 0xa9."""
    start = k - 11
    if start >= 0 and start + 3 <= len(blob):
        return blob[start : start + 3]
    return b""


def _score_seed(seed: EdbxInitSeed) -> int:
    score = 1000 - min(seed.pigeon_dist, 999)
    if seed.route_hint and "::" in seed.route_hint:
        score += 50
    if seed.nested_f21_hex == "9c0c08":
        score += 100
    return score


def scan_edbx_trailer_seeds(blob: bytes, *, window: int = 128) -> list[EdbxInitSeed]:
    """Find f13 trailer slots inside init field 6 (send template near :pigeon)."""
    out: list[EdbxInitSeed] = []
    if not blob:
        return out
    j = 0
    while j < len(blob):
        k = blob.find(b"\x6a", j)
        if k < 0:
            break
        if not _validate_trailer_slot(blob, k):
            j = k + 1
            continue
        trailer = blob[k + 2 : k + 2 + TRAILER_LEN]
        before = blob[max(0, k - window) : k]
        pig = before.rfind(PIGEON_MARK)
        dist = (k - (max(0, k - window) + pig + len(PIGEON_MARK))) if pig >= 0 else 9999
        seed = EdbxInitSeed(
            trailer=trailer,
            offset=k,
            pigeon_dist=dist,
            session_nonce=_session_nonce_before(blob, k),
            route_hint=_route_hint(before),
            nested_f21_hex=_nested_f21_payload(blob, k).hex(),
        )
        seed.score = _score_seed(seed)
        out.append(seed)
        j = k + 1
    out.sort(key=lambda s: (-s.score, s.pigeon_dist, s.offset))
    return out


def analyze_prefix8_vs_field12(
    raw: bytes,
    *,
    ts_us: int = 0,
    live_prefix: bytes | None = None,
) -> dict[str, Any]:
    """
    Compare init template prefix8 (static) vs live field-12 prefix (init field10).

    RE conclusion: live send uses derive_prefix_from_ts_us(init_field10); prefix8 in
  init field6 is a session-static template nonce paired with trailer, not f12.
    """
    from pigeon_protocol.foundation.init_timestamps import parse_init_timestamps

    report: dict[str, Any] = {}
    ts = parse_init_timestamps(raw)
    f10 = int(ts_us or ts.get("ts_start") or 0)
    f6 = extract_init_field_bytes(raw, FIELD6)
    seeds = scan_edbx_trailer_seeds(f6)
    best = seeds[0] if seeds else None

    derived = derive_prefix_from_ts_us(f10) if f10 else b""
    report["init_field10_us"] = f10
    report["init_field11_us"] = int(ts.get("ts_end") or 0)
    report["derived_live_prefix_hex"] = derived.hex() if derived else None

    if best:
        report["template_session_nonce_hex"] = best.session_nonce.hex()
        report["template_prefix8_hex"] = best.session_nonce.hex()
        report["template_nested_f21_hex"] = best.nested_f21_hex
        report["template_trailer_hex"] = best.trailer.hex()
        report["template_route_hint"] = best.route_hint
        report["template_score"] = best.score
        bogus = decode_ts_us_from_prefix(best.session_nonce)
        report["template_nonce_decode_ts_us"] = bogus
        report["nonce_equals_live_derived"] = bool(derived) and best.session_nonce == derived
        report["nonce_equals_live_capture"] = bool(live_prefix) and best.session_nonce == live_prefix
    else:
        report["template_session_nonce_hex"] = None

    report["live_field12_formula"] = "derive_prefix_from_ts_us(init_field10)"
    report["session_nonce_role"] = "init_field6_f21_fixed64_session_static"
    report["template_prefix8_role"] = report["session_nonce_role"]
    return report


def parse_pigeon_send_template(blob: bytes, offset: int) -> dict[str, Any] | None:
    """Decode nested protobuf after :pigeon at trailer slot offset."""
    if not _validate_trailer_slot(blob, offset):
        return None
    k = offset
    return {
        "field21_bytes_hex": _nested_f21_payload(blob, k).hex(),
        "field21_fixed64_hex": _session_nonce_before(blob, k).hex(),
        "field13_trailer_hex": blob[k + 2 : k + 2 + TRAILER_LEN].hex(),
        "field13_blob_hex": blob[k + 2 : k + 2 + TRAILER_SLOT_LEN].hex(),
        "route_hint": _route_hint(blob[max(0, k - 128) : k]),
    }


def persist_init_edbx_seeds(session, raw: bytes, *, source: str = "init") -> dict[str, Any]:
    """Store init-derived trailer + session nonce into session.extra after get_message_by_init."""
    trailer, via, report = extract_edbx_trailer_from_init(raw)
    if not trailer:
        return {"ok": False, "via": via, **report}

    extra = getattr(session, "extra", None) or {}
    extra["edbx_trailer_hex"] = trailer.hex()
    extra["edbx_trailer_source"] = source
    extra["edbx_trailer_via"] = via
    seeds = report.get("seeds") or []
    if seeds:
        top = seeds[0]
        extra["edbx_session_nonce_hex"] = top.get("session_nonce_hex") or top.get("prefix8_hex")
        extra["edbx_init_route_hint"] = top.get("route_hint") or ""
        tpl = parse_pigeon_send_template(extract_init_field_bytes(raw, FIELD6), int(top.get("offset") or 0))
        if tpl:
            extra["edbx_init_template"] = tpl
    session.extra = extra
    try:
        from pigeon_protocol.session import save_session

        save_session(session)
    except Exception:
        pass
    return {"ok": True, "via": via, "trailer_hex": trailer.hex(), **report}


def extract_edbx_trailer_from_init(raw: bytes) -> tuple[bytes | None, str, dict[str, Any]]:
    """
    Pure HTTP: pull 8B edbX send trailer from init field 6.

    Heuristic: among nested field-13 (tag 6a10) slots, pick closest to ':pigeon'.
    """
    report: dict[str, Any] = {"seeds": []}
    f6 = extract_init_field_bytes(raw, FIELD6)
    report["field6_len"] = len(f6)
    if not f6:
        return None, "missing_field6", report

    seeds = scan_edbx_trailer_seeds(f6)
    report["seeds"] = [s.to_dict() for s in seeds]
    if not seeds:
        return None, "no_trailer_seed", report

    best = seeds[0]
    report["selected_score"] = best.score
    if best.pigeon_dist >= 128:
        return best.trailer, "init_field6_scored", report
    return best.trailer, "init_field6_pigeon", report


def resolve_edbx_trailer_from_init(session=None, *, raw: bytes | None = None) -> tuple[bytes | None, str, dict[str, Any]]:
    if raw is None:
        from pigeon_protocol.foundation.init_timestamps import load_init_bytes

        raw, src = load_init_bytes(session)
        report_src = src
    else:
        report_src = "provided"
    trailer, via, report = extract_edbx_trailer_from_init(raw)
    report["init_source"] = report_src
    if trailer:
        report["trailer_hex"] = trailer.hex()
    return trailer, via, report
