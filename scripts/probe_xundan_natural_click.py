"""Capture natural xundan response after clicking 最近联系."""
from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs, urlparse

from pigeon_protocol.cdp_bridge import _find_feige_page
from pigeon_protocol.conv_list import parse_conversation_items


async def main() -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = _find_feige_page(browser.contexts[0].pages)
        captured: list[tuple[str, str]] = []

        async def on_resp(resp) -> None:
            u = resp.url or ""
            if "xundan_chat_list" in u:
                captured.append((u, await resp.text()))

        page.on("response", lambda r: asyncio.create_task(on_resp(r)))

        clicked = await page.evaluate(
            """async () => {
              const all = [...document.querySelectorAll('*')];
              const el = all.find(e => {
                const t = (e.textContent || '').trim();
                return t === '最近联系' && e.offsetParent && e.children.length === 0;
              }) || all.find(e => (e.textContent||'').trim() === '最近联系');
              if (el) { el.click(); return true; }
              return false;
            }"""
        )
        print("clicked 最近联系:", clicked)
        await asyncio.sleep(4)

        for url, text in captured:
            qs = parse_qs(urlparse(url).query)
            data = json.loads(text)
            items = parse_conversation_items({"data": data})
            print("\nqueue_key:", qs.get("queue_key"))
            print("params:", {k: v[0][:40] if len(v[0]) > 40 else v[0] for k, v in qs.items()})
            print("code:", data.get("code"), "items:", len(items))
            if items:
                print("sample:", json.dumps(items[0], ensure_ascii=False)[:400])


if __name__ == "__main__":
    asyncio.run(main())
