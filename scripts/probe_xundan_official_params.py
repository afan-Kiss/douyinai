"""Test xundan with official URL param shape."""
from __future__ import annotations

import asyncio
import json

from pigeon_protocol.cdp_bridge import _find_feige_page
from pigeon_protocol.conv_list import parse_conversation_items

FETCH_JS = """
async (payload) => {
  const queueKey = payload.queue_key || "no_order";
  const verifyFp = (document.cookie.match(/s_v_web_id=(verify_[^;]+)/) || [])[1] || "";
  const params = new URLSearchParams({
    biz_type: "4",
    PIGEON_BIZ_TYPE: "2",
    _pms: "1",
    device_platform: "web",
    FUSION: "true",
    queue_key: queueKey,
    security_uid_list: "",
    page_size: String(payload.page_size || 20),
  });
  if (verifyFp) {
    params.set("verifyFp", verifyFp);
    params.set("fp", verifyFp);
  }
  const url = `https://pigeon.jinritemai.com/backstage/workstation/xundan_chat_list?${params}`;
  const r = await fetch(url, { method: "GET", credentials: "include" });
  const text = await r.text();
  return { finalUrl: r.url, status: r.status, text };
}
"""


async def main() -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = _find_feige_page(browser.contexts[0].pages)
        for qk in ("no_order", "no_pay", "all"):
            raw = await page.evaluate(FETCH_JS, {"queue_key": qk, "page_size": 20})
            data = json.loads(raw["text"])
            items = parse_conversation_items({"data": data})
            print(qk, "code", data.get("code"), "msg", data.get("msg"), "items", len(items))
            if items:
                print(" ", json.dumps(items[0], ensure_ascii=False)[:200])


if __name__ == "__main__":
    asyncio.run(main())
