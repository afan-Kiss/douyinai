"""Build / scrape Feige WS v2 URLs — cold start without CDP/HAR."""
from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import quote, urlencode

logger = logging.getLogger("pigeon.ws_url")

WS_HOST = "wss://ws.fxg.jinritemai.com/ws/v2"

# Official Feige web IM constants (from im.jinritemai.com.har / reverse_session.json)
FEIGE_WS_AID = "1383"
FEIGE_WS_FPID = "92"
FEIGE_WS_ACCESS_KEY = "edc810b287161555b85f088064f8ead1"
FEIGE_WS_VERSION = "10000"

WS_URL_PATTERNS = (
    re.compile(r"wss://ws\.fxg\.jinritemai\.com/ws/v2\?[^\"'\\s<>]+"),
    re.compile(r"wss:\\/\\/ws\.fxg\.jinritemai\.com\\/ws\\/v2\?[^\"'\\s<>]+"),
    re.compile(r'"wss://ws\.fxg\.jinritemai\.com/ws/v2\?[^"]+"'),
)

TOKEN_RE = re.compile(r'"token"\s*:\s*"([A-Za-z0-9_+\-]{20,80})"')
ACCESS_KEY_RE = re.compile(r'"access_key"\s*:\s*"([a-f0-9]{32})"')
PIGEON_SIGN_RE = re.compile(r"MIHA[A-Za-z0-9+/=_\-]{80,400}")


def _normalize_url(raw: str) -> str:
    url = raw.strip().strip('"').strip("'")
    url = url.replace("\\/", "/").replace("\\u0026", "&")
    if url.endswith("\\"):
        url = url[:-1]
    return url


def scan_ws_urls(text: str) -> list[str]:
    found: list[str] = []
    for pat in WS_URL_PATTERNS:
        for m in pat.finditer(text):
            url = _normalize_url(m.group(0))
            if url.startswith("wss://") and url not in found:
                found.append(url)
    return found


def extract_ws_tokens_from_text(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    m = TOKEN_RE.search(text)
    if m:
        out["token"] = m.group(1)
    m = ACCESS_KEY_RE.search(text)
    if m:
        out["access_key"] = m.group(1)
    signs = PIGEON_SIGN_RE.findall(text)
    if signs:
        out["pigeon_sign"] = max(signs, key=len)
    return out


def _device_id(session) -> str:
    return (
        str(session.cookies.get("PIGEON_CID") or "")
        or str(getattr(session, "device_id", "") or "")
        or str(session.query_tokens.get("device_id") or "")
    )


def find_working_ws_url(session, *, timeout_sec: float = 4.0) -> str | None:
    """Return first ws.fxg URL that accepts a connect (Cookie + UA)."""
    import asyncio

    candidates: list[str] = []
    live = pick_live_ws_url(session)
    if live:
        candidates.append(live)
    for url in reversed(session.ws_urls or []):
        if "ws.fxg.jinritemai.com" in url and url not in candidates:
            candidates.append(url)
    built = build_ws_url(session)
    if built and built not in candidates:
        candidates.append(built)

    async def _scan() -> str | None:
        for url in candidates:
            pr = await probe_ws_url(session, url, timeout_sec=timeout_sec)
            if pr.get("ok"):
                return url
        return None

    try:
        return asyncio.run(_scan())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_scan())
        finally:
            loop.close()


def pick_live_ws_url(session) -> str | None:
    """Pick WS URL matching current session token (latest CDP / init sync)."""
    from urllib.parse import parse_qs, urlparse

    tok = str(session.query_tokens.get("token") or "")
    if tok:
        for url in reversed(session.ws_urls or []):
            if "ws.fxg.jinritemai.com" not in url:
                continue
            qtok = (parse_qs(urlparse(url).query).get("token") or [""])[0]
            if qtok == tok:
                return url
    for url in reversed(session.ws_urls or []):
        if "ws.fxg.jinritemai.com" in url and "token=" in url:
            return url
    return None


def canonicalize_ws_session(session) -> dict[str, Any]:
    """
    Pure-protocol WS credential sync: query_tokens.token + paired pigeon_sign → ws URL.
    Prevents stale ws_urls[] from overwriting a fresh bootstrap token.
    """
    from pigeon_protocol.foundation.pigeon_sign_service import ensure_pigeon_sign, sign_for_token

    report: dict[str, Any] = {"sources": []}

    tok = str(session.query_tokens.get("token") or "")
    if not tok:
        for url in reversed(session.ws_urls or []):
            if "ws.fxg.jinritemai.com" not in url:
                continue
            from urllib.parse import parse_qs, urlparse

            tok = (parse_qs(urlparse(url).query).get("token") or [""])[0]
            if tok:
                session.query_tokens["token"] = tok
                report["sources"].append("ws_url_token")
                break

    ensure_pigeon_sign(session)
    sign = sign_for_token(session, tok) if tok else str(session.query_tokens.get("pigeon_sign") or "")
    if sign and session.query_tokens.get("pigeon_sign") != sign:
        session.query_tokens["pigeon_sign"] = sign
        report["sources"].append("sign_for_token")

    built = build_ws_url(session, token=tok, pigeon_sign=sign) if tok and sign else build_ws_url(session)
    if built:
        promote_ws_url(session, built)
        report["ws_url"] = built[:120] + "…"
        report["sources"].append("synthesized")
        session.merge_query_tokens(built)
        session.query_tokens["access_key"] = FEIGE_WS_ACCESS_KEY

    report["has_token"] = bool(session.query_tokens.get("token"))
    report["has_sign"] = bool(session.query_tokens.get("pigeon_sign"))
    report["ok"] = bool(built or pick_live_ws_url(session))
    return report


def promote_ws_url(session, url: str) -> bool:
    """Append URL and sync query_tokens; dedupe older entries with same token."""
    from urllib.parse import parse_qs, urlparse

    if not url or not url.startswith("wss://"):
        return False
    tok = (parse_qs(urlparse(url).query).get("token") or [""])[0]
    if tok:
        session.ws_urls = [
            u
            for u in (session.ws_urls or [])
            if (parse_qs(urlparse(u).query).get("token") or [""])[0] != tok
        ]
    if url not in (session.ws_urls or []):
        session.ws_urls.append(url)
    session.merge_query_tokens(url)
    return True


def _access_key(session) -> str:
    """ws.fxg.jinritemai.com pigeon IM always uses the official constant (not get_link_info appKey)."""
    return FEIGE_WS_ACCESS_KEY


def build_ws_url(session, *, token: str = "", pigeon_sign: str = "") -> str | None:
    """Synthesize official ws.fxg v2 URL from session tokens + Feige constants."""
    from pigeon_protocol.foundation.pigeon_sign_service import ensure_pigeon_sign, sign_for_token

    ensure_pigeon_sign(session)
    tok = token or str(session.query_tokens.get("token") or "")
    sign = pigeon_sign or sign_for_token(session, tok) or str(session.query_tokens.get("pigeon_sign") or "")
    dev = _device_id(session)
    if not tok or not sign or not dev:
        return None
    params = {
        "token": tok,
        "aid": FEIGE_WS_AID,
        "fpid": FEIGE_WS_FPID,
        "device_id": dev,
        "access_key": _access_key(session),
        "device_platform": "web",
        "version_code": FEIGE_WS_VERSION,
        "pigeon_source": "web",
        "PIGEON_BIZ_TYPE": "2",
        "pigeon_sign": sign,
    }
    return f"{WS_HOST}?{urlencode(params, quote_via=quote)}"


def apply_ws_url(session, url: str) -> bool:
    return promote_ws_url(session, url)


def ensure_ws_url(session, *, html: str = "") -> dict[str, Any]:
    """
    Cold-start WS URL: scrape HTML → extract tokens → synthesize → validate.
    """
    report: dict[str, Any] = {"sources": []}

    if html:
        for url in scan_ws_urls(html):
            apply_ws_url(session, url)
            report["sources"].append("html_url")
        tok = extract_ws_tokens_from_text(html)
        for k, v in tok.items():
            if v and session.query_tokens.get(k) != v:
                session.query_tokens[k] = v
                report["sources"].append(f"html_{k}")

    built = build_ws_url(session)
    if built:
        report["built_url"] = built[:120] + "…"
        if built not in (session.ws_urls or []):
            session.ws_urls.append(built)
            report["sources"].append("synthesized")
        session.merge_query_tokens(built)

    report["ws_urls"] = len(session.ws_urls or [])
    report["has_token"] = bool(session.query_tokens.get("token"))
    report["has_pigeon_sign"] = bool(session.query_tokens.get("pigeon_sign"))
    report["ok"] = bool(session.ws_urls)
    return report


async def probe_ws_url(session, url: str | None = None, *, timeout_sec: float = 5.0) -> dict[str, Any]:
    """Lightweight connect test — confirms URL + cookies are accepted."""
    import websockets

    target = url or pick_live_ws_url(session) or ""
    if not target:
        for u in reversed(session.ws_urls or []):
            if "ws.fxg.jinritemai.com" in u:
                target = u
                break
    if not target:
        return {"ok": False, "error": "no ws url"}
    cookie = session.cookie_header()
    if not cookie:
        return {"ok": False, "error": "no cookies"}
    try:
        async with websockets.connect(
            target,
            additional_headers={"Cookie": cookie, "User-Agent": session.user_agent or ""},
            open_timeout=timeout_sec,
            close_timeout=2,
        ):
            return {"ok": True, "url": target[:120]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200], "url": target[:120]}


def probe_ws_url_sync(session, url: str | None = None) -> dict[str, Any]:
    import asyncio

    return asyncio.run(probe_ws_url(session, url))
