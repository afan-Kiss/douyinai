"""Bootstrap 169B WS inner blobs — init/HTML/bundle scan + session cache."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

from pigeon_protocol.pure_config import BUNDLE_WS_INNER, STANDALONE_BUNDLE
from pigeon_protocol.ws_sign import locate_signature_region
from pigeon_protocol.ws_sign_decode import decode_blob
from pigeon_protocol.foundation.ws_blob_compute import classify_inner

logger = logging.getLogger("pigeon.ws_inner_bootstrap")

_B64_RUN = re.compile(rb"[A-Za-z0-9+/]{220,228}={0,2}")


def _session_key(session) -> str:
    from pigeon_protocol.foundation.ws_session_inner import _session_key

    return _session_key(session)


def scan_binary_for_inners(data: bytes) -> list[dict[str, Any]]:
    """Find valid 169B inner candidates in arbitrary binary/text."""
    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in _B64_RUN.finditer(data):
        chunk = m.group(0)
        for take in (226, len(chunk)):
            if take < 220:
                continue
            blob = chunk[:take]
            try:
                inner = decode_blob(blob)
            except Exception:
                continue
            if len(inner) != 169:
                continue
            fp = hashlib.sha256(inner).hexdigest()[:16]
            if fp in seen:
                continue
            seen.add(fp)
            hits.append(
                {
                    "offset": m.start(),
                    "class_id": fp,
                    "inner_hex": inner.hex(),
                    "layout": classify_inner(inner),
                }
            )
    # Also scan WS send frames already assembled
    if b"s:client_message_id" in data:
        region = locate_signature_region(data)
        if region:
            try:
                inner = decode_blob(region.blob)
                if len(inner) == 169:
                    fp = hashlib.sha256(inner).hexdigest()[:16]
                    if fp not in seen:
                        hits.append(
                            {
                                "offset": region.blob_start,
                                "class_id": fp,
                                "inner_hex": inner.hex(),
                                "layout": classify_inner(inner),
                                "from_frame": True,
                            }
                        )
            except Exception:
                pass
    return hits


def ingest_binary_inners(session, data: bytes, *, source: str = "") -> list[str]:
    """Parse binary, map to A–G / INIT_SYNC, store into session cache."""
    from pigeon_protocol.foundation.init_inner_mapper import ingest_init_response

    report = ingest_init_response(session, data, source=source or "binary")
    applied = list(report.get("stored_keys") or [])
    for key in applied:
        logger.info("ingested inner key=%s via %s", str(key)[:12], source or "binary")
    return applied


def load_bundle_canonical(session) -> dict[str, bytes]:
    """Load session-exported canonical inners from standalone_bundle or session sidecar."""
    from pigeon_protocol.session_portable import PORTABLE_INNER

    for path in (PORTABLE_INNER, BUNDLE_WS_INNER):
        if not path.is_file():
            alt = STANDALONE_BUNDLE / "ws_inner_canonical.json"
            if path == BUNDLE_WS_INNER and alt.is_file():
                path = alt
            else:
                continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        sk = _session_key(session)
        bound = doc.get("session_key")
        if bound and bound != sk:
            logger.debug("inner export session_key mismatch — skip %s", path.name)
            continue

        out: dict[str, bytes] = {}
        for row in doc.get("classes") or []:
            if not isinstance(row, dict):
                continue
            cid = str(row.get("class_id") or "")
            hx = str(row.get("inner_hex") or "")
            if not cid or not hx:
                continue
            try:
                inner = bytes.fromhex(hx)
                if len(inner) == 169:
                    out[cid] = inner
            except ValueError:
                continue
        if out:
            return out
    return {}


def import_bundle_canonical(session, *, persist: bool = True) -> list[str]:
    from pigeon_protocol.foundation.ws_blob_compute import _store_session_class_inner

    loaded = load_bundle_canonical(session)
    applied: list[str] = []
    for class_id, inner in loaded.items():
        _store_session_class_inner(session, class_id, inner)
        applied.append(class_id)
    if applied and persist:
        logger.info("imported %s inner classes from bundle", len(applied))
    return applied


def bootstrap_session_inners(session, *, scan_init: bool = True) -> dict[str, Any]:
    """
    Cold-start inner bootstrap chain:
    1. bundle canonical export (same session)
    2. get_message_by_init + workspace HTML binary scan
    """
    report: dict[str, Any] = {"sources": []}

    bundle = import_bundle_canonical(session, persist=True)
    if bundle:
        report["sources"].append(f"bundle:{len(bundle)}")
        report["bundle_classes"] = bundle

    if scan_init:
        from pigeon_protocol.feige_init import bootstrap_feige_session

        boot = bootstrap_feige_session(session, persist=True)
        report["feige_bootstrap"] = {
            k: boot.get(k)
            for k in ("steps", "get_message_by_init", "getConfig", "body_len")
            if k in boot
        }
        init_block = boot.get("get_message_by_init") or {}
        init_inners = init_block.get("inners_from_init") or []
        if init_inners:
            report["sources"].append(f"init:{len(init_inners)}")
        if init_block.get("init_inner_mapping"):
            report["init_inner_mapping"] = init_block["init_inner_mapping"]

    from pigeon_protocol.foundation.ws_blob_compute import _load_session_class_cache

    cached = _load_session_class_cache(session)
    report["cached_classes"] = sorted(cached.keys())
    report["ready"] = len(cached) >= 4
    return report


def ensure_session_inners(session, *, min_classes: int = 4) -> dict[str, Any]:
    """Ensure enough inner classes cached for ComputedBlobStrategy."""
    from pigeon_protocol.foundation.ws_blob_compute import _load_session_class_cache, inner_class_registry

    cached = _load_session_class_cache(session)
    if len(cached) >= min_classes:
        return {"ready": True, "cached_classes": len(cached), "via": "cache"}

    report = bootstrap_session_inners(session, scan_init=True)
    cached = _load_session_class_cache(session)
    need = max(min_classes, min(7, len(inner_class_registry())))
    report["ready"] = len(cached) >= min(4, need)
    report["cached_classes"] = len(cached)

    if report["ready"]:
        try:
            from pigeon_protocol.foundation.pure_prepare import sync_standalone_bundle

            report["bundle_sync"] = sync_standalone_bundle(session)
        except Exception as exc:
            logger.debug("bundle sync skipped: %s", exc)

    try:
        from pigeon_protocol.pure_config import pure_only_mode

        if not pure_only_mode():
            from pigeon_protocol.cdp_bridge import cdp_ready
            from pigeon_protocol.foundation.ws_cdp_inner_ingest import refresh_inners_via_cdp

            if cdp_ready() and len(cached) < 7:
                report["cdp_inner_refresh"] = refresh_inners_via_cdp(session)
    except Exception as exc:
        logger.debug("cdp inner refresh skipped: %s", exc)

    if not report.get("ready"):
        try:
            from pigeon_protocol.foundation.rust_sdk_inner import rust_sdk_seed_send_inner

            rust = rust_sdk_seed_send_inner(session)
            if rust.get("ingested_classes"):
                report["rust_sdk_inner"] = rust
                cached = _load_session_class_cache(session)
                report["ready"] = len(cached) >= min(4, need)
                report["cached_classes"] = len(cached)
        except Exception as exc:
            logger.debug("rust sdk inner refresh skipped: %s", exc)

    return report
