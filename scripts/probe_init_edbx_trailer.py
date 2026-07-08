#!/usr/bin/env python3
"""Probe init field-6 edbX trailer extraction (pure HTTP path)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

KNOWN = "b38a848485e5c1a1"


def main() -> int:
    from pigeon_protocol.foundation.init_edbx_seeds import resolve_edbx_trailer_from_init
    from pigeon_protocol.foundation.ws_inner_edbx import resolve_trailer
    from pigeon_protocol.session import load_session

    session = load_session()
    # force init-only path
    session.extra = {k: v for k, v in (session.extra or {}).items() if not str(k).startswith("edbx_")}

    init_trailer, init_via, report = resolve_edbx_trailer_from_init(session)
    resolved, via = resolve_trailer(session)

    out = {
        "init_trailer_hex": init_trailer.hex() if init_trailer else None,
        "init_via": init_via,
        "init_report": report,
        "resolve_trailer_hex": resolved.hex() if resolved else None,
        "resolve_via": via,
        "matches_known": (init_trailer.hex() if init_trailer else "") == KNOWN,
    }
    path = ROOT / "analysis" / "probe_init_edbx_trailer.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["matches_known"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
