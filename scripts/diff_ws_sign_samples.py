#!/usr/bin/env python3
"""Diff captured WS text-send frames and signature blobs."""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pigeon_protocol.ws_sign import extract_client_message_id, locate_signature_region  # noqa: E402


def load_samples() -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    p = ROOT / "analysis" / "ws_sign_samples.json"
    if p.exists():
        for i, s in enumerate(json.loads(p.read_text(encoding="utf-8"))):
            raw = base64.b64decode(s["b64"])
            text = ""
            import re

            hits = re.findall(r"[\u4e00-\u9fff]{1,10}", raw.decode("utf-8", errors="ignore"))
            for h in hits:
                if len(h) <= 4:
                    text = h
                    break
            out.append((text or f"sample{i}", raw))
    cap = ROOT / "captures" / "live" / "ws_sign"
    if cap.exists():
        for fp in sorted(cap.glob("*.json")):
            ev = json.loads(fp.read_text(encoding="utf-8"))
            raw = base64.b64decode(ev["payload"])
            hint = (ev.get("text_hint") or ["?"])[0]
            out.append((hint, raw))
    return out


def text_field(raw: bytes) -> tuple[int, bytes]:
    import re

    for m in re.finditer(rb"\x0a\x04type\x12\x04text", raw):
        pos = m.start()
        scan = pos - 20
        while scan >= 0 and scan < pos:
            if raw[scan] == 0x42 and scan + 2 < len(raw):
                ln = raw[scan + 1]
                if 1 <= ln <= 40:
                    chunk = raw[scan + 2 : scan + 2 + ln]
                    try:
                        chunk.decode("utf-8")
                        return scan, chunk
                    except UnicodeDecodeError:
                        pass
            scan += 1
    return -1, b""


def main() -> int:
    samples = load_samples()
    if len(samples) < 2:
        print("need >=2 samples", file=sys.stderr)
        return 1

    print(f"=== {len(samples)} samples ===")
    for label, raw in samples:
        region = locate_signature_region(raw)
        tf_pos, tf = text_field(raw)
        print(
            f"{label!r}: len={len(raw)} text_bytes={tf!r} cid={extract_client_message_id(raw)[:8]}..."
            f" sig={'yes' if region else 'no'}"
        )

    # pairwise diff among same-length frames
    by_len: dict[int, list[tuple[str, bytes]]] = {}
    for label, raw in samples:
        by_len.setdefault(len(raw), []).append((label, raw))

    for length, group in by_len.items():
        if len(group) < 2:
            continue
        print(f"\n=== same length {length} ===")
        a_label, a = group[0]
        b_label, b = group[1]
        diffs = [i for i in range(min(len(a), len(b))) if a[i] != b[i]]
        print(f"diff bytes: {len(diffs)} / {length}")
        if diffs:
            print("first 20 offsets:", diffs[:20])
            for i in diffs[:15]:
                print(f"  @{i}: {a[i]:02x} vs {b[i]:02x}  ctx_a={a[max(0,i-3):i+4]!r}")

        ra = locate_signature_region(a)
        rb = locate_signature_region(b)
        if ra and rb:
            same_sig = ra.blob == rb.blob
            print(f"signature blob identical: {same_sig}")
            if same_sig:
                print("  blob prefix:", ra.blob[:40].decode("ascii", errors="replace"))
            else:
                sig_diff = sum(1 for x, y in zip(ra.blob, rb.blob) if x != y)
                print(f"  sig diff bytes: {sig_diff}/226")

        _, ta = text_field(a)
        _, tb = text_field(b)
        print(f"text fields: {ta!r} vs {tb!r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
