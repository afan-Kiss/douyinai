#!/usr/bin/env python3
"""Test: CDP sign tokens → httpx order query (diagnose 10001010A)."""
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
    from pigeon_protocol.session import load_session, save_session, build_signed_url
    from pigeon_protocol.sign import CdpSigner, apply_sign_tokens, parse_sign_tokens

    session = load_session()
    unsigned = f"{PIGEON_HOST}{ORDER_QUERY_PATH}?biz_type=4&PIGEON_BIZ_TYPE=2&_pms=1&device_platform=web&FUSION=true&_v=1.0.1.7626"
    body = {
        "security_user_id": USER,
        "page_no": 0,
        "page_size": 5,
        "search_words": "",
        "is_init_tab": 0,
        "tab_type": 1,
        "biz_type": 2,
        "open_params": {},
        "workstation_opt_version": "v2",
        "service_entity_id": "",
        "version": "1.0",
        "workstation_opt_gray": True,
    }

    report: dict = {}

    # A) raw httpx with session tokens
    client = BackstageHttpClient(session, dry_run=False)
    r1 = client.query_orders(USER)
    report["session_tokens"] = {
        "source": r1.source,
        "code": (r1.raw.get("data") or {}).get("code") if isinstance(r1.raw, dict) else None,
        "summary": r1.summary,
    }

    # B) fresh CDP sign then httpx
    if CdpSigner.available():
        tokens = CdpSigner().sign_tokens(unsigned, method="POST", body=body)
        session.query_tokens.update(tokens)
        save_session(session)
        signed = build_signed_url(unsigned, session)
        client2 = BackstageHttpClient(load_session(), dry_run=False)
        raw = client2._request("POST", signed, json_body=body)
        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        report["cdp_sign_httpx"] = {
            "ok": raw.get("ok"),
            "code": data.get("code"),
            "msg": data.get("msg"),
            "a_bogus_prefix": tokens.get("a_bogus", "")[:24],
            "transport": raw.get("transport"),
        }

        # C) curl_cffi after sign
        raw3 = client2._request("POST", signed, json_body=body, transport="curl_cffi")
        data3 = raw3.get("data") if isinstance(raw3.get("data"), dict) else {}
        report["cdp_sign_curl"] = {"ok": raw3.get("ok"), "code": data3.get("code"), "msg": data3.get("msg")}

        # D) CDP full fetch (baseline)
        from pigeon_protocol.cdp_bridge import CdpBridge

        cdp_raw = CdpBridge(session).query_orders(USER)
        inner = cdp_raw.get("data") if isinstance(cdp_raw.get("data"), dict) else {}
        report["cdp_fetch"] = {"ok": cdp_raw.get("ok"), "code": inner.get("code"), "via": cdp_raw.get("via")}

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
