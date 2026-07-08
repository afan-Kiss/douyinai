#!/usr/bin/env python3
"""One-shot: open chat, hook WS, send test message, dump capture."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pigeon_protocol.ws_template_harvest import (
    DEFAULT_HARVEST_UID,
    INSTALL_CAPTURE_JS,
    SEND_UI_JS,
    _OPEN_CHAT_JS,
    _send_via_ui,
)


async def main() -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = next(pg for pg in browser.contexts[0].pages if "jinritemai" in (pg.url or ""))
        report: dict = {"page": page.url}
        report["open"] = await page.evaluate(_OPEN_CHAT_JS, DEFAULT_HARVEST_UID)
        report["hook_before"] = await page.evaluate(INSTALL_CAPTURE_JS)
        report["ws_status"] = await page.evaluate("""() => ({
          cap: !!window.__pigeonWsCapture?.ws,
          state: window.__pigeonWsCapture?.ws?.readyState,
          url: (window.__pigeonWsCapture?.ws?.url || '').slice(0, 120),
          samples: window.__wsSignCapture?.samples?.length || 0,
          patched: window.__wsSignCapture?.patched,
        })""")
        before = await page.evaluate("() => window.__wsSignCapture?.samples?.length || 0")
        report["send"] = await _send_via_ui(page, "好")
        await asyncio.sleep(2)
        after = await page.evaluate("""() => ({
          n: window.__wsSignCapture?.samples?.length || 0,
          last: window.__wsSignCapture?.samples?.slice(-1)[0] || null,
          stacks: window.__wsReverseHook?.slice(-1) || [],
        })""")
        report["capture"] = after
        report["before_n"] = before
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
