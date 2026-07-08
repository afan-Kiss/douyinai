#!/usr/bin/env python3
"""Replay browser-signed order URL via curl_cffi with live CDP cookies."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

USER = "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk"


async def main() -> None:
    from playwright.async_api import async_playwright

    from pigeon_protocol.http_transport import request_json

    body = {
        "security_user_id": USER,
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
    }

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        ctx = browser.contexts[0]
        page = next(pg for pg in ctx.pages if "jinritemai" in (pg.url or ""))
        cookies = await ctx.cookies()
        cookie_hdr = "; ".join(f"{c['name']}={c['value']}" for c in cookies if c.get("name"))

        captured: list[dict] = []

        def on_req(req):
            if "order/query" in (req.url or ""):
                captured.append({"url": req.url, "headers": dict(req.headers), "post": req.post_data})

        page.on("request", on_req)
        br = await page.evaluate(
            """async (b) => {
              const u = 'https://pigeon.jinritemai.com/backstage/cmpoent/order/query?biz_type=4&PIGEON_BIZ_TYPE=2&_pms=1&device_platform=web&FUSION=true&_v=1.0.1.7626';
              const r = await fetch(u, {method:'POST', credentials:'include', headers:{'content-type':'application/json;charset=UTF-8'}, body: JSON.stringify(b)});
              const j = await r.json();
              return {code: j.code, url: r.url};
            }""",
            body,
        )
        await asyncio.sleep(0.2)
        req = captured[-1]
        hdr = dict(req["headers"])
        hdr["Cookie"] = cookie_hdr
        for drop in ("content-length", "host", ":authority", ":method", ":path", ":scheme"):
            hdr.pop(drop, None)

        report = {"browser": br, "attempts": []}
        for imp in ("chrome131", "chrome136", "chrome142", "chrome149", "edge131"):
            raw = request_json("POST", req["url"], headers=hdr, json_body=body, transport="curl_cffi", impersonate=imp)
            data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
            report["attempts"].append({
                "impersonate": imp,
                "code": data.get("code"),
                "msg": data.get("msg"),
                "transport": raw.get("transport"),
            })

        out = ROOT / "analysis" / "curl_order_replay.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
