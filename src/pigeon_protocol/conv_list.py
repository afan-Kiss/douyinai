"""Conversation list via xundan_chat_list — uses foundation relay layer."""
from __future__ import annotations

import contextvars
import json
import logging
import os
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

_conv_light_ctx: contextvars.ContextVar[bool] = contextvars.ContextVar("conv_light", default=False)

logger = logging.getLogger("pigeon.conv_list")

ROOT = None  # lazy


def _root():
    from pathlib import Path

    global ROOT
    if ROOT is None:
        ROOT = Path(__file__).resolve().parents[2]
    return ROOT


def _env_paths():
    from pigeon_protocol.account_context import analysis_env_file, bundle_file

    return analysis_env_file(), bundle_file("bdms_browser_env.json")


def _load_conv_template() -> dict[str, Any]:
    for path in _env_paths():
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            tpl = data.get("convListTemplate")
            if isinstance(tpl, dict):
                return tpl
        except (OSError, json.JSONDecodeError):
            continue
    return {}


def _resolve_whale_params(session, *, light: bool | None = None) -> dict[str, str]:
    """Live gfdatav1.ver → E.AM() whale `_v` + verifyFp (captcha fp)."""
    from pigeon_protocol.foundation.bdms_tokens import verify_fp_from_cookies

    use_light = _conv_light_ctx.get() if light is None else light
    cached = {
        "whale_v": str((session.query_tokens or {}).get("whale_v") or ""),
        "im_pc_version": str((session.query_tokens or {}).get("im_pc_version") or ""),
    }
    vers: dict[str, str] = {}
    if use_light:
        whale_v = cached["whale_v"] or "1.0.1.7626"
        im_pc = cached["im_pc_version"]
    else:
        from pigeon_protocol.whale_version import resolve_whale_versions

        vers = resolve_whale_versions(session=session)
        whale_v = vers.get("whale_v") or cached["whale_v"] or "1.0.1.7626"
        im_pc = vers.get("im_pc_version") or cached["im_pc_version"] or ""

    if session is not None:
        session.query_tokens["whale_v"] = whale_v
        if im_pc:
            session.query_tokens["im_pc_version"] = im_pc
        if vers.get("gfdata_ver"):
            session.query_tokens["gfdata_ver"] = vers["gfdata_ver"]

    fp = verify_fp_from_cookies(session.cookies if session else {})
    if not fp and session is not None:
        fp = str(session.query_tokens.get("verifyFp") or session.query_tokens.get("fp") or "")

    out = {"_v": whale_v}
    if fp:
        out["verifyFp"] = fp
        out["fp"] = fp
    if im_pc:
        out["im_pc_version"] = im_pc
    return out


def _unsigned_url(*, queue_key: str = "no_order", page_size: int = 20, session=None, light: bool | None = None) -> str:
    from pigeon_protocol.config import PIGEON_HOST, XUNDAN_CHAT_LIST_PATH

    tpl = _load_conv_template()
    whale = _resolve_whale_params(session, light=light)
    params = {
        "biz_type": "4",
        "PIGEON_BIZ_TYPE": "2",
        "_pms": "1",
        "device_platform": "web",
        "FUSION": "true",
        "queue_key": queue_key,
        "security_uid_list": str(tpl.get("security_uid_list") or ""),
        "page_size": str(page_size),
    }
    if whale.get("_v"):
        params["_v"] = whale["_v"]
    elif tpl.get("_v"):
        params["_v"] = str(tpl["_v"])
    if whale.get("verifyFp"):
        params["verifyFp"] = whale["verifyFp"]
        params["fp"] = whale["fp"]
    uid_list = tpl.get("uid_list") or tpl.get("security_uid_list")
    if uid_list:
        params["uid_list"] = str(uid_list)
    return f"{PIGEON_HOST}{XUNDAN_CHAT_LIST_PATH}?{urlencode(params)}"


def _relay_to_legacy(relay_resp, *, queue_key: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": relay_resp.ok,
        "status": relay_resp.status,
        "url": relay_resp.url,
        "data": relay_resp.data,
        "via": relay_resp.via,
    }
    if queue_key:
        out["via"] = f"conv_list/xundan/{queue_key}"
    if relay_resp.error:
        out["error"] = relay_resp.error
    return out


def warm_conv_session(session) -> dict[str, Any]:
    """HTTP warm-up before xundan relay — link_info, whale versions, CSRF, msToken."""
    report: dict[str, Any] = {"steps": []}
    if not session.cookie_header():
        report["skipped"] = "no cookies"
        return report

    from pigeon_protocol.foundation.bdms_tokens import backstage_query_tokens
    from pigeon_protocol.feige_init import _fetch_get_link_info
    from pigeon_protocol.secsdk_csrf import refresh_relay_headers
    from pigeon_protocol.session import save_session
    from pigeon_protocol.whale_version import resolve_whale_versions

    tokens = backstage_query_tokens(session)
    if tokens.get("msToken") and not session.query_tokens.get("msToken"):
        session.query_tokens["msToken"] = tokens["msToken"]
        report["steps"].append("msToken:env")

    vers = resolve_whale_versions(session=session)
    if vers.get("whale_v"):
        session.query_tokens["whale_v"] = vers["whale_v"]
        report["steps"].append(f"whale_v:{vers['whale_v']}")
    if vers.get("im_pc_version"):
        session.query_tokens["im_pc_version"] = vers["im_pc_version"]
        report["steps"].append("im_pc_version")

    try:
        link = _fetch_get_link_info(session)
        report["get_link_info"] = {"ok": link.get("ok"), "code": link.get("code")}
        if link.get("ok"):
            report["steps"].append("get_link_info")
            from pigeon_protocol.session import save_session

            try:
                save_session(session)
            except Exception:
                pass
    except Exception as exc:
        logger.debug("warm get_link_info: %s", exc)
        report["get_link_info"] = {"ok": False, "error": str(exc)}

    try:
        refresh_relay_headers(session, persist=False)
        report["steps"].append("csrf")
    except Exception as exc:
        logger.debug("warm csrf: %s", exc)

    try:
        from pigeon_protocol.fingerprint_sync import sync_fingerprint_from_session

        fp = sync_fingerprint_from_session(session)
        if fp.get("changed"):
            report["steps"].append(f"fp_sync:{','.join(fp['changed'])}")
    except Exception as exc:
        logger.debug("warm fp sync: %s", exc)

    try:
        save_session(session)
    except Exception:
        pass
    report["ok"] = bool(report["steps"])
    return report


def _relay_get_light(session, unsigned_url: str, *, via: str, timeout_sec: float, queue_key: str = "") -> dict[str, Any]:
    from pigeon_protocol.foundation.bdms_sign import persist_tokens_to_session, sign_backstage_url
    from pigeon_protocol.foundation.bdms_tokens import persist_ms_token_from_response
    from pigeon_protocol.foundation.types import RelayResponse
    from pigeon_protocol.http_client import DEFAULT_CURL_IMPERSONATE
    from pigeon_protocol.http_transport import curl_cffi_available, request_json
    from pigeon_protocol.order_relay_headers import build_order_relay_headers
    from pigeon_protocol.whale_urls import is_whale_backstage_url

    if not curl_cffi_available():
        return {"ok": False, "error": "relay unavailable", "via": via}

    def _once(*, force_csrf: bool) -> dict[str, Any]:
        sign = sign_backstage_url(unsigned_url, method="GET", prefer_python=not force_csrf)
        if not sign.ok:
            return {"ok": False, "error": sign.error or "sign failed", "via": via}

        persist_tokens_to_session(session, sign)
        hdr = build_order_relay_headers(session, force_refresh=force_csrf, for_method="GET")
        im_ver = str(session.query_tokens.get("im_pc_version") or "")
        if im_ver:
            hdr["X-IM-PC-Version"] = im_ver

        try:
            raw = request_json(
                "GET",
                sign.signed_url,
                headers=hdr,
                transport="curl_cffi",
                impersonate=DEFAULT_CURL_IMPERSONATE,
                timeout=timeout_sec,
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc), "via": via, "timeout": True}

        persist_ms_token_from_response(session, raw.get("headers") if isinstance(raw.get("headers"), dict) else None)
        relay = RelayResponse(
            ok=bool(raw.get("ok")),
            status=int(raw.get("status") or 0),
            data=raw.get("data") if isinstance(raw.get("data"), dict) else {"payload": raw.get("data")},
            via=f"{via}/csrf_retry" if force_csrf else via,
            url=str(raw.get("url") or sign.signed_url),
            headers={k: str(v) for k, v in (raw.get("headers") or {}).items()},
            sign=sign,
        )
        if isinstance(raw.get("data"), dict):
            relay.data = raw["data"]
        if relay.api_code():
            relay.ok = relay.api_ok()
        else:
            relay.ok = bool(raw.get("ok")) and relay.status == 200
        return _relay_to_legacy(relay, queue_key=queue_key)

    last_raw = _once(force_csrf=False)
    data = last_raw.get("data") if isinstance(last_raw.get("data"), dict) else {}
    if (
        not last_raw.get("ok")
        and str(data.get("code") or data.get("st") or "") == "11001"
        and is_whale_backstage_url(unsigned_url)
        and not last_raw.get("timeout")
    ):
        retry = _once(force_csrf=True)
        if retry.get("ok") or parse_conversation_items(retry):
            return retry
    return last_raw


def list_conversations_relay(
    session,
    *,
    page: int = 0,
    size: int = 30,
    queue_keys: tuple[str, ...] | None = None,
    skip_warm: bool = False,
    request_timeout_sec: float | None = None,
    snapshot_only: bool = False,
) -> dict[str, Any]:
    from pigeon_protocol.config import XUNDAN_QUEUE_KEYS
    from pigeon_protocol.foundation.relay_client import BackstageRelayClient
    from pigeon_protocol.pure_config import pure_only_mode

    light_token = _conv_light_ctx.set(True) if skip_warm else None
    try:
        if not skip_warm:
            warm_conv_session(session)

        keys = queue_keys or XUNDAN_QUEUE_KEYS
        merged_items: list[dict[str, Any]] = []
        seen_uids: set[str] = set()
        last_raw: dict[str, Any] = {}

        try_snapshot = pure_only_mode() or os.getenv("PIGEON_CONV_SNAPSHOT", "").strip().lower() in ("1", "true", "yes")
        if try_snapshot or skip_warm:
            try:
                from pigeon_protocol.conv_sign_snapshot import fetch_xundan_via_snapshot, has_fresh_snapshot

                if has_fresh_snapshot():
                    snap_attempts: list[dict[str, Any]] = []
                    for queue_key in keys:
                        snap = fetch_xundan_via_snapshot(session, queue_key=queue_key, page_size=size)
                        snap_attempts.append(
                            {
                                "queue_key": queue_key,
                                "ok": bool(snap and snap.get("ok")),
                                "code": (snap or {}).get("api_code"),
                                "items": len((snap or {}).get("items") or []),
                            }
                        )
                        if not snap or not snap.get("items"):
                            continue
                        for item in snap["items"]:
                            uid = item.get("security_user_id") or ""
                            if uid and uid in seen_uids:
                                continue
                            if uid:
                                seen_uids.add(uid)
                            item["queue_key"] = queue_key
                            merged_items.append(item)
                    if merged_items:
                        return {
                            "ok": True,
                            "items": merged_items,
                            "data": {"code": 0, "data": {"user_list": merged_items}},
                            "via": "conv_list/xundan_snapshot",
                            "queues_scanned": list(keys),
                            "snapshot_attempts": snap_attempts,
                        }
                    last_raw["snapshot_attempts"] = snap_attempts
            except Exception as exc:
                logger.debug("conv snapshot path skipped: %s", exc)

        if snapshot_only:
            return last_raw or {"ok": False, "error": "snapshot unavailable", "items": []}

        client = BackstageRelayClient(session)
        if not client.available():
            return {"ok": False, "error": "relay unavailable"}

        for queue_key in keys:
            unsigned = _unsigned_url(queue_key=queue_key, page_size=size, session=session, light=skip_warm)
            if request_timeout_sec is not None:
                last_raw = _relay_get_light(
                    session,
                    unsigned,
                    via=f"conv_list/xundan/{queue_key}",
                    timeout_sec=request_timeout_sec,
                    queue_key=queue_key,
                )
                api_ok = bool(last_raw.get("ok"))
            else:
                relay = client.get(unsigned, via=f"conv_list/xundan/{queue_key}")
                last_raw = _relay_to_legacy(relay, queue_key=queue_key)
                api_ok = relay.api_ok()

            if not api_ok:
                if last_raw.get("timeout"):
                    break
                continue
            for item in parse_conversation_items(last_raw):
                uid = item.get("security_user_id") or ""
                if uid and uid in seen_uids:
                    continue
                if uid:
                    seen_uids.add(uid)
                item["queue_key"] = queue_key
                merged_items.append(item)

        if merged_items:
            return {
                "ok": True,
                "items": merged_items,
                "data": {"code": 0, "data": {"user_list": merged_items}},
                "via": last_raw.get("via", "conv_list/xundan"),
                "queues_scanned": list(keys),
            }

        if last_raw:
            data = last_raw.get("data") if isinstance(last_raw.get("data"), dict) else {}
            api_code = str(data.get("code") or data.get("st") or "")
            if api_code == "11001" and not skip_warm:
                msg = str(data.get("msg") or "")
                try:
                    msg = msg.encode("latin-1").decode("utf-8")
                except (UnicodeDecodeError, UnicodeEncodeError):
                    pass

                if os.getenv("PIGEON_NO_CDP", "").strip().lower() not in ("1", "true", "yes"):
                    try:
                        from pigeon_protocol.conv_xundan_curl_relay import fetch_xundan_via_curl_relay

                        curl_items: list[dict[str, Any]] = []
                        curl_attempts: list[dict[str, Any]] = []
                        for qk in keys:
                            curl = fetch_xundan_via_curl_relay(session, queue_key=qk, page_size=size)
                            curl_attempts.append(
                                {
                                    "queue_key": qk,
                                    "ok": curl.get("ok"),
                                    "code": curl.get("api_code"),
                                    "items": len(curl.get("items") or []),
                                }
                            )
                            if curl.get("items"):
                                for it in curl["items"]:
                                    it["queue_key"] = qk
                                curl_items.extend(curl["items"])
                        if curl_items:
                            return {
                                "ok": True,
                                "items": curl_items,
                                "via": "conv_list/xundan_curl_relay",
                                "xundan_error": msg or "whale_block:11001",
                                "curl_relay_attempts": curl_attempts,
                            }
                        last_raw["curl_relay_attempts"] = curl_attempts
                    except Exception as exc:
                        logger.warning("conv_list curl relay failed: %s", exc)
                        last_raw["curl_relay_error"] = str(exc)

                    try:
                        from pigeon_protocol.conv_list_cdp import list_conversations_cdp

                        wait_login = float(os.getenv("PIGEON_CDP_WAIT_LOGIN", "45"))
                        cdp = list_conversations_cdp(
                            session,
                            size=size,
                            queue_keys=keys,
                            wait_login_sec=wait_login,
                        )
                        if cdp.get("ok") and cdp.get("items"):
                            cdp["xundan_error"] = msg or "whale_block:11001"
                            return cdp
                        last_raw["cdp_attempt"] = {
                            "ok": cdp.get("ok"),
                            "error": cdp.get("error"),
                            "attempts": cdp.get("attempts"),
                        }
                    except Exception as exc:
                        logger.warning("conv_list CDP failed: %s", exc)
                        last_raw["cdp_attempt"] = {"error": str(exc)}

                from pigeon_protocol.conv_list_fallback import list_conversations_fallback

                fallback = list_conversations_fallback(session, limit=size)
                if fallback.get("ok") and fallback.get("items"):
                    fallback["error"] = msg or "xundan whale_block:11001"
                    fallback["xundan_error"] = msg or "whale_block:11001"
                    return fallback
                last_raw["ok"] = False
                last_raw["api_code"] = 11001
                last_raw["error"] = msg or "whale_block:11001"
                last_raw["whale_v"] = (session.query_tokens or {}).get("whale_v")
            elif not client.available():
                last_raw["error"] = last_raw.get("error") or "relay unavailable (need curl_cffi + bdms sign)"

        if not merged_items and not skip_warm:
            try:
                from pigeon_protocol.conv_list_fallback import list_conversations_fallback

                fallback = list_conversations_fallback(session, limit=size)
                if fallback.get("ok") and fallback.get("items"):
                    if isinstance(last_raw, dict):
                        fallback["xundan_empty"] = True
                        fallback["xundan_via"] = last_raw.get("via")
                    return fallback
            except Exception as exc:
                logger.debug("conv_list empty xundan fallback: %s", exc)

        return last_raw or {"ok": False, "error": "xundan_chat_list failed for all queue keys"}
    finally:
        if light_token is not None:
            _conv_light_ctx.reset(light_token)


def _format_ts_ms(ms: Any) -> tuple[int, str]:
    if not ms:
        return 0, ""
    try:
        ts = int(str(ms)[:13])
        if ts < 1_000_000_000_000:
            ts *= 1000
        return ts, datetime.fromtimestamp(ts / 1000).strftime("%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return 0, ""


def _parse_unread(it: dict[str, Any], msg_body: dict[str, Any], ext: dict[str, Any]) -> int:
    """Prefer official server unread fields; heuristics only as last resort."""
    for key in ("unread_count", "unread_num", "un_read_count", "unread_msg_count", "unread"):
        val = it.get(key)
        if val is not None and str(val).isdigit():
            return int(val)
    inner_ext = it.get("ext") if isinstance(it.get("ext"), dict) else {}
    for key in ("unread_count", "unread_num", "unread"):
        val = inner_ext.get(key)
        if val is not None and str(val).isdigit():
            return int(val)
    for key in ("houston_unread_count", "unread_msg_num"):
        val = ext.get(key)
        if val is not None and str(val).isdigit():
            return int(val)
    shop_id = str(ext.get("shop_id") or "")
    sender = str(msg_body.get("security_sender") or ext.get("o_sender") or "")
    role = str(ext.get("s:sender_biz_role") or ext.get("sender_role") or "")
    if role == "Shop" or (shop_id and sender == shop_id):
        return 0
    if ext.get("houston_unread_type") in (1, "1"):
        return 1
    if sender.startswith("AQ"):
        return 1
    return 0


def parse_conversation_items(raw: dict[str, Any]) -> list[dict[str, Any]]:
    if raw.get("items") and isinstance(raw["items"], list):
        return raw["items"]

    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    inner = data.get("data") if isinstance(data.get("data"), (list, dict)) else data
    items: list[Any] = []

    if isinstance(inner, list):
        items = inner
    elif isinstance(inner, dict):
        items = (
            inner.get("user_list")
            or inner.get("conversation_list")
            or inner.get("list")
            or inner.get("items")
            or []
        )

    out: list[dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue

        uid = str(
            it.get("security_uid")
            or it.get("security_user_id")
            or (it.get("user_info") or {}).get("security_user_id")
            or ""
        )
        last_msg = it.get("last_history_msg") if isinstance(it.get("last_history_msg"), dict) else {}
        msg_body = last_msg.get("message_body") if isinstance(last_msg.get("message_body"), dict) else {}
        ext = msg_body.get("ext") if isinstance(msg_body.get("ext"), dict) else {}
        if not uid:
            uid = str(ext.get("security_pigeon_uid") or ext.get("s:security_invisible") or "")

        name = str(
            it.get("user_name")
            or it.get("nick_name")
            or (it.get("user_info") or {}).get("user_name")
            or it.get("title")
            or (it.get("ext") or {}).get("user_name")
            or ext.get("cname")
            or ext.get("uname")
            or ""
        )
        preview = str(
            it.get("last_message")
            or it.get("preview")
            or it.get("content")
            or msg_body.get("content")
            or ext.get("content")
            or ext.get("cs_special_content")
            or ""
        )
        talk_id = str(
            it.get("talk_id")
            or last_msg.get("server_message_id")
            or ext.get("talk_id")
            or ""
        )
        last_ts, last_time = _format_ts_ms(msg_body.get("create_time") or last_msg.get("create_time"))
        unread = _parse_unread(it, msg_body, ext)
        out.append(
            {
                "security_user_id": uid,
                "name": name,
                "preview": preview[:120],
                "talk_id": talk_id,
                "queue_key": it.get("_queue_key") or "",
                "last_time": last_time,
                "last_time_ms": last_ts,
                "unread_count": unread,
            }
        )
    out.sort(key=lambda x: int(x.get("last_time_ms") or 0), reverse=True)
    return [x for x in out if x.get("security_user_id") or x.get("name") or x.get("preview")]
