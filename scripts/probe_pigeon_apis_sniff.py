"""Sniff all pigeon backstage APIs after login."""
from __future__ import annotations

import asyncio
import json

from pigeon_protocol.cdp_bridge import _find_feige_page


async def main() -> None:
    hits: list[dict] = []

    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        ctx = browser.contexts[0]
        page = _find_feige_page(ctx.pages)
        if not page:
            return

        async def on_resp(resp) -> None:
            u = resp.url or ""
            if "pigeon.jinritemai.com" not in u:
                return
            if not any(k in u for k in ("conversation", "xundan", "chat", "workstation", "user_list", "fuzzy")):
                return
            try:
                text = await resp.text()
                data = json.loads(text)
                code = data.get("code")
                inner = data.get("data")
                n = 0
                if isinstance(inner, dict):
                    for key in ("user_list", "conversation_list", "list", "conversations"):
                        if isinstance(inner.get(key), list):
                            n = len(inner[key])
                hits.append({"url": u.split("?")[0].split("/")[-1], "full": u[:200], "code": code, "msg": data.get("msg"), "items": n})
            except Exception:
                hits.append({"url": u[:120], "code": "?", "items": 0})

        page.on("response", lambda r: asyncio.create_task(on_resp(r)))
        await page.evaluate(
            """async () => {
              const tabs = [...document.querySelectorAll('div,span,button')].filter(el => {
                const t = (el.textContent||'').trim();
                return t.length < 12 && /待支付|未下单|最近联系|全部|售后/.test(t);
              });
              for (const el of tabs.slice(0, 6)) { el.click(); await new Promise(r=>setTimeout(r,1200)); }
            }"""
        )
        await asyncio.sleep(6)

    for h in hits:
        msg = h.get("msg") or ""
        if msg and all(ord(c) < 128 for c in str(msg)):
            try:
                msg = str(msg).encode("latin-1").decode("utf-8")
            except Exception:
                pass
        print(h.get("url"), "code=", h.get("code"), "items=", h.get("items"), "msg=", msg)


if __name__ == "__main__":
    asyncio.run(main())
