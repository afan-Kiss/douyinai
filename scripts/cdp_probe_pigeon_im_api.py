#!/usr/bin/env python3
"""CDP: try pigeon-im-pc createMessage / sendText to obtain signed frame bytes."""
from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
OUT = ROOT / "analysis" / "pigeon_im_api_probe.json"

PROBE_JS = r"""
async () => {
  const out = { hits: [], send: null };
  const modKey = Object.keys(window).find(k => k.includes("pigeon-im-pc"));
  if (modKey) out.moduleKey = modKey;
  const loader = window.__pigeonPluginLoader;
  if (loader) out.loaderKeys = Object.keys(loader);

  // Search React fiber / global store for pigeonIM
  const candidates = [];
  for (const k of Object.keys(window)) {
    if (/pigeon|im|feige|mona/i.test(k)) candidates.push(k);
  }
  out.candidates = candidates.slice(0, 40);

  // Try mona_remote_pigeon
  try {
    const mp = window.mona_remote_pigeon;
    if (mp) {
      out.monaKeys = Object.keys(mp);
      const def = mp.default || mp;
      if (def && typeof def === "object") out.monaDefaultKeys = Object.keys(def).slice(0, 25);
    }
  } catch (e) {
    out.monaError = String(e);
  }

  return out;
}
"""


async def main() -> int:
    from playwright.async_api import async_playwright

    from pigeon_protocol.cdp_bridge import cdp_ready

    if not cdp_ready():
        return 2

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = next(
            (pg for pg in browser.contexts[0].pages if "jinritemai" in (pg.url or "")),
            browser.contexts[0].pages[0],
        )
        report = await page.evaluate(PROBE_JS)
        report["page"] = page.url[:200]

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
