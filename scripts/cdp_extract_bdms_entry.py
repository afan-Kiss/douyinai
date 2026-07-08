#!/usr/bin/env python3
"""Extract bdms.init config + internal sign entry from live Feige page."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "analysis" / "bdms_sign_entry.json"

EXTRACT_JS = r"""
() => {
  const out = {
    bdmsKeys: window.bdms ? Object.keys(window.bdms) : [],
    initStr: window.bdms?.init?.toString?.().slice(0, 1200) || null,
    initProps: [],
    initConfig: window.__bdmsInitConfig || null,
    cookie: document.cookie.slice(0, 400),
    ua: navigator.userAgent,
    href: location.href,
  };

  // Walk bdms.init closure props (community: init._v / init.p patterns)
  const init = window.bdms?.init;
  if (init) {
    for (const k of Object.getOwnPropertyNames(init)) {
      let v = init[k];
      let info = { key: k, type: typeof v };
      if (Array.isArray(v)) info.len = v.length;
      if (v && typeof v === "object" && !Array.isArray(v)) {
        info.subkeys = Object.keys(v).slice(0, 20);
      }
      if (typeof v === "function") info.head = v.toString().slice(0, 200);
      out.initProps.push(info);
    }
  }

  // Try common export paths from RE articles
  const paths = [
    "bdms.init._v",
    "bdms.init._u",
    "bdms.init.p",
    "bdms.init._p",
    "bdms._sign",
    "bdms.sign",
    "bdms.getSign",
    "bdms.getABogus",
  ];
  out.pathProbe = {};
  for (const p of paths) {
    try {
      const v = p.split(".").reduce((o, k) => o?.[k], window);
      out.pathProbe[p] = v == null ? null : typeof v;
    } catch (e) {
      out.pathProbe[p] = "err:" + String(e);
    }
  }

  // Deep probe init array slots (blog: init._v[2].p[42])
  out.deepSlots = [];
  try {
    const iv = init?._v || init?.p || init?._p;
    if (Array.isArray(iv)) {
      for (let i = 0; i < Math.min(iv.length, 8); i++) {
        const slot = iv[i];
        const row = { i, type: typeof slot };
        if (slot && typeof slot === "object") {
          row.keys = Object.keys(slot).slice(0, 12);
          if (Array.isArray(slot.p)) row.pLen = slot.p.length;
          if (Array.isArray(slot.u)) row.uLen = slot.u.length;
          if (Array.isArray(slot.v)) row.vLen = slot.v.length;
        }
        out.deepSlots.push(row);
      }
    }
  } catch (e) {
    out.deepSlotsError = String(e);
  }

  return out;
}
"""

SIGN_TEST_JS = r"""
async (payload) => {
  const { url, bodyStr } = payload || {};
  const report = { fetch: null, xhr: null, globals: {} };

  // fetch path (works in Feige)
  try {
    const r = await fetch(url, {
      method: "POST",
      credentials: "include",
      headers: { "content-type": "application/json;charset=UTF-8" },
      body: bodyStr,
    });
    const finalUrl = r.url || url;
    report.fetch = {
      finalUrl: finalUrl.slice(0, 2000),
      hasBogus: finalUrl.includes("a_bogus="),
      status: r.status,
    };
  } catch (e) {
    report.fetch = { error: String(e) };
  }

  // Try to expose encrypt via init internals
  try {
    const init = window.bdms?.init;
    const iv = init?._v;
    if (Array.isArray(iv)) {
      report.initVLen = iv.length;
      // scan for sign-like functions in p arrays
      const hits = [];
      for (let i = 0; i < iv.length; i++) {
        const slot = iv[i];
        if (!slot?.p || !Array.isArray(slot.p)) continue;
        for (let j = 0; j < slot.p.length; j++) {
          const fn = slot.p[j];
          if (typeof fn !== "function") continue;
          const s = fn.toString();
          if (/bogus|sign|token|msToken|verifyFp/i.test(s) || s.length > 800) {
            hits.push({ i, j, head: s.slice(0, 120), len: s.length });
          }
        }
      }
      report.signFnHits = hits.slice(0, 20);
    }
  } catch (e) {
    report.internalScan = String(e);
  }

  for (const k of ["a_bogus", "__ac_signature", "__ac_nonce", "byted_acrawler"]) {
    if (k in window) report.globals[k] = typeof window[k];
  }
  return report;
}
"""


async def main() -> int:
    from playwright.async_api import async_playwright

    url = (
        "https://pigeon.jinritemai.com/backstage/cmpoent/order/query"
        "?biz_type=4&PIGEON_BIZ_TYPE=2&_pms=1&device_platform=web&FUSION=true&_v=1.0.1.7626"
    )
    body = json.dumps(
        {
            "security_user_id": "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk",
            "page_no": 0,
            "page_size": 5,
            "tab_type": 1,
            "biz_type": 2,
            "version": "1.0",
            "workstation_opt_version": "v2",
            "workstation_opt_gray": True,
            "open_params": {},
            "service_entity_id": "",
            "search_words": "",
            "is_init_tab": 0,
        },
        ensure_ascii=False,
    )

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        pages = browser.contexts[0].pages
        page = next((pg for pg in pages if "jinritemai" in (pg.url or "")), pages[0])

        extract = await page.evaluate(EXTRACT_JS)
        sign_test = await page.evaluate(SIGN_TEST_JS, {"url": url, "bodyStr": body})

        if sign_test.get("fetch", {}).get("finalUrl"):
            qs = parse_qs(urlparse(sign_test["fetch"]["finalUrl"]).query)
            sign_test["tokens"] = {k: (qs.get(k) or [""])[0][:100] for k in ("a_bogus", "msToken", "verifyFp")}

        report = {"extract": extract, "sign_test": sign_test}
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
