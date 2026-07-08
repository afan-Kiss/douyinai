"""Probe xundan after login — no reload, passive + in-page."""
from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs, urlparse

from pigeon_protocol.cdp_bridge import _find_feige_page
from pigeon_protocol.conv_list import parse_conversation_items
from pigeon_protocol.conv_list_cdp import _FETCH_XUNDAN_JS, _ENSURE_WORKSPACE_JS
from pigeon_protocol.session import load_session


async def main() -> None:
    session = load_session()
    bodies: dict[str, str] = {}
    reqs: list[str] = []

    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        ctx = browser.contexts[0]
        page = _find_feige_page(ctx.pages)
        if page is None:
            print("no page")
            return

        warm = await page.evaluate(_ENSURE_WORKSPACE_JS)
        print("warm:", json.dumps(warm, ensure_ascii=False, indent=2))

        async def on_resp(resp) -> None:
            u = resp.url or ""
            if "xundan_chat_list" in u or "get_current_conversation_list" in u:
                reqs.append(u[:180])
                try:
                    bodies[u] = await resp.text()
                except Exception:
                    pass

        page.on("response", lambda r: asyncio.create_task(on_resp(r)))

        # trigger tab clicks to load conv list naturally
        click_js = """
        async () => {
          const tabs = [...document.querySelectorAll('[class*="tab"],[role="tab"],button')];
          const hits = tabs.filter(el => /待支付|未下单|最近|全部|售后/.test(el.textContent||''));
          for (const el of hits.slice(0, 5)) {
            try { el.click(); await new Promise(r => setTimeout(r, 1500)); } catch(e) {}
          }
          return hits.map(el => (el.textContent||'').trim()).slice(0, 8);
        }
        """
        clicked = await page.evaluate(click_js)
        print("clicked tabs:", clicked)
        await asyncio.sleep(8)

        print("\npassive reqs:", len(reqs))
        for u, text in bodies.items():
            name = "xundan" if "xundan" in u else "current_conv"
            qk = parse_qs(urlparse(u).query).get("queue_key", ["?"])[0]
            try:
                data = json.loads(text)
                items = parse_conversation_items({"data": data})
                print(name, "qk=", qk, "code=", data.get("code"), "msg=", data.get("msg"), "items=", len(items))
            except json.JSONDecodeError:
                print(name, "raw", text[:120])

        if not any(parse_conversation_items({"data": json.loads(t)}) for t in bodies.values() if t):
            raw = await page.evaluate(_FETCH_XUNDAN_JS, {"queue_key": "no_pay", "page_size": 20})
            data = json.loads(raw.get("text") or "{}")
            items = parse_conversation_items({"data": data})
            print("\nmanual xundan code:", data.get("code"), "msg:", data.get("msg"), "items:", len(items))
            print("full:", json.dumps(data, ensure_ascii=False)[:800])

        # sync cookies to session
        from pigeon_protocol.conv_list_cdp import _sync_session_from_cdp

        cookies = await ctx.cookies()
        applied = _sync_session_from_cdp(session, cookies=cookies, final_url=reqs[-1] if reqs else "")
        print("\nsynced:", applied[:8], "cookie_count", len(cookies))


if __name__ == "__main__":
    asyncio.run(main())
