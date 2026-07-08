#!/usr/bin/env python3
"""Scan all WS sent captures — text byte length vs frame length vs signature blob."""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pigeon_protocol.parsers.ws_frame_builder import WSFrameBuilder
from pigeon_protocol.ws_sign import locate_signature_region


def main() -> int:
    rows: list[tuple] = []
    for p in sorted(ROOT.glob("captures/**/*.json")):
        name = p.name
        if "ws_frame_sent" not in name and "ws_sign" not in str(p.parent):
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(d, list):
            continue
        payload = d.get("payload") or d.get("b64")
        if not payload:
            continue
        try:
            raw = base64.b64decode(str(payload))
        except Exception:
            continue
        if len(raw) < 2500:
            continue
        try:
            text = WSFrameBuilder(raw)._extract_template_text()
            bl = len(text.encode("utf-8"))
        except Exception:
            text, bl = "?", -1
        region = locate_signature_region(raw)
        blob_pre = region.blob[:16].decode("ascii", errors="replace") if region else "?"
        rows.append((bl, len(raw), p.relative_to(ROOT), text[:30], blob_pre))

    rows.sort(key=lambda r: (r[0], r[1]))
    print(f"{'textB':>5} {'frame':>5}  {'blob16':<16}  text  file")
    print("-" * 90)
    for bl, fl, path, text, blob in rows:
        print(f"{bl:>5} {fl:>5}  {blob:<16}  {text!r}  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
