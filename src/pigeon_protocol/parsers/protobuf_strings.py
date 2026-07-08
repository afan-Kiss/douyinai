from __future__ import annotations

import re
from typing import Any

from pigeon_protocol.parsers.ws_frame_builder import read_varint


def extract_strings(data: bytes, min_len: int = 4) -> list[str]:
    results: list[str] = []
    current: list[str] = []
    for b in data:
        if 32 <= b < 127:
            current.append(chr(b))
        else:
            if len(current) >= min_len:
                results.append("".join(current))
            current = []
    if len(current) >= min_len:
        results.append("".join(current))
    return results


def extract_chinese_messages(data: bytes) -> list[str]:
    """Pull UTF-8 chat fragments from protobuf (field pattern B?<len>?text)."""
    out: list[str] = []
    i = 0
    while i < len(data) - 2:
        if data[i] == 0x42:  # length-delimited string field common for content
            ln = data[i + 1]
            if 2 <= ln <= 200 and i + 2 + ln <= len(data):
                chunk = data[i + 2 : i + 2 + ln]
                try:
                    s = chunk.decode("utf-8")
                except UnicodeDecodeError:
                    i += 1
                    continue
                if re.search(r"[\u4e00-\u9fff]", s) and len(s.strip()) >= 2:
                    if not any(skip in s for skip in ("http", "Mozilla", "dimension", "pigeon")):
                        if s.strip() not in out:
                            out.append(s.strip())
                i += 2 + ln
                continue
        i += 1
    if out:
        return out
    text = data.decode("utf-8", errors="ignore")
    for p in re.findall(r"[\u4e00-\u9fff，。！？、：；（）\s]{2,120}", text):
        p = p.strip()
        if len(p) >= 2 and p not in out:
            out.append(p)
    return out


def _is_chat_text(text: str) -> bool:
    if not text or len(text) < 2:
        return False
    if any(c in text for c in "\x00\x01\x02\x03\x04\x05\x06\x07\x08"):
        return False
    if any(skip in text for skip in ("http", "Mozilla", "dimension", "pigeon_sign", "nickname", "device_platform")):
        return False
    if text.startswith("{") or "%3D" in text or "J\n" in text:
        return False
    if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", text):
        return False
    if re.fullmatch(r"[\W_]+", text):
        return False
    return True


def _role_near(data: bytes, text_pos: int) -> str:
    window = data[max(0, text_pos - 500) : text_pos + 200]
    for marker, role in (
        (b"sender_role\x12\x012", "service"),
        (b"sender_role\x12\x011", "customer"),
        (b"im_sender_role\x12\x012", "service"),
        (b"im_sender_role\x12\x011", "customer"),
    ):
        if marker in window:
            return role
    return "customer"


def parse_messages_from_protobuf(data: bytes) -> list[dict[str, Any]]:
    """Extract chat messages with role hints from pigeon_im / WS protobuf."""
    messages: list[dict[str, Any]] = []
    seen: set[str] = set()

    pos = 0
    while pos < len(data) - 8:
        if data[pos] not in (0x42, 0x22, 0x12):
            pos += 1
            continue
        tag = data[pos]
        length, value_pos = read_varint(data, pos + 1) if tag != 0x42 else (data[pos + 1], pos + 2)
        if tag == 0x42:
            if length < 2 or length > 240 or value_pos + length > len(data):
                pos += 1
                continue
            val_start, val_end = value_pos, value_pos + length
        else:
            if length < 2 or length > 240 or value_pos + length > len(data):
                pos += 1
                continue
            val_start, val_end = value_pos, value_pos + length

        chunk = data[val_start:val_end]
        try:
            text = chunk.decode("utf-8").strip()
        except UnicodeDecodeError:
            pos += 1
            continue
        if _is_chat_text(text) and re.search(r"[\u4e00-\u9fff]", text):
            if text not in seen:
                seen.add(text)
                talk = ""
                tid_idx = data.find(b"talk_id", max(pos - 200, 0), min(pos + 200, len(data)))
                if tid_idx >= 0:
                    m = re.search(rb"talk_id[\x00-\x1f]*(\d{10,22})", data[max(tid_idx - 5, 0) : tid_idx + 40])
                    if m:
                        talk = m.group(1).decode()
                messages.append(
                    {
                        "role": _role_near(data, val_start),
                        "text": text,
                        "time": "",
                        "message_id": "",
                        "talk_id": talk,
                    }
                )
        pos = val_end if tag != 0x42 else pos + 2 + length

    if messages:
        return messages

    for text in extract_chinese_messages(data):
        if text in seen or not _is_chat_text(text):
            continue
        role = "customer"
        if any(k in text for k in ("客服", "欢迎光临", "接入", "商家")):
            role = "service"
        if "系统关闭" in text or "超时未回复" in text:
            role = "system"
        messages.append({"role": role, "text": text, "time": "", "message_id": ""})
    return messages
