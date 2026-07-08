#!/usr/bin/env python3
"""Probe WS send for gap textB lengths — build + optional live cross-bucket test."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

USER_ID = "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk"

# (textB, forced_template_len) — experimental cross-bucket probes
PROBE_MATRIX = [
    (7, 6),
    (8, 6),
    (61, 60),
    (65, 60),
    (79, 77),
    (79, 78),
]


def exact_text(bl: int) -> str:
    return "a" * bl


def main() -> int:
    from pigeon_protocol.capture_loader import find_send_template
    from pigeon_protocol.config import AppConfig
    from pigeon_protocol.foundation.ws_sign_engine import WsSendEngine
    from pigeon_protocol.parsers.ws_frame_builder import WSFrameBuilder
    from pigeon_protocol.standalone import StandaloneRuntime
    from pigeon_protocol.ws_sign_bucket import GAP_PROBE, same_inner_bucket
    from pigeon_protocol.ws_session import WsSession

    dry = os.getenv("DRY", "1") != "0"
    rt = StandaloneRuntime(config=AppConfig(dry_run=dry))
    engine = WsSendEngine()
    out: list[dict] = []

    for bl, tpl_len in PROBE_MATRIX:
        text = exact_text(bl)
        assert len(text.encode("utf-8")) == bl
        row = {
            "textB": bl,
            "forced_tpl": tpl_len,
            "same_inner_as_tpl": same_inner_bucket(bl, tpl_len),
            "engine_can_send": engine.active().can_send(text),
        }
        tpl = find_send_template(byte_len=tpl_len)
        if not tpl:
            row["build_ok"] = False
            row["error"] = f"no template b{tpl_len:03d}"
            out.append(row)
            continue
        try:
            payload = WSFrameBuilder.from_template_dict(tpl).build_pure(
                text,
                security_user_id=USER_ID,
                shop_id=rt.session.shop_id or "263636465",
                ws_url=rt.pick_ws_url(),
                session=rt.session,
            )
            row["build_ok"] = True
            row["payload_len"] = len(payload)
        except Exception as exc:
            row["build_ok"] = False
            row["error"] = str(exc)

        if not dry and row.get("build_ok"):
            r = WsSession(rt.session).send_bytes_sync(payload, ws_url=rt.pick_ws_url())
            row["send_ok"] = r.ok
            row["mode"] = r.mode
            row["reason"] = r.reason

        out.append(row)

    report = {"dry_run": dry, "probes": out, "known_gaps": list(GAP_PROBE)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
