#!/usr/bin/env python3
"""RE report: init field6 prefix8 template vs live field12 (init field10)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    from pigeon_protocol.foundation.init_edbx_seeds import analyze_prefix8_vs_field12
    from pigeon_protocol.foundation.init_timestamps import load_init_bytes
    from pigeon_protocol.foundation.ws_inner_edbx import derive_prefix_from_ts_us
    from pigeon_protocol.session import load_session

    session = load_session()
    raw, src = load_init_bytes(session)
    live_prefix = None
    try:
        from pigeon_protocol.foundation.ws_blob_compute import _load_session_class_cache
        from pigeon_protocol.foundation.ws_inner_edbx import is_edbx_inner

        for inner in _load_session_class_cache(session).values():
            if is_edbx_inner(inner):
                live_prefix = inner[4:12]
                break
    except Exception:
        pass

    report = analyze_prefix8_vs_field12(raw, live_prefix=live_prefix)
    report["init_source"] = src
    if live_prefix:
        report["live_capture_prefix_hex"] = live_prefix.hex()
    ts = report.get("init_field10_us") or 0
    if ts:
        report["derived_matches_live_capture"] = derive_prefix_from_ts_us(int(ts)) == live_prefix

    out = ROOT / "analysis" / "prefix8_field12_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
