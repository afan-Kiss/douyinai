#!/usr/bin/env python3
"""Reverse WS signature inner 169-byte payload across captured samples."""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pigeon_protocol.parsers.ws_frame_builder import WSFrameBuilder
from pigeon_protocol.ws_sign_decode import analyze_frame, compare_inners, decode_blob, guess_inner_layout


def main() -> int:
    samples: list[tuple[int, bytes, str]] = []
    for p in sorted((ROOT / "captures").rglob("live_ws_frame_sent_b*.json")):
        raw = base64.b64decode(json.loads(p.read_text(encoding="utf-8"))["payload"])
        text = WSFrameBuilder(raw)._extract_template_text()
        a = analyze_frame(raw, text=text)
        if not a:
            continue
        samples.append((a.text_byte_length, a.inner, text[:40]))
        print(f"\n=== {p.name} textB={a.text_byte_length} frame={a.frame_length} ===")
        print(f"text: {text[:60]!r}")
        print(f"md5(text): {a.md5_text}")
        print(f"inner[0:32]: {a.inner[:32].hex()}")
        print(f"layout: {json.dumps(guess_inner_layout(a.inner), ensure_ascii=False)}")

    if len(samples) >= 2:
        print("\n=== compare_inners ===")
        print(json.dumps(compare_inners([(bl, inner) for bl, inner, _ in samples]), indent=2))

    print("\n=== reverse conclusion ===")
    print("- blob = standard base64 → fixed 169-byte binary")
    print("- inner payload changes entirely per text byte-length (not UUID/time patch)")
    print("- no MD5(text) visible at fixed offset — likely custom cipher/HMAC in Feige WASM/JS")
    print("- standalone send: ship live_ws_frame_sent_b{NNN}.json per length OR reverse sign VM")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
