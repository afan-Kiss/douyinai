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
    """Varint-safe route + pigeon_sign patch for get_by_conversation protobuf body."""
    from pigeon_protocol.init_body import PIGEON_SIGN_MARKER, patch_string_after_marker
    from pigeon_protocol.ws_protocol import patch_conversation_route

    data = bytearray(body)
    sid = shop_id or "263636465"
    if security_user_id.startswith("AQ"):
        patch_conversation_route(
            data,
            security_user_id=security_user_id,
            shop_id=sid,
        )
    if session:
        cid = str(session.cookies.get("PIGEON_CID") or session.device_id or "")
        if cid:
            text = data.decode("latin-1", errors="ignore")
            text = re.sub(r"(?<=J\x13)\d{15,20}(?=Z\x03web)", cid, text, count=1)
            data = bytearray(text.encode("latin-1"))
        sign = str(session.query_tokens.get("pigeon_sign") or "")
        if sign:
            patch_string_after_marker(data, PIGEON_SIGN_MARKER, sign)
    return bytes(data)


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
    from pigeon_protocol.buyer_display_name import (
        _protobuf_blob,
        extract_buyer_nickname_for_uid,
        extract_buyer_nickname_from_protobuf,
        is_bad_display_name,
        remember_buyer_display_name,
    )

    data_b64 = raw.get("body_b64") or ""
    data = base64.b64decode(data_b64) if data_b64 else b""
    messages = parse_messages_from_protobuf(data)
    buyer_name = extract_buyer_nickname_for_uid(data, security_user_id)
    if not buyer_name:
        uid_blob = _protobuf_blob(data)
        if security_user_id and security_user_id not in uid_blob:
            buyer_name = ""
        else:
            buyer_name = extract_buyer_nickname_from_protobuf(data)
    if not buyer_name:
        for msg in messages:
            if str(msg.get("role") or "") not in ("buyer", "customer"):
                continue
            nick = str(msg.get("nickname") or "").strip()
            if nick and not is_bad_display_name(nick, uid=security_user_id):
                buyer_name = nick
                break
    route = ""
    m = re.search(rb"AQ[Cc][A-Za-z0-9_-]{30,200}:\d+::\d+:\d+:pigeon", data)
    if m:
        route = m.group(0).decode("ascii", errors="ignore")
    ctx = ConversationContext(
        conversation_id=route,
        security_user_id=security_user_id,
        shop_id="",
        buyer_name=buyer_name,
        messages=[msg for msg in messages if msg.get("text")],
        source="cdp/pigeon_im/get_by_conversation",
        raw=raw,
    )
    if buyer_name and security_user_id:
        try:
            from pigeon_protocol.session import load_session

            remember_buyer_display_name(load_session(), security_user_id, buyer_name, save=True)
        except Exception:
            pass
    return ctx


def fetch_context_pure(session: SessionState, security_user_id: str, *, shop_id: str = "") -> ConversationContext:
    """Pure curl_cffi — official IM headers + signed URL + protobuf body."""
    from pigeon_protocol.foundation.chrome_hints import pigeon_im_headers
    from pigeon_protocol.http_client import DEFAULT_CURL_IMPERSONATE
    from pigeon_protocol.http_transport import curl_cffi_available

    sid = shop_id or session.shop_id or "263636465"

    def _build_body() -> bytes:
        return patch_conversation_in_body(
            _load_post_bytes(),
            security_user_id,
            sid,
            session=session,
        )

    def _ensure_im_session(*, force: bool = False) -> None:
        sign = str(session.query_tokens.get("pigeon_sign") or "")
        if sign and not force:
            return
        try:
            from pigeon_protocol.feige_init import bootstrap_feige_session

            bootstrap_feige_session(session, persist=True)
        except Exception:
            pass

    def _do_post(url: str, body: bytes):
        headers = pigeon_im_headers(session)
        if curl_cffi_available():
            from curl_cffi import requests as curl_requests

            return curl_requests.post(
                url,
                data=body,
                headers=headers,
                impersonate=DEFAULT_CURL_IMPERSONATE,
                timeout=12,
            ), "curl_cffi/pigeon_im"
        import httpx

        return (
            httpx.Client(timeout=12.0, follow_redirects=True).post(url, content=body, headers=headers),
            "httpx/pigeon_im",
        )

    _ensure_im_session()
    body = _build_body()
    url = build_get_by_conversation_url(session)
    resp, via = _do_post(url, body)
    content = resp.content or b""
    if resp.status_code == 200 and content and b"lz4:" in content[:160]:
        _ensure_im_session(force=True)
        body = _build_body()
        url2 = build_pigeon_im_url(session, GET_BY_CONV, sign=False)
        resp2, via2 = _do_post(url2, body)
        if len(resp2.content or b"") > len(content):
            resp, via = resp2, via2
            content = resp.content or b""
        elif len(content) < 300:
            resp3, via3 = _do_post(build_get_by_conversation_url(session), body)
            if len(resp3.content or b"") > len(content):
                resp, via = resp3, via3

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
