#!/usr/bin/env python3
"""Capture bdms.init(options) from live Feige page without full reload."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "analysis" / "bdms_init_captured.json"

# Wrap bdms.init on already-loaded page
WRAP_JS = r"""
() => {
  if (window.__bdmsInitCaptured) return window.__bdmsInitCaptured;
  const out = { wrapped: false, config: null, initHead: null };
  if (!window.bdms?.init) return { error: "no bdms.init" };

  out.initHead = window.bdms.init.toString().slice(0, 200);
  if (window.bdms.init.__wrappedCapture) {
    return window.__bdmsInitCaptured || { already: true };
  }

  const orig = window.bdms.init.bind(window.bdms);
  window.bdms.init = function(cfg) {
    window.__bdmsInitCaptured = {
      wrapped: true,
      config: JSON.parse(JSON.stringify(cfg || {})),
      ts: Date.now(),
    };
    return orig(cfg);
  };
  window.bdms.init.__wrappedCapture = true;
  out.wrapped = true;
  return out;
}
"""

# Scan page scripts for init call args in inline glue
SCAN_JS = r"""
() => {
  const hints = [];
  for (const s of document.scripts) {
    const t = s.textContent || "";
    if (!t.includes("bdms") && !t.includes("1383")) continue;
    if (t.includes("init") || t.includes("pageId") || t.includes("aid")) {
      hints.push(t.slice(0, 2500));
    }
  }
  // common feige glue pattern
  const m = hints.join("\n").match(/aid\s*[:=]\s*['"]?(\d+)['"]?/);
  const p = hints.join("\n").match(/pageId\s*[:=]\s*['"]?(\d+)['"]?/);
  return {
    aid: m ? m[1] : null,
    pageId: p ? p[1] : null,
    hintCount: hints.length,
    sample: hints[0]?.slice(0, 800) || null,
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
        scan = await page.evaluate(SCAN_JS)
        wrap = await page.evaluate(WRAP_JS)

        report = {
            "scan": scan,
            "wrap": wrap,
            "config": None,
        }

        # Default from feige glue + prior RE
        if scan.get("aid") or scan.get("pageId"):
            report["config"] = {
                "aid": int(scan.get("aid") or 1383),
                "pageId": int(scan.get("pageId") or 30026),
                "paths": ["^/backstage/cmpoent/", "^/backstage/", "/cmpoent/order/query"],
                "boe": False,
                "ddrt": 8.5,
                "ic": 8.5,
            }
        else:
            report["config"] = {
                "aid": 1383,
                "pageId": 30026,
                "paths": ["^/backstage/cmpoent/", "^/backstage/", "/cmpoent/order/query"],
                "boe": False,
                "ddrt": 8.5,
                "ic": 8.5,
            }

        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
