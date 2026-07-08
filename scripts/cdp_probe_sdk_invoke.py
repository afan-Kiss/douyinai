#!/usr/bin/env python3
"""CDP: hook webviewBridge + probe IM page SDK surfaces for cmd 11327."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
OUT = ROOT / "analysis" / "sdk_invoke_probe.json"
sys.path.insert(0, str(SRC))

from pigeon_protocol.foundation.pigeon_sdk_delegate import HOOK_INVOKE_JS

PROBE_JS = r"""
async () => {
  const out = {
    ua: navigator.userAgent,
    is_feige: navigator.userAgent.includes("feige"),
    globals: [],
    im_candidates: [],
  };
  for (const k of Object.keys(window)) {
    if (/pigeon|feige|im|mona|webview/i.test(k)) out.im_candidates.push(k);
  }
  for (const k of ["webviewBridge", "byted_acrawler", "bdms", "__pigeonPluginLoader", "mona_remote_pigeon"]) {
    const v = window[k];
    if (v != null) {
      out.globals.push({
        k,
        type: typeof v,
        keys: typeof v === "object" ? Object.keys(v).slice(0, 20) : [],
      });
    }
  }
  if (window.webviewBridge?.getSDKClient) {
    try {
      const client = await window.webviewBridge.getSDKClient();
      out.sdk_client = {
        ok: true,
        keys: Object.keys(client || {}).slice(0, 30),
        has_createMessage: typeof client?.createMessage === "function",
      };
    } catch (e) {
      out.sdk_client = { ok: false, error: String(e) };
    }
  } else {
    out.sdk_client = { ok: false, error: "no webviewBridge.getSDKClient" };
  }
  return out;
}
"""


async def main() -> int:
    from playwright.async_api import async_playwright

    from pigeon_protocol.cdp_bridge import cdp_ready
    from pigeon_protocol.foundation.pigeon_sdk_delegate import cdp_hook_invoke_async

    if not cdp_ready():
        OUT.write_text(json.dumps({"error": "cdp not ready"}, indent=2), encoding="utf-8")
        return 2

    report: dict = {}
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        pages = [pg for ctx in browser.contexts for pg in ctx.pages]
        report["pages"] = [pg.url[:160] for pg in pages]
        page = next(
            (pg for pg in pages if "jinritemai" in (pg.url or "")),
            pages[0] if pages else None,
        )
        if not page:
            return 2
        report["probe"] = await page.evaluate(PROBE_JS)
        report["invoke_hook"] = await page.evaluate(HOOK_INVOKE_JS)

    report["delegate"] = await cdp_hook_invoke_async()
    report["ok"] = bool(
        report.get("probe", {}).get("sdk_client", {}).get("has_createMessage")
        or report.get("delegate", {}).get("ok")
    )
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
