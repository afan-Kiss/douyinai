#!/usr/bin/env python3
"""A/B: browser vs node a_bogus on order API."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

USER = "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk"
UNSIGNED = (
    "https://pigeon.jinritemai.com/backstage/cmpoent/order/query"
    "?biz_type=4&PIGEON_BIZ_TYPE=2&_pms=1&device_platform=web&FUSION=true&_v=1.0.1.7626"
)


def order_body() -> dict:
    return {
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


def curl_post(url: str, headers: dict, body: dict) -> dict:
    from pigeon_protocol.http_client import DEFAULT_CURL_IMPERSONATE
    from pigeon_protocol.http_transport import order_api_ok, request_json

    raw = request_json(
        "POST",
        url,
        headers=headers,
        json_body=body,
        transport="curl_cffi",
        impersonate=DEFAULT_CURL_IMPERSONATE,
    )
    return {
        "ok": order_api_ok(raw),
        "code": (raw.get("data") or {}).get("code"),
        "status": raw.get("status"),
    }


def node_sign() -> dict:
    body = order_body()
    body_str = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    proc = subprocess.run(
        ["node", str(ROOT / "scripts" / "run_bdms_fetch.mjs"), UNSIGNED, body_str],
        capture_output=True,
        text=True,
        timeout=45,
        cwd=str(ROOT),
    )
    return json.loads(proc.stdout)


async def cdp_sign() -> dict:
    from pigeon_protocol.order_curl_relay import cdp_sign_order_request_sync

    cap = cdp_sign_order_request_sync(UNSIGNED, order_body())
    return cap


async def main() -> int:
    from pigeon_protocol.session import load_session

    session = load_session()
    body = order_body()

    print("=== refresh browser env (optional) ===")
    node = node_sign()
    print("node tokens:", {k: (node.get(k) or "")[:48] for k in ("verifyFp", "msToken", "a_bogus")})

    cdp = await cdp_sign()
    cdp_url = cdp.get("url") or UNSIGNED
    cdp_hdr = dict(cdp.get("headers") or {})
    print("cdp url has bogus:", "a_bogus=" in cdp_url)
    print("cdp hdr keys:", sorted(cdp_hdr.keys()))

    node_url = node.get("signedUrl") or UNSIGNED

    tests = []

    # 1) CDP baseline
    tests.append(("cdp_url+cdp_hdr", curl_post(cdp_url, cdp_hdr, body)))

    # 2) Node URL + CDP headers (isolate a_bogus quality)
    hdr2 = dict(cdp_hdr)
    tests.append(("node_url+cdp_hdr", curl_post(node_url, hdr2, body)))

    # 3) Node URL + CDP hdr + CDP cookie only
    tests.append(("node_url+cdp_hdr_cookie", curl_post(node_url, hdr2, body)))

    # 4) CDP URL + node a_bogus swapped in
    from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

    parsed = urlparse(cdp_url)
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if node.get("a_bogus"):
        params["a_bogus"] = node["a_bogus"]
    if node.get("msToken"):
        params["msToken"] = node["msToken"]
    hybrid_url = urlunparse(parsed._replace(query=urlencode(params)))
    tests.append(("cdp_url+node_bogus+cdp_hdr", curl_post(hybrid_url, cdp_hdr, body)))

    # 5) Node URL + minimal session headers
    from pigeon_protocol.http_client import BackstageHttpClient

    client = BackstageHttpClient(session, dry_run=False)
    hdr5 = client._headers(browser_hints=True)
    hdr5["Cookie"] = session.cookie_header()
    env_path = ROOT / "analysis" / "bdms_browser_env.json"
    if env_path.exists():
        env = json.loads(env_path.read_text(encoding="utf-8"))
        csrf = env.get("csrfToken")
        if csrf:
            hdr5["x-secsdk-csrf-token"] = f"000100000001{csrf},{csrf}"
    tests.append(("node_url+session_hdr+csrf", curl_post(node_url, hdr5, body)))

    report = {"node": {k: node.get(k) for k in ("ok", "status")}, "tests": dict(tests)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if any(v.get("code") == 0 or v.get("code") == "0" for _, v in tests if "node_url+cdp_hdr" in _) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
