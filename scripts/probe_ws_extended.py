#!/usr/bin/env python3
"""Probe 61-76 / 79+ WS send using bucket-B canonical or explicit templates."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

USER_ID = "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk"


def probe_build(lengths: list[int], template_len: int) -> list[dict]:
    from pigeon_protocol.capture_loader import find_send_template, load_capture
    from pigeon_protocol.parsers.ws_frame_builder import WSFrameBuilder
    from pigeon_protocol.session import load_session
    from pigeon_protocol.ws_template_harvest import text_for_byte_length

    ev = find_send_template(byte_len=template_len)
    if not ev:
        raise RuntimeError(f"template b{template_len:03d} missing")
    session = load_session()
    out = []
    for bl in lengths:
        text = text_for_byte_length(bl)
        try:
            builder = WSFrameBuilder.from_template_dict(ev)
            payload = builder.build_pure(
                text,
                security_user_id=USER_ID,
                shop_id=session.shop_id or "263636465",
                session=session,
            )
            out.append({"textB": bl, "templateB": template_len, "ok": True, "payload_len": len(payload)})
        except Exception as exc:
            out.append({"textB": bl, "templateB": template_len, "ok": False, "error": str(exc)})
    return out


def probe_live(lengths: list[int], template_len: int) -> list[dict]:
    from pigeon_protocol.capture_loader import find_send_template
    from pigeon_protocol.parsers.ws_frame_builder import WSFrameBuilder
    from pigeon_protocol.session import load_session
    from pigeon_protocol.ws_session_send import send_ws_frame
    from pigeon_protocol.ws_template_harvest import text_for_byte_length

    ev = find_send_template(byte_len=template_len)
    if not ev:
        raise RuntimeError(f"template b{template_len:03d} missing")
    session = load_session()
    out = []
    for bl in lengths:
        text = text_for_byte_length(bl)
        try:
            builder = WSFrameBuilder.from_template_dict(ev)
            payload = builder.build_pure(
                text,
                security_user_id=USER_ID,
                shop_id=session.shop_id or "263636465",
                session=session,
            )
            r = send_ws_frame(payload, session=session)
            out.append({
                "textB": bl,
                "templateB": template_len,
                "ok": r.ok,
                "reason": getattr(r, "reason", ""),
                "payload_len": len(payload),
            })
        except Exception as exc:
            out.append({"textB": bl, "templateB": template_len, "ok": False, "error": str(exc)})
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Probe extended WS lengths with explicit template")
    p.add_argument("--live", action="store_true")
    p.add_argument("--template", type=int, default=9, help="canonical template byte length (default 9 = bucket B)")
    p.add_argument("--lengths", type=str, default="61,63,66,69,72,75,79,90,100")
    args = p.parse_args()

    lengths = [int(x.strip()) for x in args.lengths.split(",") if x.strip()]
    fn = probe_live if args.live else probe_build
    report = {"template": args.template, "lengths": lengths, "results": fn(lengths, args.template)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    ok_count = sum(1 for x in report["results"] if x.get("ok"))
    return 0 if ok_count == len(lengths) else 1


if __name__ == "__main__":
    raise SystemExit(main())
