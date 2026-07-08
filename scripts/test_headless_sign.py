#!/usr/bin/env python3
"""Verify headless bdms signer produces valid a_bogus + order API."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

USER = "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk"


def main() -> int:
    from pigeon_protocol.headless_signer import HeadlessBdmsSigner
    from pigeon_protocol.http_transport import order_api_ok, request_json
    from pigeon_protocol.pure_runtime import _order_body, _order_unsigned_url
    from pigeon_protocol.session import load_session
    from pigeon_protocol.sign import apply_sign_tokens

    if not HeadlessBdmsSigner.available():
        print("headless signer unavailable (playwright?)", file=sys.stderr)
        return 1

    session = load_session()
    unsigned = _order_unsigned_url()
    body = _order_body(USER)

    signer = HeadlessBdmsSigner()
    tokens = signer.sign_tokens(unsigned, method="POST", body=body)
    signed = apply_sign_tokens(unsigned, tokens)

    from pigeon_protocol.http_client import DEFAULT_CURL_IMPERSONATE

    raw = request_json(
        "POST",
        signed,
        headers={
            "User-Agent": session.user_agent,
            "Cookie": session.cookie_header(),
            "content-type": "application/json;charset=UTF-8",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://im.jinritemai.com/pc_seller_v2/main/workspace",
            "Origin": "https://im.jinritemai.com",
            "sec-ch-ua": '"Chromium";v="131", "Google Chrome";v="131", "Not_A Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        },
        json_body=body,
        transport="curl_cffi",
        impersonate=DEFAULT_CURL_IMPERSONATE,
    )
    print(
        json.dumps(
            {
                "tokens": {k: tokens.get(k, "")[:80] for k in ("a_bogus", "msToken", "verifyFp")},
                "order_ok": order_api_ok(raw),
                "code": (raw.get("data") or {}).get("code"),
                "via": "headless_sign+curl_cffi",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if order_api_ok(raw) else 2


if __name__ == "__main__":
    raise SystemExit(main())
