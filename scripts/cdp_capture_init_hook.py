#!/usr/bin/env python3
"""Capture bdms.init(options) on fresh page load via init script hook."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

INIT_HOOK = r"""
(() => {
  let _bdms;
  Object.defineProperty(window, 'bdms', {
    configurable: true,
    enumerable: true,
    get() { return _bdms; },
    set(v) {
      _bdms = v;
      if (v && typeof v.init === 'function' && !v.__wrapped) {
        const orig = v.init.bind(v);
        v.init = function(cfg) {
          window.__bdmsInitConfig = JSON.parse(JSON.stringify(cfg || {}));
          return orig(cfg);
        };
        v.__wrapped = true;
      }
    },
  });
})();
"""


async def main(port: int = 9222) -> dict:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        ctx = browser.contexts[0]
        await ctx.add_init_script(INIT_HOOK)
        page = await ctx.new_page()
        await page.goto("https://im.jinritemai.com/pc_seller_v2/main", wait_until="networkidle", timeout=90000)
        cfg = await page.evaluate("() => window.__bdmsInitConfig || null")
        await page.close()
        return {"config": cfg}


if __name__ == "__main__":
    r = asyncio.run(main())
    out = ROOT / "analysis" / "bdms_init_live.json"
    out.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(r, ensure_ascii=False, indent=2))
