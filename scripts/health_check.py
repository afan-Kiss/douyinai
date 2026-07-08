#!/usr/bin/env python3
"""Delivery health check — verify accounts layout + session readiness (exit 0 = OK)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    try:
        from pigeon_protocol.runtime_paths import apply_runtime_env

        apply_runtime_env()
    except Exception:
        pass

    from pigeon_protocol.account_context import account_status, init_account_context
    from pigeon_protocol.session import load_session
    from pigeon_protocol.session_readiness import assess_runtime_ready

    init_account_context(migrate=True)
    session = load_session()
    ready = assess_runtime_ready(session, probe_backstage=False)
    acct = account_status()
    cookies = session.cookies or {}

    report = {
        "ok": bool(ready.get("send_ready") or ready.get("listen_ready")),
        "send_ready": ready.get("send_ready"),
        "listen_ready": ready.get("listen_ready"),
        "backstage_ok": ready.get("backstage_ok"),
        "logged_in": bool(cookies.get("sessionid") or cookies.get("sid_tt")),
        "shop_id": cookies.get("SHOP_ID") or session.shop_id or "",
        "active_account_id": acct.get("active_account_id"),
        "account_count": len(acct.get("accounts") or []),
        "session_dir": acct.get("session_dir"),
        "bundle_dir": acct.get("bundle_dir"),
        "blockers": ready.get("blockers") or [],
        "recommended_action": ready.get("recommended_action"),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] or report["logged_in"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
