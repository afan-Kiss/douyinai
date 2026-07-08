#!/usr/bin/env python3
import base64
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pigeon_protocol.ws_sign import extract_client_message_id, locate_signature_region

paths = [
    ROOT / "captures/live/from_har/har_00047_ws_frame_sent_26.json",
    ROOT / "captures/reference/20260701_112504_541619_ws_frame_sent.json",
]
for p in paths:
    raw = base64.b64decode(json.loads(p.read_text(encoding="utf-8"))["payload"])
    r = locate_signature_region(raw)
    cid = extract_client_message_id(raw)
    print("===", p.name)
    print("cid", cid)
    if r:
        print("blob has cid", cid.encode() in r.blob)
        m = re.search(rb"\$([0-9a-f-]{36})", r.blob)
        print("dollar in blob", m.group(1).decode() if m else None)
        print("suffix after blob", raw[r.blob_end : r.blob_end + 45])
