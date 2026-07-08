#!/usr/bin/env python3
"""CDP dynamic hook: capture URL before/after bdms signing + stack traces."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

HOOK_JS = r"""
() => {
  if (window.__signCapture) return window.__signCapture.samples.length;
  const cap = { samples: [], installed: Date.now() };

  const record = (phase, input, output) => {
    if (!String(input || output || '').includes('pigeon.jinritemai.com')) return;
    cap.samples.push({
      t: Date.now(),
      phase,
      in: String(input).slice(0, 800),
      out: String(output).slice(0, 1200),
      bogusIn: String(input).includes('a_bogus='),
      bogusOut: String(output).includes('a_bogus='),
      stack: (new Error()).stack?.split('\n').slice(1, 8).join(' | '),
    });
    if (cap.samples.length > 30) cap.samples.shift();
  };

  const origFetch = window.fetch;
  window.fetch = function(input, init) {
    const url = typeof input === 'string' ? input : input?.url;
    const p = origFetch.apply(this, arguments);
    return p.then(resp => {
      record('fetch', url, resp.url || url);
      return resp;
    });
  };

  const origOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function(method, url, ...rest) {
    this.__signUrlIn = String(url);
    return origOpen.call(this, method, url, ...rest);
  };
  const origSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function(...args) {
    const out = this.responseURL || this.__signUrlIn;
    record('xhr', this.__signUrlIn, out);
    return origSend.apply(this, args);
  };

  window.__signCapture = cap;
  return 0;
}
"""

TRIGGER_JS = r"""
async () => {
  const url = 'https://pigeon.jinritemai.com/backstage/cmpoent/order/query?biz_type=4&PIGEON_BIZ_TYPE=2&_pms=1&device_platform=web&FUSION=true&_v=1.0.1.7626';
  const body = {
    security_user_id: 'AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk',
    page_no: 0, page_size: 5, search_words: '', is_init_tab: 0, tab_type: 1, biz_type: 2,
    open_params: {}, workstation_opt_version: 'v2', service_entity_id: '', version: '1.0', workstation_opt_gray: true,
  };
  const r = await fetch(url, { method:'POST', credentials:'include', headers:{'content-type':'application/json;charset=UTF-8'}, body: JSON.stringify(body) });
  return { status: r.status, finalUrl: (r.url||url).slice(0,1500), code: (await r.clone().json()).code };
}
"""

EXTRACT_GLOBALS = r"""
() => {
  const out = {};
  for (const k of Object.getOwnPropertyNames(window)) {
    if (/bdms|bogus|sign|mssdk|acrawler|secsdk|_0x/i.test(k)) out[k] = typeof window[k];
  }
  // walk bdms init closure hints
  out.bdmsKeys = window.bdms ? Object.keys(window.bdms) : [];
  out.bdmsInitSrc = window.bdms?.init?.toString?.().slice(0, 500);
  // common hidden globals
  for (const k of ['__ac_referer','_msToken','msToken','byted_acrawler','_signature']) {
    if (window[k] != null) out[k] = String(window[k]).slice(0,120);
  }
  return out;
}
"""


async def main(port: int = 9222) -> dict:
    from playwright.async_api import async_playwright

    report: dict = {"ok": False}
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        page = next((pg for pg in browser.contexts[0].pages if "jinritemai" in (pg.url or "")), browser.contexts[0].pages[0])
        report["page"] = page.url

        await page.evaluate(HOOK_JS)
        report["globals_before"] = await page.evaluate(EXTRACT_GLOBALS)

        trig = await page.evaluate(TRIGGER_JS)
        report["trigger"] = trig

        await asyncio.sleep(0.5)
        samples = await page.evaluate("() => window.__signCapture?.samples || []")
        report["samples"] = samples
        report["ok"] = True

    out = ROOT / "analysis" / "sign_samples.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    r = asyncio.run(main())
    print(json.dumps(r, ensure_ascii=False, indent=2))
