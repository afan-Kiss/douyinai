#!/usr/bin/env python3
"""Probe edbX 8B trailer derivation from IM accessToken + init timestamps."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

SAMPLE_TRAILER = bytes.fromhex("b38a848485e5c1a1")


def main() -> int:
    from pigeon_protocol.foundation.im_access_token import resolve_im_access_token
    from pigeon_protocol.foundation.init_timestamps import load_init_bytes, resolve_edbx_timestamp_us
    from pigeon_protocol.foundation.ws_inner_edbx import (
        FALLBACK_TRAILER_HEX,
        derive_trailer_candidates,
        normalize_trailer,
        resolve_trailer,
    )
    from pigeon_protocol.session import load_session

    session = load_session()
    raw, init_src = load_init_bytes(session)
    ts_us, ts_via = resolve_edbx_timestamp_us(session, raw=raw)
    token, token_via = resolve_im_access_token(session, allow_node=False)
    device_id = str(getattr(session, "device_id", "") or "")

    hits: list[dict] = []
    if token:
        for label, cand in derive_trailer_candidates(token, ts_us=ts_us, device_id=device_id):
            if cand == SAMPLE_TRAILER:
                hits.append({"label": label, "hex": cand.hex()})

    resolved, via = resolve_trailer(session, access_token=token or "", ts_us=ts_us)
    out = {
        "init_source": init_src,
        "ts_us": ts_us,
        "ts_via": ts_via,
        "access_token_via": token_via,
        "access_token_preview": (token[:12] + "...") if token else None,
        "sample_trailer_hex": SAMPLE_TRAILER.hex(),
        "fallback_trailer_hex": FALLBACK_TRAILER_HEX,
        "resolved_trailer_hex": resolved.hex() if resolved else None,
        "resolved_via": via,
        "candidate_hits_vs_sample": hits,
        "candidate_count": len(derive_trailer_candidates(token or "00000000-0000-0000-0000-000000000001", ts_us=ts_us, device_id=device_id)),
        "legacy_normalize_ok": normalize_trailer(bytes.fromhex("20" + SAMPLE_TRAILER.hex())) == SAMPLE_TRAILER,
    }
    path = ROOT / "analysis" / "probe_edbx_trailer.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
