#!/usr/bin/env python3
"""Extract bdms fetch wrapper source + collect multi-URL sign samples."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

INIT_HOOK = r"""
() => {
  if (!window.__origFetchSaved) {
    window.__origFetchSaved = window.fetch.bind(window);
    window.__origXHROpen = XMLHttpRequest.prototype.open;
    window.__origXHRSend = XMLHttpRequest.prototype.send;
  }
}
"""

EXTRACT_JS = r"""
() => {
  const fetchSrc = Function.prototype.toString.call(window.fetch);
  const bdms = window.bdms;
  return {
    fetchLength: fetchSrc.length,
    fetchHead: fetchSrc.slice(0, 3000),
    fetchTail: fetchSrc.slice(-1500),
    isNative: fetchSrc.includes('[native code]'),
    bdmsInit: bdms?.init?.toString?.().slice(0, 800),
    bdmsGetReferer: bdms?.getReferer?.toString?.(),
    hasOrigFetch: !!window.__origFetchSaved,
    origIsNative: window.__origFetchSaved ? Function.prototype.toString.call(window.__origFetchSaved).includes('[native code]') : null,
    xhrOpenHead: XMLHttpRequest.prototype.open ? Function.prototype.toString.call(XMLHttpRequest.prototype.open).slice(0, 2000) : null,
    scripts: [...document.scripts].map(s => s.src).filter(Boolean).filter(u => /bdms|mssdk|secsdk|security|acrawler/i.test(u)),
  };
}
"""

async def collect_samples(page, urls: list[str]) -> list[dict]:
    samples = []
    for url in urls:
        js = """
        async (u) => {
          const r = await fetch(u, { method: 'GET', credentials: 'include' });
          return { in: u, out: (r.url || u).slice(0, 2000) };
        }
        """
        try:
            row = await page.evaluate(js, url)
            qs_out = parse_qs(urlparse(row["out"]).query)
            samples.append({
                "url_in": url,
                "url_out": row["out"],
                "a_bogus": (qs_out.get("a_bogus") or [""])[0],
                "msToken": (qs_out.get("msToken") or [""])[0][:80],
                "verifyFp": (qs_out.get("verifyFp") or [""])[0][:60],
            })
        except Exception as exc:
            samples.append({"url_in": url, "error": str(exc)})
        await asyncio.sleep(0.3)
    return samples


async def main(port: int = 9222) -> dict:
    from playwright.async_api import async_playwright

    report: dict = {"ok": False}
    test_urls = [
        "https://pigeon.jinritemai.com/backstage/getRobot?biz_type=4&PIGEON_BIZ_TYPE=2&_pms=1&device_platform=web&FUSION=true",
        "https://pigeon.jinritemai.com/backstage/global/searchTips?biz_type=4&PIGEON_BIZ_TYPE=2&_pms=1&device_platform=web&FUSION=true",
        "https://pigeon.jinritemai.com/backstage/cmpoent/order/query?biz_type=4&PIGEON_BIZ_TYPE=2&_pms=1&device_platform=web&FUSION=true&_v=1.0.1.7626",
    ]

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        ctx = browser.contexts[0]
        page = next((pg for pg in ctx.pages if "jinritemai" in (pg.url or "")), ctx.pages[0])

        await page.evaluate(EXTRACT_JS)
        report["extract"] = await page.evaluate(EXTRACT_JS)
        report["samples"] = await collect_samples(page, test_urls)

        # try calling sign via hidden webpack require — heuristic
        webpack_probe = await page.evaluate(r"""
        () => {
          const res = { candidates: [] };
          for (const k of Object.getOwnPropertyNames(window)) {
            const v = window[k];
            if (typeof v === 'object' && v && v.default && typeof v.default.init === 'function') {
              res.candidates.push(k);
            }
          }
          // performance scripts
          res.inlineHints = [...document.scripts]
            .map(s => s.textContent || '')
            .filter(t => t.length > 100 && /a_bogus|bdms|mstoken/i.test(t))
            .map(t => t.slice(0, 200));
          return res;
        }
        """)
        report["webpack_probe"] = webpack_probe
        report["ok"] = True

    out = ROOT / "analysis" / "sign_extract.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # save fetch wrapper for manual review
    fetch_head = report.get("extract", {}).get("fetchHead", "")
    if fetch_head:
        (ROOT / "analysis" / "fetch_wrapper.js").write_text(fetch_head, encoding="utf-8")

    return report


if __name__ == "__main__":
    r = asyncio.run(main())
    print(json.dumps({k: v for k, v in r.items() if k != "extract"}, ensure_ascii=False, indent=2))
    ex = r.get("extract", {})
    print("\n--- fetch wrapper head (200 chars) ---")
    print((ex.get("fetchHead") or "")[:200])
    print("isNative:", ex.get("isNative"))
