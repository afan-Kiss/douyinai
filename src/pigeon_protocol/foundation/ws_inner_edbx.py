"""Pure-Python edbX 169B inner builder (jinritemai / Rust cmd 11327 ticket variant).

RE 2026-07-07 — edbX send inner layout (169B = 4B magic + 165B payload):

  payload[0:8]     prefix = padded_varint(field12_ts_us), byte0 ^= 0x6D
  payload[8:157]   outer protobuf: field12 + field13(99900) + field2 blob(133B)
  payload[157:165] 8B trailer (opaque; session-scoped)

field2 blob = 16B wrapper + 117B core; core = {f1=0,f2=7,f3=route@110B} + 0x20 suffix.
Prefix + field12 varint are bit-exact derivable; trailer still session-scoped (see derive_trailer_candidates).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from pigeon_protocol.foundation.ws_inner_proto import MAGIC_EDBX, extract_edbx_ticket

logger = logging.getLogger("pigeon.ws_inner_edbx")

INNER_LEN = 169
PAYLOAD_LEN = 165
PREFIX_LEN = 8
OUTER_LEN = 149  # field12(9) + field13(4) + field2(136)
BODY_LEN = PREFIX_LEN + OUTER_LEN  # 157
TRAILER_LEN = 8
# Legacy aliases (old RE assumed 40B envelope + 116B core split)
ENVELOPE_LEN = 40
CORE_OFFSET = 40
CORE_LEN = 117  # protobuf core + SDK suffix byte
CORE_SUFFIX = bytes([0x20])  # field4=0 tag-only suffix observed after route in captures
ROUTE_FIELD_NUM = 3
ROUTE_PAD_LEN = 110  # fixed route width in live captures

PREFIX_XOR_MASK = 0x6D  # prefix[0] = varint(f12)[0] ^ 0x6d; rest = varint tail
DEFAULT_FIELD13 = 99900  # outer protobuf field 13 (observed constant jinritemai)
# Observed jinritemai trailer (8B) — cross-session reuse TBD; portable-pack fallback
FALLBACK_TRAILER_HEX = "b38a848485e5c1a1"
FIELD2_WRAPPER = bytes.fromhex("080132fc030a030a013212f4030af103")  # 16B nested header
FIELD2_BLOB_LEN = 133  # wrapper(16) + core(117)
FIELD2_LEN_PREFIX = b"\x81\x04"  # SDK uses fixed non-minimal length prefix regardless of 133B blob
OUTER_FIELD12 = 12
OUTER_FIELD13 = 13
OUTER_FIELD2 = 2

# Rust capture used for formula verification (jinritemai send inner)
SAMPLE_INNER_HEX = (
    "6564625896aa84fdaec0950360fbaa84fdaec0950368bc8c06128104080132fc030a030a013212f4030af103080010071a6e"
    "415141656f4d4b52624631674b494c696573425a464e79794846395050306c38577436316150663665577639314b345753497a"
    "48737535394b57576534616333575f4f4f6149486656656c48786e61523269346f704930573a3236333633363436353a3a323a"
    "313a706967656f6e20b38a848485e5c1a1"
)

ROUTE_RE = re.compile(r"^(n?AQ[Cc][A-Za-z0-9_-]{30,120}:\d+::\d+:\d+:pigeon)\s*$")


def _enc_varint(value: int) -> bytes:
    out = bytearray()
    v = int(value)
    while v > 0x7F:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    out.append(v)
    return bytes(out)


def _enc_varint_padded8(value: int) -> bytes:
    """SDK uses fixed 8-byte varint slots for large microsecond timestamps."""
    raw = _enc_varint(int(value))
    if len(raw) > 8:
        raise ValueError(f"timestamp varint too long: {len(raw)}")
    return raw.ljust(8, b"\x00")


def _enc_field12_slot(ts_us: int) -> bytes:
    return bytes([OUTER_FIELD12 << 3]) + _enc_varint_padded8(ts_us)


def _enc_field2_length_sdk(length: int) -> bytes:
    """SDK always emits 0x81 0x04 before the 133B field-2 blob (non-minimal varint)."""
    if length == FIELD2_BLOB_LEN:
        return FIELD2_LEN_PREFIX
    return _enc_varint(length)


def _enc_field_sdk(field_num: int, wire: int, payload: bytes | int) -> bytes:
    if field_num == OUTER_FIELD2 and wire == 2:
        body = bytes(payload)
        return _enc_varint((field_num << 3) | wire) + _enc_field2_length_sdk(len(body)) + body
    return _enc_field(field_num, wire, payload)


def _enc_field(field_num: int, wire: int, payload: bytes | int) -> bytes:
    tag = _enc_varint((field_num << 3) | wire)
    if wire == 0:
        return tag + _enc_varint(int(payload))
    if wire == 2:
        body = bytes(payload)
        return tag + _enc_varint(len(body)) + body
    raise ValueError(f"unsupported wire type {wire}")


def normalize_route(route: str, *, pad_len: int = ROUTE_PAD_LEN) -> bytes:
    """Normalize conversation route to fixed-width bytes (space-padded)."""
    text = str(route or "").strip()
    if text.startswith("n") and len(text) > 1 and text[1:2].upper() in ("A", "X"):
        text = text[1:]
    if not text:
        raise ValueError("empty conversation route")
    if not text.endswith(":pigeon"):
        raise ValueError(f"route missing :pigeon suffix: {text[:48]}...")
    raw = text.encode("ascii")
    if len(raw) > pad_len:
        raise ValueError(f"route too long ({len(raw)} > {pad_len}): {text[:48]}...")
    if len(raw) < pad_len:
        raw = raw + b" " * (pad_len - len(raw))
    return raw


def encode_edbx_core(route: str | bytes, *, pad_len: int = ROUTE_PAD_LEN, with_suffix: bool = True) -> bytes:
    """117B SDK core: {f1=0, f2=7, f3=route@110B} + optional 0x20 suffix."""
    route_b = normalize_route(route.decode("ascii") if isinstance(route, bytes) else route, pad_len=pad_len)
    core = _enc_field(1, 0, 0) + _enc_field(2, 0, 7) + _enc_field(ROUTE_FIELD_NUM, 2, route_b)
    if with_suffix:
        core += CORE_SUFFIX
    return core


def encode_field2_blob(route: str | bytes) -> bytes:
    """132B outer field-2 blob = fixed wrapper + route core."""
    core = encode_edbx_core(route)
    blob = FIELD2_WRAPPER + core
    if len(blob) != FIELD2_BLOB_LEN:
        raise ValueError(f"field2 blob must be {FIELD2_BLOB_LEN} bytes, got {len(blob)}")
    return blob


def derive_prefix_from_ts_us(ts_us: int) -> bytes:
    """8B payload prefix from microsecond timestamp (field 12 padded varint XOR mask)."""
    vb = _enc_varint_padded8(int(ts_us))
    pref = bytearray(vb)
    pref[0] ^= PREFIX_XOR_MASK
    return bytes(pref)


def decode_ts_us_from_prefix(prefix: bytes) -> int | None:
    """Inverse of derive_prefix_from_ts_us for cached 8B prefix."""
    if len(prefix) != PREFIX_LEN:
        return None
    vb = bytearray(prefix)
    vb[0] ^= PREFIX_XOR_MASK
    i = 0
    val = 0
    shift = 0
    while i < len(vb):
        b = vb[i]
        if b == 0 and i > 0 and shift > 0 and (i + 1 < len(vb)) and vb[i + 1] == 0:
            break
        i += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return int(val) if val else None


def encode_edbx_payload(
    route: str,
    *,
    ts_us: int,
    field13: int = DEFAULT_FIELD13,
    trailer: bytes,
) -> bytes:
    """Build 165B edbX payload (without magic)."""
    if len(trailer) != TRAILER_LEN:
        raise ValueError(f"trailer must be {TRAILER_LEN} bytes")
    blob = encode_field2_blob(route)
    outer = (
        _enc_field12_slot(int(ts_us))
        + _enc_field(OUTER_FIELD13, 0, int(field13))
        + _enc_field_sdk(OUTER_FIELD2, 2, blob)
    )
    prefix = derive_prefix_from_ts_us(ts_us)
    body = prefix + outer
    if len(body) + TRAILER_LEN != PAYLOAD_LEN:
        raise ValueError(f"payload body len {len(body)} + trailer != {PAYLOAD_LEN}")
    return body + trailer


def build_edbx_inner_derived(
    route: str,
    *,
    ts_us: int,
    trailer: bytes,
    field13: int = DEFAULT_FIELD13,
) -> bytes:
    """Full 169B inner from derived formula (no cached envelope)."""
    payload = encode_edbx_payload(route, ts_us=ts_us, field13=field13, trailer=trailer)
    return MAGIC_EDBX + payload


def normalize_trailer(raw: bytes) -> bytes | None:
    """Accept 8B trailer or legacy 9B captures that include the core 0x20 suffix."""
    if len(raw) == TRAILER_LEN:
        return raw
    if len(raw) == TRAILER_LEN + 1 and raw[0] == CORE_SUFFIX[0]:
        return raw[1:]
    return None


def derive_trailer_candidates(access_token: str, *, ts_us: int = 0, device_id: str = "") -> list[tuple[str, bytes]]:
    """Experimental trailer derivations from IM accessToken UUID."""
    import uuid

    out: list[tuple[str, bytes]] = []
    try:
        u = uuid.UUID(str(access_token).strip())
    except ValueError:
        return out
    uuid_b = u.bytes
    uuid_le = u.bytes_le
    tails = (6, 7, 8, 9, 10, 12, 16)
    mats: list[tuple[str, bytes]] = []
    for n in tails:
        mats.append((f"uuid_bytes_tail{n}", uuid_b[-n:]))
        mats.append((f"uuid_bytes_le_tail{n}", uuid_le[-n:]))
        mats.append((f"uuid_sha256_tail{n}", hashlib.sha256(uuid_b).digest()[-n:]))
        mats.append((f"uuid_md5_tail{n}", hashlib.md5(uuid_b).digest()[-n:]))
        mats.append((f"sha256_uuid_le_tail{n}", hashlib.sha256(uuid_le).digest()[-n:]))
    if ts_us:
        tb = _enc_varint(int(ts_us))
        tb8 = _enc_varint_padded8(int(ts_us))
        for n in tails:
            mats.append((f"sha256_uuid_ts_tail{n}", hashlib.sha256(uuid_b + tb).digest()[-n:]))
            mats.append((f"sha256_uuid_ts8_tail{n}", hashlib.sha256(uuid_b + tb8).digest()[-n:]))
    if device_id:
        dev = device_id.encode()
        for n in tails:
            mats.append((f"sha256_uuid_device_tail{n}", hashlib.sha256(uuid_b + dev).digest()[-n:]))
    seen: set[bytes] = set()
    for label, raw in mats:
        norm = normalize_trailer(raw)
        if norm and norm not in seen:
            seen.add(norm)
            out.append((label, norm))
    return out


def resolve_trailer(session, *, access_token: str = "", ts_us: int = 0) -> tuple[bytes | None, str]:
    """Cached trailer → portable → init field6 → envelope → derive → fallback."""
    import os

    extra = _session_extra(session)
    cached = str(extra.get("edbx_trailer_hex") or "")
    if cached:
        try:
            norm = normalize_trailer(bytes.fromhex(cached))
            if norm:
                return norm, "session.extra"
        except ValueError:
            pass

    env_hex = os.environ.get("PIGEON_EDBX_TRAILER_HEX", "").strip()
    if env_hex:
        try:
            norm = normalize_trailer(bytes.fromhex(env_hex))
            if norm:
                return norm, "env"
        except ValueError:
            pass

    try:
        from pigeon_protocol.foundation.init_edbx_seeds import resolve_edbx_trailer_from_init

        init_trailer, init_via, _ = resolve_edbx_trailer_from_init(session)
        if init_trailer:
            norm = normalize_trailer(init_trailer)
            if norm:
                return norm, init_via
    except Exception as exc:
        logger.debug("init trailer extract: %s", exc)

    portable = _load_portable_edbx_meta()
    if portable.get("trailer_hex"):
        try:
            norm = normalize_trailer(bytes.fromhex(str(portable["trailer_hex"])))
            if norm:
                return norm, "portable_sidecar"
        except ValueError:
            pass

    tpl = load_envelope_template(session)
    if tpl and tpl.get("trailer"):
        return tpl["trailer"], "envelope_template"

    token = access_token or str(extra.get("im_access_token") or "")
    device_id = str(getattr(session, "device_id", "") or "")
    for label, cand in derive_trailer_candidates(token, ts_us=ts_us, device_id=device_id):
        return cand, f"derived:{label}"

    try:
        norm = normalize_trailer(bytes.fromhex(FALLBACK_TRAILER_HEX))
        if norm:
            return norm, "fallback_constant"
    except ValueError:
        pass
    return None, "missing"


def _load_portable_edbx_meta() -> dict[str, Any]:
    from pigeon_protocol.session_portable import PORTABLE_INNER

    if not PORTABLE_INNER.is_file():
        return {}
    try:
        doc = json.loads(PORTABLE_INNER.read_text(encoding="utf-8"))
        edbx = doc.get("edbx")
        return edbx if isinstance(edbx, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _session_extra(session) -> dict[str, Any]:
    extra = getattr(session, "extra", None)
    if extra is None:
        extra = {}
        session.extra = extra
    return extra


def edbx_meta_from_inner(inner: bytes) -> dict[str, Any]:
    """Build portable edbX metadata block from a captured 169B inner."""
    tpl = extract_envelope_template(inner)
    if not tpl:
        return {}
    return {
        "trailer_hex": tpl.get("trailer_hex"),
        "prefix_sample_hex": tpl.get("prefix_hex"),
        "field13": DEFAULT_FIELD13,
        "wrapper_hex": FIELD2_WRAPPER.hex(),
        "formula": "init_field10_us + conv_route + trailer",
        "route_sample": tpl.get("route") or "",
        "field12_us": tpl.get("field12_us"),
    }


def portable_edbx_meta(session) -> dict[str, Any]:
    """Exportable edbX formula metadata for session pack."""
    tpl_bytes = load_envelope_template(session)
    if tpl_bytes and tpl_bytes.get("trailer"):
        envelope = tpl_bytes.get("envelope") or b""
        return {
            "trailer_hex": tpl_bytes["trailer"].hex(),
            "prefix_sample_hex": envelope[:PREFIX_LEN].hex() if envelope else None,
            "field13": DEFAULT_FIELD13,
            "wrapper_hex": FIELD2_WRAPPER.hex(),
            "formula": "init_field10_us + conv_route + trailer",
        }
    try:
        from pigeon_protocol.foundation.ws_blob_compute import _load_session_class_cache

        for inner in _load_session_class_cache(session).values():
            if is_edbx_inner(inner):
                meta = edbx_meta_from_inner(inner)
                if meta.get("trailer_hex"):
                    return meta
    except Exception as exc:
        logger.debug("portable_edbx_meta cache scan: %s", exc)
    return {}


def split_edbx_payload(payload: bytes) -> dict[str, bytes]:
    if len(payload) != PAYLOAD_LEN:
        raise ValueError(f"expected {PAYLOAD_LEN}-byte payload, got {len(payload)}")
    outer = payload[PREFIX_LEN:-TRAILER_LEN]
    blob_start = PREFIX_LEN + 9 + 4 + 3  # prefix + f12 + f13 + f2 tag+len
    blob = payload[blob_start : blob_start + FIELD2_BLOB_LEN]
    return {
        "prefix": payload[:PREFIX_LEN],
        "outer": outer,
        "trailer": payload[-TRAILER_LEN:],
        "field2_blob": blob,
        "core": blob[len(FIELD2_WRAPPER) :],
        # legacy aliases
        "envelope": payload[:ENVELOPE_LEN],
    }


def split_edbx_inner(inner: bytes) -> dict[str, Any]:
    if len(inner) != INNER_LEN:
        raise ValueError(f"expected {INNER_LEN} bytes, got {len(inner)}")
    if inner[:4] != MAGIC_EDBX:
        raise ValueError("not edbX variant")
    payload = inner[4:]
    parts = split_edbx_payload(payload)
    ticket = extract_edbx_ticket(inner)
    return {
        "magic": "edbX",
        "header_suffix": inner[4:8],
        **parts,
        "ticket": ticket,
        "route": parts["core"][4:].split(b"\x1a", 1)[-1] if parts["core"] else b"",
    }


def extract_envelope_template(inner: bytes) -> dict[str, str] | None:
    """Extract reusable prefix/trailer from a captured edbX inner."""
    try:
        parts = split_edbx_inner(inner)
    except ValueError:
        return None
    payload = inner[4:]
    return {
        "header_suffix_hex": inner[4:8].hex(),
        "prefix_hex": payload[:PREFIX_LEN].hex(),
        "envelope_hex": payload[:ENVELOPE_LEN].hex(),
        "trailer_hex": parts["trailer"].hex(),
        "route": extract_edbx_ticket(inner) or "",
        "field12_us": _decode_field12_us(payload),
        "core_sha256_prefix": hashlib.sha256(parts["core"]).hexdigest()[:16],
    }


def _decode_field12_us(payload: bytes) -> int | None:
    if len(payload) < PREFIX_LEN + 9:
        return None
    if payload[PREFIX_LEN] != (OUTER_FIELD12 << 3):
        return None
    vb = payload[PREFIX_LEN + 1 : PREFIX_LEN + 9]
    val, _ = read_varint(vb, 0)
    return int(val) if val is not None else None


def read_varint(data: bytes, i: int) -> tuple[int | None, int]:
    from pigeon_protocol.foundation.ws_inner_proto import read_varint as _rv

    return _rv(data, i)


def _session_extra(session) -> dict[str, Any]:
    extra = getattr(session, "extra", None)
    if extra is None:
        extra = {}
        session.extra = extra
    return extra


def load_envelope_template(session) -> dict[str, bytes] | None:
    extra = _session_extra(session)
    env_hex = str(extra.get("edbx_envelope_hex") or "")
    tail_hex = str(extra.get("edbx_trailer_hex") or "")
    suffix_hex = str(extra.get("edbx_header_suffix_hex") or "")
    if not env_hex or not tail_hex:
        try:
            from pigeon_protocol.foundation.ws_blob_compute import _load_session_class_cache

            for inner in _load_session_class_cache(session).values():
                if is_edbx_inner(inner):
                    store_envelope_template(session, inner, source="session_cache")
                    env_hex = str(extra.get("edbx_envelope_hex") or "")
                    tail_hex = str(extra.get("edbx_trailer_hex") or "")
                    suffix_hex = str(extra.get("edbx_header_suffix_hex") or "")
                    break
        except Exception as exc:
            logger.debug("envelope cache scan: %s", exc)
    if not env_hex or not tail_hex:
        return None
    try:
        envelope = bytes.fromhex(env_hex)
        trailer = bytes.fromhex(tail_hex)
        suffix = bytes.fromhex(suffix_hex) if suffix_hex else b""
    except ValueError:
        return None
    trailer = normalize_trailer(trailer) or trailer
    if len(envelope) != ENVELOPE_LEN or len(trailer) != TRAILER_LEN:
        return None
    if suffix and len(suffix) != 4:
        return None
    return {"envelope": envelope, "trailer": trailer, "header_suffix": suffix or envelope[:4]}


def store_envelope_template(session, inner: bytes, *, source: str = "ingest") -> bool:
    tpl = extract_envelope_template(inner)
    if not tpl:
        return False
    extra = _session_extra(session)
    extra["edbx_envelope_hex"] = tpl["envelope_hex"]
    extra["edbx_prefix_hex"] = tpl.get("prefix_hex") or tpl["envelope_hex"][:16]
    extra["edbx_trailer_hex"] = tpl["trailer_hex"]
    extra["edbx_header_suffix_hex"] = tpl["header_suffix_hex"]
    extra["edbx_envelope_source"] = source
    extra["edbx_route_sample"] = tpl.get("route") or ""
    if tpl.get("field12_us"):
        extra["edbx_field12_us"] = tpl["field12_us"]
    try:
        from pigeon_protocol.session import save_session

        save_session(session)
    except Exception as exc:
        logger.debug("save_session edbx envelope: %s", exc)
    return True


def derive_edbx_inner_session(session, *, conversation_id: str = "") -> tuple[bytes | None, dict[str, Any]]:
    """
    Pure HTTP + optional createUser token → full 169B edbX inner.

    Requires: init field10 (or wall µs), conversation route, trailer (cache/derive).
    """
    from pigeon_protocol.foundation.im_access_token import resolve_im_access_token
    from pigeon_protocol.foundation.init_timestamps import load_init_bytes, resolve_edbx_timestamp_us

    report: dict[str, Any] = {"variant": "edbx_derived", "ok": False}
    route, route_via = resolve_conversation_route(session, conversation_id=conversation_id)
    report["route_via"] = route_via
    if not route:
        report["error"] = "no conversation route"
        return None, report

    raw, init_src = load_init_bytes(session)
    report["init_source"] = init_src
    ts_us, ts_via = resolve_edbx_timestamp_us(session, raw=raw)
    report["ts_us"] = ts_us
    report["ts_via"] = ts_via

    token, token_via = resolve_im_access_token(session, allow_node=True)
    report["access_token_via"] = token_via
    if token:
        report["access_token_preview"] = token[:12] + "..."

    trailer, tail_via = resolve_trailer(session, access_token=token, ts_us=ts_us)
    report["trailer_via"] = tail_via
    if not trailer:
        report["error"] = "no edbX trailer (cache once or reverse accessToken trailer)"
        return None, report

    try:
        from pigeon_protocol.foundation.init_edbx_seeds import analyze_prefix8_vs_field12
        from pigeon_protocol.foundation.init_timestamps import load_init_bytes

        init_raw, _ = load_init_bytes(session)
        report["prefix8_analysis"] = analyze_prefix8_vs_field12(init_raw, ts_us=ts_us)
    except Exception as exc:
        logger.debug("prefix8 analysis: %s", exc)

    try:
        inner = build_edbx_inner_derived(route, ts_us=ts_us, trailer=trailer)
    except ValueError as exc:
        report["error"] = str(exc)
        return None, report

    report.update(
        {
            "ok": True,
            "header_hex": inner[:8].hex(),
            "ticket": extract_edbx_ticket(inner),
            "prefix_hex": inner[4 : 4 + PREFIX_LEN].hex(),
        }
    )
    return inner, report


def resolve_conversation_route(session, *, conversation_id: str = "") -> tuple[str, str]:
    env = str(__import__("os").environ.get("PIGEON_CONVERSATION_ID", "") or "").strip()
    if env:
        return env, "env"
    if conversation_id:
        return conversation_id, "arg"
    try:
        from pigeon_protocol.foundation.rust_sdk_inner import resolve_conversation_id

        route, via = resolve_conversation_id(session)
        if route:
            return route, via
    except Exception as exc:
        logger.debug("resolve_conversation_id: %s", exc)
    return "", "missing"


def assemble_edbx_payload(envelope: bytes, core: bytes, trailer: bytes) -> bytes:
    if len(envelope) != ENVELOPE_LEN:
        raise ValueError(f"envelope must be {ENVELOPE_LEN} bytes")
    if len(trailer) != TRAILER_LEN:
        raise ValueError(f"trailer must be {TRAILER_LEN} bytes")
    expected_core = PAYLOAD_LEN - ENVELOPE_LEN - TRAILER_LEN
    if len(core) != expected_core:
        raise ValueError(f"core must be {expected_core} bytes, got {len(core)}")
    return envelope + core + trailer


def assemble_edbx_inner(envelope: bytes, core: bytes, trailer: bytes) -> bytes:
    payload = assemble_edbx_payload(envelope, core, trailer)
    return MAGIC_EDBX + payload


def build_edbx_inner(session, route: str, *, envelope_tpl: dict[str, bytes] | None = None) -> bytes:
    """Build 169B using cached trailer + field12 from session/template."""
    tpl = envelope_tpl or load_envelope_template(session)
    if not tpl:
        raise ValueError("missing edbx envelope template")
    trailer = tpl["trailer"]
    extra = _session_extra(session)
    ts_us = int(extra.get("edbx_field12_us") or 0)
    if not ts_us and tpl.get("envelope"):
        ts_us = decode_ts_us_from_prefix(tpl["envelope"][:PREFIX_LEN]) or 0
    if not ts_us:
        raise ValueError("missing edbx field12 timestamp — run derive or ingest once")
    return build_edbx_inner_derived(route, ts_us=ts_us, trailer=trailer)


def is_edbx_inner(inner: bytes) -> bool:
    return len(inner) == INNER_LEN and inner[:4] == MAGIC_EDBX


def try_build_edbx_inner(session, *, conversation_id: str = "") -> tuple[bytes | None, dict[str, Any]]:
    """Best-effort edbX synthesis — derived formula first, cached template fallback."""
    inner, report = derive_edbx_inner_session(session, conversation_id=conversation_id)
    if inner:
        return inner, report

    report = {"variant": "edbx_cache", "ok": False}
    route, via = resolve_conversation_route(session, conversation_id=conversation_id)
    report["route_via"] = via
    if not route:
        report["error"] = report.get("error") or "no conversation route"
        return None, report
    tpl = load_envelope_template(session)
    if not tpl:
        report["error"] = report.get("error") or "no edbx envelope template"
        return None, report
    try:
        inner = build_edbx_inner(session, route, envelope_tpl=tpl)
    except ValueError as exc:
        report["error"] = str(exc)
        return None, report
    report.update(
        {
            "ok": True,
            "via": "cached_envelope",
            "route_len": len(normalize_route(route)),
            "header_hex": inner[:8].hex(),
            "ticket": extract_edbx_ticket(inner),
        }
    )
    return inner, report


def ingest_derived_inners(session, inner: bytes, *, source: str = "derive") -> list[str]:
    """Store derived edbX inner for all send classes + envelope metadata."""
    if not is_edbx_inner(inner):
        return []
    store_envelope_template(session, inner, source=source)
    from pigeon_protocol.foundation.ws_blob_compute import _store_session_class_inner, inner_class_registry

    applied: list[str] = []
    for ic in inner_class_registry().values():
        _store_session_class_inner(session, ic.class_id, inner)
        applied.append(ic.name)
    try:
        from pigeon_protocol.foundation.ws_inner_normalize import normalize_session_inners

        normalize_session_inners(session, persist=True)
    except Exception as exc:
        logger.debug("normalize after derive: %s", exc)
    return applied


def envelope_from_init_timestamps(ts_start: int, ts_span: int, *, device_id: str = "") -> bytes:
    """Legacy 40B envelope guess — superseded by derive_prefix_from_ts_us (8B prefix)."""
    return derive_prefix_from_ts_us(ts_start).ljust(ENVELOPE_LEN, b"\x00")[:ENVELOPE_LEN]


def verify_sample_formula(*, sample_hex: str = SAMPLE_INNER_HEX) -> dict[str, Any]:
    """Bit-exact rebuild check against Rust capture (returns diagnostic dict)."""
    sample = bytes.fromhex(sample_hex)
    route = extract_edbx_ticket(sample) or ""
    prefix = sample[4 : 4 + PREFIX_LEN]
    trailer = sample[4 + BODY_LEN : 4 + PAYLOAD_LEN]
    f12 = decode_ts_us_from_prefix(prefix) or _decode_field12_us(sample[4:]) or 0
    rebuilt = build_edbx_inner_derived(route.lstrip("n"), ts_us=f12, trailer=trailer)
    parts = split_edbx_payload(sample[4:])
    return {
        "ok": rebuilt == sample,
        "sample_len": len(sample),
        "f12_us": f12,
        "prefix_match": derive_prefix_from_ts_us(f12) == prefix,
        "trailer_hex": trailer.hex(),
        "core_len": len(parts["core"]),
        "outer_len": len(parts["outer"]),
        "rebuilt_header": rebuilt[:8].hex(),
    }
