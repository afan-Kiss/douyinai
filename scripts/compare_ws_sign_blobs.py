#!/usr/bin/env python3
"""Compare WS signature blobs across captured text-send frames."""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pigeon_protocol.ws_sign import locate_signature_region  # noqa: E402


def load(path: Path) -> bytes:
    return base64.b64decode(json.loads(path.read_text(encoding="utf-8"))["payload"])


def main() -> int:
    samples = [
        ("收到6B", ROOT / "captures/live/from_har/har_00047_ws_frame_sent_26.json"),
        ("长文本", ROOT / "captures/reference/20260701_112504_541619_ws_frame_sent.json"),
    ]
    blobs: list[tuple[str, bytes]] = []
    for label, path in samples:
        raw = load(path)
        region = locate_signature_region(raw)
        if not region:
            print(label, "no sig region")
            continue
        blobs.append((label, region.blob))
        print(f"{label}: blob_len={len(region.blob)} prefix={region.blob[:16]!r}")

    if len(blobs) == 2:
        a, b = blobs[0][1], blobs[1][1]
        same = sum(1 for x, y in zip(a, b) if x == y)
        print(f"byte overlap at same offsets: {same}/{min(len(a), len(b))}")
        # custom alphabet check: blob is mostly ascii
        for label, blob in blobs:
            ascii_ratio = sum(32 <= c < 127 for c in blob) / len(blob)
            print(f"{label} ascii_ratio={ascii_ratio:.2%}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
