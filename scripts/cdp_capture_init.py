#!/usr/bin/env python3
"""Extract bdms.init config from live Feige page."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

JS = r"""
() => {
  const out = { inits: [], hints: [] };
  // search inline scripts for aid/pageId
  for (const s of document.scripts) {
    const t = s.textContent || '';
    if (/bdms|pageId|paths|aid|1383/.test(t)) out.hints.push(t.slice(0, 1200));
  }
  // try to intercept next bdms.init
  if (window.bdms && !window.__initCaptured) {
    const orig = window.bdms.init;
    window.bdms.init = function(cfg) {
      out.inits.push(cfg);
      window.__lastBdmsInit = cfg;
      return orig.apply(this, arguments);
    };
    window.__initCaptured = true;
  }
  if (window.__lastBdmsInit) out.lastInit = window.__lastBdmsInit;
  return out;
}
"""


async def main(port: int = 9222) -> dict:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        page = await browser.contexts[0].new_page()
        await page.goto("https://im.jinritemai.com/pc_seller_v2/main", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(6000)
        data = await page.evaluate(JS)
        await page.close()
        return data


if __name__ == "__main__":
    r = asyncio.run(main())
    out = ROOT / "analysis" / "bdms_init_config.json"
    out.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(r, ensure_ascii=False, indent=2)[:4000])
