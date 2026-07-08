#!/usr/bin/env python3
"""Test order fetch via page context — let bdms hook append a_bogus."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

ORDER_JS = r"""
async (payload) => {
  const { security_user_id } = payload;
  const base = "https://pigeon.jinritemai.com/backstage/cmpoent/order/query";
  const qs = new URLSearchParams({
    biz_type: "4",
    PIGEON_BIZ_TYPE: "2",
    _pms: "1",
    device_platform: "web",
    FUSION: "true",
    _v: "1.0.1.7626",
  });
  const url = `${base}?${qs.toString()}`;
  const body = {
    security_user_id,
    page_no: 0,
    page_size: 5,
    search_words: "",
    is_init_tab: 0,
    tab_type: 1,
    biz_type: 2,
    open_params: {},
    workstation_opt_version: "v2",
    service_entity_id: "",
    version: "1.0",
    workstation_opt_gray: true,
  };
  const resp = await fetch(url, {
    method: "POST",
    credentials: "include",
    headers: { "content-type": "application/json;charset=UTF-8" },
    body: JSON.stringify(body),
  });
  const finalUrl = resp.url || url;
  const text = await resp.text();
  return {
    requestUrl: url,
    finalUrl: finalUrl.slice(0, 1200),
    hasBogus: finalUrl.includes("a_bogus="),
    status: resp.status,
    preview: text.slice(0, 1200),
  };
}
"""

USER_ID = "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk"


async def main() -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = browser.contexts[0].pages[0]
        result = await page.evaluate(ORDER_JS, {"security_user_id": USER_ID})
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
