"""Probe xundan via CDP sign + curl_cffi GET."""
from __future__ import annotations

import asyncio
import json

from pigeon_protocol.cdp_bridge import _find_feige_page
from pigeon_protocol.conv_list import _unsigned_url
from pigeon_protocol.http_client import DEFAULT_CURL_IMPERSONATE
from pigeon_protocol.http_transport import curl_cffi_available, request_json
from pigeon_protocol.session import load_session

SIGN_JS = """
async (payload) => {
  const url = payload.url;
  const r = await fetch(url, { method: 'GET', credentials: 'include' });
  const text = await r.text();
  return { finalUrl: (r.url||url).slice(0,4000), status: r.status, text: text.slice(0,500000) };
}
"""


async def main() -> None:
    session = load_session()
    unsigned = _unsigned_url(queue_key="no_pay", page_size=20, session=session)
    print("unsigned has a_bogus:", "a_bogus=" in unsigned)

    captured: list[dict] = []
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        ctx = browser.contexts[0]
        page = _find_feige_page(ctx.pages)
        cookies = await ctx.cookies()
        cookie_hdr = "; ".join(f"{c['name']}={c['value']}" for c in cookies if c.get("name"))

        def on_req(req) -> None:
            u = req.url or ""
            if "xundan_chat_list" in u and "a_bogus=" in u:
                hdr = dict(req.headers)
                for drop in ("content-length", "host", ":authority", ":method", ":path", ":scheme"):
                    hdr.pop(drop, None)
                hdr["Cookie"] = cookie_hdr
                captured.append({"url": u, "headers": hdr})

        page.on("request", on_req)
        if "jinritemai" not in (page.url or ""):
            await page.goto(
                "https://im.jinritemai.com/pc_seller_v2/main/workspace",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            await page.wait_for_timeout(5000)
        raw = await page.evaluate(SIGN_JS, {"url": unsigned})
        await asyncio.sleep(0.5)

    print("captured:", len(captured), "eval status:", raw.get("status"))
    try:
        data = json.loads(raw.get("text") or "{}")
        print("in-page code:", data.get("code"), "msg:", data.get("msg"))
    except json.JSONDecodeError as exc:
        print("in-page parse err:", exc, (raw.get("text") or "")[:120])

    if not captured:
        print("no signed request captured")
        return

    cap = captured[-1]
    hdr = cap["headers"]
    im_ver = session.query_tokens.get("im_pc_version") or ""
    if im_ver:
        hdr["X-IM-PC-Version"] = im_ver

    if not curl_cffi_available():
        print("curl_cffi missing")
        return

    result = request_json(
        "GET",
        cap["url"],
        headers=hdr,
        transport="curl_cffi",
        impersonate=DEFAULT_CURL_IMPERSONATE,
    )
    data = result.get("data") or {}
    print("curl code:", data.get("code"), "msg:", data.get("msg"))
    payload = data.get("data") if isinstance(data.get("data"), dict) else {}
    user_list = payload.get("user_list") if isinstance(payload, dict) else None
    print("user_list len:", len(user_list or []))


if __name__ == "__main__":
    asyncio.run(main())
