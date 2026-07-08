#!/usr/bin/env python3
"""Capture browser order/query request headers vs httpx replay."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

USER = "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk"

ORDER_JS = r"""
async (uid) => {
  const url = 'https://pigeon.jinritemai.com/backstage/cmpoent/order/query?biz_type=4&PIGEON_BIZ_TYPE=2&_pms=1&device_platform=web&FUSION=true&_v=1.0.1.7626';
  const body = {
    security_user_id: uid,
    page_no: 0, page_size: 5, search_words: '', is_init_tab: 0, tab_type: 1, biz_type: 2,
    open_params: {}, workstation_opt_version: 'v2', service_entity_id: '', version: '1.0', workstation_opt_gray: true,
  };
  const bodyStr = JSON.stringify(body);
  const r = await fetch(url, {
    method: 'POST', credentials: 'include',
    headers: { 'content-type': 'application/json;charset=UTF-8' },
    body: bodyStr,
  });
  const text = await r.text();
  return { finalUrl: (r.url||url).slice(0,2000), status: r.status, bodyStr, text: text.slice(0,500) };
}
"""


async def main() -> None:
    from playwright.async_api import async_playwright

    from pigeon_protocol.http_transport import request_json

    captured: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = next(pg for pg in browser.contexts[0].pages if "jinritemai" in (pg.url or ""))

        def on_req(req):
            if "order/query" in (req.url or ""):
                captured.append({
                    "url": req.url[:2000],
                    "method": req.method,
                    "headers": dict(req.headers),
                    "post": req.post_data,
                })

        page.on("request", on_req)
        js_result = await page.evaluate(ORDER_JS, USER)
        await asyncio.sleep(0.5)

    report = {"js_result": js_result, "captured": captured[-1] if captured else None}

    if captured:
        cap = captured[-1]
        headers = dict(cap["headers"])
        # httpx replay with browser headers
        body = json.loads(cap["post"] or "{}")
        replay = request_json("POST", cap["url"], headers=headers, json_body=body, transport="httpx")
        report["httpx_replay_browser_headers"] = {
            "ok": replay.get("ok"),
            "code": (replay.get("data") or {}).get("code") if isinstance(replay.get("data"), dict) else None,
            "msg": (replay.get("data") or {}).get("msg") if isinstance(replay.get("data"), dict) else None,
        }
        replay2 = request_json("POST", cap["url"], headers=headers, json_body=body, transport="curl_cffi")
        report["curl_replay_browser_headers"] = {
            "ok": replay2.get("ok"),
            "code": (replay2.get("data") or {}).get("code") if isinstance(replay2.get("data"), dict) else None,
        }

    out = ROOT / "analysis" / "order_request_replay.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
