#!/usr/bin/env python3
"""Import cookies from Chrome profile into session.json."""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from pigeon_protocol.session import load_session, save_session

DEFAULT_PROFILE = Path(r"D:\douyin-customer-assistant\data\chrome-profile")
DOMAINS = ("jinritemai.com", "douyin.com", "snssdk.com")


def read_cookies(db_path: Path) -> dict[str, str]:
    tmp = Path(tempfile.mkdtemp()) / "cookies.sqlite"
    shutil.copy2(db_path, tmp)
    conn = sqlite3.connect(tmp)
    cur = conn.cursor()
    cur.execute("SELECT host_key, name, value, encrypted_value FROM cookies")
    out: dict[str, str] = {}
    for host, name, value, enc in cur.fetchall():
        if not any(d in host for d in DOMAINS):
            continue
        val = value or ""
        if not val and enc:
            continue
        if name and val:
            out[name] = val
    conn.close()
    tmp.unlink(missing_ok=True)
    return out


def main() -> int:
    profile = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PROFILE
    db = profile / "Default" / "Network" / "Cookies"
    if not db.exists():
        db = profile / "Network" / "Cookies"
    if not db.exists():
        print(f"cookie db not found: {db}", file=sys.stderr)
        return 1

    cookies = read_cookies(db)
    session = load_session()
    session.cookies.update(cookies)
    session.notes.append(f"imported {len(cookies)} cookies from {db}")
    path = save_session(session)
    print(json.dumps({"session": str(path), "cookies": len(cookies), "keys": sorted(cookies.keys())[:20]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
