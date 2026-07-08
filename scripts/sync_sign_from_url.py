#!/usr/bin/env python3
"""Update msToken/a_bogus/verifyFp in session from a DevTools Network URL."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from pigeon_protocol.session import load_session, save_session

SIGN_KEYS = ("verifyFp", "fp", "msToken", "a_bogus")


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python scripts/sync_sign_from_url.py '<full request URL>'", file=sys.stderr)
        return 1

    url = sys.argv[1].strip()
    qs = parse_qs(urlparse(url).query)
    session = load_session()
    updated: dict[str, str] = {}
    for key in SIGN_KEYS:
        if qs.get(key):
            session.query_tokens[key] = qs[key][0]
            updated[key] = session.query_tokens[key][:48] + ("..." if len(session.query_tokens[key]) > 48 else "")

    if not updated:
        print("no sign tokens found in URL query", file=sys.stderr)
        return 1

    session.notes.append(f"synced sign tokens from {urlparse(url).path}")
    path = save_session(session)
    print(json.dumps({"session": str(path), "updated": updated}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
