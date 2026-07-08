#!/usr/bin/env python3
"""Smoke test: multi-account session isolation, logout, unread bumps."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _setup_temp_root() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="pigeon-iso-"))
    os.environ["PIGEON_ROOT"] = str(tmp)
    import pigeon_protocol.config as cfg

    cfg.ROOT = tmp
    import pigeon_protocol.account_context as ac

    ac.ROOT = tmp
    ac.ACCOUNTS_ROOT = tmp / "accounts"
    ac.REGISTRY_FILE = ac.ACCOUNTS_ROOT / "registry.json"
    ac.LEGACY_SESSION_DIR = tmp / "session"
    ac.LEGACY_BUNDLE_DIR = tmp / "standalone_bundle"
    ac._initialized = False
    return tmp


def _write_session(ac, aid: str, *, shop_id: str, sessionid: str) -> None:
    from pigeon_protocol.session import SessionState, save_session

    ac.apply_account_env(aid)
    ac.ensure_account_dirs(aid)
    save_session(
        SessionState(cookies={"sessionid": sessionid, "SHOP_ID": shop_id}, shop_id=shop_id),
        ac.session_file(),
    )


def main() -> int:
    tmp = _setup_temp_root()
    failures: list[str] = []
    try:
        import pigeon_protocol.account_context as ac
        from pigeon_protocol.session import load_session

        ac.init_account_context(migrate=False)
        ac.register_account("shop_a", label="A店", shop_id="111", set_active=True)
        ac.register_account("shop_b", label="B店", shop_id="222")

        _write_session(ac, "shop_a", shop_id="111", sessionid="sess_a")
        _write_session(ac, "shop_b", shop_id="222", sessionid="sess_b")

        ac.switch_account("shop_a")
        sess = load_session()
        if sess.cookies.get("sessionid") != "sess_a":
            failures.append("switch A: expected sess_a")

        ac.switch_account("shop_b")
        sess = load_session()
        if sess.cookies.get("sessionid") != "sess_b":
            failures.append("switch B: expected sess_b")

        out = ac.logout_account("shop_a", backup=False)
        if not out.get("ok"):
            failures.append(f"logout A failed: {out}")
        ac.switch_account("shop_b")
        sess = load_session()
        if sess.cookies.get("sessionid") != "sess_b":
            failures.append("logout A should not affect B session")

        doc = ac.load_registry()
        row_a = next((r for r in doc.get("accounts") or [] if r.get("id") == "shop_a"), None)
        if not row_a or not row_a.get("logged_out_at"):
            failures.append("logout A should set logged_out_at in registry")

        ac.remove_account("shop_a", backup=False)
        doc = ac.load_registry()
        if any(str(r.get("id") or "") == "shop_a" for r in doc.get("accounts") or []):
            failures.append("remove A should drop registry row")
        ac.switch_account("shop_b")
        sess = load_session()
        if sess.cookies.get("sessionid") != "sess_b":
            failures.append("remove A should not affect B")

        from pigeon_protocol import api_server as api

        api._unread_bump.clear()
        with api._unread_lock:
            bumps_a = api._unread_bump.setdefault("shop_a", {})
            bumps_a["uid1"] = 2
            bumps_b = api._unread_bump.setdefault("shop_b", {})
            bumps_b["uid1"] = 5
        with api._unread_lock:
            if api._unread_bump.get("shop_a", {}).get("uid1") != 2:
                failures.append("unread bump A missing")
            if api._unread_bump.get("shop_b", {}).get("uid1") != 5:
                failures.append("unread bump B missing")
        with api._unread_lock:
            api._unread_bump.pop("shop_a", None)
        with api._unread_lock:
            if api._unread_bump.get("shop_b", {}).get("uid1") != 5:
                failures.append("clear A bumps should not affect B")

        events = []
        with api._event_lock:
            api._event_queue.clear()
        api._push_event("message", {"message": {"security_user_id": "u1"}, "account_id": "shop_a"})
        api._push_event("message", {"message": {"security_user_id": "u2"}, "account_id": "shop_b"})
        with api._event_lock:
            for e in api._event_queue:
                evt_aid = str(e.get("account_id") or "")
                if evt_aid == "shop_b":
                    events.append(e)
        if len(events) != 1 or events[0].get("message", {}).get("security_user_id") != "u2":
            failures.append("event filter by account_id failed")

        if failures:
            print("FAIL")
            for f in failures:
                print(" -", f)
            return 1
        print("PASS test_account_isolation")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
