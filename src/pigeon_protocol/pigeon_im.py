from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from pigeon_protocol.capture_loader import load_capture
from pigeon_protocol.config import LIVE_CAPTURES
from pigeon_protocol.models import ConversationContext
from pigeon_protocol.pure_config import BUNDLE_CONTEXT_BODY, bundle_first_assets
from pigeon_protocol.session import SessionState
from pigeon_protocol.parsers.protobuf_strings import parse_messages_from_protobuf

FXG_HOST = "https://fxg.jinritemai.com"
GET_BY_CONV = "/pigeon_im/v1/message/get_by_conversation"
HAR_TEMPLATE = LIVE_CAPTURES / "from_har" / "har_00327_http_body.json"


def _load_post_bytes(path: Path | None = None) -> bytes:
    if path and path.is_file():
        if path.suffix == ".bin":
            return path.read_bytes()
        event = load_capture(path)
        raw = str(event.get("post_data") or "")
        return raw.encode("latin-1")
    if bundle_first_assets() and BUNDLE_CONTEXT_BODY.is_file():
        return BUNDLE_CONTEXT_BODY.read_bytes()
    if HAR_TEMPLATE.is_file():
        event = load_capture(HAR_TEMPLATE)
        raw = str(event.get("post_data") or "")
        return raw.encode("latin-1")
    if BUNDLE_CONTEXT_BODY.is_file():
        return BUNDLE_CONTEXT_BODY.read_bytes()
    raise FileNotFoundError("get_by_conversation body missing — export standalone_bundle/get_by_conversation_body.bin")


def patch_conversation_in_body(body: bytes, security_user_id: str, shop_id: str, session: SessionState | None = None) -> bytes:
    text = body.decode("latin-1", errors="ignore")
    m = re.search(r"AQ[Cc][A-Za-z0-9_-]{30,200}:\d+::\d+:\d+:pigeon", text)
    if m and security_user_id.startswith("AQ"):
        new_route = f"{security_user_id}:{shop_id or '263636465'}::2:1:pigeon"
        text = text[: m.start()] + new_route + text[m.end() :]
    if session:
        cid = session.cookies.get("PIGEON_CID") or session.device_id or ""
        if cid:
            text = re.sub(r"(?<=J\x13)\d{15,20}(?=Z\x03web)", cid, text, count=1)
        sign = session.query_tokens.get("pigeon_sign") or ""
        if sign:
            text = re.sub(
                r"(pigeon_sign\x12[\x84-\x86]\x02)MIHA[A-Za-z0-9+/=_\-]{80,400}",
                rf"\1{sign}",
                text,
                count=1,
            )
    return text.encode("latin-1")


def build_pigeon_im_url(session: SessionState, path: str, *, sign: bool = True) -> str:
    """Official query: pigeon_source + PIGEON_BIZ_TYPE + pigeon_sign + bdms tokens."""
    params: dict[str, str] = {
        "pigeon_source": "web",
        "PIGEON_BIZ_TYPE": "2",
    }
    sign_val = session.query_tokens.get("pigeon_sign") or ""
    if sign_val:
        params["pigeon_sign"] = sign_val
    unsigned = f"{FXG_HOST}{path}?{urlencode(params)}"
    if not sign:
        return unsigned

    from pigeon_protocol.foundation.bdms_sign import sign_backstage_url
    from pigeon_protocol.foundation.bdms_tokens import append_backstage_query_tokens

    result = sign_backstage_url(unsigned, method="POST")
    if result.ok and result.signed_url:
        return result.signed_url
    return append_backstage_query_tokens(unsigned, session)


def build_get_by_conversation_url(session: SessionState | None = None, *, sign: bool | None = None) -> str:
    if session is None:
        from pigeon_protocol.session import load_session

        session = load_session()
    if sign is None:
        from pigeon_protocol.pure_config import pigeon_im_needs_sign

        sign = pigeon_im_needs_sign()
    return build_pigeon_im_url(session, GET_BY_CONV, sign=sign)


def context_from_cdp_fetch(raw: dict[str, Any], *, security_user_id: str) -> ConversationContext:
    data_b64 = raw.get("body_b64") or ""
    data = base64.b64decode(data_b64) if data_b64 else b""
    messages = parse_messages_from_protobuf(data)
    route = ""
    m = re.search(rb"AQ[Cc][A-Za-z0-9_-]{30,200}:\d+::\d+:\d+:pigeon", data)
    if m:
        route = m.group(0).decode("ascii", errors="ignore")
    return ConversationContext(
        conversation_id=route,
        security_user_id=security_user_id,
        shop_id="",
        buyer_name="",
        messages=[msg for msg in messages if msg.get("text")],
        source="cdp/pigeon_im/get_by_conversation",
        raw=raw,
    )


def fetch_context_pure(session: SessionState, security_user_id: str, *, shop_id: str = "") -> ConversationContext:
    """Pure curl_cffi — official IM headers + signed URL + protobuf body."""
    from pigeon_protocol.foundation.chrome_hints import pigeon_im_headers
    from pigeon_protocol.http_client import DEFAULT_CURL_IMPERSONATE
    from pigeon_protocol.http_transport import curl_cffi_available

    sid = shop_id or session.shop_id or "263636465"
    body = patch_conversation_in_body(
        _load_post_bytes(),
        security_user_id,
        sid,
        session=session,
    )
    url = build_get_by_conversation_url(session)
    headers = pigeon_im_headers(session)

    if curl_cffi_available():
        from curl_cffi import requests as curl_requests

        resp = curl_requests.post(
            url,
            data=body,
            headers=headers,
            impersonate=DEFAULT_CURL_IMPERSONATE,
            timeout=20,
        )
        via = "curl_cffi/pigeon_im"
    else:
        import httpx

        resp = httpx.Client(timeout=15.0, follow_redirects=True).post(url, content=body, headers=headers)
        via = "httpx/pigeon_im"

    raw: dict[str, Any] = {
        "ok": resp.status_code == 200,
        "status": resp.status_code,
        "url": str(resp.url),
        "body_b64": base64.b64encode(resp.content).decode("ascii") if resp.content else "",
        "via": via,
    }
    ctx = context_from_cdp_fetch(raw, security_user_id=security_user_id)
    ctx.source = "pure/pigeon_im/get_by_conversation"
    ctx.shop_id = sid
    return ctx
