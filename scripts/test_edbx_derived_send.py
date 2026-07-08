#!/usr/bin/env python3
"""Send one WS message using pure-Python derived edbX inner (init f10 + trailer)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("PIGEON_STANDALONE", "1")
os.environ.setdefault("PIGEON_NO_CDP", "1")

# Observed jinritemai trailer — testing cross-session reuse for cold start
DEFAULT_TRAILER_HEX = os.environ.get("PIGEON_EDBX_TRAILER_HEX", "b38a848485e5c1a1")


def main() -> int:
    from pigeon_protocol.foundation.im_access_token import resolve_im_access_token
    from pigeon_protocol.foundation.ws_inner_edbx import (
        build_edbx_inner_derived,
        derive_edbx_inner_session,
        ingest_derived_inners,
        resolve_trailer,
    )
    from pigeon_protocol.send import SendService
    from pigeon_protocol.session import load_session, save_session
    from pigeon_protocol.ws_sign import locate_signature_region
    from pigeon_protocol.ws_sign_decode import decode_blob

    session = load_session()
    token, token_via = resolve_im_access_token(session, allow_node=False)
    report: dict = {"token_via": token_via, "token_preview": token[:12] + "..." if token else None}

    inner, derive = derive_edbx_inner_session(session)
    report["derive"] = derive

    if not inner:
        from pigeon_protocol.foundation.init_timestamps import resolve_edbx_timestamp_us
        from pigeon_protocol.foundation.rust_sdk_inner import resolve_conversation_id

        route, route_via = resolve_conversation_id(session)
        ts_us, ts_via = resolve_edbx_timestamp_us(session)
        trailer, tail_via = resolve_trailer(session, access_token=token, ts_us=ts_us)
        if not trailer:
            trailer = bytes.fromhex(DEFAULT_TRAILER_HEX)
            tail_via = "default_constant"
        inner = build_edbx_inner_derived(route.lstrip("n"), ts_us=ts_us, trailer=trailer)
        derive = {
            "ok": True,
            "via": "fallback_build",
            "route_via": route_via,
            "ts_via": ts_via,
            "trailer_via": tail_via,
            "header_hex": inner[:8].hex(),
        }
        report["derive"] = derive

    classes = ingest_derived_inners(session, inner, source="derived_send_test")
    report["ingested"] = classes
    save_session(session)

    uid = os.environ.get("PIGEON_SECURITY_USER_ID", "").strip()
    if not uid.startswith("AQ"):
        route, _ = __import__(
            "pigeon_protocol.foundation.rust_sdk_inner", fromlist=["resolve_conversation_id"]
        ).resolve_conversation_id(session)
        if route.startswith("AQ"):
            uid = route.split(":", 1)[0]

    text = "好"
    sender = SendService(session, dry_run=False)
    frame = sender.build_payload(text, security_user_id=uid)
    region = locate_signature_region(frame)
    patched = decode_blob(region.blob) if region else b""
    result = sender.send_text(text, security_user_id=uid, auto_harvest=False)
    report["send"] = {
        "text": text,
        "inner_match": patched == inner,
        "inner_header": inner[:8].hex(),
        "frame_len": len(frame),
        "send_ok": bool(result.ok),
        "mode": result.mode,
        "reason": result.reason or "",
    }
    out = ROOT / "analysis" / "edbx_derived_send.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["send"].get("send_ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
