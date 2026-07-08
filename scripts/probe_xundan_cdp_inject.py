"""Test: inject session cookies into CDP Chrome then xundan."""
from __future__ import annotations

import asyncio
import json

from pigeon_protocol.cdp_bridge import _find_feige_page
from pigeon_protocol.conv_list import parse_conversation_items
from pigeon_protocol.conv_list_cdp import _FETCH_XUNDAN_JS, _ENSURE_WORKSPACE_JS
from pigeon_protocol.session import load_session

DOMAINS = (".jinritemai.com", "im.jinritemai.com", "pigeon.jinritemai.com", "fxg.jinritemai.com")


def cookies_for_playwright(session) -> list[dict]:
    out: list[dict] = []
    for name, value in (session.cookies or {}).items():
        if not name or value is None:
            continue
        val = str(value)
        if not val:
            continue
        out.append({"name": str(name), "value": val, "url": "https://im.jinritemai.com/"})
    return out


async def main() -> None:
    session = load_session()
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        ctx = browser.contexts[0]
        await ctx.add_cookies(cookies_for_playwright(session))
        page = _find_feige_page(ctx.pages)
        if page is None:
            page = await ctx.new_page()
        await page.goto(
            "https://im.jinritemai.com/pc_seller_v2/main/workspace",
            wait_until="domcontentloaded",
            timeout=45000,
        )
        await page.wait_for_timeout(8000)
        warm = await page.evaluate(_ENSURE_WORKSPACE_JS)
        print("warm:", json.dumps(warm, ensure_ascii=False))
        raw = await page.evaluate(_FETCH_XUNDAN_JS, {"queue_key": "no_pay", "page_size": 20})
        data = json.loads(raw.get("text") or "{}")
        items = parse_conversation_items({"data": data})
        print("code:", data.get("code"), "msg:", data.get("msg"), "items:", len(items))


if __name__ == "__main__":
    asyncio.run(main())
