#!/usr/bin/env python3
"""Simulate new-machine cold start: portable pack + pure edbX derive, no Rust."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("PIGEON_NO_RUST", "1")
os.environ.setdefault("PIGEON_NO_CDP", "1")
os.environ.setdefault("PIGEON_WS_HOST", "jinritemai")


def main() -> int:
    from pigeon_protocol.foundation.ws_inner_edbx import derive_edbx_inner_session, resolve_trailer
    from pigeon_protocol.foundation.ws_inner_health import session_inner_health
    from pigeon_protocol.session import load_session, save_session
    from pigeon_protocol.session_portable import ensure_portable_ready, sync_portable_inner_sidecar

    session = load_session()
    portable = ensure_portable_ready(session, heal=True, trust_pack=True)
    sidecar = sync_portable_inner_sidecar(session, force=True)

    trailer, tail_via = resolve_trailer(session)
    inner, derive = derive_edbx_inner_session(session)
    health = session_inner_health(session)

    out = {
        "portable": portable,
        "sidecar_written": sidecar.get("written"),
        "trailer_hex": trailer.hex() if trailer else None,
        "trailer_via": tail_via,
        "derive_ok": bool(derive.get("ok")),
        "derive": derive,
        "inner_header": inner[:8].hex() if inner else None,
        "send_ready": bool(health.get("ready")),
        "health": health,
    }

    path = ROOT / "analysis" / "portable_cold_start.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    save_session(session)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["derive_ok"] and out["send_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
