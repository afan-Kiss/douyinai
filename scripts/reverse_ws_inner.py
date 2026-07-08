#!/usr/bin/env python3
"""Deep 169B inner blob reverse — field map + cross-bucket diff."""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

OUT = ROOT / "analysis" / "ws_inner_map.json"


def main() -> int:
    from pigeon_protocol.foundation.ws_blob_re import collect_inner_samples, compare_bucket_inners
    from pigeon_protocol.ws_sign_decode import decode_blob, guess_inner_layout
    from pigeon_protocol.ws_sign import locate_signature_region

    samples = collect_inner_samples()
    report: dict = {
        "samples": [
            {
                "textB": s.text_byte_length,
                "bucket": s.bucket,
                "source": s.source,
                "frame_length": s.frame_length,
                "inner_len": len(bytes.fromhex(s.inner_hex)),
                "layout": s.layout,
                "inner_hex": s.inner_hex,
            }
            for s in samples
        ],
        "bucket_diff": compare_bucket_inners(),
    }

    # Within bucket B: all inners must match
    b_inners = [s for s in samples if s.bucket == "B"]
    if len(b_inners) >= 2:
        ref = bytes.fromhex(b_inners[0].inner_hex)
        report["bucket_B_identical"] = all(bytes.fromhex(s.inner_hex) == ref for s in b_inners)

    # Scan for MS4w ticket prefix in inner (IM session ticket pattern)
    for s in samples:
        inner = bytes.fromhex(s.inner_hex)
        ms = inner.find(b"MS4w")
        report.setdefault("ms4w_offsets", {})[s.bucket] = ms

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUT} ({len(samples)} samples)")
    print("bucket_B_identical:", report.get("bucket_B_identical"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
