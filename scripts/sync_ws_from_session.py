#!/usr/bin/env python3
"""Offline sync WS URL/tokens from session.json only (no CDP)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from pigeon_protocol.session import load_session, save_session  # noqa: E402


def main() -> int:
    session = load_session()
    print(json.dumps({
        "cookies": len(session.cookies),
        "ws_urls": len(session.ws_urls),
        "has_token": bool(session.query_tokens.get("token")),
        "has_pigeon_sign": bool(session.query_tokens.get("pigeon_sign")),
    }, indent=2))
    if not session.ws_urls:
        print("No ws_urls — import from HAR or run prepare once", file=sys.stderr)
        return 1
    save_session(session)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
