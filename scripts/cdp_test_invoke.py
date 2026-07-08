#!/usr/bin/env python3
"""Test xhr.bdmsInvokeList trick in live browser (community reverse method)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]

URL = (
    "https://pigeon.jinritemai.com/backstage/cmpoent/order/query"
    "?biz_type=4&PIGEON_BIZ_TYPE=2&_pms=1&device_platform=web&FUSION=true&_v=1.0.1.7626"
)

JS = r"""
(url) => {
  const results = { attempts: [] };
  window.a_bogus = null;

  const tryXhr = (label, list) => {
    try {
      const xhr = new XMLHttpRequest();
      xhr.bdmsInvokeList = list;
      xhr.open('POST', url, true);
      xhr.setRequestHeader('content-type', 'application/json;charset=UTF-8');
      xhr.send('{}');
      results.attempts.push({ label, a_bogus: window.a_bogus, responseURL: xhr.responseURL?.slice(0,500) });
    } catch (e) {
      results.attempts.push({ label, error: String(e) });
    }
  };

  tryXhr('invokeList_v1', [
    { args: ['POST', url, true] },
    { args: ['Accept', 'application/json, text/plain, */*'] },
  ]);

  tryXhr('invokeList_v2', [
    { args: ['POST', url, true], func: function(){} },
    { args: ['Accept', 'application/json, text/plain, */*'], func: function(){} },
  ]);

  // sync open only
  try {
    const xhr2 = new XMLHttpRequest();
    xhr2.open('POST', url, false);
    results.openOnly = xhr2.responseURL || 'no responseURL';
  } catch (e) {
    results.openOnly = String(e);
  }

  return results;
}
"""


async def main(port: int = 9222) -> dict:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        page = next((pg for pg in browser.contexts[0].pages if "jinritemai" in (pg.url or "")), browser.contexts[0].pages[0])

        captured: list[str] = []

        def on_req(req):
            u = req.url or ""
            if "order/query" in u and "a_bogus=" in u:
                captured.append(u)

        page.on("request", on_req)
        result = await page.evaluate(JS, URL)
        result["network"] = captured[-3:]
        if captured:
            qs = parse_qs(urlparse(captured[-1]).query)
            result["parsed"] = {
                "a_bogus": (qs.get("a_bogus") or [""])[0][:80],
                "msToken": (qs.get("msToken") or [""])[0][:60],
            }
        return result


if __name__ == "__main__":
    r = asyncio.run(main())
    out = ROOT / "analysis" / "bdms_invoke_test.json"
    out.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(r, ensure_ascii=False, indent=2))
