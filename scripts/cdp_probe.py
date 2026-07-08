#!/usr/bin/env python3
"""Connect to Feige Chrome via CDP: capture sign tokens, probe a_bogus, test order fetch."""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from pigeon_protocol.session import load_session, save_session

CDP_PORT = 9222
ORDER_PATH = "/backstage/cmpoent/order/query"
SIGN_KEYS = ("verifyFp", "fp", "msToken", "a_bogus")

PROBE_JS = r"""
() => {
  const out = { globals: [], hooks: [], samples: {} };

  const names = [
    "byted_acrawler", "bdms", "_bdms", "secsdk", "window.secsdk",
    "xss", "webmssdk", "mssdk", "sign", "_sign", "a_bogus",
    "__ac_signature", "bytedance", "Slardar", "bdSign",
  ];
  for (const n of names) {
    try {
      const v = n.includes(".") ? null : window[n];
      if (v != null) out.globals.push({ name: n, type: typeof v, keys: typeof v === "object" ? Object.keys(v).slice(0, 20) : [] });
    } catch (e) {}
  }

  // common SDK entry points
  const candidates = [
    window.byted_acrawler,
    window.bdms,
    window.secsdk,
    window.webmssdk,
  ].filter(Boolean);

  for (const obj of candidates) {
    for (const key of Object.keys(obj || {})) {
      if (/sign|bogus|token|encrypt|ac/i.test(key)) out.hooks.push({ obj: obj?.constructor?.name || "obj", key, type: typeof obj[key] });
    }
  }

  // try invoke known patterns
  const url = location.href;
  const attempts = [];
  const tryCall = (label, fn) => {
    try {
      const r = fn();
      attempts.push({ label, ok: true, result: String(r).slice(0, 200) });
    } catch (e) {
      attempts.push({ label, ok: false, error: String(e).slice(0, 200) });
    }
  };

  if (window.byted_acrawler?.frontierSign) {
    tryCall("byted_acrawler.frontierSign", () => window.byted_acrawler.frontierSign({ url }));
  }
  if (window.byted_acrawler?.sign) {
    tryCall("byted_acrawler.sign", () => window.byted_acrawler.sign({ url }));
  }
  if (typeof window._msToken === "string") out.samples._msToken = window._msToken.slice(0, 80);

  out.attempts = attempts;
  out.pageUrl = url;
  out.qs = Object.fromEntries(new URLSearchParams(location.search));
  return out;
}
"""

FETCH_JS = r"""
async (payload) => {
  const { url, method = "POST", body = null } = payload || {};
  const resp = await fetch(url, {
    method,
    credentials: "include",
    headers: { "content-type": "application/json;charset=UTF-8" },
    body: body != null ? JSON.stringify(body) : undefined,
  });
  const text = await resp.text();
  return { ok: resp.ok, status: resp.status, text: text.slice(0, 120000), url: resp.url };
}
"""


def _sync_tokens(session, url: str) -> dict[str, str]:
    qs = parse_qs(urlparse(url).query)
    updated: dict[str, str] = {}
    for key in SIGN_KEYS:
        if qs.get(key):
            session.query_tokens[key] = qs[key][0]
            updated[key] = session.query_tokens[key][:60]
    return updated


async def run_probe(cdp_port: int, user_id: str, wait_sec: float) -> dict[str, Any]:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {"ok": False, "error": "playwright not installed — pip install playwright"}

    report: dict[str, Any] = {"ok": False, "cdp_port": cdp_port, "captured_urls": [], "probe": {}, "order_test": {}}
    session = load_session()
    captured: list[str] = []

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        pages = [pg for pg in context.pages if "jinritemai.com" in (pg.url or "")]
        if not pages:
            pages = context.pages[:1]
        if not pages:
            return {"ok": False, "error": "no browser pages — open Feige and log in"}

        page = pages[0]
        report["page_url"] = page.url

        def on_request(req) -> None:
            u = req.url or ""
            if ORDER_PATH in u and "a_bogus=" in u:
                captured.append(u)
                _sync_tokens(session, u)

        page.on("request", on_request)

        probe = await page.evaluate(PROBE_JS)
        report["probe"] = probe

        # Build order URL from page query + known path
        page_qs = parse_qs(urlparse(page.url).query)
        params = {
            "biz_type": "4",
            "PIGEON_BIZ_TYPE": "2",
            "_pms": "1",
            "device_platform": "web",
            "FUSION": "true",
            "_v": "1.0.1.7626",
        }
        for key in SIGN_KEYS:
            val = page_qs.get(key) or ([session.query_tokens.get(key)] if session.query_tokens.get(key) else None)
            if val:
                params[key] = val[0] if isinstance(val, list) else val

        from urllib.parse import urlencode

        order_url = f"https://pigeon.jinritemai.com{ORDER_PATH}?{urlencode(params)}"
        body = {
            "security_user_id": user_id,
            "page_no": 0,
            "page_size": 5,
            "search_words": "",
            "is_init_tab": 0,
            "tab_type": 1,
            "biz_type": 2,
            "open_params": {},
            "workstation_opt_version": "v2",
            "service_entity_id": "",
            "version": "1.0",
            "workstation_opt_gray": True,
        }

        # Trigger UI refresh to capture live signed URL if possible
        try:
            await page.evaluate(
                """() => {
                  const btn = [...document.querySelectorAll('button,span,div')]
                    .find(el => (el.textContent||'').includes('刷新订单'));
                  btn?.click?.();
                }"""
            )
        except Exception:
            pass

        if wait_sec > 0:
            await asyncio.sleep(wait_sec)

        report["captured_urls"] = captured[-5:]
        if captured:
            _sync_tokens(session, captured[-1])
            order_url = captured[-1]

        fetch_result = await page.evaluate(FETCH_JS, {"url": order_url, "method": "POST", "body": body})
        report["order_test"] = {
            "url_used": order_url[:240],
            "status": fetch_result.get("status"),
            "ok": fetch_result.get("ok"),
            "preview": (fetch_result.get("text") or "")[:800],
        }

        try:
            parsed = json.loads(fetch_result.get("text") or "{}")
            report["order_test"]["code"] = parsed.get("code")
            report["order_test"]["msg"] = parsed.get("msg")
            if parsed.get("componentized_data") or parsed.get("data"):
                report["order_test"]["has_data"] = True
        except Exception:
            pass

        session.notes.append(f"cdp_probe captured={len(captured)} urls")
        save_session(session)
        report["session_tokens"] = {k: session.query_tokens.get(k, "")[:60] for k in SIGN_KEYS if session.query_tokens.get(k)}
        report["ok"] = True

    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=CDP_PORT)
    parser.add_argument("--user-id", default="AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk")
    parser.add_argument("--wait", type=float, default=5.0)
    args = parser.parse_args()

    result = asyncio.run(run_probe(args.port, args.user_id, args.wait))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
