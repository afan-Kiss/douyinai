#!/usr/bin/env python3
"""Try invoking bdms sign directly in browser page context."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PROBE_JS = r"""
(url, bodyStr) => {
  const report = { attempts: [], globals: {} };

  // surface hooks
  for (const k of Object.getOwnPropertyNames(window)) {
    if (/bdms|secsdk|bogus|sign|acrawler|mssdk|byted|_0x/i.test(k))
      report.globals[k] = typeof window[k];
  }

  // Try secsdk
  if (window.secsdk?.csrf?.setOptions) {
    try {
      report.secsdk_csrf = 'present';
    } catch (e) { report.secsdk_csrf = String(e); }
  }

  // Hook fetch to see what bdms adds
  const origFetch = window.fetch;
  let captured = null;
  window.fetch = function(input, init) {
    const u = typeof input === 'string' ? input : input?.url;
    captured = {
      in: String(u).slice(0, 800),
      initHeaders: init?.headers ? Object.fromEntries(new Headers(init.headers)) : {},
      method: init?.method,
      bodyLen: init?.body ? String(init.body).length : 0,
    };
    return origFetch.apply(this, arguments).then(r => {
      captured.out = String(r.url || u).slice(0, 1500);
      captured.hasBogus = captured.out.includes('a_bogus=');
      return r;
    });
  };

  // sync XHR path used by some RE articles
  window.a_bogus = null;
  try {
    const xhr = new XMLHttpRequest();
    xhr.bdmsInvokeList = [
      { args: ['POST', url, true] },
      { args: ['content-type', 'application/json;charset=UTF-8'] },
    ];
    xhr.open('POST', url, true);
    xhr.setRequestHeader('content-type', 'application/json;charset=UTF-8');
    xhr.send(bodyStr);
    report.xhrInvoke = { a_bogus: window.a_bogus, responseURL: xhr.responseURL?.slice(0, 800) };
  } catch (e) {
    report.xhrInvoke = { error: String(e) };
  }

  // fetch trigger (async handled below)
  report.capturedRef = 'pending';
  return report;
}
"""

FETCH_JS = r"""
async (url, bodyStr) => {
  const r = await fetch(url, {
    method: 'POST', credentials: 'include',
    headers: { 'content-type': 'application/json;charset=UTF-8' },
    body: bodyStr,
  });
  const j = await r.json().catch(() => ({}));
  return {
    finalUrl: (r.url||url).slice(0, 1500),
    code: j.code,
    captured: window.__lastFetchCapture || null,
  };
}
"""

INSTALL_CAPTURE = r"""
() => {
  if (window.__fetchCapInstalled) return;
  window.__fetchCapInstalled = true;
  window.__lastFetchCapture = null;
  const orig = window.fetch;
  window.fetch = async function(input, init) {
    const u = typeof input === 'string' ? input : input?.url;
    const cap = {
      in: String(u).slice(0, 800),
      headers: init?.headers ? Object.fromEntries(new Headers(init.headers)) : {},
      bodyLen: init?.body ? String(init.body).length : 0,
    };
    const r = await orig.apply(this, arguments);
    cap.out = String(r.url || u).slice(0, 1500);
    cap.hasBogus = cap.out.includes('a_bogus=');
    window.__lastFetchCapture = cap;
    return r;
  };
}
"""


async def main() -> None:
    from playwright.async_api import async_playwright

    url = "https://pigeon.jinritemai.com/backstage/cmpoent/order/query?biz_type=4&PIGEON_BIZ_TYPE=2&_pms=1&device_platform=web&FUSION=true&_v=1.0.1.7626"
    body = json.dumps({
        "security_user_id": "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk",
        "page_no": 0, "page_size": 5, "tab_type": 1, "biz_type": 2, "version": "1.0",
        "workstation_opt_version": "v2", "workstation_opt_gray": True, "open_params": {},
        "service_entity_id": "", "search_words": "", "is_init_tab": 0,
    }, ensure_ascii=False)

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = next(pg for pg in browser.contexts[0].pages if "jinritemai" in (pg.url or ""))
        await page.evaluate(INSTALL_CAPTURE)
        probe = await page.evaluate(PROBE_JS, url, body)
        fetch_r = await page.evaluate(FETCH_JS, url, body)
        report = {"probe": probe, "fetch": fetch_r}
        (ROOT / "analysis" / "bdms_invoke_probe.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
