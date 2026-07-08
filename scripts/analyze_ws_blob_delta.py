#!/usr/bin/env python3
"""Analyze WS signature blob shift vs text byte length / frame size."""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pigeon_protocol.parsers.ws_frame_builder import WSFrameBuilder
from pigeon_protocol.ws_sign import locate_signature_region


def load_sent(path: Path) -> tuple[str, bytes, int, int]:
    d = json.loads(path.read_text(encoding="utf-8"))
    raw = base64.b64decode(str(d.get("payload") or ""))
    text = WSFrameBuilder(raw)._extract_template_text()
    bl = len(text.encode("utf-8")) if text else int(d.get("text_byte_length") or 0)
    return text, raw, bl, len(raw)


def main() -> int:
    rows: list[tuple[str, bytes, int, int, Path]] = []
    for p in sorted(ROOT.glob("captures/**/*.json")):
        if "ws_frame_sent" not in p.name and "ws_sign" not in str(p.parent):
            continue
        try:
            text, raw, bl, fl = load_sent(p)
        except Exception:
            continue
        if "s:client_message_id" not in raw.decode("latin-1", errors="ignore"):
            continue
        if not locate_signature_region(raw):
            continue
        rows.append((text, raw, bl, fl, p))

    if len(rows) < 2:
        print("need >=2 signed text-send frames in captures/")
        for text, _, bl, fl, p in rows:
            print(f"  only: {p.name} textB={bl} frame={fl} text={text!r}")
        return 1

    rows.sort(key=lambda r: (r[2], r[3]))
    print("=== signed text-send pool ===")
    for text, raw, bl, fl, p in rows:
        region = locate_signature_region(raw)
        assert region
        print(
            f"textB={bl:>3} frame={fl:>5} sig@{region.blob_start:>4} "
            f"text={text[:20]!r} file={p.name}"
        )

    print("\n=== pairwise: frame delta vs text byte delta ===")
    for i in range(len(rows) - 1):
        t1, r1, b1, f1, p1 = rows[i]
        t2, r2, b2, f2, p2 = rows[i + 1]
        s1 = locate_signature_region(r1)
        s2 = locate_signature_region(r2)
        assert s1 and s2
        print(
            f"\n{p1.name} -> {p2.name}: "
            f"textB {b1}->{b2} (Δ{b2-b1}), frame {f1}->{f2} (Δ{f2-f1}), "
            f"sig_pos {s1.blob_start}->{s2.blob_start} (Δ{s2.blob_start-s1.blob_start})"
        )
        if s1.blob == s2.blob:
            print("  blob IDENTICAL (same-length text hypothesis holds across frame versions)")
        else:
            diff = sum(1 for a, b in zip(s1.blob, s2.blob) if a != b)
            print(f"  blob differs {diff}/226 — need per-textB template, cannot reuse blob")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
