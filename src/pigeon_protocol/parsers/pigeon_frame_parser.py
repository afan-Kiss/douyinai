from __future__ import annotations

import base64
import re
import time
from typing import Any

from pigeon_protocol.parsers.text_filters import is_meaningless_message


def read_varint(data: bytes | bytearray, pos: int) -> tuple[int, int] | None:
    result = shift = 0
    index = pos
    while index < len(data):
        byte = data[index]
        index += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, index
        shift += 7
        if shift > 35:
            break
    return None


def decode_bytes(payload_text: str = "") -> tuple[str, bytes]:
    raw = (payload_text or "").strip()
    if not raw:
        return "empty", b""

    if re.fullmatch(r"[0-9a-fA-F]+", raw) and len(raw) % 2 == 0:
        try:
            return "hex", bytes.fromhex(raw)
        except ValueError:
            pass

    if re.fullmatch(r"[A-Za-z0-9+/_=-]+", raw) and len(raw) >= 8:
        try:
            normalized = raw.replace("-", "+").replace("_", "/")
            pad = len(normalized) % 4
            if pad:
                normalized += "=" * (4 - pad)
            decoded = base64.b64decode(normalized)
            if decoded:
                return "base64", decoded
        except Exception:
            pass

    return "text", raw.encode("utf-8")


def is_clean_text(text: str) -> bool:
    if not text:
        return False
    for char in text:
        code = ord(char)
        if code < 0x20 and code not in (0x0A, 0x09):
            return False
    if '":' in text or '":"' in text:
        return False
    if re.search(r"[{}\[\]]", text):
        return False
    if re.match(r"^https?:", text, re.I):
        return False
    if re.fullmatch(r"[A-Za-z0-9+/=_-]{28,}", text):
        return False
    if re.search(r"[\u4e00-\u9fff]", text):
        return True
    if re.fullmatch(r"[\x20-\x7e]{1,200}", text) and re.search(r"[A-Za-z0-9]", text):
        return True
    return False


def extract_body_text(data: bytes) -> str:
    pos = 0
    while pos + 1 < len(data):
        if data[pos] != 0x42:
            pos += 1
            continue
        parsed = read_varint(data, pos + 1)
        if not parsed:
            pos += 1
            continue
        length, start = parsed[0], parsed[1]
        if length < 1 or length > 1000:
            pos += 1
            continue
        end = start + length
        if end > len(data):
            pos += 1
            continue
        chunk = data[start:end]
        try:
            decoded = chunk.decode("utf-8")
        except UnicodeDecodeError:
            pos += 1
            continue
        if len(decoded.encode("utf-8")) != length:
            pos += 1
            continue
        if not is_clean_text(decoded):
            nested = extract_message_text(chunk, _depth=1)
            if nested:
                return nested
            pos += 1
            continue
        return decoded.strip()
    return ""


def _last_clean_delimited_string(buf: bytes) -> str:
    best = ""
    pos = 0
    while pos + 1 < len(buf):
        if (buf[pos] & 0x07) != 2:
            pos += 1
            continue
        parsed = read_varint(buf, pos + 1)
        if not parsed:
            pos += 1
            continue
        length, start = parsed[0], parsed[1]
        end = start + length
        if length < 1 or length > 500 or end > len(buf):
            pos += 1
            continue
        chunk = buf[start:end]
        try:
            decoded = chunk.decode("utf-8")
        except UnicodeDecodeError:
            pos += 1
            continue
        if len(decoded.encode("utf-8")) != length:
            pos += 1
            continue
        if is_clean_text(decoded):
            best = decoded.strip()
        pos += 1
    return best


def extract_text_before_type_marker(data: bytes) -> str:
    marker = b"type\x12\x04text"
    best = ""
    start = 0
    while True:
        idx = data.find(marker, start)
        if idx < 0:
            break
        candidate = _last_clean_delimited_string(data[max(0, idx - 400) : idx])
        if candidate and (not best or len(candidate) > len(best)):
            best = candidate
        start = idx + 1
    return best


METADATA_FRAGMENT_RE = re.compile(
    r"^(native|sub|biz|text|type|iid|true|false|default|ios|dy|pc|web|App Store|Buyer|pigeon|"
    r"common_LT_QT_kanjianle|[A-Za-z0-9_./:-]{1,12})$",
    re.I,
)


def iter_proto_strings(data: bytes, *, max_length: int = 500) -> list[str]:
    strings: list[str] = []
    pos = 0
    while pos + 1 < len(data):
        if (data[pos] & 0x07) != 2:
            pos += 1
            continue
        parsed = read_varint(data, pos + 1)
        if not parsed:
            pos += 1
            continue
        length, start = parsed[0], parsed[1]
        end = start + length
        if length < 1 or length > max_length or end > len(data):
            pos += 1
            continue
        chunk = data[start:end]
        try:
            decoded = chunk.decode("utf-8")
        except UnicodeDecodeError:
            pos += 1
            continue
        if len(decoded.encode("utf-8")) != length:
            pos += 1
            continue
        value = decoded.strip()
        if value:
            strings.append(value)
        pos += 1
    return strings


def is_metadata_fragment(text: str = "") -> bool:
    value = (text or "").strip()
    if not value:
        return True
    if METADATA_FRAGMENT_RE.fullmatch(value):
        return True
    if re.fullmatch(r"[A-Za-z0-9_./:-]{1,40}", value) and not re.search(r"[\u4e00-\u9fff]", value):
        return True
    if re.fullmatch(r"[a-z][a-z0-9_]{2,40}", value):
        return True
    return False


FEIGE_EMOJI_TAG_RE = re.compile(r"\[[^\[\]]{1,20}\]")


def normalize_chat_text(text: str = "") -> str:
    value = FEIGE_EMOJI_TAG_RE.sub("", str(text or ""))
    return re.sub(r"\s+", " ", value).strip()


def is_relaxed_message_text(text: str = "") -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    if any(ord(char) < 0x20 and char not in "\n\t" for char in raw):
        return False
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", raw):
        return False
    normalized = normalize_chat_text(raw)
    if not normalized or is_metadata_fragment(normalized):
        return False
    if not re.search(r"[\u4e00-\u9fff]", normalized):
        return False
    if len(normalized) > 120:
        return False
    if re.search(
        r"pigeon|CurrentServer|biz_sender|flow_extra|shop_order|product_id|avatar_uri|https?://|"
        r"device_platform|encode_shop_id|security_receiver|sender_role|uname",
        raw,
        re.I,
    ):
        return False
    if raw.startswith("{") or '":"' in raw:
        return False
    if SYSTEM_HINT_RE.search(normalized):
        return False
    return True


def extract_tagged_body_text(data: bytes) -> str:
    best = ""
    pos = 0
    while pos + 1 < len(data):
        if data[pos] != 0x42:
            pos += 1
            continue
        parsed = read_varint(data, pos + 1)
        if not parsed:
            pos += 1
            continue
        length, start = parsed[0], parsed[1]
        if length < 1 or length > 1000:
            pos += 1
            continue
        end = start + length
        if end > len(data):
            pos += 1
            continue
        chunk = data[start:end]
        try:
            decoded = chunk.decode("utf-8")
        except UnicodeDecodeError:
            pos += 1
            continue
        if len(decoded.encode("utf-8")) != length:
            pos += 1
            continue
        if not is_relaxed_message_text(decoded):
            pos += 1
            continue
        normalized = normalize_chat_text(decoded)
        if len(normalized) > len(best):
            best = normalized
        pos += 1
    return best


def extract_chinese_message_candidates(data: bytes, nickname: str = "") -> list[str]:
    nick = (nickname or "").strip()
    candidates: list[str] = []
    tagged = extract_tagged_body_text(data)
    if tagged and tagged not in candidates:
        candidates.append(tagged)

    for value in iter_proto_strings(data):
        if not is_relaxed_message_text(value):
            continue
        normalized = normalize_chat_text(value)
        if nick and normalized == nick:
            continue
        if normalized in candidates:
            continue
        candidates.append(normalized)
    return candidates


def has_readable_message_text(data: bytes, nickname: str = "") -> bool:
    return bool(extract_chinese_message_candidates(data, nickname))


def detect_non_text_message_label(data: bytes, nickname: str = "") -> str:
    if has_readable_message_text(data, nickname):
        return ""
    text = data.decode("utf-8", errors="ignore")
    if "point_info" in text and "product_id" in text:
        return "[商品卡片]"
    if b"flow_extra" not in data:
        return ""

    marker = data.find(b"flow_extra")
    tail = data[marker:]
    for value in iter_proto_strings(tail, max_length=2000):
        if len(value) < 48:
            continue
        if re.fullmatch(r"[A-Za-z0-9+/_=-]{48,}", value):
            return "[图片]"
        if value.startswith("http") and re.search(r"\.(webp|jpg|jpeg|png|gif)", value, re.I):
            return "[图片]"
    return ""


SYSTEM_HINT_RE = re.compile(
    r"电脑端查看|不支持查看此消息|请更新App|当前版本不支持",
    re.I,
)


def detect_emotion_message_label(data: bytes, text_blob: str = "") -> str:
    blob = text_blob or data.decode("utf-8", errors="ignore")
    if "emotionSetId" in blob:
        return "[表情]"
    if "hint_content" in blob and SYSTEM_HINT_RE.search(blob):
        return "[表情]"
    if re.search(r'"phase"\s*:\s*30', blob) and re.search(r"emotion|custom_sticker", blob, re.I):
        return "[表情]"
    if re.search(r"sendEmotion|custom_sticker|emotion_message", blob, re.I):
        return "[表情]"
    return ""


def pick_best_message_text(data: bytes, primary: str = "", nickname: str = "", text_blob: str = "") -> str:
    emotion_label = detect_emotion_message_label(data, text_blob)
    if emotion_label:
        return emotion_label

    primary = normalize_chat_text(primary)
    if primary and not is_metadata_fragment(primary):
        return primary

    for candidate in extract_chinese_message_candidates(data, nickname):
        if not is_metadata_fragment(candidate):
            return candidate

    label = detect_non_text_message_label(data, nickname)
    if label:
        return label

    return primary


def extract_text_from_type_utf8_scan(data: bytes) -> str:
    text = data.decode("utf-8", errors="ignore")
    idx = text.find("type")
    if idx < 0:
        return ""
    before = text[max(0, idx - 160) : idx]
    candidates = re.findall(r"[\u4e00-\u9fffA-Za-z0-9，。！？,.!? ]{1,200}", before)
    for candidate in reversed(candidates):
        value = candidate.strip()
        if not value or len(value) <= 1:
            continue
        if re.search(r"pigeon|main|chat|AQ|type$|CurrentServer|biz_", value, re.I):
            continue
        if re.search(r"[\u4e00-\u9fff]", value):
            return value
        if re.search(r"[A-Za-z0-9]", value) and len(value) >= 2:
            return value
    return ""


def extract_message_text(data: bytes, _depth: int = 0) -> str:
    if _depth > 4:
        return ""

    if _depth == 0:
        text = extract_body_text(data)
        if text:
            return text

    text = extract_text_before_type_marker(data)
    if text:
        return text

    text = extract_text_from_type_utf8_scan(data)
    if text:
        return text

    if _depth == 0:
        marker = b"type\x12\x04text"
        pos = 0
        while pos + 1 < len(data):
            if (data[pos] & 0x07) != 2:
                pos += 1
                continue
            parsed = read_varint(data, pos + 1)
            if not parsed:
                pos += 1
                continue
            length, start = parsed[0], parsed[1]
            end = start + length
            if length < 8 or end > len(data):
                pos += 1
                continue
            chunk = data[start:end]
            if marker in chunk or b"flow_extra" in chunk:
                nested = extract_message_text(chunk, _depth + 1)
                if nested:
                    return nested
            pos += 1
    return ""


def extract_sender_role(data: bytes) -> int:
    match = re.search(rb"sender_role\x12\x01(\d)", data)
    return int(match.group(1)) if match else 0


def match_first(text: str, pattern: str) -> str:
    match = re.search(pattern, text)
    return match.group(1) if match else ""


def build_conversation_route(security_receiver_id: str = "", shop_id: str = "") -> str:
    sec = (security_receiver_id or "").strip()
    shop = (shop_id or "").strip()
    if not sec or not shop:
        return ""
    route_key = f"n{sec[1:]}" if sec.startswith("X") else sec
    return f"{route_key}:{shop}::2:1:pigeon"


def extract_conversation_route_from_text(text: str) -> str:
    """Parse security conversation id from JSON or protobuf text blobs."""
    blob = str(text or "")
    if not blob:
        return ""
    for pattern in (
        r'"security_conversation_id"\s*:\s*"([^"\\]+)"',
        r'"security_biz_conversation_id"\s*:\s*"([^"\\]+)"',
        r"security_conversation_id[\x12\|:]['\"]?([A-Za-z0-9_:/-]{20,160})",
        r"security_biz_conversation_id[\x12\|:]['\"]?([A-Za-z0-9_:/-]{20,160})",
        r"(n?AQ[Cc][A-Za-z0-9_-]{30,120}:\d+::\d+:\d+:pigeon)",
    ):
        route = match_first(blob, pattern)
        if route and ":pigeon" in route:
            return route.strip()
    return ""


def extract_pigeon_ids(data: bytes) -> dict[str, str]:
    text = data.decode("utf-8", errors="ignore")
    shop_id = match_first(text, r"shop_id\|(\d{6,})") or match_first(text, r"shop_id[^\d]{0,4}(\d{6,})")
    security_receiver_id = match_first(text, r"security_receiver_id\|([A-Za-z0-9_:-]{20,})")
    if not security_receiver_id:
        candidates = sorted(set(re.findall(r"XAQ[A-Za-z0-9_-]{30,120}", text)), key=len, reverse=True)
        if candidates:
            security_receiver_id = candidates[0]
    conversation_route = extract_conversation_route_from_text(text)
    if not conversation_route:
        conversation_route = build_conversation_route(security_receiver_id, shop_id)
    return {
        "security_receiver_id": security_receiver_id,
        "shop_id": shop_id,
        "conversation_route": conversation_route,
    }


MESSAGE_KINDS = frozenset({"buyer_message", "seller_message", "system_message", "inbound_message"})


def parse_inbound_frame(event: dict[str, Any] | None = None) -> dict[str, Any]:
    event = event or {}
    ws_direction = str(event.get("direction") or "in")
    payload_text = str(
        event.get("payload")
        or event.get("payloadText")
        or event.get("payload_hex")
        or ""
    )
    if event.get("format") == "binary" and event.get("payload_hex"):
        payload_text = str(event["payload_hex"])
    elif event.get("format") == "binary" and event.get("payload"):
        payload_text = str(event["payload"])

    payload_format, data = decode_bytes(payload_text)
    text_blob = data.decode("utf-8", errors="ignore")

    is_message = bool(re.search(r'"msg_type":\s*\d+', text_blob)) or "flow_extra" in text_blob
    if not is_message:
        return {
            "kind": "ws_frame",
            "role": "seller" if ws_direction == "out" else "unknown",
            "ws_direction": ws_direction,
            "payload_format": payload_format,
            "payload_bytes": len(data),
            "timestamp": event.get("ts") or event.get("timestamp") or time.time(),
        }

    direction = int(match_first(text_blob, r'"direction":(\d+)') or 0)
    msg_type = int(match_first(text_blob, r'"msg_type":(\d+)') or 0)
    sender_role = extract_sender_role(data)
    nickname = match_first(text_blob, r'"nickname":"([^"]+)"') or match_first(text_blob, r'"uname":"([^"]+)"')
    conversation_id = match_first(text_blob, r'"talk_id":"?(\d+)"?') or match_first(
        text_blob, r"talk_id\D{0,4}(\d{10,})"
    )
    server_message_id = match_first(text_blob, r'"server_message_id":"?(\d+)"?') or match_first(
        text_blob, r"server_message_id\D{0,4}(\d{10,})"
    )
    client_message_id = match_first(text_blob, r'"client_message_id":"?([0-9a-f-]{12,})"?') or match_first(
        text_blob, r"client_message_id\D{0,4}([0-9a-f-]{12,})"
    )

    if direction == 1:
        role = "buyer"
    elif direction == 2:
        role = "seller"
    elif direction in {3, 10} or msg_type in {2004, 2008}:
        role = "system"
    elif sender_role == 1:
        role = "buyer"
    elif sender_role == 2:
        role = "seller"
    else:
        role = "seller" if ws_direction == "out" else "unknown"

    text = pick_best_message_text(data, extract_message_text(data), nickname, text_blob)
    if text and is_meaningless_message(text, role, nickname):
        text = ""
    pigeon_ids = extract_pigeon_ids(data)

    kind = "ws_frame"
    if text:
        if role == "buyer":
            kind = "buyer_message"
        elif role == "seller":
            kind = "seller_message"
        elif role == "system":
            kind = "system_message"
        else:
            kind = "inbound_message"

    return {
        "kind": kind,
        "role": role,
        "direction": direction,
        "msg_type": msg_type,
        "sender_role": sender_role,
        "nickname": nickname,
        "text": text,
        "text_preview": text,
        "conversation_id": conversation_id,
        "server_message_id": server_message_id,
        "client_message_id": client_message_id,
        "security_receiver_id": pigeon_ids["security_receiver_id"],
        "shop_id": pigeon_ids["shop_id"],
        "conversation_route": pigeon_ids["conversation_route"],
        "ws_direction": ws_direction,
        "payload_format": payload_format,
        "payload_bytes": len(data),
        "timestamp": event.get("ts") or event.get("timestamp") or time.time(),
        "url": event.get("url", ""),
    }


def is_message_kind(kind: str) -> bool:
    return kind in MESSAGE_KINDS
