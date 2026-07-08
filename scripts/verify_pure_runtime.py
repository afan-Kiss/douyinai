#!/usr/bin/env python3
"""End-to-end pure-protocol verification — no CDP required."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("PIGEON_STANDALONE", "1")


def main() -> int:
    from pigeon_protocol.foundation.pure_prepare import prepare_pure_runtime
    from pigeon_protocol.foundation.ws_blob_compute import compute_inner_bytes, inner_class_for_text_b
    from pigeon_protocol.foundation.ws_sign_engine import ComputedBlobStrategy
    from pigeon_protocol.session import load_session
    from pigeon_protocol.standalone import StandaloneRuntime
    from pigeon_protocol.ws_sign import locate_signature_region
    from pigeon_protocol.ws_sign_decode import decode_blob

    session = load_session()
    report: dict = {"checks": []}

    prep = prepare_pure_runtime(session, probe_ws=False)
    report["prepare"] = {"ok": prep.get("ok"), "steps": prep.get("steps")}
    report["checks"].append(("prepare_pure", prep.get("ok", False)))

    strat = ComputedBlobStrategy()
    send_ok = True
    for text in ["你好", "好的好的", "好" * 25]:
        bl = len(text.encode("utf-8"))
        ic = inner_class_for_text_b(bl)
        try:
            frame = strat.build_frame(text, session=session, security_user_id="AQtest", shop_id="263636465")
            region = locate_signature_region(frame)
            inner = decode_blob(region.blob) if region else b""
            expected = compute_inner_bytes(session, bl, bootstrap=False)
            ok = inner == expected and len(frame) > 2000
            report["checks"].append((f"send_build_{bl}B", ok))
            if not ok:
                send_ok = False
        except Exception as exc:
            report["checks"].append((f"send_build_{bl}B", False))
            report.setdefault("errors", []).append(f"textB={bl}: {exc}")
            send_ok = False
    report["send_build"] = send_ok

    rt = StandaloneRuntime()
    health = rt.health()
    report["standalone"] = {
        "pure_ready": health.get("pure_ready"),
        "blockers": health.get("blockers"),
        "template_gaps": health.get("template_gaps"),
    }
    report["checks"].append(("standalone_ready", not health.get("blockers")))

    failed = [name for name, ok in report["checks"] if not ok]
    report["ok"] = not failed
    report["failed"] = failed

    out = ROOT / "analysis" / "verify_pure_runtime.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
