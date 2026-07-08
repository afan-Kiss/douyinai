#!/usr/bin/env python3
"""Live pure-protocol WS send smoke test (requires valid session + inner cache)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("PIGEON_STANDALONE", "1")

UID = os.environ.get("PIGEON_SECURITY_USER_ID", "").strip()


def _resolve_uid(session) -> str:
    from pigeon_protocol.ws_template_harvest import DEFAULT_HARVEST_UID

    if UID.startswith("AQ"):
        return UID
    try:
        from pigeon_protocol.foundation.rust_sdk_inner import resolve_conversation_id

        route, _via = resolve_conversation_id(session)
        if route.startswith("AQ"):
            return route.split(":", 1)[0]
    except Exception:
        pass
    return DEFAULT_HARVEST_UID


def main() -> int:
    from pigeon_protocol.config import AppConfig
    from pigeon_protocol.foundation.ws_blob_compute import compute_inner_bytes, inner_class_for_text_b
    from pigeon_protocol.send import SendService
    from pigeon_protocol.session import load_session
    from pigeon_protocol.ws_sign import locate_signature_region
    from pigeon_protocol.ws_sign_decode import decode_blob
    from pigeon_protocol.ws_template_harvest import DEFAULT_HARVEST_UID

    session = load_session()
    texts = ["好", "好的", "你好"]
    report: dict = {"sends": []}

    # Align inner cache with live session before smoke send (skip if already ready)
    try:
        from pigeon_protocol.foundation.ws_inner_health import session_inner_health
        from pigeon_protocol.pure_config import cdp_allowed
        from pigeon_protocol.session import save_session

        health = session_inner_health(session)
        if not health.get("ready") or health.get("needs_cdp_warm"):
            from pigeon_protocol.foundation.pigeon_sdk_delegate import ensure_send_inner

            seed = ensure_send_inner(session, cdp_if_available=cdp_allowed())
            report["inner_seed"] = {"ok": seed.get("ok"), "via": seed.get("via")}
        else:
            report["inner_seed"] = {"ok": True, "via": "cache", "skipped": True}
        try:
            from pigeon_protocol.foundation.ws_inner_normalize import normalize_session_inners

            report["normalize"] = normalize_session_inners(session, persist=True)
        except Exception as exc:
            report["normalize"] = {"error": str(exc)[:120]}
        save_session(session)
    except Exception as exc:
        report["inner_seed"] = {"error": str(exc)[:120]}

    sender = SendService(session, dry_run=False)
    uid = _resolve_uid(session)

    for text in texts:
        bl = len(text.encode("utf-8"))
        ic = inner_class_for_text_b(bl)
        inner = compute_inner_bytes(session, bl, bootstrap=True)
        frame = sender.build_payload(text, security_user_id=uid)
        region = locate_signature_region(frame)
        patched = decode_blob(region.blob) if region else b""
        row = {
            "text": text,
            "text_b": bl,
            "class": ic.name if ic else "?",
            "inner_match": patched == inner,
            "frame_len": len(frame),
        }
        result = sender.send_text(text, security_user_id=uid, auto_harvest=False)
        row["send_ok"] = result.ok
        row["mode"] = result.mode
        row["reason"] = result.reason
        row["ack"] = result.raw
        report["sends"].append(row)

    report["ok"] = all(r.get("send_ok") and r.get("inner_match") for r in report["sends"])
    out = ROOT / "analysis" / "live_send_pure.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
