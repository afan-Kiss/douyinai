#!/usr/bin/env python3
"""Test CDP sign_tokens + curl_cffi + browser headers for orders."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

USER = "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk"


def main() -> None:
    from pigeon_protocol.config import ORDER_QUERY_PATH, PIGEON_HOST
    from pigeon_protocol.http_client import BackstageHttpClient
    from pigeon_protocol.http_transport import request_json, curl_cffi_available
    from pigeon_protocol.session import load_session, save_session, build_signed_url
    from pigeon_protocol.sign import CdpSigner

    session = load_session()
    unsigned = f"{PIGEON_HOST}{ORDER_QUERY_PATH}?biz_type=4&PIGEON_BIZ_TYPE=2&_pms=1&device_platform=web&FUSION=true&_v=1.0.1.7626"
    body = {
        "security_user_id": USER,
        "page_no": 0,
        "page_size": 5,
        "tab_type": 1,
        "biz_type": 2,
        "version": "1.0",
        "workstation_opt_version": "v2",
        "workstation_opt_gray": True,
        "open_params": {},
        "service_entity_id": "",
        "search_words": "",
        "is_init_tab": 0,
    }

    client = BackstageHttpClient(session, dry_run=False)
    report: dict = {}

    if CdpSigner.available():
        tokens = CdpSigner().sign_tokens(unsigned, method="POST", body=body)
        session.query_tokens.update(tokens)
        save_session(session)
        signed = build_signed_url(unsigned, session)
        hdr = client._headers(browser_hints=True)
        report["cdp_sign_httpx"] = (request_json("POST", signed, headers=hdr, json_body=body, transport="httpx").get("data") or {}).get("code")
        if curl_cffi_available():
            report["cdp_sign_curl131"] = (request_json("POST", signed, headers=hdr, json_body=body, transport="curl_cffi", impersonate="chrome131").get("data") or {}).get("code")
            report["cdp_sign_curl142"] = (request_json("POST", signed, headers=hdr, json_body=body, transport="curl_cffi", impersonate="chrome142").get("data") or {}).get("code")

    # via BackstageHttpClient after token refresh
    session = load_session()
    client2 = BackstageHttpClient(load_session(), dry_run=False, use_cdp_sign=False, use_curl_cffi=True)
    r = client2.query_orders(USER)
    report["client_query"] = {"source": r.source, "code": (r.raw.get("data") or {}).get("code"), "summary": r.summary}

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
