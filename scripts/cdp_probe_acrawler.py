#!/usr/bin/env python3
"""CDP: probe bdms/glue for byted_acrawler.frontierSign + pigeon IM APIs."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
OUT = ROOT / "analysis" / "acrawler_frontier_probe.json"

PROBE_JS = r"""
async () => {
  const out = { bdms: {}, glue: false, frontier: null, attempts: [] };
  if (window.bdms) {
    out.bdms.keys = Object.keys(window.bdms).slice(0, 30);
    out.bdms.hasInit = typeof window.bdms.init === "function";
  }
  if (window.byted_acrawler) {
    out.acrawler = {
      keys: Object.keys(window.byted_acrawler).slice(0, 30),
      hasFrontier: typeof window.byted_acrawler.frontierSign === "function",
    };
  }
  // try bdms init path (aid 1383 feige)
  try {
    if (window.bdms?.init && !window.__bdmsFeigeInited) {
      window.bdms.init({ aid: 1383, pageId: 30026, paths: { include: ["/pigeon_im/"] } });
      window.__bdmsFeigeInited = true;
      out.bdms.initCalled = true;
    }
  } catch (e) {
    out.bdms.initError = String(e);
  }
  await new Promise(r => setTimeout(r, 1500));
  if (window.byted_acrawler?.frontierSign) {
    try {
      const stub = { "X-MS-STUB": "d41d8cd98f00b204e9800998ecf8427e" };
      out.frontier = window.byted_acrawler.frontierSign(stub);
      out.frontierOk = true;
    } catch (e) {
      out.frontierError = String(e);
    }
  }
  // check if bdms exposes acrawler
  for (const path of ["window.byted_acrawler", "window.bdms?.byted_acrawler"]) {
    try {
      const v = eval(path);
      if (v) out.attempts.push({ path, keys: Object.keys(v).slice(0, 20) });
    } catch (e) {}
  }
  return out;
}
"""


async def main() -> int:
    from playwright.async_api import async_playwright

    from pigeon_protocol.cdp_bridge import cdp_ready

    if not cdp_ready():
        OUT.write_text(json.dumps({"error": "cdp not ready"}, indent=2))
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
    return 0 if report.get("frontierOk") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
