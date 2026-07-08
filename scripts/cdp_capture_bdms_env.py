#!/usr/bin/env python3
"""Capture localStorage + secsdk + order relay headers for Node bdms env."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "analysis" / "bdms_browser_env.json"

CAPTURE_JS = r"""
() => {
  const ls = {};
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    if (/token|ms|bogus|fp|bdms|sec|ac_|web_id|__tea|xmst|SLARDAR/i.test(k)) ls[k] = localStorage.getItem(k)?.slice(0, 500);
  }
  const ss = {};
  for (let i = 0; i < sessionStorage.length; i++) {
    const k = sessionStorage.key(i);
    if (/token|ms|bogus|fp|bdms|sec|ac_|web_id|__tea/i.test(k)) ss[k] = sessionStorage.getItem(k)?.slice(0, 500);
  }
  return {
    localStorage: ls,
    sessionStorage: ss,
    secsdkKeys: window.secsdk ? Object.keys(window.secsdk) : [],
    csrfToken: document.cookie.match(/csrf_session_id=([^;]+)/)?.[1] || null,
    scripts: [...document.scripts].map(s => s.src).filter(u => /bdms|secsdk|security/i.test(u)),
  };
}
"""

ORDER_URL = (
    "https://pigeon.jinritemai.com/backstage/cmpoent/order/query"
    "?biz_type=4&PIGEON_BIZ_TYPE=2&_pms=1&device_platform=web&FUSION=true&_v=1.0.1.7626"
)


async def main() -> None:
    from pigeon_protocol.order_curl_relay import cdp_sign_order_request_sync
    from pigeon_protocol.pure_runtime import _order_body

    from playwright.async_api import async_playwright

    uid = "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk"

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = next(
            (pg for pg in browser.contexts[0].pages if "jinritemai" in (pg.url or "")),
            browser.contexts[0].pages[0],
        )
        data = await page.evaluate(CAPTURE_JS)

    cap = cdp_sign_order_request_sync(ORDER_URL, _order_body(uid))
    hdr = dict(cap.get("headers") or {})
    for drop in ("content-length", "host", ":authority", ":method", ":path", ":scheme"):
        hdr.pop(drop, None)

    data["relayHeaders"] = hdr
    data["csrfHeader"] = hdr.get("x-secsdk-csrf-token")

    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"csrfHeader": data.get("csrfHeader", "")[:80], "relayKeys": sorted(hdr.keys())}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
