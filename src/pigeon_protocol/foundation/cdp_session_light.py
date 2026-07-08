"""Sync WS URLs + tokens from live Feige Chrome tab (no page reload)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("pigeon.cdp_session_light")


async def sync_from_feige_page_async(session) -> dict[str, Any]:
    """Pull wss:// URLs and hook status from open Feige tab."""
    from playwright.async_api import async_playwright

    from pigeon_protocol.cdp_ws import _WS_STATUS_JS
    from pigeon_protocol.ws_url_builder import apply_ws_url

    report: dict[str, Any] = {"ws_urls": [], "applied": []}
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = next(
            (pg for pg in browser.contexts[0].pages if "jinritemai" in (pg.url or "")),
            browser.contexts[0].pages[0],
        )
        ws_status = await page.evaluate(_WS_STATUS_JS)
        report["ws_status"] = ws_status

        urls: list[str] = []
        live_url = await page.evaluate(
            """() => {
              const cap = window.__pigeonWsCapture;
              const ws = cap?.ws;
              if (ws?.url) return ws.url;
              if (cap?.wsUrl) return cap.wsUrl;
              return "";
            }"""
        )
        if live_url:
            urls.append(live_url)

        extra = await page.evaluate(
            """() => {
              const out = [];
              const hook = window.__pigeonWsCapture?.wsUrl || window.__wsSignCapture?.wsUrl;
              if (hook) out.push(hook);
              for (const e of performance.getEntriesByType('resource')) {
                if (e.name && e.name.startsWith('wss://')) out.push(e.name);
              }
              return [...new Set(out)];
            }"""
        )
        for url in extra or []:
            if url and url not in urls:
                urls.append(url)

        for url in urls:
            if "ws.fxg.jinritemai.com" not in url:
                continue
            report["ws_urls"].append(url[:200])
            if apply_ws_url(session, url):
                report["applied"].append("ws_url")

        from pigeon_protocol.foundation.pigeon_sign_service import ensure_pigeon_sign
        from pigeon_protocol.session_health import refresh_ws_tokens_from_urls
        from pigeon_protocol.ws_url_builder import build_ws_url

        sign_r = ensure_pigeon_sign(session)
        if sign_r.get("ok"):
            report["applied"].append(f"pigeon_sign:{sign_r.get('via', 'ok')}")

        tok = refresh_ws_tokens_from_urls(session)
        if tok:
            report["applied"].append(f"tokens:{','.join(tok)}")

        # Rebuild full URL when live socket omitted pigeon_sign (truncated capture).
        built = build_ws_url(session)
        if built and built not in (session.ws_urls or []):
            if apply_ws_url(session, built):
                report["applied"].append("ws_url_built")
                report["ws_urls"].append(built[:200])

    report["ok"] = bool(report.get("applied")) or bool(report.get("ws_urls"))
    return report


def sync_from_feige_page(session) -> dict[str, Any]:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(sync_from_feige_page_async(session))
    # Called from inside an active event loop — run in a worker thread.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(sync_from_feige_page_async(session))).result(timeout=60)
