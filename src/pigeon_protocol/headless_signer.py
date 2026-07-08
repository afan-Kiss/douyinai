"""Headless bdms signer — self-contained Playwright browser (no external CDP 9222)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from pigeon_protocol.sign import SIGN_KEYS, parse_sign_tokens

logger = logging.getLogger("pigeon.headless_sign")

_PAGE_FETCH_JS = r"""
async (payload) => {
  const { url, method = "GET", body = null } = payload || {};
  const resp = await fetch(url, {
    method,
    credentials: "include",
    headers: body != null ? { "content-type": "application/json;charset=UTF-8" } : undefined,
    body: body != null ? (typeof body === "string" ? body : JSON.stringify(body)) : undefined,
  });
  return { finalUrl: (resp.url || url).slice(0, 3000), status: resp.status };
}
"""

_LOCK = threading.Lock()
_RUNTIME: "_HeadlessRuntime | None" = None


class _HeadlessRuntime:
    def __init__(self) -> None:
        self._ready = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._page = None
        self._browser = None
        self._playwright = None

    def start(self) -> None:
        if self._ready:
            return
        with _LOCK:
            if self._ready:
                return
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()
            fut = asyncio.run_coroutine_threadsafe(self._boot(), self._loop)
            fut.result(timeout=120)
            self._ready = True

    def _run_loop(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _boot(self) -> None:
        from playwright.async_api import async_playwright

        from pigeon_protocol.config import IM_HOST, SESSION_FILE
        from pigeon_protocol.session import load_session

        session = load_session()
        user_data = Path(__file__).resolve().parents[2] / "session" / "headless_profile"
        user_data.mkdir(parents=True, exist_ok=True)

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(user_data),
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
            user_agent=session.user_agent or None,
            viewport={"width": 853, "height": 817},
        )
        self._page = self._browser.pages[0] if self._browser.pages else await self._browser.new_page()

        cookies = []
        for c in session.cookies or []:
            if not c.get("name"):
                continue
            cookies.append(
                {
                    "name": c["name"],
                    "value": c.get("value", ""),
                    "domain": c.get("domain") or ".jinritemai.com",
                    "path": c.get("path") or "/",
                }
            )
        if cookies:
            await self._browser.add_cookies(cookies)

        await self._page.goto(
            f"{IM_HOST}/pc_seller_v2/main/workspace",
            wait_until="domcontentloaded",
            timeout=90000,
        )
        await self._page.wait_for_timeout(3000)
        has_bdms = await self._page.evaluate("() => !!window.bdms?.init")
        if not has_bdms:
            await self._page.wait_for_timeout(5000)
            has_bdms = await self._page.evaluate("() => !!window.bdms?.init")
        if not has_bdms:
            raise RuntimeError("headless page: bdms not loaded")
        logger.info("headless bdms ready (profile=%s)", user_data)

    async def _sign_async(
        self,
        url: str,
        *,
        method: str = "GET",
        body: dict[str, Any] | str | None = None,
    ) -> dict[str, str]:
        if not self._page:
            raise RuntimeError("headless signer not booted")
        result = await self._page.evaluate(
            _PAGE_FETCH_JS,
            {"url": url, "method": method, "body": body},
        )
        return parse_sign_tokens(str(result.get("finalUrl") or url))

    def sign_tokens(
        self,
        url: str,
        *,
        method: str = "GET",
        body: dict[str, Any] | str | None = None,
    ) -> dict[str, str]:
        self.start()
        assert self._loop is not None
        fut = asyncio.run_coroutine_threadsafe(
            self._sign_async(url, method=method, body=body),
            self._loop,
        )
        return fut.result(timeout=30)


class HeadlessBdmsSigner:
    """Offline-capable a_bogus via embedded headless Chromium (bdms VM)."""

    @staticmethod
    def available() -> bool:
        if os.getenv("PIGEON_NO_HEADLESS_SIGN", "").strip().lower() in ("1", "true", "yes"):
            return False
        try:
            import playwright  # noqa: F401

            return True
        except ImportError:
            return False

    def sign_tokens(
        self,
        url: str,
        *,
        method: str = "GET",
        body: dict[str, Any] | str | None = None,
    ) -> dict[str, str]:
        global _RUNTIME
        if _RUNTIME is None:
            _RUNTIME = _HeadlessRuntime()
        return _RUNTIME.sign_tokens(url, method=method, body=body)

    def sign_url(self, url: str, **kwargs: Any) -> str:
        from pigeon_protocol.sign import apply_sign_tokens

        return apply_sign_tokens(url, self.sign_tokens(url, **kwargs))


def shutdown_headless() -> None:
    global _RUNTIME
    if _RUNTIME and _RUNTIME._loop:
        _RUNTIME._loop.call_soon_threadsafe(_RUNTIME._loop.stop)
    _RUNTIME = None
