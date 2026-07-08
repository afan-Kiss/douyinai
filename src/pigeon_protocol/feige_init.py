"""Feige workspace bootstrap — pure HTTP, mirrors official post-login flow."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from pigeon_protocol.capture_loader import load_capture
from pigeon_protocol.config import GET_LINK_INFO_PATH, IM_HOST, LIVE_CAPTURES, PIGEON_HOST
from pigeon_protocol.pure_config import BUNDLE_INIT_BODY, bundle_first_assets
from pigeon_protocol.ws_url_builder import (
    PIGEON_SIGN_RE,
    apply_ws_url,
    build_ws_url,
    ensure_ws_url,
    extract_ws_tokens_from_text,
    scan_ws_urls,
)

logger = logging.getLogger("pigeon.feige_init")

INIT_TEMPLATE = LIVE_CAPTURES / "from_har" / "har_00067_http_body.json"
BUNDLE_INIT = BUNDLE_INIT_BODY
GET_MESSAGE_BY_INIT = "/pigeon_im/v1/message/get_message_by_init"
_BACKSTAGE_LOGIN_EXPIRED = frozenset({"10005"})


def probe_backstage_session(session) -> dict[str, Any]:
    """Lightweight pigeon.jinritemai.com backstage auth probe (get_link_info)."""
    link = _fetch_get_link_info(session)
    code = str(link.get("code") or "")
    out: dict[str, Any] = {
        "ok": bool(link.get("ok")),
        "code": code,
        "via": link.get("via") or "get_link_info",
        "sign_via": link.get("sign_via"),
        "error": link.get("error"),
        "expired": code in _BACKSTAGE_LOGIN_EXPIRED,
    }
    for key in ("frontier_msgServiceId", "frontier_temaiServiceId", "token", "pigeon_sign"):
        if link.get(key) is not None:
            out[key] = link.get(key)
    return out


def _latest_ws_token(session) -> str:
    for url in reversed(session.ws_urls or []):
        if "ws.fxg.jinritemai.com" not in url:
            continue
        tok = (parse_qs(urlparse(url).query).get("token") or [""])[0]
        if tok:
            return tok
    return str(session.query_tokens.get("token") or "")


def _patch_init_body(body: bytes, session) -> bytes:
    from pigeon_protocol.init_body import patch_init_body

    return patch_init_body(body, session)


def _load_init_bytes() -> bytes:
    if bundle_first_assets() and BUNDLE_INIT.is_file():
        return BUNDLE_INIT.read_bytes()
    if INIT_TEMPLATE.is_file():
        event = load_capture(INIT_TEMPLATE)
        raw = str(event.get("post_data") or "")
        return raw.encode("latin-1")
    if BUNDLE_INIT.is_file():
        return BUNDLE_INIT.read_bytes()
    return b""


def _scan_ws_urls(text: str) -> list[str]:
    return scan_ws_urls(text)


def _apply_ws_urls(session, urls: list[str]) -> list[str]:
    applied: list[str] = []
    for url in urls:
        if apply_ws_url(session, url):
            applied.append("ws_url")
    return applied


def _fetch_workspace_html(session) -> str:
    from pigeon_protocol.foundation.chrome_hints import im_workspace_referer, sec_ch_ua_headers
    from pigeon_protocol.http_client import DEFAULT_CURL_IMPERSONATE, DEFAULT_USER_AGENT
    from pigeon_protocol.http_transport import curl_cffi_available

    if not curl_cffi_available():
        return ""
    from curl_cffi import requests as curl_requests

    ua = session.user_agent or DEFAULT_USER_AGENT
    resp = curl_requests.get(
        f"{IM_HOST}/pc_seller_v2/main/workspace",
        headers={
            "User-Agent": ua,
            "Referer": im_workspace_referer(session),
            "Accept": "text/html,application/xhtml+xml",
            "Cookie": session.cookie_header(),
            **sec_ch_ua_headers(ua),
        },
        impersonate=DEFAULT_CURL_IMPERSONATE,
        timeout=20,
        allow_redirects=True,
    )
    return resp.text if resp.status_code == 200 else ""


def _fetch_get_link_info(session) -> dict[str, Any]:
    """Official IM bootstrap — fresh WS token + pigeon_sign (no CDP)."""
    from pigeon_protocol.foundation.bdms_tokens import append_backstage_query_tokens
    from pigeon_protocol.foundation.relay_client import BackstageRelayClient

    client = BackstageRelayClient(session)
    if not client.available():
        return {"ok": False, "skipped": "relay unavailable"}

    whale_v = str(session.query_tokens.get("whale_v") or "")
    if not whale_v:
        whale_v = _sync_whale_versions(session, html="").get("whale_v") or "1.0.1.7626"
    unsigned = append_backstage_query_tokens(
        (
            f"{PIGEON_HOST}{GET_LINK_INFO_PATH}"
            f"?biz_type=4&PIGEON_BIZ_TYPE=2&_pms=1&device_platform=web&FUSION=true&_v={whale_v}"
        ),
        session,
    )
    relay = client.get(unsigned, via="feige_init/get_link_info")
    data = relay.data if isinstance(relay.data, dict) else {}
    inner = data.get("data") if isinstance(data.get("data"), dict) else {}
    out: dict[str, Any] = {
        "ok": relay.api_ok(),
        "code": relay.api_code(),
        "via": relay.via,
        "sign_via": relay.sign.via if relay.sign else None,
    }
    if not relay.api_ok():
        out["error"] = str(data.get("msg") or relay.error or "get_link_info failed")
        return out

    token = str(inner.get("token") or "")
    sign = str(inner.get("pigeon_sign") or "")
    fc = inner.get("frontierConfig") if isinstance(inner.get("frontierConfig"), dict) else {}
    if token:
        session.query_tokens["token"] = token
        out["token"] = "refreshed"
    if sign:
        session.query_tokens["pigeon_sign"] = sign
        out["pigeon_sign"] = "refreshed"
    if fc.get("appKey"):
        # frontierConfig.appKey is for other frontier hosts — ws.fxg uses FEIGE_WS_ACCESS_KEY constant
        session.query_tokens["frontier_app_key"] = str(fc["appKey"])
        out["frontier_app_key"] = "refreshed"
    if fc.get("fpId") is not None:
        session.query_tokens["fpid"] = str(fc["fpId"])
    for key in ("msgServiceId", "temaiServiceId"):
        if fc.get(key) is not None:
            session.query_tokens[f"frontier_{key}"] = fc[key]
            out[f"frontier_{key}"] = fc[key]

    from pigeon_protocol.ws_url_builder import build_ws_url, promote_ws_url

    built = build_ws_url(session, token=token, pigeon_sign=sign)
    if built:
        promote_ws_url(session, built)
        out["ws_url"] = built[:120] + "…"
    return out


def _post_get_message_by_init(session) -> dict[str, Any]:
    from pigeon_protocol.foundation.chrome_hints import pigeon_im_headers
    from pigeon_protocol.http_client import DEFAULT_CURL_IMPERSONATE
    from pigeon_protocol.http_transport import curl_cffi_available
    from pigeon_protocol.pigeon_im import build_pigeon_im_url

    if not curl_cffi_available():
        return {"ok": False, "skipped": "curl_cffi required"}

    body = _patch_init_body(_load_init_bytes(), session)
    if not body:
        return {"ok": False, "skipped": "empty init body"}

    from curl_cffi import requests as curl_requests

    url = build_pigeon_im_url(session, GET_MESSAGE_BY_INIT, sign=True)
    from pigeon_protocol.init_body import validate_init_body

    body_check = validate_init_body(body)
    if not body_check.get("ok"):
        logger.warning("init body invalid before POST: %s", body_check.get("error"))

    def _do_post(post_url: str) -> Any:
        return curl_requests.post(
            post_url,
            data=body,
            headers=pigeon_im_headers(session),
            impersonate=DEFAULT_CURL_IMPERSONATE,
            timeout=20,
        )

    resp = _do_post(url)
    if resp.status_code == 200 and resp.content and b"lz4:" in resp.content[:120]:
        url2 = build_pigeon_im_url(session, GET_MESSAGE_BY_INIT, sign=False)
        resp2 = _do_post(url2)
        if len(resp2.content or b"") > len(resp.content or b""):
            resp = resp2
            out_sign_mode = "unsigned_retry"
        else:
            out_sign_mode = "signed_lz4_error"
    else:
        out_sign_mode = "signed"
    out: dict[str, Any] = {
        "ok": resp.status_code == 200,
        "status": resp.status_code,
        "url": str(resp.url)[:200],
        "body_len": len(resp.content or b""),
        "init_sign_mode": out_sign_mode,
        "body_valid": body_check.get("ok"),
    }
    if resp.content and b"lz4:" in resp.content[:160]:
        out["ok"] = False
        out["error"] = resp.content.decode("utf-8", errors="ignore")[:200]
    elif resp.content and len(resp.content) > 500:
        out["init_ok"] = True
        try:
            from pigeon_protocol.pure_config import BUNDLE_INIT_BODY, STANDALONE_BUNDLE

            BUNDLE_INIT_BODY.parent.mkdir(parents=True, exist_ok=True)
            BUNDLE_INIT_BODY.write_bytes(body)
            out["init_body_cached"] = str(BUNDLE_INIT_BODY)
            STANDALONE_BUNDLE.mkdir(parents=True, exist_ok=True)
            resp_cache = STANDALONE_BUNDLE / "get_message_by_init_response.bin"
            resp_cache.write_bytes(resp.content)
            out["init_response_cached"] = str(resp_cache)
        except OSError as exc:
            logger.debug("init body cache skipped: %s", exc)
    if resp.content:
        text = resp.content.decode("latin-1", errors="ignore")
        tok_from_init = extract_ws_tokens_from_text(text)
        for k, v in tok_from_init.items():
            if v and session.query_tokens.get(k) != v:
                session.query_tokens[k] = v
                out[f"init_{k}"] = "refreshed"
        for sign in PIGEON_SIGN_RE.findall(text):
            if len(sign) > 80 and session.query_tokens.get("pigeon_sign") != sign:
                session.query_tokens["pigeon_sign"] = sign
                out["pigeon_sign"] = "refreshed"
                break
        ws_found = _scan_ws_urls(text)
        if ws_found:
            out["ws_from_init"] = _apply_ws_urls(session, ws_found)
        try:
            from pigeon_protocol.foundation.init_inner_mapper import (
                export_init_mapping,
                ingest_init_response,
            )

            ingest_report = ingest_init_response(session, resp.content, source="get_message_by_init")
            ingested = ingest_report.get("stored_keys") or []
            try:
                from pigeon_protocol.foundation.init_edbx_seeds import persist_init_edbx_seeds

                edbx = persist_init_edbx_seeds(session, resp.content, source="get_message_by_init")
                if edbx.get("ok"):
                    out["edbx_init_seeds"] = {
                        "trailer_hex": edbx.get("trailer_hex"),
                        "via": edbx.get("via"),
                        "seed_count": len(edbx.get("seeds") or []),
                    }
            except Exception as exc:
                logger.debug("init edbx seeds: %s", exc)
            if ingested:
                out["inners_from_init"] = ingested
                out["init_inner_mapping"] = {
                    "init_sync": ingest_report.get("init_sync"),
                    "send_from_init": ingest_report.get("send_from_init"),
                    "parsed": {
                        k: ingest_report["parsed"].get(k)
                        for k in ("send_class_count", "init_sync_count", "ws_send_frames", "body_len")
                        if ingest_report.get("parsed")
                    },
                }
                out["init_ok"] = True
                try:
                    export_init_mapping(session, resp.content)
                    out["init_mapping_export"] = "standalone_bundle/ws_inner_from_init.json"
                except OSError as exc:
                    logger.debug("init mapping export skipped: %s", exc)
        except Exception as exc:
            logger.debug("init inner scan skipped: %s", exc)
    return out


def _sync_whale_versions(session, html: str = "") -> dict[str, str]:
    from pigeon_protocol.whale_version import resolve_whale_versions

    vers = resolve_whale_versions(html=html, session=session)
    if vers.get("whale_v"):
        session.query_tokens["whale_v"] = str(vers["whale_v"])
    if vers.get("im_pc_version"):
        session.query_tokens["im_pc_version"] = str(vers["im_pc_version"])
    if vers.get("gfdata_ver"):
        session.query_tokens["gfdata_ver"] = str(vers["gfdata_ver"])
    return vers


def _warm_backstage_config(session, *, html: str = "") -> dict[str, Any]:
    """Official workspace boot: lightweight signed getConfig GET."""
    from pigeon_protocol.foundation.relay_client import BackstageRelayClient

    client = BackstageRelayClient(session)
    if not client.available():
        return {"ok": False, "skipped": "relay unavailable"}
    vers = _sync_whale_versions(session, html)
    whale_v = vers.get("whale_v") or "1.0.1.7626"
    unsigned = (
        f"{PIGEON_HOST}/backstage/getConfig"
        f"?tcc_keys=checkFeVersionConf&biz_type=4&PIGEON_BIZ_TYPE=2"
        f"&_pms=1&device_platform=web&FUSION=true&_v={whale_v}"
    )
    relay = client.get(unsigned, via="feige_init/getConfig")
    return {"ok": relay.api_ok(), "via": relay.via, "code": relay.api_code(), "whale_v": whale_v}


def bootstrap_feige_session(session, *, persist: bool = True) -> dict[str, Any]:
    """
    Post-login / session-doctor: warm backstage + IM init + scrape WS URLs.
    No CDP — aligns with official im.jinritemai.com workspace open sequence.
    """
    from pigeon_protocol.session import save_session

    report: dict[str, Any] = {"steps": []}

    from pigeon_protocol.foundation.pigeon_sign_service import ensure_pigeon_sign

    sign_boot = ensure_pigeon_sign(session)
    if sign_boot.get("ok"):
        report["steps"].append(f"pigeon_sign:{sign_boot.get('via', 'ok')}")
    report["pigeon_sign"] = sign_boot

    html = _fetch_workspace_html(session)
    if html:
        ws_html = _scan_ws_urls(html)
        if ws_html:
            report["steps"].append(f"workspace_html_ws:{len(ws_html)}")
            _apply_ws_urls(session, ws_html)
        tok = extract_ws_tokens_from_text(html)
        for k, v in tok.items():
            if v:
                session.query_tokens[k] = v
        if tok:
            report["steps"].append(f"html_tokens:{','.join(tok)}")

    ws_build = ensure_ws_url(session, html="")
    if ws_build.get("sources"):
        report["steps"].append(f"ws_url:{','.join(ws_build['sources'])}")
    report["ws_url_build"] = ws_build

    whale_vers = _sync_whale_versions(session, html)
    report["whale_versions"] = whale_vers

    cfg = _warm_backstage_config(session, html=html)
    report["getConfig"] = cfg
    if cfg.get("ok"):
        report["steps"].append("getConfig:ok")

    link = _fetch_get_link_info(session)
    report["get_link_info"] = link
    if link.get("token"):
        report["steps"].append("get_link_info:token")

    init = _post_get_message_by_init(session)
    report["get_message_by_init"] = init
    if init.get("init_ok") or init.get("inners_from_init") or init.get("body_len", 0) > 500:
        report["steps"].append("get_message_by_init:ok")
    elif init.get("ok"):
        report["steps"].append("get_message_by_init:http_ok")

    from pigeon_protocol.ws_url_builder import canonicalize_ws_session

    ws_canon = canonicalize_ws_session(session)
    report["ws_canonical"] = ws_canon
    if ws_canon.get("sources"):
        report["steps"].append(f"ws_canon:{','.join(ws_canon['sources'])}")

    if session.cookies.get("PIGEON_CID") and not session.device_id:
        session.device_id = session.cookies["PIGEON_CID"]
    if session.cookies.get("SHOP_ID") and not session.shop_id:
        session.shop_id = session.cookies["SHOP_ID"]

    report["ok"] = bool(
        session.ws_urls
        or build_ws_url(session)
        or session.query_tokens.get("pigeon_sign")
        or link.get("ok")
        or init.get("ok")
        or cfg.get("ok")
    )
    report["ws_urls"] = len(session.ws_urls)
    report["has_pigeon_sign"] = bool(session.query_tokens.get("pigeon_sign"))

    if persist:
        try:
            save_session(session)
        except Exception as exc:
            logger.debug("save_session bootstrap: %s", exc)
    return report
