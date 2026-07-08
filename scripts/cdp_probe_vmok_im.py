#!/usr/bin/env python3
"""CDP: deep probe @ecom-vmok/pigeon-im-pc exports for createMessage / send APIs."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
OUT = ROOT / "analysis" / "vmok_im_probe.json"

PROBE_JS = r"""
() => {
  const out = { modules: [], send_paths: [] };
  const modKey = Object.keys(window).find(k => k.includes("pigeon-im-pc"));
  if (modKey) {
    const mod = window[modKey];
    out.moduleKey = modKey;
    out.moduleType = typeof mod;
    if (Array.isArray(mod)) {
      out.modules = mod.slice(0, 30).map((x, i) => ({
        i,
        type: typeof x,
        keys: x && typeof x === "object" ? Object.keys(x).slice(0, 20) : [],
        name: x?.name || x?.default?.name || "",
      }));
      for (let i = 0; i < mod.length; i++) {
        const x = mod[i];
        if (!x || typeof x !== "object") continue;
        const blob = JSON.stringify(Object.keys(x));
        if (/createMessage|sendMessage|sendText|innerSend/i.test(blob)) {
          out.send_paths.push({ i, keys: Object.keys(x).slice(0, 30) });
        }
      }
    } else if (mod && typeof mod === "object") {
      out.moduleKeys = Object.keys(mod).slice(0, 40);
    }
  }
  const store = window.__mona_store__ || window.__monaGlobalStore;
  if (store) {
    try {
      const st = typeof store.getState === "function" ? store.getState() : store;
      out.storeKeys = Object.keys(st || {}).slice(0, 25);
    } catch (e) {
      out.storeError = String(e);
    }
  }
  const cap = window.__pigeonWsCapture;
  if (cap) out.ws_capture = { keys: Object.keys(cap), wsUrl: (cap.wsUrl || "").slice(0, 120) };
  return out;
}
"""


async def main() -> int:
    from playwright.async_api import async_playwright

    from pigeon_protocol.cdp_bridge import cdp_ready

    if not cdp_ready():
        OUT.write_text(json.dumps({"error": "cdp not ready"}, indent=2), encoding="utf-8")
        return 2

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = next(
            (pg for pg in browser.contexts[0].pages if "jinritemai" in (pg.url or "")),
            browser.contexts[0].pages[0],
        )
        report = await page.evaluate(PROBE_JS)
        report["page"] = page.url[:160]

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
