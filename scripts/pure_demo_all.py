#!/usr/bin/env python3
"""Full pure-protocol demo: listen (short) + send + context + orders."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

USER = "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk"


async def main() -> int:
    os.environ.setdefault("PIGEON_STANDALONE", "1")
    from pigeon_protocol.config import AppConfig
    from pigeon_protocol.standalone import StandaloneRuntime
    from pigeon_protocol.ws_template_harvest import text_for_byte_length

    client = StandaloneRuntime(config=AppConfig(dry_run=False))
    report: dict = {"health": client.health()}

    # context
    ctx = client.get_context(USER)
    report["context"] = {
        "messages": len(ctx.messages),
        "source": ctx.source,
        "preview": [m.get("text", "")[:50] for m in ctx.messages[:3]],
    }

    # orders (offline cache / HAR / user_card chain)
    orders = client.get_orders(USER)
    report["orders"] = {
        "has_order": orders.has_order,
        "source": orders.source,
        "summary": orders.summary,
        "code": ((orders.raw or {}).get("data") or {}).get("code") if isinstance(orders.raw, dict) else None,
    }

    # send 18B
    send_text = "您好，已收到您的消息"
    if len(send_text.encode("utf-8")) != 18:
        send_text = text_for_byte_length(18)
    send_r = client.send_text(send_text, security_user_id=USER)
    report["send"] = {
        "ok": send_r.ok,
        "mode": send_r.mode,
        "payload_length": send_r.payload_length,
        "ack_len": (send_r.raw or {}).get("ack_len"),
    }

    # listen 8s
    seen: list[dict] = []

    def on_msg(msg):
        seen.append({"role": msg.role, "text": msg.text[:80]})

    try:
        await client.listen(on_msg, timeout_sec=8)
    except Exception as exc:
        report["listen_error"] = str(exc)
    report["listen"] = {"messages": len(seen), "preview": seen[:5]}

    out = ROOT / "analysis" / "pure_demo_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    ok = report["context"]["messages"] > 0 and report["send"]["ok"]
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
