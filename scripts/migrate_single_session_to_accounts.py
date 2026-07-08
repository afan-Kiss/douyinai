#!/usr/bin/env python3
"""One-time migration: session/ + standalone_bundle/ → accounts/shop_<id>/."""
from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate legacy single-session layout to accounts/")
    parser.add_argument("--dry-run", action="store_true", help="report only, do not write")
    args = parser.parse_args()

    from pigeon_protocol.account_context import (
        LEGACY_SESSION_DIR,
        init_account_context,
        list_accounts,
    )

    if not LEGACY_SESSION_DIR.joinpath("session.json").is_file():
        print(json.dumps({"ok": True, "note": "no legacy session to migrate"}, ensure_ascii=False, indent=2))
        return 0

    if args.dry_run:
        sess = json.loads(LEGACY_SESSION_DIR.joinpath("session.json").read_text(encoding="utf-8"))
        shop = sess.get("shop_id") or (sess.get("cookies") or {}).get("SHOP_ID") or ""
        print(
            json.dumps(
                {"ok": True, "dry_run": True, "would_migrate_shop": shop},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    report = init_account_context(migrate=True)
    print(
        json.dumps(
            {"ok": True, "migration": report.get("migration"), "accounts": list_accounts()},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
