#!/usr/bin/env python3
"""Capture browser fingerprint values used by bdms VM from live Feige page."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "analysis" / "browser_fingerprint.json"

FP_JS = r"""
() => {
  const canvas = document.createElement("canvas");
  canvas.width = 300; canvas.height = 150;
  const ctx2d = canvas.getContext("2d");
  let canvasData = "";
  if (ctx2d) {
    ctx2d.textBaseline = "top";
    ctx2d.font = "14px Arial";
    ctx2d.fillText("bdms,fingerprint,test", 2, 2);
    canvasData = canvas.toDataURL().slice(0, 120);
  }
  const c2 = document.createElement("canvas");
  const gl = c2.getContext("webgl") || c2.getContext("experimental-webgl");
  let webgl = {};
  if (gl) {
    const dbg = gl.getExtension("WEBGL_debug_renderer_info");
    webgl = {
      vendor: dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : gl.getParameter(0x9245),
      renderer: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(0x9246),
      version: gl.getParameter(0x1f02),
      shading: gl.getParameter(0x8b8c),
    };
  }
  return {
    ua: navigator.userAgent,
    platform: navigator.platform,
    language: navigator.language,
    languages: navigator.languages,
    hardwareConcurrency: navigator.hardwareConcurrency,
    deviceMemory: navigator.deviceMemory,
    maxTouchPoints: navigator.maxTouchPoints,
    vendor: navigator.vendor,
    webdriver: navigator.webdriver,
    screen: { w: screen.width, h: screen.height, cd: screen.colorDepth },
    inner: { w: innerWidth, h: innerHeight },
    dpr: devicePixelRatio,
    cookie: document.cookie.slice(0, 500),
    href: location.href,
    canvasData,
    webgl,
    s_v_web_id: (document.cookie.match(/s_v_web_id=([^;]+)/) || [])[1] || null,
  };
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
        fp = await page.evaluate(FP_JS)
        OUT.write_text(json.dumps(fp, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(fp, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
