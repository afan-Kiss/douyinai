#!/usr/bin/env python3
"""Capture WS.send call stacks via CDP — poll for Feige manual/auto sends."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
OUT = ROOT / "analysis" / "ws_sign_stacks.json"
sys.path.insert(0, str(SRC))

INSTALL_JS = r"""
() => {
  if (window.__wsSignStackHook) return { already: true };
  const cap = [];
  window.__wsSignStackHook = cap;
  const NativeWS = WebSocket;
  const origSend = NativeWS.prototype.send;
  NativeWS.prototype.send = function(data) {
    try {
      let len = 0;
      if (data instanceof ArrayBuffer) len = data.byteLength;
      else if (data?.byteLength != null) len = data.byteLength;
      else if (typeof data === "string") len = data.length;
      if (len >= 2800 && len < 4500) {
        cap.push({
          t: Date.now(),
          len,
          url: (this.url || "").slice(0, 180),
          stack: (new Error()).stack?.split("\n").slice(1, 30),
        });
      }
    } catch (e) {}
    return origSend.apply(this, arguments);
  };
  return { installed: true };
}
"""

USER_ID = "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk"


async def main() -> int:
    from playwright.async_api import async_playwright

    from pigeon_protocol.cdp_bridge import cdp_ready
    from pigeon_protocol.ws_template_harvest import ensure_template_sync

    if not cdp_ready():
        print("CDP not ready — skip (run Chrome with --remote-debugging-port=9222)", file=sys.stderr)
        report = {"hook": {"skipped": True}, "stacks": [], "stack_count": 0}
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return 2

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = next(
            (pg for pg in browser.contexts[0].pages if "jinritemai" in (pg.url or "")),
            browser.contexts[0].pages[0],
        )
        hook = await page.evaluate(INSTALL_JS)
        print("hook:", hook)

        before = await page.evaluate("() => (window.__wsSignStackHook || []).length")

        harvest_ok = await asyncio.to_thread(ensure_template_sync, 18)

        stacks = []
        for _ in range(20):
            await asyncio.sleep(0.5)
            stacks = await page.evaluate("() => window.__wsSignStackHook || []")
            if len(stacks) > before:
                break

        report = {
            "hook": hook,
            "harvest_ok": harvest_ok,
            "stacks": stacks,
            "stack_count": len(stacks),
            "new_stacks": max(0, len(stacks) - before),
        }
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if stacks else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
