"""Pure HTTP IM / pigeon backstage session renewal — extend抖店 SSO into飞鸽."""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import quote, urlparse

from pigeon_protocol.config import IM_HOST, PIGEON_HOST
from pigeon_protocol.qr_login import FEIGE_WORKSPACE, FXG_HOST, IM_HOST as _IM

logger = logging.getLogger("pigeon.session_renewal")

IM_WORKSPACE = FEIGE_WORKSPACE
SSO_HOST = "https://doudian-sso.jinritemai.com"


def _merge_cookies(session, jar: dict[str, str]) -> int:
    n = 0
    for k, v in (jar or {}).items():
        if k and v is not None and str(v) != "":
            session.cookies[str(k)] = str(v)
            n += 1
    if session.cookies.get("SHOP_ID"):
        session.shop_id = str(session.cookies["SHOP_ID"])
    if session.cookies.get("PIGEON_CID"):
        session.device_id = str(session.cookies["PIGEON_CID"])
    passport = session.cookies.get("passport_csrf_token") or ""
    csrf_sid = session.cookies.get("csrf_session_id") or ""
    if passport and csrf_sid:
        session.headers["x-secsdk-csrf-token"] = f"000100000001{passport},{csrf_sid}"
    fp = session.cookies.get("s_v_web_id") or ""
    if fp:
        session.query_tokens["verifyFp"] = fp
        session.query_tokens["fp"] = fp
    return n


def _http_client(session, *, impersonate: str = "chrome131"):
    from curl_cffi import requests as curl_requests

    from pigeon_protocol.http_client import DEFAULT_USER_AGENT
    from pigeon_protocol.foundation.chrome_hints import sec_ch_ua_headers

    ua = session.user_agent or DEFAULT_USER_AGENT
    client = curl_requests.Session(impersonate=impersonate)
    client.headers.update(
        {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            **sec_ch_ua_headers(ua),
        }
    )
    for name, value in (session.cookies or {}).items():
        if name and value is not None:
            try:
                client.cookies.set(str(name), str(value), domain=".jinritemai.com")
            except Exception:
                pass
    return client


def _get(
    client,
    session,
    url: str,
    *,
    referer: str = "",
    origin: str = "",
    allow_redirects: bool = True,
) -> Any:
    headers: dict[str, str] = {}
    if referer:
        headers["Referer"] = referer
    if origin:
        headers["Origin"] = origin
    resp = client.get(url, headers=headers, timeout=25, allow_redirects=allow_redirects)
    _merge_cookies(session, dict(client.cookies.get_dict()))
    return resp


def _fxg_session_alive(session) -> bool:
    """抖店 sessionid 是否仍有效（未完全过期）。"""
    cookies = session.cookies or {}
    if not (cookies.get("sessionid") or cookies.get("sid_tt")):
        return False
    try:
        client = _http_client(session)
        resp = _get(
            client,
            session,
            f"{FXG_HOST}/ffa/mshop/homepage/index",
            referer=f"{FXG_HOST}/",
            origin=FXG_HOST,
        )
        final = str(resp.url or "")
        if "login/common" in final or "login?" in final.lower():
            return False
        return resp.status_code == 200
    except Exception as exc:
        logger.debug("fxg session ping: %s", exc)
        return bool(cookies.get("sessionid"))


def renew_im_session_via_cdp(session, *, persist: bool = True, launch: bool = True) -> dict[str, Any]:
    """
    用已登录的 Chrome CDP 同步 IM cookie（含 PHPSESSID），无需扫码。
    抖店 session 仍有效时，Chrome profile 会自动 SSO 进飞鸽工作台。
    """
    from pigeon_protocol.feige_init import bootstrap_feige_session, probe_backstage_session
    from pigeon_protocol.session import save_session

    report: dict[str, Any] = {"steps": ["cdp_renew"], "via": "cdp_sync"}
    try:
        from pigeon_protocol.cdp_launch import ensure_cdp_ready

        if not ensure_cdp_ready(launch=launch, wait_sec=25):
            report["ok"] = False
            report["error"] = "CDP 不可用，无法自动续期"
            report["needs_cdp_onboard"] = True
            return report

        from pigeon_protocol.session_sync import CdpSessionSync

        sync = CdpSessionSync(session)
        report["cdp_sync"] = {
            "cookies": sync.sync().get("cookies"),
            "shop_id": session.shop_id,
            "has_phpsessid": bool(session.cookies.get("PHPSESSID")),
        }
        report["steps"].append("cdp_sync_ok")

        boot = bootstrap_feige_session(session, persist=False)
        report["bootstrap"] = {
            "ok": boot.get("ok"),
            "get_link_info": (boot.get("get_link_info") or {}).get("ok"),
        }
        if boot.get("steps"):
            report["steps"].append("feige_bootstrap")

        probe = probe_backstage_session(session)
        report["backstage_after"] = probe
        report["ok"] = bool(probe.get("ok"))
        if not report["ok"]:
            code = probe.get("code") or ""
            report["error"] = probe.get("error") or f"backstage not ready ({code})"
            report["needs_cdp_onboard"] = bool(probe.get("expired") or code == "10005")
    except Exception as exc:
        logger.warning("cdp renew: %s", exc)
        report["ok"] = False
        report["error"] = str(exc)[:200]
        report["needs_cdp_onboard"] = True

    if persist:
        try:
            save_session(session)
        except OSError as exc:
            report["save_error"] = str(exc)[:120]

    return report


def establish_im_session_http(session, *, persist: bool = True, cdp_fallback: bool = True) -> dict[str, Any]:
    """
    用现有抖店 Cookie 走 IM 工作台跳转链，尝试建立 pigeon backstage 会话。
    对齐浏览器打开 im.jinritemai.com 后的 cookie / SSO 传递。
    """
    from pigeon_protocol.feige_init import bootstrap_feige_session, probe_backstage_session
    from pigeon_protocol.session import save_session

    report: dict[str, Any] = {"steps": []}

    probe = probe_backstage_session(session)
    report["backstage_before"] = probe
    if probe.get("ok"):
        report["ok"] = True
        report["via"] = "already_ok"
        return report

    if not _fxg_session_alive(session):
        report["ok"] = False
        report["error"] = "抖店 session 已失效，需重新扫码登录"
        report["needs_full_login"] = True
        return report

    try:
        from pigeon_protocol.session_backup import backup_session

        backup_session(tag="before_renew")
        report["steps"].append("backup")
    except Exception:
        pass

    client = _http_client(session)
    im_next = quote(IM_WORKSPACE, safe="")
    cid = session.cookies.get("PIGEON_CID") or session.device_id or ""
    workspace = IM_WORKSPACE
    if cid and "selfId=" not in workspace:
        workspace = f"{IM_WORKSPACE}?selfId={cid}"

    hops: list[tuple[str, str, str]] = [
        (f"{FXG_HOST}/ffa/mshop/homepage/index", f"{FXG_HOST}/", FXG_HOST),
        (f"{FXG_HOST}/ffa-micro-config/afc/home", f"{FXG_HOST}/ffa/mshop/homepage/index", FXG_HOST),
        (f"{IM_HOST}/", f"{FXG_HOST}/", IM_HOST),
        (f"{IM_HOST}/pc_seller_v2/main", f"{IM_HOST}/", IM_HOST),
        (workspace, f"{IM_HOST}/pc_seller_v2/main", IM_HOST),
        (
            f"{FXG_HOST}/passport/sso/login/callback/?next={im_next}",
            f"{IM_HOST}/pc_seller_v2/main",
            FXG_HOST,
        ),
        (
            f"{SSO_HOST}/passport/sso/login/callback/?next={im_next}",
            f"{IM_HOST}/pc_seller_v2/main",
            SSO_HOST,
        ),
    ]
    for url, ref, origin in hops:
        try:
            resp = _get(client, session, url, referer=ref, origin=origin)
            path = urlparse(url).path or url[:60]
            report["steps"].append(f"hop:{path}:{resp.status_code}")
            final = str(resp.url or "")
            if "login/common" in final or "passport/web" in final:
                report.setdefault("login_redirects", []).append(final[:200])
        except Exception as exc:
            report["steps"].append(f"hop_err:{urlparse(url).path}:{exc}")

    html = ""
    try:
        from pigeon_protocol.feige_init import _fetch_workspace_html

        html = _fetch_workspace_html(session)
        if html:
            report["steps"].append("workspace_html")
            if re.search(r"login|passport|扫码", html[:8000], re.I):
                report["workspace_login_hint"] = True
    except Exception as exc:
        report["workspace_html_error"] = str(exc)[:120]

    boot = bootstrap_feige_session(session, persist=False)
    report["bootstrap"] = {
        "ok": boot.get("ok"),
        "steps": boot.get("steps"),
        "get_link_info": (boot.get("get_link_info") or {}).get("ok"),
    }
    if boot.get("steps"):
        report["steps"].append("feige_bootstrap")

    probe2 = probe_backstage_session(session)
    report["backstage_after"] = probe2
    report["ok"] = bool(probe2.get("ok"))
    if not report["ok"]:
        code = probe2.get("code") or ""
        if code in ("10005",) and cdp_fallback:
            report["steps"].append("http_10005_try_cdp")
            cdp = renew_im_session_via_cdp(session, persist=False, launch=True)
            report["cdp_renew"] = {
                "ok": cdp.get("ok"),
                "steps": cdp.get("steps"),
                "error": cdp.get("error"),
                "has_phpsessid": bool(session.cookies.get("PHPSESSID")),
            }
            if cdp.get("ok"):
                report["ok"] = True
                report["via"] = "cdp_sync"
                report["backstage_after"] = cdp.get("backstage_after") or probe_backstage_session(session)
            else:
                report["error"] = cdp.get("error") or "飞鸽 backstage 续期失败(10005)"
                report["needs_cdp_onboard"] = True
        elif code in ("10005",):
            report["error"] = "飞鸽 backstage 续期失败(10005)，需浏览器登录一次"
            report["needs_cdp_onboard"] = True
        else:
            report["error"] = probe2.get("error") or f"backstage not ready ({code})"

    if persist:
        try:
            save_session(session)
        except OSError as exc:
            report["save_error"] = str(exc)[:120]

    return report


def renew_session_if_needed(session, *, persist: bool = True) -> dict[str, Any]:
    """Backstage 过期或 listen 异常时尝试 HTTP+CDP 续期；成功则返回 ok."""
    from pigeon_protocol.feige_init import probe_backstage_session
    from pigeon_protocol.session_readiness import _BACKSTAGE_CACHE, assess_runtime_ready

    if not session_alive(session):
        return {"ok": False, "error": "not logged in", "needs_full_login": True}

    probe = probe_backstage_session(session)
    if probe.get("ok"):
        ready = assess_runtime_ready(session, probe_backstage=False)
        if ready.get("backstage_ok") and ready.get("listen_ready"):
            return {"ok": True, "skipped": True, "readiness": ready}

    report = establish_im_session_http(session, persist=persist, cdp_fallback=True)
    _BACKSTAGE_CACHE.clear()
    after = report.get("backstage_after")
    if not isinstance(after, dict) or not after:
        after = probe_backstage_session(session)
    if report.get("ok"):
        report["readiness"] = {
            "send_ready": bool(session.ws_urls),
            "listen_ready": bool(session.ws_urls),
            "backstage_ok": bool(after.get("ok")),
            "backstage_expired": bool(after.get("expired")),
            "recommended_action": "ready" if after.get("ok") else "cdp_onboard",
        }
    else:
        report["readiness"] = assess_runtime_ready(session, probe_backstage=True)
    return report


def session_alive(session) -> bool:
    """用户仍「登录」— 有抖店 session cookie（不等于 backstage 就绪）。"""
    c = session.cookies or {}
    return bool(c.get("sessionid") or c.get("sid_tt"))


def session_public_status(session) -> dict[str, Any]:
    """UI/API: 区分登录态 vs 飞鸽 backstage 就绪。"""
    from pigeon_protocol.session_readiness import assess_runtime_ready

    ready = assess_runtime_ready(session, probe_backstage=True)
    alive = session_alive(session)
    return {
        "logged_in": alive,
        "session_alive": alive,
        "backstage_ok": ready.get("backstage_ok"),
        "backstage_expired": ready.get("backstage_expired"),
        "send_ready": ready.get("send_ready"),
        "listen_ready": ready.get("listen_ready"),
        "recommended_action": ready.get("recommended_action"),
        "needs_renew": alive and not ready.get("backstage_ok"),
        "needs_full_login": not alive,
        "blockers": ready.get("blockers"),
        "shop_id": session.cookies.get("SHOP_ID") or session.shop_id,
        "shop_name": f"店铺 {session.shop_id or session.cookies.get('SHOP_ID', '')}".strip()
        if (session.shop_id or session.cookies.get("SHOP_ID"))
        else "飞鸽客服",
    }
