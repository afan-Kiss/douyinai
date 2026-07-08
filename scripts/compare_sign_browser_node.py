#!/usr/bin/env python3
"""Compare browser vs Node bdms sign tokens + order API result."""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


async def browser_sign(unsigned: str, body: dict) -> dict:
    from playwright.async_api import async_playwright
    from urllib.parse import parse_qs, urlparse

    js = """
    async (p) => {
      const r = await fetch(p.url, {
        method: 'POST', credentials: 'include',
        headers: { 'content-type': 'application/json;charset=UTF-8' },
        body: JSON.stringify(p.body),
      });
      return { finalUrl: r.url, status: r.status };
    }
    """
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = next(
            (pg for pg in browser.contexts[0].pages if "jinritemai" in (pg.url or "")),
            browser.contexts[0].pages[0],
        )
        r = await page.evaluate(js, {"url": unsigned, "body": body})
        qs = parse_qs(urlparse(r["finalUrl"]).query)
        return {
            "finalUrl": r["finalUrl"][:500],
            "status": r["status"],
            "tokens": {k: (qs.get(k) or [""])[0][:80] for k in ("verifyFp", "msToken", "a_bogus")},
        }


def node_sign(unsigned: str, body: dict) -> dict:
    proc = subprocess.run(
        ["node", str(ROOT / "scripts" / "run_bdms_fetch.mjs"), unsigned, json.dumps(body, ensure_ascii=False)],
        capture_output=True,
        text=True,
        timeout=45,
        cwd=str(ROOT),
    )
    return json.loads(proc.stdout)


def order_test(signed_url: str, body: dict) -> dict:
    import sys

    sys.path.insert(0, str(ROOT / "src"))
    from pigeon_protocol.http_client import BackstageHttpClient, DEFAULT_CURL_IMPERSONATE
    from pigeon_protocol.http_transport import order_api_ok, request_json
    from pigeon_protocol.session import load_session

    session = load_session()
    client = BackstageHttpClient(session, dry_run=False)
    hdr = client._headers(browser_hints=True)
    hdr["Cookie"] = session.cookie_header()
    raw = request_json(
        "POST",
        signed_url,
        headers=hdr,
        json_body=body,
        transport="curl_cffi",
        impersonate=DEFAULT_CURL_IMPERSONATE,
    )
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    return {"code": data.get("code"), "ok": order_api_ok(raw)}


async def main() -> None:
    unsigned = (
        "https://pigeon.jinritemai.com/backstage/cmpoent/order/query"
        "?biz_type=4&PIGEON_BIZ_TYPE=2&_pms=1&device_platform=web&FUSION=true&_v=1.0.1.7626"
    )
    body = {
        "security_user_id": "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk",
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

    report = {"browser": None, "node": None}
    try:
        report["browser"] = await browser_sign(unsigned, body)
        report["browser"]["order"] = order_test(report["browser"]["finalUrl"], body)
    except Exception as e:
        report["browser_error"] = str(e)

    report["node"] = node_sign(unsigned, body)
    if report["node"].get("signedUrl"):
        report["node"]["order"] = order_test(report["node"]["signedUrl"], body)

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
