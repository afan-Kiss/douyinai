#!/usr/bin/env python3
"""Verify WS bucket-canonical coverage + optional live cross-length send."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

USER_ID = "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk"


def build_only(lengths: list[int]) -> list[dict]:
    from pigeon_protocol.foundation.ws_sign_engine import WsSendEngine
    from pigeon_protocol.session import load_session
    from pigeon_protocol.ws_template_harvest import text_for_byte_length

    engine = WsSendEngine()
    session = load_session()
    out = []
    for bl in lengths:
        text = text_for_byte_length(bl) if bl >= 9 else ("好" * max(1, bl // 3))[:bl] or "测"
        while len(text.encode("utf-8")) < bl:
            text += "a"
        text = text.encode("utf-8")[:bl].decode("utf-8", errors="ignore")
        if len(text.encode("utf-8")) != bl:
            text = text_for_byte_length(bl)
        try:
            payload = engine.build_frame(
                text,
                security_user_id=USER_ID,
                shop_id=session.shop_id or "263636465",
                session=session,
            )
            out.append({"textB": bl, "ok": True, "payload_len": len(payload), "can_send": engine.active().can_send(text)})
        except Exception as exc:
            out.append({"textB": bl, "ok": False, "error": str(exc)})
    return out


def live_send(lengths: list[int]) -> list[dict]:
    from pigeon_protocol.pure_runtime import PureProtocolRuntime
    from pigeon_protocol.ws_template_harvest import text_for_byte_length

    rt = PureProtocolRuntime()
    out = []
    for bl in lengths:
        text = text_for_byte_length(bl)
        r = rt.send_text(text, security_user_id=USER_ID)
        out.append({"textB": bl, "text": text[:20], "ok": r.ok, "mode": r.mode, "reason": r.reason, "payload_length": r.payload_length})
    return out


def main() -> int:
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--live", action="store_true")
    args = p.parse_args()

    from pigeon_protocol.ws_sign_bucket import coverage_report

    report = {"coverage": coverage_report()}
    test_lens = [6, 9, 25, 45, 60, 77, 78, 7, 61, 100]
    if args.live:
        report["live"] = live_send([6, 25, 45, 77])
    else:
        report["build"] = build_only(test_lens)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    builds = report.get("build") or report.get("live") or []
    return 0 if all(x.get("ok") for x in builds if x.get("textB") in (6, 9, 25, 45, 60, 77, 78)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
