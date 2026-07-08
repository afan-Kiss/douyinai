#!/usr/bin/env python3
"""Analyze WS text-frame signature blob between client_message_id and route embed."""
from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from pigeon_protocol.parsers.ws_frame_builder import read_varint  # noqa: E402


def load_payload(path: Path) -> bytes:
    ev = json.loads(path.read_text(encoding="utf-8"))
    return base64.b64decode(ev["payload"])


def extract_uuid(raw: bytes) -> str:
    m = re.search(
        rb"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        raw,
    )
    return m.group(0).decode() if m else ""


def find_sig_region(raw: bytes) -> tuple[int, int, bytes]:
    marker = b"s:client_message_id"
    pos = raw.find(marker)
    if pos < 0:
        return 0, 0, b""
    scan = pos + len(marker)
    # skip to tag 0xe8 0x07 0x3a (field ~125 length-delimited)
    idx = raw.find(b"\xe8\x07\x3a", scan)
    if idx < 0:
        idx = raw.find(b"\xe8\x07", scan)
    if idx < 0:
        return 0, 0, b""
    # parse length after 0x3a (tag 7 wire 2) -> 0xe2 0x01 = 226 bytes
    length, end = read_varint(raw, idx + 3)
    start = end
    blob = raw[start : start + length]
    return start, start + length, blob


def main() -> int:
    paths = [
        ROOT / "captures/live/from_har/har_00047_ws_frame_sent_26.json",
        ROOT / "captures/reference/20260701_112504_541619_ws_frame_sent.json",
    ]
    for path in paths:
        raw = load_payload(path)
        uuid = extract_uuid(raw)
        start, end, blob = find_sig_region(raw)
        text_hits = re.findall(r"[\u4e00-\u9fff，。！？]{2,40}", raw.decode("utf-8", errors="ignore"))
        print("=" * 60)
        print(path.name, "len", len(raw))
        print("uuid", uuid)
        print("text", text_hits[:2])
        print("sig_region", start, end, "blob_len", len(blob))
        print("blob_ascii_prefix", blob[:80].decode("ascii", errors="replace"))
        print("blob_hex_prefix", blob[:32].hex())
        # uuid appears at end of blob?
        if uuid.encode() in blob or ("$" + uuid).encode() in blob:
            print("uuid_embedded_in_blob: yes")
        m = re.search(rb"\$([0-9a-f-]{36})", blob)
        if m:
            print("dollar_uuid_in_blob", m.group(1).decode())
        cid = re.search(
            rb"s:client_message_id\x12\$([0-9a-f-]{36})",
            raw,
        )
        if cid:
            print("client_message_id", cid.group(1).decode())
        print("blob_tail", blob[-64:].decode("ascii", errors="replace"))
        # MS4w pattern inside blob (base64 1.0.)
        ms = blob.find(b"MS4w")
        print("MS4w_in_blob", ms)
        if ms >= 0:
            tail = blob[ms : ms + 48]
            print("MS4w_chunk", tail)
            try:
                print("MS4w_b64decode", base64.b64decode(tail + b"==")[:32].hex())
            except Exception as exc:
                print("MS4w_decode_err", exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
