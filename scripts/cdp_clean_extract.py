#!/usr/bin/env python3
"""Reload Feige page cleanly and extract native bdms fetch patch (before our hooks)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXTRACT = r"""
() => ({
  fetchNative: Function.prototype.toString.call(window.fetch).includes('[native code]'),
  fetchLen: Function.prototype.toString.call(window.fetch).length,
  fetchSrc: Function.prototype.toString.call(window.fetch).slice(0, 6000),
  xhrOpenNative: Function.prototype.toString.call(XMLHttpRequest.prototype.open).includes('[native code]'),
  xhrOpenSrc: Function.prototype.toString.call(XMLHttpRequest.prototype.open).slice(0, 4000),
  xhrSendSrc: Function.prototype.toString.call(XMLHttpRequest.prototype.send).slice(0, 2000),
  bdms: !!window.bdms,
})
"""


async def main(port: int = 9222) -> dict:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        ctx = browser.contexts[0]
        page = await ctx.new_page()
        await page.goto("https://im.jinritemai.com/pc_seller_v2/main", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(8000)
        data = await page.evaluate(EXTRACT)
        await page.close()
        return data


if __name__ == "__main__":
    r = asyncio.run(main())
    out = ROOT / "analysis" / "bdms_fetch_patch.json"
    out.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    if r.get("fetchSrc"):
        (ROOT / "analysis" / "fetch_patch_clean.js").write_text(r["fetchSrc"], encoding="utf-8")
    print("fetchNative:", r.get("fetchNative"))
    print("fetchLen:", r.get("fetchLen"))
    print("xhrOpenNative:", r.get("xhrOpenNative"))
    print("head:", (r.get("fetchSrc") or "")[:300])
