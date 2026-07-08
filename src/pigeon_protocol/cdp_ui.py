"""Send Feige messages via browser UI (full client signing stack)."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger("pigeon.cdp_ui")

DEFAULT_CDP_PORT = int(os.getenv("CDP_PORT", "9222"))
FEIGE_MAIN = "https://im.jinritemai.com/pc_seller_v2/main"

_SEND_JS = r"""
async (text) => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const editors = [
    ...document.querySelectorAll('[contenteditable="true"]'),
    ...document.querySelectorAll('textarea'),
  ].filter(el => el.offsetParent !== null);
  let editor = editors.find(el => {
    const r = el.getBoundingClientRect();
    return r.width > 120 && r.height > 20;
  }) || editors[0];
  if (!editor) return { ok: false, error: "no_editor" };

  editor.focus();
  if (editor.tagName === "TEXTAREA") {
    editor.value = text;
    editor.dispatchEvent(new Event("input", { bubbles: true }));
  } else {
    editor.textContent = text;
    editor.dispatchEvent(new InputEvent("input", { bubbles: true, data: text }));
  }
  await sleep(200);

  const buttons = [...document.querySelectorAll("button, [role=button], span")]
    .filter(el => /发送|send/i.test((el.textContent || "").trim()) && el.offsetParent !== null);
  const btn = buttons[0];
  if (!btn) {
    editor.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", keyCode: 13, bubbles: true }));
    return { ok: true, mode: "enter_key", editor: editor.tagName };
  }
  btn.click();
  return { ok: true, mode: "button_click", label: (btn.textContent || "").trim().slice(0,20) };
}
"""


class CdpUiSender:
    def __init__(self, *, port: int = DEFAULT_CDP_PORT, timeout_sec: float = 25.0) -> None:
        self.port = port
        self.timeout_sec = timeout_sec

    @staticmethod
    def available(port: int = DEFAULT_CDP_PORT) -> bool:
        from pigeon_protocol.cdp_bridge import cdp_ready

        return cdp_ready(port)

    async def send_text(self, text: str) -> dict[str, Any]:
        from playwright.async_api import async_playwright

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
                await page.goto(FEIGE_MAIN, wait_until="domcontentloaded")
                await page.wait_for_timeout(4000)
            result = await page.evaluate(_SEND_JS, text)
            return result if isinstance(result, dict) else {"ok": False, "error": "bad_result"}

    def send(self, text: str) -> dict[str, Any]:
        if not self.available(self.port):
            return {"ok": False, "error": f"CDP not ready on {self.port}"}
        try:
            return asyncio.run(self.send_text(text))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
