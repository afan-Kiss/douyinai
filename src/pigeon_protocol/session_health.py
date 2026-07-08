"""Session health + auto-heal for standalone runtime."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger("pigeon.session_health")

REQUIRED_COOKIES = ("sessionid", "sid_tt", "s_v_web_id")
OPTIONAL_COOKIES = ("csrf_session_id", "SHOP_ID", "PIGEON_CID")
BACKSTAGE_SIGN_KEYS = ("verifyFp", "fp", "msToken", "a_bogus")
WS_SIGN_KEYS = ("token", "access_key", "device_id", "pigeon_sign")


@dataclass
class SessionHealth:
    ok: bool = False
    cookies: int = 0
    has_ws_url: bool = False
    has_s_v_web_id: bool = False
    has_csrf_cookie: bool = False
    has_relay_headers: bool = False
    relay_age_sec: int | None = None
    issues: list[str] = field(default_factory=list)
    fixes_applied: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "cookies": self.cookies,
            "has_ws_url": self.has_ws_url,
            "has_s_v_web_id": self.has_s_v_web_id,
            "has_csrf_cookie": self.has_csrf_cookie,
            "has_relay_headers": self.has_relay_headers,
            "relay_age_sec": self.relay_age_sec,
            "issues": self.issues,
            "fixes_applied": self.fixes_applied,
        }


def check_session(session) -> SessionHealth:
    from pigeon_protocol.order_relay_headers import load_relay_header_template

    h = SessionHealth()
    h.cookies = len(session.cookies or {})
    h.has_ws_url = bool(session.ws_urls)
    h.has_s_v_web_id = bool(session.cookies.get("s_v_web_id"))
    h.has_csrf_cookie = bool(session.cookies.get("csrf_session_id"))

    tpl = load_relay_header_template()
    h.has_relay_headers = bool(tpl.get("x-secsdk-csrf-token"))

    try:
        import json

        from pigeon_protocol.account_context import analysis_env_file, bundle_file

        for p in (analysis_env_file(), bundle_file("bdms_browser_env.json")):
            if p.exists():
                env = json.loads(p.read_text(encoding="utf-8"))
                ts = int(env.get("relayHeadersTs") or 0)
                if ts:
                    h.relay_age_sec = int(time.time()) - ts
                break
    except Exception:
        pass

    if h.cookies < 5:
        h.issues.append("few cookies — import HAR or login capture")
    for k in REQUIRED_COOKIES:
        if not session.cookies.get(k):
            h.issues.append(f"missing cookie: {k}")
    if not h.has_ws_url:
        h.issues.append("no ws_url — import HAR with WS or bootstrap")
    if not h.has_s_v_web_id:
        h.issues.append("missing s_v_web_id (verifyFp)")
    if not h.has_csrf_cookie:
        h.issues.append("missing csrf_session_id — login Feige once")

    h.ok = not any("missing cookie: sessionid" in i or "missing cookie: sid_tt" in i for i in h.issues)
    return h


def _load_browser_env() -> dict[str, Any]:
    from pigeon_protocol.account_context import analysis_env_file, bundle_file

    for path in (bundle_file("bdms_browser_env.json"), analysis_env_file()):
        if not path.exists():
            continue
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return {}


def refresh_query_tokens_from_env(session) -> list[str]:
    """Sync msToken/verifyFp from captured bdms env + cookies (no CDP)."""
    from pigeon_protocol.foundation.bdms_tokens import backstage_query_tokens
    from pigeon_protocol.session import save_session

    applied: list[str] = []
    tokens = backstage_query_tokens(session)
    env = _load_browser_env()
    xmst = str((env.get("localStorage") or {}).get("xmst") or "")
    if xmst and session.query_tokens.get("msToken") != xmst:
        session.query_tokens["msToken"] = xmst
        applied.append("msToken")

    fp = session.cookies.get("s_v_web_id") or tokens.get("verifyFp") or ""
    if fp:
        if session.query_tokens.get("verifyFp") != fp:
            session.query_tokens["verifyFp"] = fp
            applied.append("verifyFp")
        if session.query_tokens.get("fp") != fp:
            session.query_tokens["fp"] = fp
            applied.append("fp")

    if applied:
        try:
            save_session(session)
        except Exception as exc:
            logger.debug("save_session after env token sync: %s", exc)
    return applied


def refresh_ws_tokens_from_urls(session) -> list[str]:
    """Promote token/pigeon_sign from latest pigeon WS URL into session.query_tokens."""
    from pigeon_protocol.session import save_session

    applied: list[str] = []
    ws_url = ""
    for url in reversed(session.ws_urls or []):
        if "ws.fxg.jinritemai.com" in url and "token=" in url:
            ws_url = url
            break
    if not ws_url:
        return applied

    qs = parse_qs(urlparse(ws_url).query)
    for key in WS_SIGN_KEYS:
        val = (qs.get(key) or [""])[0]
        if val and session.query_tokens.get(key) != val:
            session.query_tokens[key] = val
            applied.append(key)

    if applied:
        try:
            save_session(session)
        except Exception as exc:
            logger.debug("save_session after ws token sync: %s", exc)
    return applied


def refresh_backstage_sign_tokens(session) -> list[str]:
    """Refresh a_bogus/msToken — Python or Node bdms on lightweight GET."""
    from pigeon_protocol.config import PIGEON_HOST
    from pigeon_protocol.foundation.bdms_sign import persist_tokens_to_session, sign_available, sign_backstage_url
    from pigeon_protocol.session import save_session

    if not sign_available():
        return []

    from pigeon_protocol.whale_params import backstage_query_base, whale_v_for_session

    whale_v = whale_v_for_session(session=session)
    unsigned = (
        f"{PIGEON_HOST}/backstage/getConfig"
        f"?tcc_keys=checkFeVersionConf&{backstage_query_base(session=session)}"
    )
    result = sign_backstage_url(unsigned, method="GET")
    if not result.ok or not result.tokens.get("a_bogus"):
        return []

    persist_tokens_to_session(session, result)
    applied = [k for k in BACKSTAGE_SIGN_KEYS if result.tokens.get(k)]
    try:
        save_session(session)
    except Exception as exc:
        logger.debug("save_session after backstage sign refresh: %s", exc)
    return applied


def refresh_feige_bootstrap(session) -> list[str]:
    """Official post-login IM workspace warm-up (getConfig + get_message_by_init)."""
    from pigeon_protocol.feige_init import bootstrap_feige_session

    if not session.cookie_header():
        return []
    before_ws = len(session.ws_urls or [])
    before_sign = bool(session.query_tokens.get("pigeon_sign"))
    report = bootstrap_feige_session(session, persist=True)
    applied: list[str] = []
    if len(session.ws_urls or []) > before_ws:
        applied.append("ws_urls")
    if session.query_tokens.get("pigeon_sign") and not before_sign:
        applied.append("pigeon_sign")
    if report.get("getConfig", {}).get("ok"):
        applied.append("getConfig")
    if report.get("get_message_by_init", {}).get("ok"):
        applied.append("get_message_by_init")
    return applied


def ensure_ws_ready(session, *, probe: bool = False) -> dict[str, Any]:
    """Ensure WS URL + tokens exist — bootstrap + synthesize + optional connect probe."""
    from pigeon_protocol.feige_init import bootstrap_feige_session
    from pigeon_protocol.ws_url_builder import build_ws_url, ensure_ws_url, probe_ws_url_sync

    report = bootstrap_feige_session(session, persist=True)
    if not session.ws_urls:
        ensure_ws_url(session)
        built = build_ws_url(session)
        if built:
            report["synthesized_ws"] = built[:100] + "…"
    if probe and session.ws_urls:
        report["probe"] = probe_ws_url_sync(session)
    report["ws_ready"] = bool(session.ws_urls)
    try:
        from pigeon_protocol.foundation.ws_inner_bootstrap import ensure_session_inners

        report["ws_inners"] = ensure_session_inners(session, min_classes=4)
    except Exception as exc:
        report["ws_inners"] = {"ready": False, "error": str(exc)}
    return report


def auto_heal_session(session, *, refresh_csrf: bool = True, refresh_sign: bool = True) -> SessionHealth:
    """Refresh CSRF + backstage/ws tokens when possible; return health report."""
    h = check_session(session)
    if not session.cookie_header():
        return h

    if refresh_sign:
        for label, fn in (
            ("query_tokens_env", refresh_query_tokens_from_env),
            ("ws_tokens_urls", refresh_ws_tokens_from_urls),
            ("backstage_sign", refresh_backstage_sign_tokens),
            ("feige_bootstrap", refresh_feige_bootstrap),
        ):
            try:
                applied = fn(session)
                if applied:
                    h.fixes_applied.append(f"{label}:{','.join(applied)}")
            except Exception as exc:
                logger.warning("auto_heal %s failed: %s", label, exc)
                h.issues.append(f"{label} failed: {exc}")

    if not h.has_ws_url and session.cookie_header():
        try:
            ws = ensure_ws_ready(session, probe=False)
            if ws.get("ws_ready"):
                h.has_ws_url = True
                h.fixes_applied.append("ws_url_cold_start")
        except Exception as exc:
            logger.warning("ws cold start failed: %s", exc)

    try:
        from pigeon_protocol.ws_token_refresh import ensure_fresh_ws_token

        wr = ensure_fresh_ws_token(session, probe=True)
        if wr.get("ok"):
            h.has_ws_url = True
            if wr.get("steps"):
                h.fixes_applied.append(f"ws_token:{','.join(wr['steps'])}")
    except Exception as exc:
        logger.debug("auto_heal ws token: %s", exc)

    if not refresh_csrf:
        return h

    try:
        from pigeon_protocol.secsdk_csrf import refresh_relay_headers

        refresh_relay_headers(session, persist=True)
        h.fixes_applied.append("csrf_relay_headers_refreshed")
        h.has_relay_headers = True
        h.relay_age_sec = 0
    except Exception as exc:
        logger.warning("auto_heal csrf refresh failed: %s", exc)
        h.issues.append(f"csrf refresh failed: {exc}")

    return h


def ensure_order_ready(session) -> SessionHealth:
    """Called before order queries — heal if relay headers stale (>1h) or missing."""
    h = check_session(session)
    stale = h.relay_age_sec is None or h.relay_age_sec > 3600
    if stale or not h.has_relay_headers:
        return auto_heal_session(session, refresh_csrf=True, refresh_sign=True)
    return h
