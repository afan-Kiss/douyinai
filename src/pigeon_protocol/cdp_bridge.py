from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from typing import Any
from urllib.parse import parse_qs, urlparse

from pigeon_protocol.config import ORDER_QUERY_PATH, PIGEON_HOST
from pigeon_protocol.session import SessionState, save_session

logger = logging.getLogger("pigeon.cdp")

DEFAULT_CDP_PORT = int(os.getenv("CDP_PORT", "9222"))
SIGN_KEYS = ("verifyFp", "fp", "msToken", "a_bogus")

_PAGE_FETCH_JS = r"""
async (payload) => {
  const { url, method = "POST", body = null, bodyB64 = null, contentType = null } = payload || {};
  if (!url) return { ok: false, status: 0, error: "no_url" };
  try {
    let reqBody = undefined;
    const headers = {};
    if (bodyB64) {
      const bin = Uint8Array.from(atob(bodyB64), c => c.charCodeAt(0));
      reqBody = bin;
      headers["content-type"] = contentType || "application/x-protobuf";
    } else if (body != null) {
      reqBody = JSON.stringify(body);
      headers["content-type"] = contentType || "application/json;charset=UTF-8";
    }
    const resp = await fetch(url, { method, credentials: "include", headers, body: reqBody });
    const ct = resp.headers.get("content-type") || "";
    let text = "";
    let bodyB64Out = "";
    if (ct.includes("protobuf") || ct.includes("octet-stream")) {
      const buf = await resp.arrayBuffer();
      const arr = new Uint8Array(buf);
      let s = "";
      for (let i = 0; i < arr.length; i++) s += String.fromCharCode(arr[i]);
      bodyB64Out = btoa(s);
    } else {
      text = await resp.text();
    }
    return {
      ok: resp.ok,
      status: resp.status,
      finalUrl: (resp.url || url).slice(0, 2000),
      text: text.slice(0, 800000),
      bodyB64: bodyB64Out.slice(0, 1200000),
      contentType: ct,
    };
  } catch (error) {
    return { ok: false, status: 0, error: String(error), text: "" };
  }
}
"""


def cdp_ready(port: int = DEFAULT_CDP_PORT) -> bool:
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _sync_sign_tokens(session: SessionState, url: str) -> dict[str, str]:
    qs = parse_qs(urlparse(url).query)
    updated: dict[str, str] = {}
    for key in SIGN_KEYS:
        if qs.get(key):
            session.query_tokens[key] = qs[key][0]
            updated[key] = session.query_tokens[key][:48]
    return updated


def _find_feige_page(pages: list[Any]) -> Any | None:
    for page in pages:
        url = page.url or ""
        if "jinritemai.com" in url and "about:blank" not in url:
            return page
    return pages[0] if pages else None


class CdpBridge:
    """Run signed backstage HTTP inside Feige Chrome (bdms auto-signs fetch)."""

    def __init__(
        self,
        session: SessionState,
        *,
        port: int = DEFAULT_CDP_PORT,
        timeout_sec: float = 15.0,
    ) -> None:
        self.session = session
        self.port = port
        self.timeout_sec = timeout_sec

    async def _page_fetch(
        self,
        *,
        url: str,
        method: str = "POST",
        body: dict[str, Any] | None = None,
        body_bytes: bytes | None = None,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        from playwright.async_api import async_playwright

        payload: dict[str, Any] = {"url": url, "method": method, "body": body}
        if body_bytes is not None:
            payload["bodyB64"] = base64.b64encode(body_bytes).decode("ascii")
            payload["contentType"] = content_type or "application/x-protobuf"
        elif content_type:
            payload["contentType"] = content_type

        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(
                f"http://127.0.0.1:{self.port}",
                timeout=int(self.timeout_sec * 1000),
            )
            ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = _find_feige_page(ctx.pages)
            if page is None:
                page = await ctx.new_page()
                await page.goto(
                    "https://im.jinritemai.com/pc_seller_v2/main",
                    wait_until="domcontentloaded",
                    timeout=int(self.timeout_sec * 1000),
                )
                await page.wait_for_timeout(2500)
            try:
                result = await asyncio.wait_for(
                    page.evaluate(_PAGE_FETCH_JS, payload),
                    timeout=self.timeout_sec,
                )
            except Exception as exc:
                return {"ok": False, "error": str(exc), "via": "cdp_fetch"}
            if not isinstance(result, dict):
                return {"ok": False, "error": "bad_eval_result", "text": ""}

            final_url = str(result.get("finalUrl") or url)
            if "a_bogus=" in final_url:
                _sync_sign_tokens(self.session, final_url)
                save_session(self.session)

            text = str(result.get("text") or "")
            body_b64 = str(result.get("bodyB64") or "")
            parsed: Any
            if body_b64:
                parsed = {"protobuf": True, "size": len(base64.b64decode(body_b64))}
            else:
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    parsed = {"raw_text": text[:2000]}

            return {
                "ok": bool(result.get("ok")),
                "status": result.get("status"),
                "url": url,
                "final_url": final_url,
                "data": parsed,
                "body_b64": body_b64,
                "headers": {},
                "via": "cdp_fetch",
            }

    def fetch_binary(
        self,
        *,
        url: str,
        method: str = "POST",
        body_bytes: bytes,
        content_type: str = "application/x-protobuf",
    ) -> dict[str, Any]:
        if not cdp_ready(self.port):
            return {"ok": False, "error": f"Chrome CDP not ready on port {self.port}", "via": "cdp_fetch"}
        try:
            return asyncio.run(
                self._page_fetch(
                    url=url,
                    method=method,
                    body_bytes=body_bytes,
                    content_type=content_type,
                )
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc), "via": "cdp_fetch"}

    def fetch_pigeon_im_history(self, security_user_id: str, *, shop_id: str = "") -> dict[str, Any]:
        from pigeon_protocol.pigeon_im import (
            build_get_by_conversation_url,
            patch_conversation_in_body,
            _load_post_bytes,
        )

        body = patch_conversation_in_body(
            _load_post_bytes(),
            security_user_id,
            shop_id or self.session.shop_id,
            session=self.session,
        )
        return self.fetch_binary(
            url=build_get_by_conversation_url(self.session),
            method="POST",
            body_bytes=body,
        )

    def fetch_json(
        self,
        *,
        url: str,
        method: str = "POST",
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not cdp_ready(self.port):
            return {
                "ok": False,
                "error": f"Chrome CDP not ready on port {self.port}",
                "via": "cdp_fetch",
            }
        try:
            return asyncio.run(self._page_fetch(url=url, method=method, body=body))
        except Exception as exc:
            return {"ok": False, "error": str(exc), "via": "cdp_fetch"}

    def query_orders(self, security_user_id: str) -> dict[str, Any]:
        base = f"{PIGEON_HOST}{ORDER_QUERY_PATH}"
        url = (
            f"{base}?biz_type=4&PIGEON_BIZ_TYPE=2&_pms=1&device_platform=web"
            f"&FUSION=true&_v=1.0.1.7626"
        )
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
        return self.fetch_json(url=url, method="POST", body=body)

    async def probe_bdms(self) -> dict[str, Any]:
        probe_js = r"""
        () => {
          const b = window.bdms;
          if (!b) return { hasBdms: false };
          const info = { hasBdms: true, keys: Object.keys(b) };
          for (const k of info.keys) {
            const v = b[k];
            info[k] = typeof v === "function" ? v.toString().slice(0, 240) : typeof v;
          }
          info.script = [...document.scripts].map(s => s.src).filter(u => /bdms|secsdk|mssdk/i.test(u||''));
          return info;
        }
        """
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{self.port}")
            page = _find_feige_page(browser.contexts[0].pages)
            if page is None:
                return {"ok": False, "error": "no page"}
            return await page.evaluate(probe_js)
