"""Order query via CDP sign URL + curl_cffi TLS (no browser response fetch)."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger("pigeon.order_curl")

SIGN_URL_JS = r"""
async (payload) => {
  const { url, body } = payload;
  const r = await fetch(url, {
    method: 'POST', credentials: 'include',
    headers: { 'content-type': 'application/json;charset=UTF-8' },
    body: JSON.stringify(body),
  });
  return { finalUrl: (r.url || url).slice(0, 3000), status: r.status };
}
"""


async def cdp_sign_order_request(unsigned: str, body: dict[str, Any], *, port: int = 9222) -> dict[str, str]:
    from playwright.async_api import async_playwright

    captured: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        ctx = browser.contexts[0]
        page = next((pg for pg in ctx.pages if "jinritemai" in (pg.url or "")), None)
        if not page:
            raise RuntimeError("no Feige page for order sign")
        cookies = await ctx.cookies()
        cookie_hdr = "; ".join(f"{c['name']}={c['value']}" for c in cookies if c.get("name"))

        def on_req(req):
            u = req.url or ""
            if "order/query" in u and "a_bogus=" in u:
                hdr = dict(req.headers)
                for drop in ("content-length", "host", ":authority", ":method", ":path", ":scheme"):
                    hdr.pop(drop, None)
                hdr["Cookie"] = cookie_hdr
                captured.append({"url": u, "headers": hdr, "post": req.post_data or ""})

        page.on("request", on_req)
        await page.evaluate(SIGN_URL_JS, {"url": unsigned, "body": body})
        await asyncio.sleep(0.4)

        if captured:
            return captured[-1]

        result = await page.evaluate(SIGN_URL_JS, {"url": unsigned, "body": body})
        return {"url": str(result.get("finalUrl") or unsigned), "headers": {}, "post": ""}


def cdp_sign_order_request_sync(unsigned: str, body: dict[str, Any], *, port: int = 9222) -> dict[str, str]:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(cdp_sign_order_request(unsigned, body, port=port))
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(cdp_sign_order_request(unsigned, body, port=port))).result(timeout=20)


def cdp_sign_order_url_sync(unsigned: str, body: dict[str, Any], *, port: int = 9222) -> str:
    return cdp_sign_order_request_sync(unsigned, body, port=port).get("url", unsigned)


def query_orders_via_curl_relay(session, security_user_id: str) -> dict[str, Any]:
    """CDP signs URL only; curl_cffi performs POST (pure TLS, no browser response path)."""
    from pigeon_protocol.config import ORDER_QUERY_PATH, PIGEON_HOST
    from pigeon_protocol.http_client import BackstageHttpClient, DEFAULT_CURL_IMPERSONATE
    from pigeon_protocol.http_transport import curl_cffi_available, request_json, order_api_ok
    from pigeon_protocol.sign import parse_sign_tokens
    from pigeon_protocol.session import save_session

    if not curl_cffi_available():
        return {"ok": False, "error": "curl_cffi not installed"}

    unsigned = f"{PIGEON_HOST}{ORDER_QUERY_PATH}?biz_type=4&PIGEON_BIZ_TYPE=2&_pms=1&device_platform=web&FUSION=true&_v=1.0.1.7626"
    body = {
        "security_user_id": security_user_id,
        "page_no": 0,
        "page_size": 5,
        "search_words": "",
        "is_init_tab": 0,
        "tab_type": 1,
        "biz_type": 2,
        "open_params": {},
        "workstation_opt_version": "v2",
        "service_entity_id": "",
        "version": "1.0",
        "workstation_opt_gray": True,
    }

    try:
        cap = cdp_sign_order_request_sync(unsigned, body)
        signed_url = cap.get("url") or unsigned
        hdr = cap.get("headers") or {}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "via": "curl_relay"}

    tokens = parse_sign_tokens(signed_url)
    if tokens.get("a_bogus"):
        session.query_tokens.update(tokens)
        try:
            save_session(session)
        except Exception:
            pass

    if not hdr:
        client = BackstageHttpClient(session, dry_run=False)
        hdr = client._headers(browser_hints=True)
        cookie_hdr = session.cookie_header()
        if cookie_hdr:
            hdr["Cookie"] = cookie_hdr

    raw = request_json(
        "POST",
        signed_url,
        headers=hdr,
        json_body=body,
        transport="curl_cffi",
        impersonate=DEFAULT_CURL_IMPERSONATE,
    )
    raw["via"] = "curl_relay"
    raw["_capture"] = {"url": signed_url, "headers": hdr}
    return raw
