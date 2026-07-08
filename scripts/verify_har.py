#!/usr/bin/env python3
"""Verify HAR import: replay WS + order + context from captures."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from pigeon_protocol.capture_loader import index_captures
from pigeon_protocol.config import LIVE_CAPTURES
from pigeon_protocol.har_replay import context_from_har, order_from_har
from pigeon_protocol.session import load_session
from pigeon_protocol.ws_client import WsListener
from pigeon_protocol.send import SendService


def main() -> int:
    root = LIVE_CAPTURES / "from_har"
    listener = WsListener(load_session())
    sender = SendService(load_session(), dry_run=True)

    ws_files = sorted(root.glob("*ws_frame*.json"))
    buyer = seller = 0
    samples = []
    for p in ws_files:
        n = listener.replay_capture_file(p, lambda m: None)
        if n:
            ev = json.loads(p.read_text(encoding="utf-8"))
            msgs = listener.parse_ws_payload(
                ev.get("payload") or ev.get("payload_hex", ""),
                url=str(ev.get("url") or ""),
                direction="in" if "received" in p.name else "out",
            )
            for m in msgs:
                if m.role == "buyer":
                    buyer += 1
                elif m.role in ("seller", "service"):
                    seller += 1
                if m.text and len(samples) < 10:
                    samples.append({"file": p.name, "role": m.role, "text": m.text})

    order = order_from_har(root)
    ctx = context_from_har(root)
    idx = index_captures([root])

    send_ok = False
    send_len = 0
    try:
        payload = sender.build_payload("亲，HAR测试回复")
        send_ok = True
        send_len = len(payload)
    except Exception as exc:
        send_err = str(exc)

    out = {
        "ws_files": len(ws_files),
        "ws_parsed_buyer": buyer,
        "ws_parsed_seller": seller,
        "ws_samples": samples,
        "order_from_har": {
            "has_order": order.has_order if order else False,
            "summary": order.summary if order else "",
            "user_id": (order.raw.get("post") or {}).get("security_user_id") if order else "",
        },
        "context_from_har": {
            "source": ctx.source if ctx else "",
            "message_count": len(ctx.messages) if ctx else 0,
        },
        "send_build": {"ok": send_ok, "length": send_len, "error": send_err if not send_ok else ""},
        "captures": {
            "http": len(idx.http_bodies),
            "ws_sent": len(idx.ws_sent),
            "ws_received": len(idx.ws_received),
        },
    }
    path = ROOT / "logs" / "har_verify.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
