#!/usr/bin/env python3
"""Inject session.json cookies into CDP Chrome context."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

DOMAINS = [
    ".jinritemai.com",
    "im.jinritemai.com",
    "pigeon.jinritemai.com",
    "fxg.jinritemai.com",
]


async def main() -> int:
    from playwright.async_api import async_playwright
    from pigeon_protocol.config import FEIGE_URL

    WORKSPACE_URL = "https://im.jinritemai.com/pc_seller_v2/main/workspace"
    from pigeon_protocol.session import load_session

    session = load_session()
    cookies = session.cookies
    if not cookies:
        print(json.dumps({"ok": False, "error": "no cookies in session.json"}))
        return 1

    pw_cookies = []
    for name, value in cookies.items():
        if not name or not value:
            continue
        for domain in DOMAINS:
            pw_cookies.append(
                {
                    "name": name,
                    "value": str(value),
                    "domain": domain,
                    "path": "/",
                    "secure": True,
                    "httpOnly": False,
                    "sameSite": "Lax",
                }
            )

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        ctx = browser.contexts[0]
        await ctx.add_cookies(pw_cookies)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        url_before = page.url or ""
        if "/workspace" not in url_before:
            await page.goto(WORKSPACE_URL, wait_until="domcontentloaded", timeout=60000)
        else:
            # Avoid navigation — keeps WS + open chat; only refresh cookies in-place
            pass
        await page.wait_for_timeout(3000)
        url = page.url
        editors = await page.locator("textarea, [contenteditable=true]").count()
        print(
            json.dumps(
                {
                    "ok": "login" not in url,
                    "url": url[:120],
                    "editors": editors,
                    "cookies_injected": len(cookies),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
