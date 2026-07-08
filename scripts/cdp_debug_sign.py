#!/usr/bin/env python3
"""Use CDP Debugger to pause inside bdms when signing order/query."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TRIGGER = r"""
async () => {
  const url = 'https://pigeon.jinritemai.com/backstage/cmpoent/order/query?biz_type=4&PIGEON_BIZ_TYPE=2&_pms=1&device_platform=web&FUSION=true&_v=1.0.1.7626';
  await fetch(url, { method:'POST', credentials:'include', headers:{'content-type':'application/json'}, body:'{}' });
  return 'done';
}
"""


async def main(port: int = 9222) -> dict:
    from playwright.async_api import async_playwright

    report: dict = {"pauses": [], "ok": False}

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        page = next((pg for pg in browser.contexts[0].pages if "jinritemai" in (pg.url or "")), browser.contexts[0].pages[0])
        cdp = await page.context.new_cdp_session(page)
        await cdp.send("Debugger.enable")

        async def on_paused(params: dict) -> None:
            call_frames = params.get("callFrames") or []
            top = call_frames[0] if call_frames else {}
            loc = top.get("location") or {}
            fn = top.get("functionName") or ""
            url = top.get("url") or ""
            report["pauses"].append({
                "reason": params.get("reason"),
                "function": fn,
                "url": url[:200],
                "line": loc.get("lineNumber"),
                "col": loc.get("columnNumber"),
                "stack": [
                    {
                        "fn": f.get("functionName"),
                        "url": (f.get("url") or "")[:120],
                        "line": (f.get("location") or {}).get("lineNumber"),
                    }
                    for f in call_frames[:12]
                ],
            })
            await cdp.send("Debugger.resume")

        cdp.on("Debugger.paused", lambda params: asyncio.create_task(on_paused(params)))

        # breakpoint in bdms bundle
        try:
            await cdp.send(
                "Debugger.setBreakpointByUrl",
                {"lineNumber": 1, "columnNumber": 5000, "urlRegex": "bdms\\.js"},
            )
        except Exception as exc:
            report["bp_error"] = str(exc)

        # also break on XHR open when script contains bdms
        await page.evaluate(
            r"""
            () => {
              if (window.__dbgOpen) return;
              const o = XMLHttpRequest.prototype.open;
              XMLHttpRequest.prototype.open = function(m,u,...r){
                if (String(u).includes('order/query')) debugger;
                return o.call(this,m,u,...r);
              };
              window.__dbgOpen = true;
            }
            """
        )

        try:
            await page.evaluate(TRIGGER)
        except Exception as exc:
            report["trigger_error"] = str(exc)

        await asyncio.sleep(1)
        report["ok"] = True

    out = ROOT / "analysis" / "debugger_pauses.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    r = asyncio.run(main())
    print(json.dumps(r, ensure_ascii=False, indent=2))
