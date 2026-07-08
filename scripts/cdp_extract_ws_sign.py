#!/usr/bin/env python3
"""One-shot CDP hook: capture WS outbound frames BEFORE/AFTER browser send for sign RE."""
from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
OUT = ROOT / "analysis" / "ws_sign_samples.json"
sys.path.insert(0, str(SRC))

HOOK = r"""
() => {
  if (window.__wsSignCapture) return window.__wsSignCapture.samples.length;
  window.__wsSignCapture = { samples: [] };
  const orig = WebSocket.prototype.send;
  WebSocket.prototype.send = function(data) {
    try {
      let bytes;
      if (data instanceof ArrayBuffer) bytes = new Uint8Array(data);
      else if (data instanceof Uint8Array) bytes = data;
      else if (typeof data === 'string') bytes = new TextEncoder().encode(data);
      else return orig.apply(this, arguments);
      if (bytes.length > 2000 && bytes.length < 5000) {
        let s = '';
        for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
        window.__wsSignCapture.samples.push({
          t: Date.now(),
          len: bytes.length,
          b64: btoa(s).slice(0, 8000),
          url: (this.url || '').slice(0, 200),
        });
      }
    } catch (e) {}
    return orig.apply(this, arguments);
  };
  return 0;
}
"""


async def main() -> int:
    from playwright.async_api import async_playwright

    from pigeon_protocol.cdp_bridge import cdp_ready

    if not cdp_ready():
        print("CDP not ready", file=sys.stderr)
        return 1
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = None
        for pg in browser.contexts[0].pages:
            if "jinritemai.com" in (pg.url or ""):
                page = pg
                break
        if not page:
            print("no feige page", file=sys.stderr)
            return 1
        await page.evaluate(HOOK)
        print("Hook installed. Send a message manually in Feige, then press Enter here.")
        await asyncio.sleep(30)
        samples = await page.evaluate("() => window.__wsSignCapture?.samples || []")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {len(samples)} samples -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
