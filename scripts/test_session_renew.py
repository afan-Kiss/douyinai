#!/usr/bin/env python3
"""Simulate backstage expiry (PHPSESSID drop) and test auto renew without QR."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

SESSION = ROOT / "session" / "session.json"
BACKUP = ROOT / "session" / "backups" / "test_renew_before.json"
REPORT = ROOT / "analysis" / "session_renew_test.json"

_PHP_KEYS = ("PHPSESSID", "PHPSESSID_SS")
_SOFT_KEYS = (
    "csrf_session_id",
    "fg_uid",
    "fems-gray-random",
    "has_biz_token",
)


def _log(msg: str) -> None:
    print(msg, flush=True)


def _invalidate_backstage(session) -> list[str]:
    removed: list[str] = []
    for key in _PHP_KEYS + _SOFT_KEYS:
        if session.cookies.pop(key, None) is not None:
            removed.append(f"cookie:{key}")
    session.headers.pop("x-secsdk-csrf-token", None)
    if any(k.endswith("csrf_session_id") for k in removed):
        removed.append("header:x-secsdk-csrf-token")
    for tk in ("pigeon_sign", "token", "access_key"):
        if session.query_tokens.pop(tk, None) is not None:
            removed.append(f"token:{tk}")
    session.ws_urls = []
    return removed


def main() -> int:
    from pigeon_protocol.feige_init import probe_backstage_session
    from pigeon_protocol.session import load_session, save_session
    from pigeon_protocol.session_readiness import _BACKSTAGE_CACHE
    from pigeon_protocol.session_renewal import establish_im_session_http

    SESSION.parent.mkdir(parents=True, exist_ok=True)
    BACKUP.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SESSION, BACKUP)

    session = load_session()
    report: dict = {"phase": "test_session_renew", "removed": _invalidate_backstage(session)}
    save_session(session)
    _BACKSTAGE_CACHE.clear()

    _log("probe before renew…")
    before = probe_backstage_session(session)
    report["backstage_before"] = before
    report["simulated_10005"] = bool(before.get("expired") or before.get("code") == "10005")

    _log("renew (HTTP → CDP fallback)…")
    renew = establish_im_session_http(session, persist=True, cdp_fallback=True)
    report["renew"] = {
        "ok": renew.get("ok"),
        "via": renew.get("via"),
        "steps": renew.get("steps"),
        "cdp_renew": renew.get("cdp_renew"),
        "error": renew.get("error"),
        "needs_cdp_onboard": renew.get("needs_cdp_onboard"),
    }
    save_session(session)
    _BACKSTAGE_CACHE.clear()

    _log("probe after renew…")
    after = probe_backstage_session(session)
    report["backstage_after"] = after
    report["ok"] = bool(after.get("ok"))
    report["restored_without_qr"] = bool(after.get("ok")) and bool(report["simulated_10005"])

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if not report["ok"]:
        shutil.copy2(BACKUP, SESSION)
        print("RESTORED session from backup (renew failed)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
