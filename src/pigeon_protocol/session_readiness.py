"""Runtime readiness — assess, heal, recommend next action (priority-ordered)."""
from __future__ import annotations

import logging
import time
from typing import Any

from pigeon_protocol.session_renewal import session_alive

logger = logging.getLogger("pigeon.session_readiness")

_BACKSTAGE_CACHE: dict[str, Any] = {"ts": 0.0, "result": {}}
_BACKSTAGE_TTL_SEC = 120.0


def _probe_backstage_cached(session) -> dict[str, Any]:
    now = time.time()
    if now - float(_BACKSTAGE_CACHE.get("ts") or 0) < _BACKSTAGE_TTL_SEC:
        cached = _BACKSTAGE_CACHE.get("result")
        if isinstance(cached, dict) and cached:
            return dict(cached)
    try:
        from pigeon_protocol.feige_init import probe_backstage_session

        result = probe_backstage_session(session)
    except Exception as exc:
        result = {"ok": False, "error": str(exc)[:200]}
    _BACKSTAGE_CACHE["ts"] = now
    _BACKSTAGE_CACHE["result"] = result
    return dict(result)


def recommend_action(
    *,
    send_ready: bool,
    listen_ready: bool,
    conv_ready: bool,
    backstage_ok: bool,
    backstage_expired: bool,
    inner_cached: int = 0,
) -> str:
    from pigeon_protocol.pure_config import cdp_allowed

    use_cdp = cdp_allowed()
    if backstage_expired or (not backstage_ok and not send_ready):
        return "cdp_onboard" if use_cdp else "session_renew"
    if not send_ready:
        if backstage_ok:
            import os

            if os.environ.get("PIGEON_NO_RUST", "").strip().lower() in ("1", "true", "yes"):
                return "edbx_derive"
            return "cdp_warm_inners" if use_cdp else "rust_sdk_inner"
        return "cdp_onboard" if use_cdp else "session_renew"
    if not listen_ready:
        return "session_doctor"
    if not conv_ready:
        return "session_doctor"
    return "ready"


def assess_runtime_ready(session, *, probe_backstage: bool = True) -> dict[str, Any]:
    """Extended readiness with backstage probe + recommended_action."""
    from pigeon_protocol.foundation.bdms_sign import sign_available
    from pigeon_protocol.foundation.status import foundation_report
    from pigeon_protocol.foundation.ws_inner_health import session_inner_health
    from pigeon_protocol.session_portable import _assess_runtime_ready_legacy

    base = _assess_runtime_ready_legacy(session)
    inner = session_inner_health(session)
    foundation = foundation_report(session)

    backstage: dict[str, Any] = {"ok": True, "skipped": True}
    if probe_backstage and session.cookie_header():
        backstage = _probe_backstage_cached(session)

    backstage_ok = bool(backstage.get("ok"))
    backstage_expired = bool(backstage.get("expired"))

    blockers = list(base.get("blockers") or [])
    if backstage_expired:
        msg = "pigeon backstage 已过期(10005)，需浏览器登录飞鸽"
        if msg not in blockers:
            blockers.insert(0, msg)
    elif probe_backstage and session.cookie_header() and not backstage_ok:
        code = backstage.get("code") or backstage.get("error") or "unknown"
        msg = f"backstage 未就绪({code})，抖店二维码无法建立飞鸽会话"
        if msg not in blockers:
            blockers.append(msg)

    from pigeon_protocol.pure_config import cdp_allowed

    action = recommend_action(
        send_ready=bool(base.get("send_ready")),
        listen_ready=bool(base.get("listen_ready")),
        conv_ready=bool(base.get("conv_ready")),
        backstage_ok=backstage_ok,
        backstage_expired=backstage_expired,
        inner_cached=int(inner.get("cached_count") or 0),
    )

    needs_cdp = cdp_allowed() and action in ("cdp_onboard", "cdp_warm_inners")

    return {
        **base,
        "blockers": blockers,
        "backstage_ok": backstage_ok,
        "backstage_expired": backstage_expired,
        "backstage": {k: backstage.get(k) for k in ("ok", "code", "expired", "via", "error") if k in backstage},
        "recommended_action": action,
        "needs_cdp_onboard": needs_cdp,
        "foundation_ok": bool(foundation.ok),
        "sign_available": bool(sign_available()),
    }


def heal_for_send(session, *, save: bool = True) -> dict[str, Any]:
    """Best-effort heal before WS send — restore inners, tokens, WS URL."""
    from pigeon_protocol.session import save_session
    from pigeon_protocol.session_portable import ensure_portable_ready

    report: dict[str, Any] = {"steps": []}

    portable = ensure_portable_ready(session, heal=True)
    report["portable"] = portable
    if portable.get("steps"):
        report["steps"].extend(portable["steps"])

    ready = assess_runtime_ready(session, probe_backstage=True)
    report["readiness_before_rust"] = ready

    if not ready.get("backstage_ok") and session_alive(session):
        try:
            from pigeon_protocol.session_renewal import establish_im_session_http

            renew = establish_im_session_http(session, persist=False)
            report["im_renew"] = {
                "ok": renew.get("ok"),
                "steps": renew.get("steps"),
                "error": renew.get("error"),
            }
            if renew.get("ok"):
                report["steps"].append("im_renew")
            ready = assess_runtime_ready(session, probe_backstage=True)
        except Exception as exc:
            report["im_renew_error"] = str(exc)[:200]

    if not ready.get("send_ready") and ready.get("backstage_ok"):
        try:
            from pigeon_protocol.foundation.rust_sdk_inner import rust_sdk_seed_send_inner

            rust = rust_sdk_seed_send_inner(session)
            report["rust_sdk"] = {
                "ok": rust.get("ok"),
                "ingested": rust.get("ingested_classes"),
                "error": (rust.get("error") or "")[:200],
            }
            if rust.get("ingested_classes"):
                report["steps"].append("rust_sdk_inner")
        except Exception as exc:
            report["rust_sdk_error"] = str(exc)[:200]

        ready = assess_runtime_ready(session, probe_backstage=False)
        if not ready.get("send_ready"):
            from pigeon_protocol.pure_config import cdp_allowed

            if cdp_allowed():
                try:
                    from pigeon_protocol.cdp_warm_inners import auto_warm_inners_if_needed

                    warm = auto_warm_inners_if_needed(launch=True, background=False)
                    report["cdp_warm"] = {
                        "ok": warm.get("ok"),
                        "skipped": warm.get("skipped"),
                        "reason": warm.get("reason"),
                        "stored": len((warm.get("stored") or [])),
                        "error": warm.get("error"),
                    }
                    if warm.get("ok") and not warm.get("skipped"):
                        report["steps"].append("cdp_warm_inners")
                except Exception as exc:
                    report["cdp_warm_error"] = str(exc)[:200]
            else:
                try:
                    from pigeon_protocol.cdp_warm_inners import auto_warm_inners_if_needed

                    warm = auto_warm_inners_if_needed(launch=False, background=False, force=True)
                    report["rust_warm"] = warm
                    if warm.get("rust") or warm.get("reason") == "rust_sdk_no_cdp":
                        report["steps"].append("rust_sdk_warm")
                except Exception as exc:
                    report["rust_warm_error"] = str(exc)[:200]

    ready = assess_runtime_ready(session, probe_backstage=False)
    report["readiness"] = ready
    report["send_ready"] = ready.get("send_ready")
    report["recommended_action"] = ready.get("recommended_action")

    if save:
        try:
            save_session(session)
        except OSError as exc:
            report["save_error"] = str(exc)[:120]

    report["ok"] = bool(ready.get("send_ready"))
    return report
