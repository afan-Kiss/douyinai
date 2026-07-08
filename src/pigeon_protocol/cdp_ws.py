"""CDP helpers for sending WS frames through the browser's active Feige socket."""
from __future__ import annotations

import asyncio
import base64
import logging
import os
from typing import Any

logger = logging.getLogger("pigeon.cdp_ws")

DEFAULT_CDP_PORT = int(os.getenv("CDP_PORT", "9222"))

_WS_HOOK_JS = r"""
() => {
  if (window.__pigeonWsCapture) return { status: "already" };
  window.__pigeonWsCapture = { ws: null, url: "" };
  const NativeWS = WebSocket;
  function track(ws, url) {
    if (String(url || ws.url || "").includes("ws.fxg.jinritemai.com")) {
      window.__pigeonWsCapture.ws = ws;
      window.__pigeonWsCapture.url = ws.url || String(url);
    }
  }
  WebSocket = function(url, protocols) {
    const ws = protocols !== undefined ? new NativeWS(url, protocols) : new NativeWS(url);
    track(ws, url);
    return ws;
  };
  WebSocket.prototype = NativeWS.prototype;
  WebSocket.CONNECTING = NativeWS.CONNECTING;
  WebSocket.OPEN = NativeWS.OPEN;
  WebSocket.CLOSING = NativeWS.CLOSING;
  WebSocket.CLOSED = NativeWS.CLOSED;
  const nativeSend = NativeWS.prototype.send;
  NativeWS.prototype.send = function(data) {
    track(this, this.url);
    return nativeSend.apply(this, arguments);
  };
  return { status: "installed" };
}
"""

_WS_HOOK_INSTALL_JS = _WS_HOOK_JS

_WS_INIT_SCRIPT = r"""(() => {
  if (window.__pigeonWsCapture) return;
  window.__pigeonWsCapture = { ws: null, url: "" };
  const NativeWS = WebSocket;
  function track(ws, url) {
    if (String(url || ws.url || "").includes("ws.fxg.jinritemai.com")) {
      window.__pigeonWsCapture.ws = ws;
      window.__pigeonWsCapture.url = ws.url || String(url);
    }
  }
  WebSocket = function(url, protocols) {
    const ws = protocols !== undefined ? new NativeWS(url, protocols) : new NativeWS(url);
    track(ws, url);
    return ws;
  };
  WebSocket.prototype = NativeWS.prototype;
  WebSocket.CONNECTING = NativeWS.CONNECTING;
  WebSocket.OPEN = NativeWS.OPEN;
  WebSocket.CLOSING = NativeWS.CLOSING;
  WebSocket.CLOSED = NativeWS.CLOSED;
  const nativeSend = NativeWS.prototype.send;
  NativeWS.prototype.send = function(data) {
    track(this, this.url);
    return nativeSend.apply(this, arguments);
  };
})();"""

_WS_SEND_JS = r"""
async (payloadB64) => {
  const cap = window.__pigeonWsCapture;
  const ws = cap?.ws;
  if (!ws) return { ok: false, error: "no_captured_ws", hint: "reload Feige after prepare()" };
  if (ws.readyState !== 1) return { ok: false, error: "ws_not_open", state: ws.readyState, url: (ws.url||"").slice(0,120) };
  const bin = Uint8Array.from(atob(payloadB64), c => c.charCodeAt(0));
  ws.send(bin);
  return { ok: true, bytes: bin.length, url: (ws.url||"").slice(0,120) };
}
"""

_WS_STATUS_JS = r"""
() => {
  const cap = window.__pigeonWsCapture;
  const ws = cap?.ws;
  if (!ws) return { ok: false, has_ws: false };
  return { ok: true, has_ws: true, state: ws.readyState, url: (ws.url||"").slice(0,200) };
}
"""


class CdpWsSender:
    """Send binary WS frames via browser socket (signatures validated server-side)."""

    def __init__(self, *, port: int = DEFAULT_CDP_PORT, timeout_sec: float = 20.0) -> None:
        self.port = port
        self.timeout_sec = timeout_sec

    @staticmethod
    def available(port: int = DEFAULT_CDP_PORT) -> bool:
        from pigeon_protocol.cdp_bridge import cdp_ready

        return cdp_ready(port)

    async def install_hook(self, page) -> dict[str, Any]:
        return await page.evaluate(_WS_HOOK_INSTALL_JS)

    async def send_bytes(self, payload: bytes) -> dict[str, Any]:
        from playwright.async_api import async_playwright

        from pigeon_protocol.cdp_ws import _WS_INIT_SCRIPT

        b64 = base64.b64encode(payload).decode("ascii")
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(
                f"http://127.0.0.1:{self.port}",
                timeout=int(self.timeout_sec * 1000),
            )
            ctx = browser.contexts[0]
            page = None
            for pg in ctx.pages:
                if "jinritemai.com" in (pg.url or ""):
                    page = pg
                    break
            if page is None:
                page = await ctx.new_page()
            await page.add_init_script(_WS_INIT_SCRIPT)
            await self.install_hook(page)
            status = await page.evaluate(_WS_STATUS_JS)
            if not status.get("has_ws") or status.get("state") != 1:
                if "jinritemai.com" not in (page.url or ""):
                    await page.goto(
                        "https://im.jinritemai.com/pc_seller_v2/main",
                        wait_until="domcontentloaded",
                        timeout=int(self.timeout_sec * 1000),
                    )
                    await page.wait_for_timeout(4000)
                else:
                    await page.reload(wait_until="domcontentloaded")
                    await page.wait_for_timeout(5000)
                status = await page.evaluate(_WS_STATUS_JS)
            result = await page.evaluate(_WS_SEND_JS, b64)
            result["ws_status"] = status
            return result

    def send(self, payload: bytes) -> dict[str, Any]:
        if not self.available(self.port):
            return {"ok": False, "error": f"CDP not ready on {self.port}"}
        try:
            return asyncio.run(self.send_bytes(payload))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
