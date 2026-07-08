#!/usr/bin/env python3
"""Derive edbX 169B inner from init timestamps + IM accessToken (pure HTTP path)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

SAMPLE_INNER = (
    "6564625896aa84fdaec0950360fbaa84fdaec0950368bc8c06128104080132fc030a030a013212f4030af103080010071a6e415141656f4d4b52624631674b494c696573425a464e79794846395050306c38577436316150663665577639314b345753497a48737535394b57576534616333575f4f4f6149486656656c48786e61523269346f704930573a3236333633363436353a3a323a313a706967656f6e20b38a848485e5c1a1"
)


def main() -> int:
    from pigeon_protocol.foundation.init_timestamps import load_init_bytes, parse_init_timestamps
    from pigeon_protocol.foundation.ws_inner_edbx import (
        build_edbx_inner_derived,
        decode_ts_us_from_prefix,
        derive_prefix_from_ts_us,
        extract_edbx_ticket,
    )
    from pigeon_protocol.session import load_session

    session = load_session()
    raw, init_src = load_init_bytes(session)
    ts = parse_init_timestamps(raw)
    sample = bytes.fromhex(SAMPLE_INNER)
    prefix = sample[4:12]
    trailer = sample[4 + 157 : 4 + 165]
    f12 = decode_ts_us_from_prefix(prefix) or 1783420470498683
    route = extract_edbx_ticket(sample) or ""

    rebuilt = build_edbx_inner_derived(route.lstrip("n"), ts_us=f12, trailer=trailer)
    formula_ok = rebuilt == sample

    from pigeon_protocol.foundation.ws_inner_edbx import verify_sample_formula

    verify = verify_sample_formula(sample_hex=SAMPLE_INNER)

    inner, report = __import__(
        "pigeon_protocol.foundation.ws_inner_edbx", fromlist=["derive_edbx_inner_session"]
    ).derive_edbx_inner_session(session)

    out = {
        "formula_rebuild_sample": formula_ok,
        "verify_sample_formula": verify,
        "init_source": init_src,
        "init_timestamps": ts,
        "sample_f12_us": f12,
        "sample_prefix": prefix.hex(),
        "derived_prefix_from_f12": derive_prefix_from_ts_us(f12).hex(),
        "prefix_match": derive_prefix_from_ts_us(f12) == prefix,
        "session_derive": report,
        "session_inner_ok": bool(inner),
        "session_header": inner[:8].hex() if inner else None,
    }
    path = ROOT / "analysis" / "derive_edbx_report.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if formula_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
