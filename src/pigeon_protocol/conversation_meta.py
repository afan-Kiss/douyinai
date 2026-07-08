"""Resolve talk_id / conversation route for WS send and context."""
from __future__ import annotations

import json
import re
from typing import Any

from pigeon_protocol.capture_loader import index_captures, load_capture
from pigeon_protocol.http_client import BackstageHttpClient
from pigeon_protocol.session import SessionState
from pigeon_protocol.ws_protocol import ConversationMeta


def resolve_conversation_meta(
    session: SessionState,
    security_user_id: str,
    *,
    talk_id: str = "",
    use_cdp: bool = False,
) -> ConversationMeta:
    shop_id = session.shop_id or session.cookies.get("SHOP_ID") or "263636465"
    meta = ConversationMeta(security_user_id=security_user_id, shop_id=shop_id, talk_id=talk_id)

    if talk_id:
        return meta

    # HAR captures often contain talk_id for this buyer
    for path in index_captures().http_bodies:
        try:
            event = load_capture(path)
        except Exception:
            continue
        post = str(event.get("post_data") or "")
        if security_user_id not in post:
            continue
        m = re.search(r'"talk_id"\s*:\s*"(\d+)"', post)
        if m:
            meta.talk_id = m.group(1)
            return meta
        url = str(event.get("url") or "")
        m2 = re.search(r"talk_id=(\d+)", url)
        if m2 and security_user_id in url:
            meta.talk_id = m2.group(1)
            return meta

    card = BackstageHttpClient(session).get_user_card(security_user_id)
    if isinstance(card, dict):
        data = card.get("data") if isinstance(card.get("data"), dict) else {}
        inner = data.get("data") if isinstance(data.get("data"), dict) else data
        tid = str(inner.get("talk_id") or inner.get("conversation_id") or "")
        if tid.isdigit():
            meta.talk_id = tid

    return meta


def resolve_from_send_template(template: dict[str, Any]) -> ConversationMeta | None:
    import base64

    payload = template.get("payload") or ""
    if not payload:
        return None
    try:
        raw = base64.b64decode(str(payload))
    except Exception:
        return None
    from pigeon_protocol.ws_protocol import extract_meta_from_bytes

    return extract_meta_from_bytes(raw)
