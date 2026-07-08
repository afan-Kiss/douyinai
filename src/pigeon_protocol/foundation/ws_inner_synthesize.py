"""169B inner body synthesis — session-scoped crypto body (Rust SDK output)."""
from __future__ import annotations

import logging
from typing import Any

from pigeon_protocol.foundation.ws_inner_layout import BODY_LEN, assemble_inner, class_header

logger = logging.getLogger("pigeon.ws_inner_synthesize")


class InnerSynthesisError(RuntimeError):
    """Cannot synthesize 161-byte inner body without Rust SDK or prior harvest."""


def get_init_sync_body(session) -> bytes | None:
    """Return 161-byte INIT_SYNC body if cached (inbox seed, not send-usable)."""
    from pigeon_protocol.foundation.ws_session_inner import _load_cache, _session_key

    key = _session_key(session)
    entry = _load_cache().get(key, {})
    hx = entry.get("__init_sync__")
    if not hx:
        return None
    try:
        inner = bytes.fromhex(str(hx))
        if len(inner) == 169:
            return inner[8:]
    except ValueError:
        pass
    return None


def derive_body_from_init(session, class_name: str) -> bytes | None:
    """
    Attempt to derive send-class body from INIT_SYNC seed.

    RE 2026-07-06: XOR/transform does not yield valid send bodies; Rust SDK required.
    """
    init_body = get_init_sync_body(session)
    if not init_body or not class_header(class_name):
        return None
    # Empirical: no linear transform from INIT_SYNC → A–G (tested XOR, prefix-stable=0)
    return None


def synthesize_inner_body(session, class_name: str, class_id: str) -> bytes:
    """
    Synthesize 161-byte session crypto body for equivalence class.

    Requires Pigeon Rust SDK (packedMessage) or prior harvest/cache.
    """
    from pigeon_protocol.foundation.ws_blob_compute import _load_session_class_cache

    cached = _load_session_class_cache(session).get(class_id)
    if cached and len(cached) == 169:
        return cached[8:]

    derived = derive_body_from_init(session, class_name)
    if derived and len(derived) == BODY_LEN:
        return derived

    try:
        from pigeon_protocol.foundation.pigeon_sdk_delegate import ensure_send_inner
        from pigeon_protocol.pure_config import pure_only_mode

        seed = ensure_send_inner(session, cdp_if_available=not pure_only_mode())
        if seed.get("ok"):
            cached = _load_session_class_cache(session).get(class_id)
            if cached and len(cached) == 169:
                return cached[8:]
    except Exception as exc:
        logger.debug("sdk delegate seed skipped: %s", exc)

    raise InnerSynthesisError(
        f"cannot synthesize class {class_name} body — "
        "harvest once, export bundle, or hook Pigeon Rust SDK (invokeWithoutReturn)"
    )


def synthesize_inner_bytes(session, class_name: str, class_id: str) -> bytes:
    """Full 169B inner — edbX ticket variant or 8B class header + 161B encrypted body."""
    try:
        from pigeon_protocol.foundation.ws_inner_edbx import try_build_edbx_inner

        inner, edbx_report = try_build_edbx_inner(session)
        if inner and len(inner) == 169:
            logger.info(
                "synthesized edbX inner class=%s route_via=%s",
                class_name,
                edbx_report.get("route_via"),
            )
            return inner
    except Exception as exc:
        logger.debug("edbX synthesize skipped: %s", exc)

    body = synthesize_inner_body(session, class_name, class_id)
    return assemble_inner(class_name, body)


def synthesis_status(session) -> dict[str, Any]:
    from pigeon_protocol.foundation.ws_blob_compute import (
        _session_unified_send_inner,
        inner_class_registry,
        _load_session_class_cache,
    )

    from pigeon_protocol.foundation.ws_inner_edbx import load_envelope_template

    cached = _load_session_class_cache(session)
    unified = _session_unified_send_inner(session)
    reg = inner_class_registry()
    rows = []
    for ic in reg.values():
        have = ic.class_id in cached
        rows.append(
            {
                "class": ic.name,
                "class_id": ic.class_id[:16],
                "header_known": class_header(ic.name) is not None,
                "cached": have,
                "synthesizable": have or unified is not None,
            }
        )
    return {
        "init_sync_body": get_init_sync_body(session) is not None,
        "session_unified_inner": unified[:8].hex() if unified else None,
        "unified_mode": unified is not None,
        "classes": rows,
        "rust_sdk_required": unified is None and load_envelope_template(session) is None,
        "edbx_envelope": load_envelope_template(session) is not None,
        "pure_edbx_core": "protobuf {f1=0,f2=7,f3=route@110B} — ws_inner_edbx.encode_edbx_core",
        "frontier_sign": "byted_acrawler.frontierSign(X-MS-STUB=md5(pb)) — load via sdk-glue+bdms Node",
        "inner_sign": "edbX ticket (jinritemai) or ring AES-GCM 161B (encrypted_send)",
    }
