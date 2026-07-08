#!/usr/bin/env python3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pigeon_protocol.foundation.ws_blob_compute import compute_inner_bytes, inner_class_for_text_b
from pigeon_protocol.foundation.ws_sign_engine import ComputedBlobStrategy
from pigeon_protocol.session import load_session
from pigeon_protocol.ws_sign import locate_signature_region
from pigeon_protocol.ws_sign_decode import decode_blob

s = load_session()
strat = ComputedBlobStrategy()
for text in ["你好", "好的好的好的好的好的好的好的", "好" * 15]:
    bl = len(text.encode("utf-8"))
    ic = inner_class_for_text_b(bl)
    frame = strat.build_frame(
        text,
        session=s,
        security_user_id="AQtest",
        shop_id="263636465",
        preserve_signature=True,
    )
    region = locate_signature_region(frame)
    inner = decode_blob(region.blob) if region else b""
    expected = compute_inner_bytes(s, bl)
    print(
        f"textB={bl:3d} class={ic.name if ic else '?'} "
        f"frame={len(frame)} inner_match={inner == expected}"
    )
