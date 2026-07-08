#!/usr/bin/env python3
"""Test WS send using 9B inner template for various text lengths 9-60."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

USER_ID = "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk"


def cross_send(text: str) -> dict:
    import base64

    from pigeon_protocol.capture_loader import find_send_template
    from pigeon_protocol.pure_runtime import PureProtocolRuntime
    from pigeon_protocol.parsers.ws_frame_builder import WSFrameBuilder
    from pigeon_protocol.ws_session import WsSession

    bl = len(text.encode("utf-8"))
    canonical = find_send_template(byte_len=bl)  # bucket-aware (9-60 → b009)
    if not canonical:
        return {"ok": False, "reason": "no b009 template"}

    rt = PureProtocolRuntime()
    builder = WSFrameBuilder.from_template_dict(canonical)
    payload = builder.build_pure(
        text,
        security_user_id=USER_ID,
        shop_id=rt.session.shop_id,
        ws_url=rt.listener.pick_ws_url(),
        preserve_signature=True,
        session=rt.session,
    )
    ws = WsSession(rt.session)
    result = ws.send_bytes_sync(payload, ws_url=rt.listener.pick_ws_url())
    return {
        "text": text,
        "byte_len": bl,
        "ok": result.ok,
        "mode": result.mode,
        "payload_length": len(payload),
        "ack_len": getattr(result, "ack_len", None) or (result.raw or {}).get("ack_len"),
        "reason": result.reason,
    }


def main() -> int:
    from pigeon_protocol.ws_template_harvest import text_for_byte_length

    cases = [21, 18, 30]
    out = []
    for bl in cases:
        text = text_for_byte_length(bl)
        out.append(cross_send(text))
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if all(x.get("ok") for x in out) else 2


if __name__ == "__main__":
    raise SystemExit(main())
