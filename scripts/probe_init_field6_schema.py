#!/usr/bin/env python3
"""Decode init field-6 nested protobuf around edbX send template."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def walk_pb(data: bytes, *, prefix: str = "", depth: int = 0, max_depth: int = 5) -> list[dict]:
    rows: list[dict] = []
    i = 0
    while i < len(data):
        start = i
        tag = data[i]
        fn = tag >> 3
        wire = tag & 7
        i += 1
        name = f"{prefix}{fn}" if prefix else str(fn)
        if wire == 0:
            val = 0
            shift = 0
            while i < len(data):
                b = data[i]
                i += 1
                val |= (b & 0x7F) << shift
                if not (b & 0x80):
                    break
                shift += 7
            rows.append({"field": name, "wire": 0, "offset": start, "varint": val})
        elif wire == 2:
            if i >= len(data):
                break
            ln = data[i]
            i += 1
            if ln & 0x80:
                if i >= len(data):
                    break
                ln = (ln & 0x7F) | (data[i] << 7)
                i += 1
            chunk = data[i : i + ln]
            i += ln
            row = {
                "field": name,
                "wire": 2,
                "offset": start,
                "len": ln,
                "head_hex": chunk[:32].hex(),
            }
            rows.append(row)
            if depth < max_depth and ln <= 256:
                rows.extend(walk_pb(chunk, prefix=f"{name}.", depth=depth + 1, max_depth=max_depth))
        elif wire == 1:
            rows.append({"field": name, "wire": 1, "offset": start, "fixed64": data[i : i + 8].hex()})
            i += 8
        elif wire == 5:
            rows.append({"field": name, "wire": 5, "offset": start, "fixed32": data[i : i + 4].hex()})
            i += 4
        else:
            break
    return rows


def main() -> int:
    from pigeon_protocol.foundation.init_edbx_seeds import (
        extract_init_field_bytes,
        scan_edbx_trailer_seeds,
    )
    from pigeon_protocol.foundation.init_timestamps import load_init_bytes
    from pigeon_protocol.session import load_session

    session = load_session()
    raw, src = load_init_bytes(session)
    f6 = extract_init_field_bytes(raw, 6)
    seeds = scan_edbx_trailer_seeds(f6)
    best = seeds[0] if seeds else None

    report: dict = {
        "init_source": src,
        "field6_len": len(f6),
        "seed_count": len(seeds),
        "seeds": [s.to_dict() for s in seeds],
    }

    if best:
        k = best.offset
        pig = f6.rfind(b":pigeon", 0, k)
        block = f6[max(0, pig - 96) : k + 24]
        report["template_block_offset"] = max(0, pig - 96)
        report["template_block_hex"] = block.hex()
        report["template_fields"] = walk_pb(block, max_depth=4)
        # extract nested message starting at aa tag after pigeon
        aa = block.find(bytes([0xAA]))
        if aa >= 0:
            report["nested_from_aa"] = walk_pb(block[aa:], max_depth=4)

    out = ROOT / "analysis" / "init_field6_schema.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
