"""Background session keepalive — refresh CSRF/WS without wiping login."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger("pigeon.session_keepalive")

_thread: threading.Thread | None = None
_stop = threading.Event()
_interval_sec = 10 * 60
_renew_interval_sec = 5 * 60
_last_tick: dict[str, Any] = {"ts": 0, "ok": False}


def keepalive_tick(*, save: bool = True) -> dict[str, Any]:
    """Light refresh — never clears cookies on failure."""
    from pigeon_protocol.session import load_session, save_session
    from pigeon_protocol.session_health import auto_heal_session, check_session
    from pigeon_protocol.session_readiness import assess_runtime_ready

    from pigeon_protocol.session_renewal import session_alive

    session = load_session()
    report: dict[str, Any] = {"steps": []}
    alive = session_alive(session)

    if not alive:
        report["ok"] = False
        report["error"] = "not logged in"
        return report

    h = check_session(session)
    csrf_stale = h.relay_age_sec is None or h.relay_age_sec > 1800

    heal = auto_heal_session(session, refresh_csrf=csrf_stale, refresh_sign=True)
    if heal.fixes_applied:
        report["steps"].extend(heal.fixes_applied)

    try:
        from pigeon_protocol.ws_token_refresh import ensure_fresh_ws_token

        wr = ensure_fresh_ws_token(session, probe=True)
        report["ws_refresh"] = wr
        if wr.get("ok"):
            report["steps"].append("ws_token_ok")
    except Exception as exc:
        logger.debug("keepalive ws: %s", exc)

    ready = assess_runtime_ready(session, probe_backstage=True)
    report["readiness"] = {
        "send_ready": ready.get("send_ready"),
        "listen_ready": ready.get("listen_ready"),
        "backstage_ok": ready.get("backstage_ok"),
        "backstage_expired": ready.get("backstage_expired"),
        "recommended_action": ready.get("recommended_action"),
    }

    needs_renew = alive and (
        ready.get("backstage_expired")
        or (not ready.get("backstage_ok") and not ready.get("send_ready"))
    )
    if needs_renew:
        try:
            from pigeon_protocol.session_renewal import renew_session_if_needed

            renew = renew_session_if_needed(session, persist=True)
            report["renew"] = {
                "ok": renew.get("ok"),
                "steps": renew.get("steps"),
                "error": renew.get("error"),
                "needs_cdp_onboard": renew.get("needs_cdp_onboard"),
            }
            if renew.get("steps"):
                report["steps"].extend([f"renew:{s}" for s in renew["steps"][:8]])
            ready = assess_runtime_ready(session, probe_backstage=True)
            report["readiness"] = {
                "send_ready": ready.get("send_ready"),
                "listen_ready": ready.get("listen_ready"),
                "backstage_ok": ready.get("backstage_ok"),
                "backstage_expired": ready.get("backstage_expired"),
                "recommended_action": ready.get("recommended_action"),
            }
        except Exception as exc:
            logger.warning("keepalive renew: %s", exc)
            report["renew_error"] = str(exc)[:200]

    if ready.get("backstage_ok") and not ready.get("send_ready"):
        try:
            from pigeon_protocol.foundation.ws_inner_health import session_inner_health
            from pigeon_protocol.cdp_warm_inners import auto_warm_inners_if_needed

            inner = session_inner_health(session)
            if inner.get("needs_cdp_warm"):
                warm = auto_warm_inners_if_needed(launch=True, background=True)
                report["cdp_warm"] = {
                    "ok": warm.get("ok"),
                    "background": warm.get("background"),
                    "skipped": warm.get("skipped"),
                    "reason": warm.get("reason"),
                }
                if warm.get("background") and warm.get("ok"):
                    report["steps"].append("cdp_warm_bg")
        except Exception as exc:
            logger.warning("keepalive cdp warm: %s", exc)
            report["cdp_warm_error"] = str(exc)[:200]

    if save:
        try:
            save_session(session)
        except OSError as exc:
            report["save_error"] = str(exc)[:120]

    report["ok"] = bool(ready.get("listen_ready"))
    report["ts"] = int(time.time())
    _last_tick.clear()
    _last_tick.update(report)
    return report


def last_keepalive() -> dict[str, Any]:
    return dict(_last_tick)


def start_keepalive_loop(interval_sec: int = 0) -> None:
    global _thread, _interval_sec
    if interval_sec > 0:
        _interval_sec = interval_sec
    if _thread and _thread.is_alive():
        return
    _stop.clear()

    def _loop() -> None:
        try:
            keepalive_tick(save=True)
        except Exception as exc:
            logger.warning("keepalive initial tick: %s", exc)
        while not _stop.is_set():
            _stop.wait(_interval_sec)
            if _stop.is_set():
                break
            try:
                keepalive_tick(save=True)
            except Exception as exc:
                logger.warning("keepalive tick: %s", exc)

    _thread = threading.Thread(target=_loop, daemon=True, name="session-keepalive")
    _thread.start()


def stop_keepalive_loop() -> None:
    _stop.set()
