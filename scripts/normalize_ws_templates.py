#!/usr/bin/env python3
"""Normalize ws_sign template filenames to b{byte_len:03d}.json."""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pigeon_protocol.parsers.ws_frame_builder import WSFrameBuilder

CAP = ROOT / "captures" / "live" / "ws_sign"


def main() -> int:
    CAP.mkdir(parents=True, exist_ok=True)
    for p in list(CAP.glob("*.json")) + list((ROOT / "captures" / "reference").glob("*ws_frame_sent*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            raw = base64.b64decode(str(d.get("payload") or ""))
            if len(raw) < 2500:
                continue
            text = WSFrameBuilder(raw)._extract_template_text()
            bl = len(text.encode("utf-8")) if text else int(d.get("text_byte_length") or 0)
            if bl <= 0:
                continue
            target = CAP / f"live_ws_frame_sent_b{bl:03d}.json"
            if p == target:
                continue
            if target.exists() and p.parent == CAP:
                p.unlink()
                continue
            d["text_byte_length"] = bl
            target.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"{p.name} -> {target.name} ({bl}B)")
        except Exception as exc:
            print(f"skip {p.name}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
