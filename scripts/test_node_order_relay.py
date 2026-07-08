#!/usr/bin/env python3
"""Verify Node/jsdom a_bogus + relay headers → order code 0."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

USER = "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk"


def main() -> int:
    from pigeon_protocol.http_transport import order_api_ok
    from pigeon_protocol.order_node_relay import query_orders_via_node_relay
    from pigeon_protocol.order_relay_headers import load_relay_header_template
    from pigeon_protocol.session import load_session

    hdr = load_relay_header_template()
    if not hdr:
        print("WARN: no relayHeaders — run: python scripts/cdp_capture_bdms_env.py", file=sys.stderr)

    raw = query_orders_via_node_relay(load_session(), USER)
    out = {
        "ok": order_api_ok(raw),
        "code": (raw.get("data") or {}).get("code"),
        "via": raw.get("via"),
        "has_relay_headers": bool(hdr),
        "node_sign": raw.get("_node_sign"),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
