"""Hybrid inner seed — scan Rust push captures; CDP warm when ttnet WS fails."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("pigeon.python_ws_hybrid_inner")

ROOT = Path(__file__).resolve().parents[3]


def _scan_push_files() -> tuple[str | None, str | None]:
    from pigeon_protocol.foundation.ws_inner_bootstrap import scan_binary_for_inners
    from pigeon_protocol.ws_sign import locate_signature_region
    from pigeon_protocol.ws_sign_decode import decode_blob

    for path in (
        ROOT / "analysis" / "feige_push_all.bin",
        ROOT / "analysis" / "feige_push_capture.bin",
    ):
        if not path.is_file() or path.stat().st_size < 500:
            continue
        raw = path.read_bytes()
        region = locate_signature_region(raw)
        if region:
            try:
                inner = decode_blob(region.blob)
                if len(inner) == 169:
                    return inner.hex(), f"push_file:{path.name}"
            except Exception:
                pass
        for hit in scan_binary_for_inners(raw):
            layout = hit.get("layout") or {}
            hx = str(hit.get("inner_hex") or "")
            if layout.get("bucket") and len(hx) == 338:
                return hx, f"push_scan:{path.name}"
    return None, None


def hybrid_seed_after_rust(session, rust_report: dict[str, Any]) -> dict[str, Any]:
    if rust_report.get("ingested_classes"):
        return {"ok": True, "via": "rust_ingest", "skipped": True}

    inner_hex, inner_via = _scan_push_files()
    if inner_hex:
        from pigeon_protocol.foundation.rust_sdk_inner import _ingest_inner_hex

        ingested = _ingest_inner_hex(session, inner_hex, source=inner_via or "push_scan")
        if ingested:
            return {
                "ok": True,
                "via": inner_via or "push_scan",
                "ingested_classes": ingested,
            }

    node = rust_report.get("node") or {}
    steps = node.get("steps") or {}
    if not (steps.get("createMessage") or {}).get("ok"):
        return {"ok": False, "skipped": "createMessage not ok"}

    if steps.get("ws_error_push") and not steps.get("message_send_push"):
        try:
            from pigeon_protocol.pure_config import cdp_allowed

            if not cdp_allowed():
                return {"ok": False, "skipped": "cdp disabled", "ws_error": steps.get("ws_error_push")}

            from pigeon_protocol.cdp_bridge import cdp_ready
            from pigeon_protocol.foundation.ws_cdp_inner_ingest import refresh_inners_via_cdp

            if cdp_ready():
                warm = refresh_inners_via_cdp(session, warm_all=True)
                if warm.get("ok") and warm.get("applied"):
                    from pigeon_protocol.foundation.ws_inner_health import session_inner_health
                    from pigeon_protocol.foundation.ws_inner_normalize import normalize_session_inners

                    normalize_session_inners(session, persist=True)
                    health = session_inner_health(session)
                    return {
                        "ok": bool(health.get("ready")),
                        "via": "cdp_warm_after_rust_ws_fail",
                        "warm": warm,
                        "health": health,
                    }
        except Exception as exc:
            logger.debug("cdp warm fallback: %s", exc)

    return {
        "ok": False,
        "via": "hybrid_partial",
        "ws_error": steps.get("ws_error_push"),
    }
