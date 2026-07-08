#!/usr/bin/env python3
"""Test curl_relay order path."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

USER = "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk"


def main() -> None:
    from pigeon_protocol.order_curl_relay import query_orders_via_curl_relay
    from pigeon_protocol.order_parse import parse_order_response
    from pigeon_protocol.session import load_session

    raw = query_orders_via_curl_relay(load_session(), USER)
    ctx = parse_order_response(raw, source="curl_relay/test")
    print(json.dumps({"raw_ok": raw.get("ok"), "code": (raw.get("data") or {}).get("code"), "ctx": ctx.summary, "via": raw.get("via")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
