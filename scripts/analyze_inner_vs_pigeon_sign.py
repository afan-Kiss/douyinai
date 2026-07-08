#!/usr/bin/env python3
"""Analyze pigeon_sign ticket vs 169B inner blob in WS send frames."""
from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def analyze_frame(raw: bytes) -> dict:
    from pigeon_protocol.ws_sign import locate_signature_region
    from pigeon_protocol.ws_sign_decode import decode_blob, guess_inner_layout

    row: dict = {}
    region = locate_signature_region(raw)
    if region:
        inner = decode_blob(region.blob)
        row["inner_hex"] = inner.hex()
        row["inner_layout"] = guess_inner_layout(inner)
        row["blob_start"] = region.blob_start

    mii_hits = [m.group(0).decode("ascii", errors="ignore") for m in re.finditer(rb"MII[A-Za-z0-9+/=]{80,500}", raw)]
    row["mii_tickets"] = mii_hits[:3]
    row["frame_len"] = len(raw)
    return row


def main() -> int:
    samples: list[dict] = []

    hook = ROOT / "analysis" / "pigeon_rust_hook.json"
    if hook.is_file():
        doc = json.loads(hook.read_text(encoding="utf-8"))
        for ws in doc.get("ws_sends") or []:
            b64 = ws.get("b64")
            if b64:
                samples.append({"source": "hook", **analyze_frame(base64.b64decode(b64))})

    for path in sorted((ROOT / "captures").rglob("live_ws_frame_sent_b*.json"))[:12]:
        ev = json.loads(path.read_text(encoding="utf-8"))
        raw = base64.b64decode(ev["payload"])
        samples.append({"source": path.name, **analyze_frame(raw)})

    # pairwise inner prefix stability
    inners = [bytes.fromhex(s["inner_hex"]) for s in samples if s.get("inner_hex")]
    report = {"samples": samples, "inner_count": len(inners)}
    if len(inners) >= 2:
        stable = 0
        for i in range(min(len(x) for x in inners)):
            if all(x[i] == inners[0][i] for x in inners):
                stable += 1
            else:
                break
        report["stable_prefix_bytes"] = stable
        report["unique_inners"] = len({x.hex() for x in inners})

    out = ROOT / "analysis" / "inner_vs_pigeon_sign.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
