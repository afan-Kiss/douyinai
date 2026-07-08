#!/usr/bin/env python3
"""Compare browser fetch vs httpx order request — headers, cookies, URL."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

USER = "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk"

ORDER_JS = r"""
async (payload) => {
  const { security_user_id } = payload;
  const url = 'https://pigeon.jinritemai.com/backstage/cmpoent/order/query?biz_type=4&PIGEON_BIZ_TYPE=2&_pms=1&device_platform=web&FUSION=true&_v=1.0.1.7626';
  const body = {
    security_user_id, page_no: 0, page_size: 5, tab_type: 1, biz_type: 2, version: '1.0',
    workstation_opt_version: 'v2', workstation_opt_gray: true, open_params: {}, service_entity_id: '',
    search_words: '', is_init_tab: 0,
  };
  const r = await fetch(url, {
    method: 'POST', credentials: 'include',
    headers: { 'content-type': 'application/json;charset=UTF-8' },
    body: JSON.stringify(body),
  });
  const j = await r.json();
  return { status: r.status, finalUrl: (r.url||url).slice(0,2000), code: j.code, msg: j.msg };
}
"""


async def capture_browser_request() -> dict:
    from playwright.async_api import async_playwright

    captured: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = next(pg for pg in browser.contexts[0].pages if "jinritemai" in (pg.url or ""))

        def on_req(req):
            if "order/query" in (req.url or ""):
                captured.append({
                    "url": req.url[:2000],
                    "method": req.method,
                    "headers": dict(req.headers),
                    "post_data": (req.post_data or "")[:500],
                })

        page.on("request", on_req)
        result = await page.evaluate(ORDER_JS, {"security_user_id": USER})
        await asyncio.sleep(0.3)
        return {"fetch_result": result, "requests": captured}


def httpx_attempt(signed_url: str, body: dict, headers: dict) -> dict:
    from pigeon_protocol.http_transport import request_json

    return request_json("POST", signed_url, headers=headers, json_body=body, transport="httpx")


async def main() -> None:
    from pigeon_protocol.config import ORDER_QUERY_PATH, PIGEON_HOST
    from pigeon_protocol.http_client import BackstageHttpClient
    from pigeon_protocol.session import load_session, build_signed_url

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

    browser = await capture_browser_request()
    req = browser["requests"][-1] if browser.get("requests") else {}
    final_url = req.get("url") or browser["fetch_result"].get("finalUrl", "")

    # sync tokens from browser URL
    qs = parse_qs(urlparse(final_url).query)
    for k in ("verifyFp", "fp", "msToken", "a_bogus"):
        if qs.get(k):
            session.query_tokens[k] = qs[k][0]

    client = BackstageHttpClient(session, dry_run=False)
    py_headers = client._headers()

    # Attempt 1: python default headers + browser signed URL
    r1 = httpx_attempt(final_url, body, py_headers)

    # Attempt 2: merge browser request headers (minus host/content-length)
    browser_headers = dict(req.get("headers") or {})
    for drop in ("host", "content-length", "connection", ":authority", ":method", ":path", ":scheme"):
        browser_headers.pop(drop, None)
    r2 = httpx_attempt(final_url, body, browser_headers)

    # Attempt 3: browser headers + exact post from browser
    r3 = httpx_attempt(final_url, body, browser_headers)

    report = {
        "browser_fetch": browser["fetch_result"],
        "browser_header_keys": sorted((req.get("headers") or {}).keys()),
        "python_header_keys": sorted(py_headers.keys()),
        "browser_only_headers": sorted(set(browser_headers) - set(py_headers)),
        "attempts": {
            "python_headers": {"code": (r1.get("data") or {}).get("code"), "msg": (r1.get("data") or {}).get("msg")},
            "browser_headers": {"code": (r2.get("data") or {}).get("code"), "msg": (r2.get("data") or {}).get("msg")},
        },
        "a_bogus_from_browser": (qs.get("a_bogus") or [""])[0][:40],
    }
    out = ROOT / "analysis" / "order_request_diff.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
