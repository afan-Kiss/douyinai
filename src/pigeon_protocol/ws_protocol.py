"""WS protobuf helpers — conversation route, talk_id, token patching."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

ROUTE_RE = re.compile(rb"AQ[Cc][A-Za-z0-9_-]{30,200}:\d+::\d+:\d+:pigeon")
SECURITY_RECEIVER_MARKER = b"security_receiver_id"
SECURITY_BIZ_MARKER = b"security_biz_conversation_id"
TALK_ID_MARKER = b"talk_id"
PIGEON_SIGN_MARKER = b"pigeon_sign"


@dataclass
class ConversationMeta:
    security_user_id: str
    shop_id: str
    talk_id: str = ""
    conversation_route: str = ""

    @property
    def route(self) -> str:
        if self.conversation_route:
            return self.conversation_route
        return f"{self.security_user_id}:{self.shop_id}::2:1:pigeon"


def extract_meta_from_bytes(data: bytes) -> ConversationMeta | None:
    text = data.decode("latin-1", errors="ignore")
    m = re.search(r"(AQ[Cc][A-Za-z0-9_-]{30,200}):\d+::\d+:\d+:pigeon", text)
    if not m:
        return None
    route = m.group(0)
    uid = m.group(1)
    shop_m = re.search(r":(\d+)::", route)
    shop_id = shop_m.group(1) if shop_m else ""
    talk_m = re.search(r"talk_id[\x00-\x1f]*(\d{10,22})", text)
    talk_id = talk_m.group(1) if talk_m else ""
    return ConversationMeta(security_user_id=uid, shop_id=shop_id, talk_id=talk_id, conversation_route=route)


def patch_bytes_field(data: bytearray, marker: bytes, new_value: str) -> bool:
    """Replace protobuf string value immediately after ASCII field name marker."""
    new_bytes = new_value.encode("utf-8")
    idx = 0
    while True:
        pos = data.find(marker, idx)
        if pos < 0:
            return False
        scan = pos + len(marker)
        # skip protobuf tag/length wrappers until string tag 0x12 or 0x1a
        while scan < len(data) and data[scan] not in (0x12, 0x1A, 0x22):
            scan += 1
        if scan >= len(data):
            return False
        length_pos = scan + 1
        length = data[length_pos]
        # single-byte length (values we patch are always <127)
        if length >= 0x80:
            idx = pos + 1
            continue
        val_start = length_pos + 1
        val_end = val_start + length
        if val_end > len(data):
            idx = pos + 1
            continue
        old = data[val_start:val_end]
        if len(new_bytes) != len(old):
            # same-length only for signature-surrounded fields; caller handles resize separately
            return False
        data[val_start:val_end] = new_bytes
        return True


def patch_conversation_route(
    data: bytearray,
    *,
    security_user_id: str,
    shop_id: str,
    talk_id: str = "",
) -> dict[str, Any]:
    """Patch security_receiver_id, security_biz_conversation_id, embedded route strings."""
    report: dict[str, Any] = {"patched": []}
    route = f"{security_user_id}:{shop_id}::2:1:pigeon"

    for m in ROUTE_RE.finditer(bytes(data)):
        old = m.group(0)
        if old.decode("ascii", errors="ignore") == route:
            continue
        if len(old) != len(route.encode("ascii")):
            report.setdefault("skipped", []).append({"reason": "route_len_mismatch", "old_len": len(old)})
            continue
        start, end = m.start(), m.end()
        data[start:end] = route.encode("ascii")
        report["patched"].append("route_embed")

    patch_bytes_field(data, SECURITY_RECEIVER_MARKER, security_user_id)
    patch_bytes_field(data, SECURITY_BIZ_MARKER, route)
    if talk_id:
        if patch_bytes_field(data, TALK_ID_MARKER, talk_id):
            report["patched"].append("talk_id")
    return report


def pick_template_ws_url(template: dict[str, Any], session_urls: list[str], *, session=None) -> str:
    """Prefer live session token URL, then template access_key/device_id match."""
    from urllib.parse import parse_qs, urlparse

    template_url = str(template.get("url") or "")
    if not session_urls:
        return template_url

    live_tok = ""
    if session is not None:
        live_tok = str(getattr(session, "query_tokens", {}).get("token") or "")
    if live_tok:
        for url in reversed(session_urls):
            if "ws.fxg.jinritemai.com" not in url:
                continue
            tok = (parse_qs(urlparse(url).query).get("token") or [""])[0]
            if tok == live_tok:
                return url

    tpl_key = _qs(template_url, "access_key")
    tpl_dev = _qs(template_url, "device_id")
    for url in reversed(session_urls):
        if tpl_key and _qs(url, "access_key") == tpl_key:
            return url
        if tpl_dev and _qs(url, "device_id") == tpl_dev:
            return url
    return session_urls[-1] if session_urls else template_url


def _qs(url: str, key: str) -> str:
    from urllib.parse import parse_qs, urlparse, unquote

    qs = parse_qs(urlparse(url).query)
    val = qs.get(key, [""])[0]
    return unquote(val) if val else ""


def parse_ws_url_tokens(ws_url: str) -> dict[str, str]:
    return {
        "token": _qs(ws_url, "token"),
        "pigeon_sign": _qs(ws_url, "pigeon_sign"),
        "device_id": _qs(ws_url, "device_id"),
        "access_key": _qs(ws_url, "access_key"),
    }


def _replace_ascii_token(data: bytearray, old: bytes, new: bytes) -> bool:
    if not old or len(old) != len(new):
        return False
    idx = 0
    replaced = False
    while True:
        pos = data.find(old, idx)
        if pos < 0:
            return replaced
        data[pos : pos + len(old)] = new
        replaced = True
        idx = pos + len(new)
    return replaced


def _patch_pigeon_sign_fields(data: bytearray, new_sign: str) -> int:
    from pigeon_protocol.parsers.ws_frame_builder import read_varint, write_varint

    new_bytes = new_sign.encode("ascii")
    count = 0
    pos = 0
    while True:
        idx = data.find(PIGEON_SIGN_MARKER, pos)
        if idx < 0:
            break
        scan = idx + len(PIGEON_SIGN_MARKER)
        while scan < len(data) and data[scan] not in (0x12, 0x1A):
            scan += 1
        if scan >= len(data):
            break
        length, val_start = read_varint(data, scan + 1)
        val_end = val_start + length
        if val_end > len(data):
            break
        old = data[val_start:val_end]
        if len(new_bytes) == len(old):
            data[val_start:val_end] = new_bytes
            count += 1
        elif len(new_bytes) < 127 and len(new_bytes) != length:
            # single-byte length prefix replacement
            encoded = write_varint(len(new_bytes))
            if len(encoded) == 1 and scan + 1 < len(data):
                length_pos = scan + 1
                old_length_size = 1 if data[length_pos] < 0x80 else 0
                if old_length_size == 1:
                    delta = len(new_bytes) - length
                    data[length_pos] = len(new_bytes)
                    data[val_start:val_end] = new_bytes
                    count += 1
        pos = idx + 1
    return count


def patch_ws_credentials(data: bytearray, ws_url: str, *, session: Any = None) -> dict[str, Any]:
    """Sync embedded WS token/pigeon_sign/device_id with live connection URL."""
    from pigeon_protocol.session import SessionState

    tokens = parse_ws_url_tokens(ws_url)
    report: dict[str, Any] = {"patched": [], "skipped": []}

    if tokens.get("token"):
        new_tok = tokens["token"].encode("ascii")
        # quoted token in inbox header: "6<token>
        quote_marker = b'"6'
        qpos = data.find(quote_marker)
        if qpos >= 0:
            start = qpos + 2
            end = start + len(new_tok)
            if end <= len(data) and len(new_tok) == 54:
                old = bytes(data[start:end])
                if len(old) == len(new_tok):
                    data[start:end] = new_tok
                    report["patched"].append("token_quoted")
        if "token" not in report["patched"]:
            for cand in re.finditer(rb"[A-Za-z0-9_+-]{54}", bytes(data)):
                old = cand.group(0)
                if old != new_tok and _replace_ascii_token(data, old, new_tok):
                    report["patched"].append("token")
                    break

    if tokens.get("pigeon_sign"):
        n = _patch_pigeon_sign_fields(data, tokens["pigeon_sign"])
        if n:
            report["patched"].append(f"pigeon_sign_x{n}")
        else:
            report["skipped"].append("pigeon_sign_len_mismatch")

    if tokens.get("device_id") and session and isinstance(session, SessionState):
        patch_bytes_field(data, b"session_did", tokens["device_id"])

    if session and isinstance(session, SessionState):
        for key in ("token", "pigeon_sign", "device_id", "access_key"):
            if tokens.get(key):
                session.query_tokens[key] = tokens[key]

    return report
