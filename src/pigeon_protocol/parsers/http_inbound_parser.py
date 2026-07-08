from __future__ import annotations

import json
import re
from typing import Any

from pigeon_protocol.parsers.text_filters import is_meaningless_message, normalize_text
from pigeon_protocol.parsers.pigeon_frame_parser import (
    MESSAGE_KINDS,
    build_conversation_route,
    match_first,
    parse_inbound_frame,
)


HTTP_INBOUND_URL_HINTS = (
    "get_by_conversation",
    "pull",
    "sync",
    "inbox",
    "message/list",
    "answerRecommend",
    "msg_body",
    "pigeon_im",
    "backstage/robot",
)


def _role_from_sender_role(value: Any) -> str:
    role = str(value or "").strip()
    if role == "1":
        return "buyer"
    if role == "2":
        return "seller"
    return "system"


def _frame_to_parsed(frame: dict[str, Any], event: dict[str, Any]) -> dict[str, Any] | None:
    role = str(frame.get("role") or "buyer")
    text = str(frame.get("text") or "").strip()
    nickname = str(frame.get("nickname") or "")
    if not text or is_meaningless_message(text, role, nickname):
        return None
    return {
        "kind": frame.get("kind") or "inbound_message",
        "role": role,
        "text": text,
        "nickname": frame.get("nickname") or "",
        "conversation_id": frame.get("conversation_id") or "",
        "conversation_route": frame.get("conversation_route") or "",
        "security_receiver_id": frame.get("security_receiver_id") or "",
        "shop_id": frame.get("shop_id") or "",
        "server_message_id": frame.get("server_message_id") or "",
        "client_message_id": frame.get("client_message_id") or "",
        "direction": frame.get("direction"),
        "timestamp": event.get("ts"),
        "url": event.get("url") or "",
        "source": "http_cdp",
    }


def _parse_msg_body_item(item: dict[str, Any], event: dict[str, Any]) -> dict[str, Any] | None:
    ext = item.get("ext") if isinstance(item.get("ext"), dict) else {}
    text = str(item.get("content") or "").strip()
    sender_role = item.get("sender_role") or ext.get("sender_role")
    role = _role_from_sender_role(sender_role)
    nickname = str(ext.get("nickname") or ext.get("uname") or "").strip()
    if not text or is_meaningless_message(text, role, nickname):
        return None
    talk_id = str(ext.get("talk_id") or item.get("talk_id") or "").strip()
    security_sender_id = str(
        ext.get("security_sender_id") or item.get("sender") or ""
    ).strip()
    shop_id = str(ext.get("shop_id") or "").strip()
    conversation_route = str(
        ext.get("security_conversation_id")
        or ext.get("security_biz_conversation_id")
        or ""
    ).strip()
    if not conversation_route and security_sender_id and shop_id:
        conversation_route = build_conversation_route(security_sender_id, shop_id)

    flow_extra_raw = str(ext.get("flow_extra") or "")
    direction = int(match_first(flow_extra_raw, r'"direction"\s*:\s*(\d+)') or 0)
    if direction == 1:
        role = "buyer"
    elif direction == 2:
        role = "seller"
    elif direction in {3, 9, 10}:
        role = "system"

    return {
        "kind": "buyer_message" if role == "buyer" else "seller_message" if role == "seller" else "system_message",
        "role": role,
        "text": text,
        "nickname": nickname,
        "conversation_id": talk_id,
        "conversation_route": conversation_route,
        "security_receiver_id": security_sender_id,
        "shop_id": shop_id,
        "server_message_id": str(item.get("serverId") or ext.get("b_temp_track_message_id") or "").strip(),
        "client_message_id": str(ext.get("s:client_message_id") or "").strip(),
        "direction": direction,
        "timestamp": event.get("ts"),
        "url": event.get("url") or "",
        "source": "http_cdp",
    }


def _parse_msg_body_list_json(payload: str, event: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return []

    items = data.get("msg_body_list")
    if not isinstance(items, list):
        return []

    parsed: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized = _parse_msg_body_item(item, event)
        if normalized:
            parsed.append(normalized)
    return parsed


def _to_raw_bytes(body: str) -> bytes:
    try:
        return body.encode("latin-1")
    except UnicodeEncodeError:
        return body.encode("utf-8", errors="ignore")


def _parse_protobuf_batch(body: str, event: dict[str, Any]) -> list[dict[str, Any]]:
    data = _to_raw_bytes(body)
    parsed: list[dict[str, Any]] = []
    seen: set[str] = set()

    anchors = [match.start() for match in re.finditer(rb"s:client_message_id", data)]
    if not anchors:
        anchors = [0]

    for index, anchor in enumerate(anchors):
        start = max(0, anchor - 800)
        end = anchors[index + 1] if index + 1 < len(anchors) else min(len(data), anchor + 8000)
        chunk = data[start:end]
        if len(chunk) < 64:
            continue
        frame = parse_inbound_frame({"format": "binary", "payload_hex": chunk.hex()})
        if frame.get("kind") not in MESSAGE_KINDS:
            continue
        normalized = _frame_to_parsed(frame, event)
        if not normalized:
            continue
        dedupe_key = (
            normalized.get("server_message_id")
            or normalized.get("client_message_id")
            or f"{normalized.get('role')}:{normalized.get('text')}"
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        parsed.append(normalized)

    return parsed


def _scan_inline_json_messages(payload: str, event: dict[str, Any]) -> list[dict[str, Any]]:
    if not payload or "content" not in payload:
        return []

    parsed: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in re.finditer(r'"content"\s*:\s*"((?:\\.|[^"\\])*)"', payload):
        raw = match.group(1)
        try:
            text = json.loads(f'"{raw}"')
        except json.JSONDecodeError:
            text = raw.replace("\\n", "\n").replace('\\"', '"')
        text = str(text or "").strip()
        if not text:
            continue

        ctx = payload[max(0, match.start() - 800) : min(len(payload), match.end() + 800)]
        sender_role = match_first(ctx, r'"sender_role"\s*:\s*"?(1|2)"?')
        if not sender_role:
            continue
        role = _role_from_sender_role(sender_role)
        nickname = match_first(ctx, r'"nickname"\s*:\s*"([^"]+)"') or match_first(
            ctx, r'"uname"\s*:\s*"([^"]+)"'
        )
        if is_meaningless_message(text, role, nickname):
            continue
        talk_id = match_first(ctx, r'"talk_id"\s*:\s*"?(\d+)"?')
        server_id = match_first(ctx, r'"serverId"\s*:\s*"?(\d+)"?') or match_first(
            ctx, r'"b_temp_track_message_id"\s*:\s*"?(\d+)"?'
        )
        conversation_route = match_first(ctx, r'"security_conversation_id"\s*:\s*"([^"\\]+)"')
        if not conversation_route:
            conversation_route = match_first(ctx, r'"security_biz_conversation_id"\s*:\s*"([^"\\]+)"')

        dedupe_key = server_id or f"{role}:{text}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        parsed.append(
            {
                "kind": "buyer_message" if role == "buyer" else "seller_message" if role == "seller" else "system_message",
                "role": role,
                "text": text,
                "nickname": nickname,
                "conversation_id": talk_id,
                "conversation_route": conversation_route,
                "server_message_id": server_id,
                "timestamp": event.get("ts"),
                "url": event.get("url") or "",
                "source": "http_cdp",
            }
        )
    return parsed


def parse_http_inbound_messages(event: dict[str, Any]) -> list[dict[str, Any]]:
    url = str(event.get("url") or "")
    post_data = str(event.get("post_data") or "")
    response_body = event.get("response_body")

    parsed: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_items(items: list[dict[str, Any]]) -> None:
        for item in items:
            key = (
                item.get("server_message_id")
                or item.get("client_message_id")
                or f"{item.get('role')}:{item.get('text')}"
            )
            if key in seen:
                continue
            seen.add(key)
            parsed.append(item)

    for payload in (post_data, response_body if isinstance(response_body, str) else ""):
        if not payload:
            continue
        if "msg_body_list" in payload:
            add_items(_parse_msg_body_list_json(payload, event))
            continue
        if (
            "pigeon.jinritemai.com" in url
            or "fxg.jinritemai.com" in url
            or "im.jinritemai.com" in url
        ):
            add_items(_scan_inline_json_messages(payload, event))

    if (
        isinstance(response_body, str)
        and response_body
        and "get_by_conversation" in url
        and not response_body.lstrip().startswith("<!")
    ):
        add_items(_parse_protobuf_batch(response_body, event))

    if parsed:
        return parsed

    if not any(hint in url for hint in HTTP_INBOUND_URL_HINTS):
        return []
    return parsed
