"""Deep probe: xundan CDP — login state, signed URL params, full API body."""
from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs, urlparse

from pigeon_protocol.cdp_bridge import _find_feige_page
from pigeon_protocol.conv_list import _unsigned_url
from pigeon_protocol.session import load_session

SIGN_JS = """
async (payload) => {
  const url = payload.url;
  const r = await fetch(url, { method: 'GET', credentials: 'include' });
  const text = await r.text();
  return { finalUrl: (r.url||url).slice(0,4000), status: r.status, text };
}
"""

STATE_JS = """
() => {
  const ls = {};
  try {
    for (const k of ['xmst','msToken','__msuuid__']) {
      const v = localStorage.getItem(k);
      if (v) ls[k] = v.slice(0, 120);
    }
  } catch (e) {}
  return {
    href: location.href,
    title: document.title,
    gfdata: (window.gfdatav1 && window.gfdatav1.ver) || '',
    shopId: (document.cookie.match(/SHOP_ID=(\\d+)/) || [])[1] || '',
    pigeonCid: (document.cookie.match(/PIGEON_CID=([^;]+)/) || [])[1] || '',
    ls,
  };
}
"""


async def main() -> None:
    session = load_session()
    unsigned = _unsigned_url(queue_key="no_pay", page_size=20, session=session)
    captured: list[dict] = []

    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        ctx = browser.contexts[0]
        page = _find_feige_page(ctx.pages)
        state = await page.evaluate(STATE_JS)
        print("page state:", json.dumps(state, ensure_ascii=False, indent=2))

        def on_req(req) -> None:
            u = req.url or ""
            if "xundan_chat_list" in u:
                captured.append({"url": u, "method": req.method})

        page.on("request", on_req)
        raw = await page.evaluate(SIGN_JS, {"url": unsigned})
        await asyncio.sleep(0.3)

    print("\nunsigned params:", parse_qs(urlparse(unsigned).query))
    if captured:
        cap = captured[-1]
        print("\nsigned params:", parse_qs(urlparse(cap["url"]).query))
        print("signed has a_bogus:", "a_bogus" in parse_qs(urlparse(cap["url"]).query))

    try:
        data = json.loads(raw.get("text") or "{}")
        print("\nfull response:", json.dumps(data, ensure_ascii=False, indent=2)[:2500])
    except json.JSONDecodeError:
        print("\nraw:", (raw.get("text") or "")[:500])


if __name__ == "__main__":
    asyncio.run(main())
