"""a_bogus / msToken signing for backstage HTTP.

Pure algorithm (bdms 1.0.1.20):
- Python: foundation.bdms_abogus.FeigeABogus + bdms_tokens append
- Node fallback: scripts/run_bdms_fetch.mjs
- CDP: CdpSigner when browser tab available
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger("pigeon.sign")

SIGN_KEYS = ("verifyFp", "fp", "msToken", "a_bogus")
DEFAULT_CDP_PORT = int(os.getenv("CDP_PORT", "9222"))

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


def parse_sign_tokens(url: str) -> dict[str, str]:
    qs = parse_qs(urlparse(url).query)
    return {k: qs[k][0] for k in SIGN_KEYS if qs.get(k)}


def apply_sign_tokens(base_url: str, tokens: dict[str, str]) -> str:
    from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

    parsed = urlparse(base_url)
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    params.update({k: v for k, v in tokens.items() if v})
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, urlencode(params), parsed.fragment))


class CdpSigner:
    """Sign URLs using bdms hooks inside logged-in Feige Chrome."""

    def __init__(self, port: int = DEFAULT_CDP_PORT, timeout_sec: float = 15.0) -> None:
        self.port = port
        self.timeout_sec = timeout_sec

    @staticmethod
    def available(port: int = DEFAULT_CDP_PORT) -> bool:
        import urllib.error
        import urllib.request

        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as resp:
                return resp.status == 200
        except (urllib.error.URLError, TimeoutError, OSError):
            return False

    async def _sign_async(
        self,
        url: str,
        *,
        method: str = "GET",
        body: dict[str, Any] | str | None = None,
    ) -> dict[str, str]:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(
                f"http://127.0.0.1:{self.port}",
                timeout=int(self.timeout_sec * 1000),
            )
            pages = browser.contexts[0].pages
            page = next((pg for pg in pages if "jinritemai" in (pg.url or "")), pages[0] if pages else None)
            if page is None:
                raise RuntimeError("no Feige page in CDP browser")

            result = await asyncio.wait_for(
                page.evaluate(_PAGE_FETCH_JS, {"url": url, "method": method, "body": body}),
                timeout=self.timeout_sec,
            )
            final_url = str(result.get("finalUrl") or url)
            tokens = parse_sign_tokens(final_url)
            if not tokens.get("a_bogus"):
                logger.warning("sign produced no a_bogus for %s", url[:120])
            return tokens

    def sign_url(
        self,
        url: str,
        *,
        method: str = "GET",
        body: dict[str, Any] | str | None = None,
    ) -> str:
        if not self.available(self.port):
            raise RuntimeError(f"Chrome CDP not ready on port {self.port}")
        tokens = asyncio.run(self._sign_async(url, method=method, body=body))
        return apply_sign_tokens(url, tokens)

    def sign_tokens(
        self,
        url: str,
        *,
        method: str = "GET",
        body: dict[str, Any] | str | None = None,
    ) -> dict[str, str]:
        if not self.available(self.port):
            raise RuntimeError(f"Chrome CDP not ready on port {self.port}")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._sign_async(url, method=method, body=body))
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(
                lambda: asyncio.run(self._sign_async(url, method=method, body=body))
            ).result(timeout=self.timeout_sec)


class NodeBdmsSigner:
    """Node/jsdom bdms signer — delegates to foundation.bdms_sign."""

    def __init__(self, root: str | None = None) -> None:
        from pathlib import Path

        self.root = Path(root or Path(__file__).resolve().parents[2])

    def available(self) -> bool:
        from pigeon_protocol.foundation.bdms_sign import node_available

        return node_available()

    def sign_tokens(
        self,
        url: str,
        *,
        method: str = "GET",
        body: dict[str, Any] | str | None = None,
        **_: Any,
    ) -> dict[str, str]:
        from pigeon_protocol.foundation.bdms_sign import sign_backstage_url

        body_dict = body if isinstance(body, dict) else None
        result = sign_backstage_url(url, method=method, body=body_dict, prefer_python=False)
        if not result.ok:
            raise RuntimeError(result.error or "node bdms sign failed")
        return result.tokens


def get_signer(*, prefer: str = "auto") -> CdpSigner | NodeBdmsSigner | Any:
    from pigeon_protocol.headless_signer import HeadlessBdmsSigner

    order = prefer.split(",") if prefer != "auto" else ["node", "headless", "cdp"]
    for name in order:
        name = name.strip().lower()
        if name == "node":
            node = NodeBdmsSigner()
            if node.available():
                return node
        elif name == "headless":
            if HeadlessBdmsSigner.available():
                return HeadlessBdmsSigner()
        elif name == "cdp" and CdpSigner.available():
            return CdpSigner()
    return CdpSigner()
