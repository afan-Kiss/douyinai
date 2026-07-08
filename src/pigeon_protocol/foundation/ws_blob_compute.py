"""169B WS inner blob — equivalence-class formula + session-scoped resolution."""
from __future__ import annotations

import base64
import hashlib
import logging
import struct
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from pigeon_protocol.ws_inner_buckets import BUCKET_INNER_FP, EMPIRICAL_INNER_FP, classify_inner_bucket
from pigeon_protocol.ws_sign_decode import decode_blob, encode_blob

logger = logging.getLogger("pigeon.ws_blob_compute")

INNER_LEN = 169
BLOB_LEN = 226


class InnerComputeError(RuntimeError):
    """Cannot resolve 169B inner for text length / session."""


@dataclass(frozen=True)
class InnerClass:
    """One equivalence class of identical 169B inners across text byte-lengths."""

    class_id: str
    name: str
    header: tuple[int, int]
    text_lengths: tuple[int, ...]
    canonical_text_b: int

    @property
    def header_bytes(self) -> bytes:
        return struct.pack("<II", self.header[0], self.header[1])


def _inner_from_capture(byte_len: int) -> bytes | None:
    from pigeon_protocol.capture_loader import index_send_templates, load_capture
    from pigeon_protocol.ws_sign import locate_signature_region

    info = index_send_templates().get(byte_len)
    if not info:
        return None
    try:
        ev = load_capture(info.path)
        raw = base64.b64decode(str(ev.get("payload") or ""))
        region = locate_signature_region(raw)
        if not region:
            return None
        inner = decode_blob(region.blob)
        return inner if len(inner) == INNER_LEN else None
    except Exception as exc:
        logger.debug("inner load textB=%s failed: %s", byte_len, exc)
        return None


def _class_name(class_id: str, header: tuple[int, int]) -> str:
    for name, fp in BUCKET_INNER_FP.items():
        if (fp["le32_0"], fp["le32_4"]) == header:
            return name
    return EMPIRICAL_INNER_FP.get(class_id, class_id[:8])


@lru_cache(maxsize=1)
def inner_class_registry() -> dict[str, InnerClass]:
    """
    Build equivalence classes from harvested pool.

    Formula: inner(textB) = inner(class(textB)) where class groups lengths
    sharing identical 169B payload (session-scoped constant per class).
    """
    from pigeon_protocol.ws_sign_bucket import bucket_map

    bmap = bucket_map()
    grouped: dict[str, list[int]] = {}
    headers: dict[str, tuple[int, int]] = {}

    for bl, fp in sorted(bmap.items()):
        grouped.setdefault(fp, []).append(bl)
        inner = _inner_from_capture(bl)
        if inner and len(inner) >= 8:
            headers[fp] = (
                int.from_bytes(inner[0:4], "little"),
                int.from_bytes(inner[4:8], "little"),
            )

    registry: dict[str, InnerClass] = {}
    for class_id, lengths in grouped.items():
        hdr = headers.get(class_id, (0, 0))
        if hdr == (0, 0):
            inner = _inner_from_capture(min(lengths))
            if inner and len(inner) >= 8:
                hdr = (
                    int.from_bytes(inner[0:4], "little"),
                    int.from_bytes(inner[4:8], "little"),
                )
        registry[class_id] = InnerClass(
            class_id=class_id,
            name=_class_name(class_id, hdr),
            header=hdr,
            text_lengths=tuple(sorted(lengths)),
            canonical_text_b=min(lengths),
        )
    return registry


def inner_class_for_text_b(text_b: int) -> InnerClass | None:
    """Map UTF-8 byte length → inner equivalence class (the 169B formula selector)."""
    if text_b <= 0:
        return None

    registry = inner_class_registry()
    from pigeon_protocol.ws_sign_bucket import bucket_for_text_len, bucket_map, same_inner_bucket

    bmap = bucket_map()
    if text_b in bmap:
        return registry.get(bmap[text_b])

    spec = bucket_for_text_len(text_b)
    if spec:
        for bl in range(spec.text_min, spec.text_max + 1):
            fp = bmap.get(bl)
            if fp and fp in registry:
                return registry[fp]
        spec_id = f"spec_{spec.name}"
        fp = BUCKET_INNER_FP.get(spec.name)
        if fp:
            return InnerClass(
                class_id=spec_id,
                name=spec.name,
                header=(fp["le32_0"], fp["le32_4"]),
                text_lengths=tuple(range(spec.text_min, spec.text_max + 1)),
                canonical_text_b=spec.canonical_len,
            )

    for bl, fp in sorted(bmap.items()):
        if same_inner_bucket(bl, text_b):
            return registry.get(fp)

    return None


def _session_cache_key(session) -> str:
    from pigeon_protocol.foundation.ws_session_inner import _session_key

    return _session_key(session)


def _load_session_class_cache(session) -> dict[str, bytes]:
    from pigeon_protocol.foundation.ws_session_inner import _load_cache

    entry = _load_cache().get(_session_cache_key(session), {})
    out: dict[str, bytes] = {}
    for key, hex_val in entry.items():
        if key.startswith("_"):
            continue
        try:
            raw = bytes.fromhex(str(hex_val))
            if len(raw) == INNER_LEN:
                out[key] = raw
        except ValueError:
            continue
    return out


def _session_unified_send_inner(session) -> bytes | None:
    """
    Live sessions (im_normal_server_send_msg_fix) often use one 169B inner for all A–G.

    When every cached send class shares the same payload, return it for any textB
    instead of falling back to stale pool templates from another login.
    """
    cached = _load_session_class_cache(session)
    if not cached:
        return None
    unique = {raw.hex() for raw in cached.values()}
    if len(unique) == 1:
        return next(iter(cached.values()))
    # Majority vote when partial warm left mixed keys
    from collections import Counter

    top_hex, count = Counter(raw.hex() for raw in cached.values()).most_common(1)[0]
    if count >= max(4, len(cached) // 2):
        return bytes.fromhex(top_hex)
    return None


def _store_session_class_inner(session, class_id: str, inner: bytes) -> None:
    from pigeon_protocol.foundation.ws_session_inner import _load_cache, _save_cache

    if len(inner) != INNER_LEN:
        return
    cache = _load_cache()
    key = _session_cache_key(session)
    entry = cache.setdefault(key, {})
    entry[class_id] = inner.hex()
    entry["_meta"] = {
        "inner_fp": hashlib.sha256(inner).hexdigest()[:16],
        "class_id": class_id,
    }
    cache[key] = entry
    _save_cache(cache)


def pool_inner_for_class(inner_class: InnerClass, *, text_b: int = 0) -> bytes | None:
    """Load representative 169B inner from template pool for this class."""
    if text_b > 0:
        inner = _inner_from_capture(text_b)
        if inner:
            return inner
    return _inner_from_capture(inner_class.canonical_text_b)


def compute_inner_bytes(
    session: Any,
    text_b: int,
    *,
    client_message_id: str = "",
    timestamp_ms: int | None = None,
    bootstrap: bool = True,
) -> bytes:
    """
    Resolve 169B inner blob for text byte-length.

    Structural formula (RE 2026-07-06):
      inner = session_constant[class(textB)]
      class(textB) from equivalence registry (7 groups for 1-200)
      blob  = base64(inner) padded to 226 ASCII bytes

    Session constant is IM-SDK-generated once per class; we reuse via cache/pool.
    Full crypto body synthesis (bytes 8-168) still requires WASM RE.
    """
    inner_class = inner_class_for_text_b(text_b)
    if not inner_class:
        raise InnerComputeError(f"no inner equivalence class for textB={text_b}")

    class_id = inner_class.class_id

    if session is not None:
        unified = _session_unified_send_inner(session)
        if unified:
            logger.debug(
                "inner class=%s from session unified inner textB=%s",
                inner_class.name,
                text_b,
            )
            return unified

        cached = _load_session_class_cache(session).get(class_id)
        if cached:
            logger.debug("inner class=%s from session cache textB=%s", inner_class.name, text_b)
            return cached

        if bootstrap:
            from pigeon_protocol.foundation.ws_inner_bootstrap import import_bundle_canonical

            import_bundle_canonical(session, persist=True)
            cached = _load_session_class_cache(session).get(class_id)
            if cached:
                logger.debug("inner class=%s from bundle bootstrap textB=%s", inner_class.name, text_b)
                return cached

        try:
            from pigeon_protocol.foundation.ws_inner_synthesize import synthesize_inner_bytes

            synthesized = synthesize_inner_bytes(session, inner_class.name, class_id)
            if session is not None:
                _store_session_class_inner(session, class_id, synthesized)
            logger.debug("inner class=%s synthesized textB=%s", inner_class.name, text_b)
            return synthesized
        except Exception as exc:
            logger.debug("inner synthesis skipped textB=%s: %s", text_b, exc)

    # Never poison session cache with cross-login pool templates when we already
    # know this session uses a different unified inner (partial cache).
    if session is not None and _session_unified_send_inner(session):
        raise InnerComputeError(
            f"session has unified send inner but class {inner_class.name} ({class_id[:8]}) "
            f"not keyed — run prepare-pure or warm one send"
        )

    inner = pool_inner_for_class(inner_class, text_b=text_b)
    if inner:
        if session is not None:
            _store_session_class_inner(session, class_id, inner)
        logger.debug(
            "inner class=%s from pool canonical=%s textB=%s",
            inner_class.name,
            inner_class.canonical_text_b,
            text_b,
        )
        return inner

    if inner_class.header != (0, 0):
        raise InnerComputeError(
            f"inner class {inner_class.name} known but no pool template — "
            f"harvest b{inner_class.canonical_text_b:03d} or send once in-session"
        )
    raise InnerComputeError(f"cannot compute inner for textB={text_b} class={class_id}")


def compute_blob_ascii(
    session: Any,
    text_b: int,
    *,
    client_message_id: str = "",
    timestamp_ms: int | None = None,
) -> bytes:
    """226-byte ASCII signature blob = base64(169B inner)."""
    inner = compute_inner_bytes(
        session,
        text_b,
        client_message_id=client_message_id,
        timestamp_ms=timestamp_ms,
    )
    blob = encode_blob(inner)
    if len(blob) != BLOB_LEN:
        raise InnerComputeError(f"encoded blob length {len(blob)} != {BLOB_LEN}")
    return blob


def patch_computed_blob(frame: bytearray, session: Any, text_b: int, **kwargs: Any) -> bool:
    from pigeon_protocol.foundation.ws_blob_store import patch_inner_blob

    inner = compute_inner_bytes(session, text_b, **kwargs)
    return patch_inner_blob(frame, inner)


def registry_report() -> dict[str, Any]:
    reg = inner_class_registry()
    return {
        "class_count": len(reg),
        "classes": [
            {
                "id": c.class_id[:16],
                "name": c.name,
                "header_hex": c.header_bytes.hex(),
                "canonical_text_b": c.canonical_text_b,
                "text_lengths": list(c.text_lengths),
                "range": f"{min(c.text_lengths)}-{max(c.text_lengths)}",
            }
            for c in reg.values()
        ],
        "formula": "inner(textB) = session_constant[class(textB)]; blob = b64(inner)",
        "crypto_body_re": "bytes 8-168 IM SDK WASM — session-scoped, not text-dependent within class",
    }


def classify_inner(inner: bytes) -> dict[str, Any]:
    if len(inner) != INNER_LEN:
        return {"error": f"expected {INNER_LEN} bytes"}
    return {
        "header_hex": inner[:8].hex(),
        "bucket": classify_inner_bucket(inner),
        "sha256_prefix": hashlib.sha256(inner).hexdigest()[:16],
        "le32_0": int.from_bytes(inner[0:4], "little"),
        "le32_4": int.from_bytes(inner[4:8], "little"),
    }
