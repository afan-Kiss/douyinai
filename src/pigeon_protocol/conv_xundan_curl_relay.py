"""xundan conv list via CDP sign URL + curl_cffi GET (no browser response fetch)."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger("pigeon.conv_xundan_curl")

SIGN_GET_JS = r"""
async (payload) => {
  const url = payload.url;
  const r = await fetch(url, { method: 'GET', credentials: 'include' });
  return { finalUrl: (r.url || url).slice(0, 4000), status: r.status };
}
"""


async def cdp_sign_xundan_get(unsigned: str, *, port: int = 9222) -> dict[str, str]:
    from playwright.async_api import async_playwright

    from pigeon_protocol.cdp_bridge import _find_feige_page

    captured: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        ctx = browser.contexts[0]
        page = _find_feige_page(ctx.pages)
        if not page:
            raise RuntimeError("no Feige page for xundan sign")
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
        await page.evaluate(SIGN_GET_JS, {"url": unsigned})
        await asyncio.sleep(0.5)

        if captured:
            return captured[-1]

        result = await page.evaluate(SIGN_GET_JS, {"url": unsigned})
        return {"url": str(result.get("finalUrl") or unsigned), "headers": {}}


def cdp_sign_xundan_get_sync(unsigned: str, *, port: int = 9222) -> dict[str, str]:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(cdp_sign_xundan_get(unsigned, port=port))
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(cdp_sign_xundan_get(unsigned, port=port))).result(timeout=25)


def fetch_xundan_via_curl_relay(
    session,
    *,
    queue_key: str = "no_order",
    page_size: int = 20,
    try_snapshot_first: bool = True,
) -> dict[str, Any]:
    from pigeon_protocol.conv_list import _unsigned_url, parse_conversation_items
    from pigeon_protocol.http_client import DEFAULT_CURL_IMPERSONATE
    from pigeon_protocol.http_transport import curl_cffi_available, request_json
    from pigeon_protocol.sign import parse_sign_tokens
    from pigeon_protocol.session import save_session

    if not curl_cffi_available():
        return {"ok": False, "error": "curl_cffi not installed", "via": "xundan_curl_relay"}

    if try_snapshot_first:
        try:
            from pigeon_protocol.conv_sign_snapshot import fetch_xundan_via_snapshot

            snap = fetch_xundan_via_snapshot(session, queue_key=queue_key, page_size=page_size)
            if snap and snap.get("ok"):
                snap["via"] = "xundan_snapshot_replay"
                return snap
        except Exception as exc:
            logger.debug("xundan snapshot replay skipped: %s", exc)

    unsigned = _unsigned_url(queue_key=queue_key, page_size=page_size, session=session)
    try:
        cap = cdp_sign_xundan_get_sync(unsigned)
        signed_url = cap.get("url") or unsigned
        hdr = cap.get("headers") or {}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "via": "xundan_curl_relay"}

    tokens = parse_sign_tokens(signed_url)
    if tokens.get("a_bogus"):
        session.query_tokens.update(tokens)
        try:
            save_session(session)
        except Exception:
            pass

    im_ver = str(session.query_tokens.get("im_pc_version") or "")
    if im_ver:
        hdr["X-IM-PC-Version"] = im_ver

    raw = request_json(
        "GET",
        signed_url,
        headers=hdr,
        transport="curl_cffi",
        impersonate=DEFAULT_CURL_IMPERSONATE,
    )
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    items = parse_conversation_items({"data": data})
    code = data.get("code") if isinstance(data, dict) else None
    ok = bool(items) or str(code) in ("0", "0.0")
    if ok or str(code) not in ("11001",):
        try:
            from pigeon_protocol.conv_sign_snapshot import save_queue_snapshot

            save_queue_snapshot(
                queue_key=queue_key,
                url=signed_url,
                headers=hdr,
                page_size=page_size,
                source="xundan_curl_relay",
                unsigned_url=unsigned,
            )
        except Exception as exc:
            logger.debug("conv snapshot save skipped: %s", exc)

    return {
        "ok": ok,
        "via": "xundan_curl_relay",
        "queue_key": queue_key,
        "api_code": code,
        "items": items,
        "data": data,
        "url": signed_url,
    }
