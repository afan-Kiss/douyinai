#!/usr/bin/env python3
"""Dump bdms fetch hook internals from live Feige page for offline RE."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "analysis" / "bdms_fetch_internals.json"

PROBE_JS = r"""
() => {
  const out = {
    fetchHead: window.fetch?.toString?.().slice(0, 500),
    fetchName: window.fetch?.name,
    xhrOpenHead: XMLHttpRequest.prototype.open?.toString?.().slice(0, 300),
    bdmsKeys: window.bdms ? Object.keys(window.bdms) : [],
    getRefererHead: window.bdms?.getReferer?.toString?.().slice(0, 200),
    initHead: window.bdms?.init?.toString?.().slice(0, 200),
    // Scan window for bdms-related closures
    windowHits: [],
    storageKeys: [],
  };

  for (const k of Object.keys(window)) {
    if (!/bdms|bogus|secsdk|acrawler|byted/i.test(k)) continue;
    const v = window[k];
    out.windowHits.push({ k, type: typeof v, head: typeof v === "function" ? v.toString().slice(0, 120) : null });
  }

  try {
    out.storageKeys = Object.keys(localStorage).filter(k => /bdms|token|fp|bogus/i.test(k)).slice(0, 30);
  } catch (e) {
    out.storageKeys = ["err:" + String(e)];
  }

  // Compare native vs hooked fetch
  const iframe = document.createElement("iframe");
  iframe.style.display = "none";
  document.body.appendChild(iframe);
  try {
    out.iframeFetchHead = iframe.contentWindow.fetch.toString().slice(0, 200);
    out.fetchIsHooked = out.fetchHead !== out.iframeFetchHead;
  } catch (e) {
    out.iframeErr = String(e);
  }
  iframe.remove();

  // Try to find sign state on bdms module via getReferer call
  try {
    out.referer = window.bdms?.getReferer?.();
  } catch (e) {
    out.refererErr = String(e);
  }

  return out;
}
"""


async def main() -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = next(
            (pg for pg in browser.contexts[0].pages if "jinritemai" in (pg.url or "")),
            browser.contexts[0].pages[0],
        )
        data = await page.evaluate(PROBE_JS)
        OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
