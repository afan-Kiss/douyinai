"""Ensure WS token is live before connect/send — pure HTTP get_link_info."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("pigeon.ws_refresh")


def ensure_fresh_ws_token(session, *, probe: bool = True) -> dict[str, Any]:
    """
    Find a connectable ws.fxg URL — scan session history, then get_link_info bootstrap.
    """
    from pigeon_protocol.ws_url_builder import find_working_ws_url, promote_ws_url
    from pigeon_protocol.session import save_session

    report: dict[str, Any] = {"steps": []}

    working = find_working_ws_url(session)
    if working:
        promote_ws_url(session, working)
        report["ok"] = True
        report["url"] = working[:120]
        report["via"] = "probe_scan"
        return report

    if probe:
        report["steps"].append("probe_scan_empty")

    from pigeon_protocol.feige_init import _fetch_get_link_info

    link = _fetch_get_link_info(session)
    report["get_link_info"] = link
    if link.get("ok"):
        report["steps"].append("get_link_info")
    else:
        report["ok"] = False
        report["error"] = link.get("error") or "get_link_info failed"
        return report

    working = find_working_ws_url(session)
    if working:
        promote_ws_url(session, working)
        report["steps"].append("ws_url_working")
        try:
            save_session(session)
        except Exception:
            pass
        report["ok"] = True
        report["url"] = working[:120]
        return report

    # Optional one-shot CDP sync when Chrome is logged in
    try:
        from pigeon_protocol.session_sync import CdpSessionSync

        if CdpSessionSync.available():
            sync = CdpSessionSync(session)
            sr = sync.sync()
            report["cdp_sync"] = sr
            if sr.get("ok"):
                report["steps"].append("cdp_sync")
                working = find_working_ws_url(session)
                if working:
                    promote_ws_url(session, working)
                    save_session(session)
                    report["ok"] = True
                    report["url"] = working[:120]
                    return report
    except Exception as exc:
        logger.debug("cdp ws sync skipped: %s", exc)

    report["ok"] = False
    report["error"] = "no connectable ws url"
    return report
