"""One-shot CDP session sync → pure Python runtime (WS + signed HTTP)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any
from urllib.parse import parse_qs, urlparse

from pigeon_protocol.cdp_ws import _WS_HOOK_INSTALL_JS, _WS_INIT_SCRIPT, _WS_STATUS_JS
from pigeon_protocol.config import FEIGE_URL, ORDER_QUERY_PATH, PIGEON_HOST
from pigeon_protocol.session import SessionState, save_session

logger = logging.getLogger("pigeon.session_sync")

DEFAULT_CDP_PORT = int(os.getenv("CDP_PORT", "9222"))
SIGN_KEYS = ("verifyFp", "fp", "msToken", "a_bogus", "token", "access_key", "pigeon_sign")


class CdpSessionSync:
    """Pull fresh cookies, WS URLs, sign tokens from logged-in Feige Chrome."""

    def __init__(self, session: SessionState, *, port: int = DEFAULT_CDP_PORT, timeout_sec: float = 20.0) -> None:
        self.session = session
        self.port = port
        self.timeout_sec = timeout_sec

    @staticmethod
    def available(port: int = DEFAULT_CDP_PORT) -> bool:
        from pigeon_protocol.cdp_bridge import cdp_ready

        return cdp_ready(port)

    async def _sync_async(self) -> dict[str, Any]:
        from playwright.async_api import async_playwright

        report: dict[str, Any] = {"ws_urls": [], "tokens": {}, "cookies": 0}
        captured_ws: list[str] = []
        ws_status: dict[str, Any] = {}

        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(
                f"http://127.0.0.1:{self.port}",
                timeout=int(self.timeout_sec * 1000),
            )
            ctx = browser.contexts[0]

            feige_page = None
            for pg in ctx.pages:
                if "jinritemai.com" in (pg.url or ""):
                    feige_page = pg
                    break
            page = feige_page or await ctx.new_page()
            await page.add_init_script(_WS_INIT_SCRIPT)
            await page.evaluate(_WS_HOOK_INSTALL_JS)

            def on_ws(ws) -> None:
                u = ws.url or ""
                if u.startswith("wss://") and u not in captured_ws:
                    captured_ws.append(u)

            page.on("websocket", on_ws)

            ws_status = await page.evaluate(_WS_STATUS_JS)
            ws_ready = ws_status.get("has_ws") and ws_status.get("state") == 1

            if ws_ready:
                # Keep buyer chat open — reload would reset workspace UI and break template harvest.
                logger.info("WS already open (state=1), skip page reload")
            elif "jinritemai.com/pc_seller" in (page.url or ""):
                await page.reload(wait_until="domcontentloaded", timeout=int(self.timeout_sec * 1000))
                await page.wait_for_timeout(5000)
                ws_status = await page.evaluate(_WS_STATUS_JS)
            else:
                await page.goto(FEIGE_URL, wait_until="domcontentloaded", timeout=int(self.timeout_sec * 1000))
                await page.wait_for_timeout(5000)
                ws_status = await page.evaluate(_WS_STATUS_JS)

            if not ws_status.get("has_ws") or ws_status.get("state") != 1:
                await page.reload(wait_until="domcontentloaded")
                await page.wait_for_timeout(5000)
                ws_status = await page.evaluate(_WS_STATUS_JS)

            cookies = await ctx.cookies()
            for c in cookies:
                if c.get("name") and c.get("value"):
                    self.session.cookies[c["name"]] = c["value"]

            # xmst → msToken for bdms sign chain
            try:
                ls = await page.evaluate(
                    """() => ({
                      xmst: localStorage.getItem('xmst') || '',
                      msToken: localStorage.getItem('msToken') || '',
                    })"""
                )
                xmst = str((ls or {}).get("xmst") or (ls or {}).get("msToken") or "")
                if xmst:
                    self.session.query_tokens["msToken"] = xmst
            except Exception as exc:
                logger.debug("localStorage xmst sync: %s", exc)

            csrf = self.session.cookies.get("csrf_session_id") or ""
            passport = self.session.cookies.get("passport_csrf_token") or ""
            if csrf and passport:
                self.session.headers["x-secsdk-csrf-token"] = f"000100000001{passport},{csrf}"

            if self.session.cookies.get("SHOP_ID"):
                self.session.shop_id = self.session.cookies["SHOP_ID"]
            if self.session.cookies.get("s_v_web_id"):
                self.session.query_tokens["verifyFp"] = self.session.cookies["s_v_web_id"]
                self.session.query_tokens["fp"] = self.session.cookies["s_v_web_id"]
            if self.session.cookies.get("PIGEON_CID"):
                self.session.device_id = self.session.cookies["PIGEON_CID"]

            unsigned = (
                f"{PIGEON_HOST}{ORDER_QUERY_PATH}"
                "?biz_type=4&PIGEON_BIZ_TYPE=2&_pms=1&device_platform=web&FUSION=true&_v=1.0.1.7626"
            )
            probe = await page.evaluate(
                """async (url) => {
                  const r = await fetch(url, {
                    method: 'POST',
                    credentials: 'include',
                    headers: {'content-type':'application/json;charset=UTF-8'},
                    body: JSON.stringify({security_user_id:'AQ',page_no:0,page_size:1,version:'1.0'}),
                  });
                  return (r.url || url).slice(0, 2500);
                }""",
                unsigned,
            )
            qs = parse_qs(urlparse(str(probe)).query)
            for key in SIGN_KEYS:
                if qs.get(key):
                    self.session.query_tokens[key] = qs[key][0]

            # xundan snapshot bootstrap (whale GET signed in-page)
            try:
                from pigeon_protocol.config import XUNDAN_QUEUE_KEYS
                from pigeon_protocol.conv_list import _unsigned_url
                from pigeon_protocol.conv_sign_snapshot import save_queue_snapshot
                from pigeon_protocol.order_relay_headers import build_order_relay_headers

                snap_saved: list[str] = []
                for qk in XUNDAN_QUEUE_KEYS[:3]:
                    unsigned_x = _unsigned_url(queue_key=qk, page_size=20, session=self.session)
                    x_probe = await page.evaluate(
                        """async (url) => {
                          const r = await fetch(url, { method: 'GET', credentials: 'include' });
                          return (r.url || url).slice(0, 4000);
                        }""",
                        unsigned_x,
                    )
                    x_url = str(x_probe or "")
                    if "xundan_chat_list" not in x_url or "a_bogus=" not in x_url:
                        continue
                    hdr = build_order_relay_headers(self.session, for_method="GET")
                    save_queue_snapshot(
                        queue_key=qk,
                        url=x_url,
                        headers=hdr,
                        page_size=20,
                        source="session_sync/cdp",
                        unsigned_url=unsigned_x,
                    )
                    snap_saved.append(qk)
                if snap_saved:
                    report["conv_snapshot"] = ",".join(snap_saved)
            except Exception as exc:
                logger.debug("xundan snapshot during sync: %s", exc)

            perf_ws = await page.evaluate(
                """() => performance.getEntriesByType('resource')
                  .map(e => e.name)
                  .filter(u => u.startsWith('wss://'))"""
            )
            for u in perf_ws or []:
                if u not in captured_ws:
                    captured_ws.append(u)

            # Keep Feige tab alive for CDP WS send — do not close page.

        if captured_ws:
            self.session.ws_urls = captured_ws + [u for u in self.session.ws_urls if u not in captured_ws]
            for ws_url in captured_ws:
                self.session.merge_query_tokens(ws_url)

        self.session.notes.append(f"cdp_sync ws={len(captured_ws)} cookies={len(self.session.cookies)}")
        save_session(self.session)

        report["ws_urls"] = captured_ws[:5]
        report["tokens"] = {k: self.session.query_tokens.get(k, "")[:48] for k in SIGN_KEYS if self.session.query_tokens.get(k)}
        report["cookies"] = len(self.session.cookies)
        report["shop_id"] = self.session.shop_id
        report["ws_hook"] = ws_status
        return report

    def sync(self) -> dict[str, Any]:
        if not self.available(self.port):
            raise RuntimeError(f"CDP not ready on port {self.port}")
        return asyncio.run(self._sync_async())
