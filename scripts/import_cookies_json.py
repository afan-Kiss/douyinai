#!/usr/bin/env python3
"""Import Cookie-Editor JSON export into session/session.json."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from pigeon_protocol.session import load_session, save_session


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "session" / "cookies_import.json"
    items = json.loads(src.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        print("expected JSON array", file=sys.stderr)
        return 1

    session = load_session()
    for item in items:
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "").strip()
        if not name or not value or value == "undefined":
            continue
        session.cookies[name] = value

    csrf = session.cookies.get("csrf_session_id") or session.cookies.get("passport_csrf_token_default", "")
    passport = session.cookies.get("passport_csrf_token") or session.cookies.get("passport_csrf_token_default", "")
    if csrf and passport:
        session.headers["x-secsdk-csrf-token"] = f"000100000001{passport},{csrf}"

    if session.cookies.get("SHOP_ID"):
        session.shop_id = session.cookies["SHOP_ID"]
    if session.cookies.get("s_v_web_id"):
        session.query_tokens["verifyFp"] = session.cookies["s_v_web_id"]
        session.query_tokens["fp"] = session.cookies["s_v_web_id"]

    session.notes.append(f"imported {len(session.cookies)} cookies from {src.name}")
    path = save_session(session)
    print(json.dumps({"session": str(path), "cookies": len(session.cookies), "shop_id": session.shop_id}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
