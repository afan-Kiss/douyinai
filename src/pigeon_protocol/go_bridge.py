"""JSON stdin/stdout bridge for Go desktop — pure-protocol worker RPC."""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from dataclasses import asdict
from typing import Any

logger = logging.getLogger("pigeon.go_bridge")


def _ensure_stdio_utf8() -> None:
    """Windows 默认 stdout 常为 GBK，Go 按 UTF-8 读 JSON 会导致中文乱码。"""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


_ensure_stdio_utf8()

_listen_thread: threading.Thread | None = None
_listen_stop = threading.Event()
_listen_account_id: str = ""
_event_queue: list[dict[str, Any]] = []
_event_lock = threading.Lock()
_event_seq = 0


def _ok(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": True}
    if payload:
        out.update(payload)
    return out


def _err(msg: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": msg, **extra}


def _push_event(kind: str, payload: dict[str, Any]) -> None:
    global _event_seq
    with _event_lock:
        _event_seq += 1
        _event_queue.append({"seq": _event_seq, "kind": kind, "ts": int(time.time()), **payload})
        if len(_event_queue) > 500:
            del _event_queue[: len(_event_queue) - 500]


def _listen_worker() -> None:
    os.environ.setdefault("PIGEON_STANDALONE", "1")
    import asyncio

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        while not _listen_stop.is_set():
            bound_aid = _listen_account_id
            if not bound_aid:
                time.sleep(0.5)
                continue
            from pigeon_protocol.config import AppConfig
            from pigeon_protocol.standalone import StandaloneRuntime

            rt = StandaloneRuntime(config=AppConfig(dry_run=False))

            def on_msg(msg) -> None:
                if bound_aid != _listen_account_id:
                    return
                _push_event("message", {"message": asdict(msg), "account_id": bound_aid})

            try:
                loop.run_until_complete(rt.listen(on_msg, timeout_sec=30))
            except Exception as exc:
                if bound_aid == _listen_account_id:
                    _push_event("error", {"error": str(exc), "account_id": bound_aid})
                time.sleep(2)
    finally:
        loop.close()


def _post_import_warm() -> None:
    try:
        from pigeon_protocol.conv_list import warm_conv_session
        from pigeon_protocol.foundation.pure_prepare import prepare_pure_runtime
        from pigeon_protocol.session import load_session

        session = load_session()
        warm_conv_session(session)
        try:
            from pigeon_protocol.fingerprint_sync import sync_fingerprint_from_session

            sync_fingerprint_from_session(session)
        except Exception as exc:
            logger.debug("fingerprint sync: %s", exc)
        prepare_pure_runtime(session, probe_ws=False)
    except Exception as exc:
        logger.debug("post import warm: %s", exc)


_bridge_ready = False


def _ensure_bridge_ready(*, migrate: bool = True) -> None:
    global _bridge_ready
    from pigeon_protocol.account_context import init_account_context

    init_account_context(migrate=migrate and not _bridge_ready)
    _bridge_ready = True


def handle(action: str, params: dict[str, Any]) -> dict[str, Any]:
    global _listen_thread, _listen_account_id

    os.environ.setdefault("PIGEON_STANDALONE", "1")
    action = (action or "").strip().lower()
    fast_actions = {
        "ping",
        "session_status",
        "qr_login_status",
        "qr_login_start",
        "list_accounts",
        "health",
        "session_keepalive",
    }
    _ensure_bridge_ready(migrate=action not in fast_actions)

    if action == "ping":
        return _ok({"pong": True, "standalone": os.getenv("PIGEON_STANDALONE")})

    if action == "list_accounts":
        from pigeon_protocol.account_context import account_status

        return _ok(account_status())

    if action == "switch_account":
        from pigeon_protocol import api_server as api

        aid = str(params.get("account_id") or params.get("id") or "")
        if not aid:
            return _err("account_id required")
        restart = bool(params.get("restart_listen", True))
        result = api.switch_active_account(aid, restart_listen=restart)
        if not result.get("ok"):
            return _err(result.get("error") or "switch failed", **result)
        return _ok(result)

    if action == "create_account":
        from pigeon_protocol import api_server as api

        label = str(params.get("label") or "新账号")
        return api.create_account_api(label=label)

    if action == "prepare_pure":
        from pigeon_protocol.foundation.pure_prepare import prepare_pure_runtime
        from pigeon_protocol.session import load_session

        session = load_session()
        report = prepare_pure_runtime(session, probe_ws=bool(params.get("probe_ws")))
        return _ok({"report": report, "ready": bool(report.get("ok"))})

    if action == "warm_conv":
        from pigeon_protocol.conv_list import warm_conv_session
        from pigeon_protocol.session import load_session

        return _ok({"warm": warm_conv_session(load_session())})

    if action == "sign_url":
        from pigeon_protocol.foundation.bdms_sign import sign_backstage_url, persist_tokens_to_session
        from pigeon_protocol.session import load_session, save_session

        url = str(params.get("url") or "")
        method = str(params.get("method") or "GET").upper()
        if not url:
            return _err("url required")
        session = load_session()
        sign = sign_backstage_url(url, method=method, prefer_python=params.get("prefer_python"))
        persist_tokens_to_session(session, sign)
        try:
            save_session(session)
        except OSError:
            pass
        return _ok(
            {
                "signed_url": sign.signed_url,
                "via": sign.via,
                "tokens": sign.tokens,
                "sign_ok": sign.ok,
            }
        )

    if action == "session_status":
        from pigeon_protocol.account_context import account_status
        from pigeon_protocol.session import load_session
        from pigeon_protocol.session_keepalive import last_keepalive

        from pigeon_protocol import api_server as api

        t0 = time.monotonic()
        session = load_session()
        cookies = session.cookies or {}
        logged_in = bool(cookies.get("sessionid") or cookies.get("sid_tt"))
        acct = account_status()
        qr_snap = api.qr_active_snapshot()
        qr_active = bool(qr_snap.get("active"))
        if qr_active:
            logged_in = False
        snap = last_keepalive()
        ready = snap.get("readiness") if isinstance(snap.get("readiness"), dict) else {}
        shop = cookies.get("SHOP_ID") or session.shop_id or ""
        send_ready = ready.get("send_ready")
        listen_ready = ready.get("listen_ready")
        if send_ready is None:
            send_ready = bool(session.query_tokens.get("pigeon_sign") and session.ws_urls)
        if listen_ready is None:
            listen_ready = logged_in
        backstage_ok = ready.get("backstage_ok")
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "session_status active=%s logged_in=%s cookies=%d ms=%.0f",
            acct.get("active_account_id") or "",
            logged_in,
            len(cookies),
            elapsed_ms,
        )
        return _ok(
            {
                "logged_in": logged_in,
                "session_alive": bool(cookies.get("sessionid") or cookies.get("sid_tt")),
                "shop_id": shop,
                "shop_name": f"店铺 {shop}" if shop else "飞鸽客服",
                "cookie_count": len(cookies),
                "qr": {
                    "phase": qr_snap.get("phase") or ("logged_in" if logged_in else "logged_out"),
                    "error": "",
                    "running": bool(qr_snap.get("running")),
                    "job_id": qr_snap.get("job_id", ""),
                },
                "active_account_id": acct.get("active_account_id"),
                "accounts": acct.get("accounts") or [],
                "send_ready": send_ready,
                "listen_ready": listen_ready,
                "backstage_ok": backstage_ok,
                "backstage_expired": ready.get("backstage_expired"),
                "needs_renew": logged_in and backstage_ok is False,
                "needs_full_login": not logged_in,
                "blockers": ready.get("blockers") or [],
                "recommended_action": ready.get("recommended_action") or ("ready" if send_ready else ""),
                "has_ws": bool(session.ws_urls),
                "has_pigeon_sign": bool(session.query_tokens.get("pigeon_sign")),
            }
        )

    if action == "session_doctor":
        from pigeon_protocol.session import load_session, save_session
        from pigeon_protocol.session_health import check_session
        from pigeon_protocol.session_readiness import assess_runtime_ready, heal_for_send

        session = load_session()
        if params.get("fix", True):
            heal = heal_for_send(session, save=True)
            ready = heal.get("readiness") or assess_runtime_ready(session)
            return _ok(
                {
                    "health": heal.get("session_health") or heal.get("portable"),
                    "heal": heal,
                    "readiness": ready,
                    "ready": bool(ready.get("send_ready")),
                    "send_ready": ready.get("send_ready"),
                    "recommended_action": ready.get("recommended_action"),
                    "blockers": ready.get("blockers"),
                }
            )
        health = check_session(session)
        ready = assess_runtime_ready(session)
        return _ok({"health": health.to_dict(), "readiness": ready, "ready": ready.get("send_ready")})

    if action == "export_session_pack":
        from pathlib import Path

        from pigeon_protocol.account_context import session_pack_file
        from pigeon_protocol.session_portable import export_session_pack

        dest = str(params.get("path") or params.get("file") or session_pack_file())
        report = export_session_pack(Path(dest))
        return _ok(report) if report.get("ok") else _err(report.get("error") or "export failed", **report)

    if action == "import_session_pack":
        from pathlib import Path

        from pigeon_protocol.session_portable import import_session_pack

        src = str(params.get("path") or params.get("file") or "")
        if not src:
            return _err("path required")
        report = import_session_pack(
            Path(src),
            run_prepare=not params.get("no_prepare"),
            set_active=bool(params.get("set_active")),
        )
        ready = report.get("readiness") or {}
        ok = bool(report.get("send_ready") or ready.get("send_ready") or report.get("ok"))
        return _ok({**report, "ok": ok}) if ok or report.get("copied") else _err(
            report.get("error") or "import not ready", **report
        )

    if action == "health":
        from pigeon_protocol.config import AppConfig
        from pigeon_protocol.foundation.status import foundation_report
        from pigeon_protocol.session import load_session
        from pigeon_protocol.standalone import StandaloneRuntime

        session = load_session()
        rt = StandaloneRuntime(config=AppConfig(dry_run=False))
        foundation = foundation_report(session).to_dict()
        return _ok({"health": rt.health(), "foundation": foundation})

    if action == "conv_list":
        from pigeon_protocol.conv_list_service import fetch_conversations

        page = int(params.get("page") or 0)
        size = int(params.get("size") or 30)
        category = str(params.get("category") or "")
        return fetch_conversations(page=page, size=size, category=category)

    if action == "import_har":
        from pathlib import Path

        from pigeon_protocol.har_session_import import import_har_session

        har = str(params.get("path") or params.get("file") or "")
        if not har:
            return _err("path required")
        result = import_har_session(
            Path(har),
            merge=not params.get("replace"),
            run_parse=not params.get("no_captures"),
        )
        _post_import_warm()
        return {"ok": True, **result}

    if action == "import_cookies":
        from pathlib import Path

        from pigeon_protocol.cookie_import import import_cookies

        cf = str(params.get("path") or params.get("file") or "")
        if not cf:
            return _err("path required")
        session = import_cookies(
            Path(cf),
            merge=not params.get("replace"),
            shop_id=str(params.get("shop_id") or ""),
            user_agent=str(params.get("user_agent") or ""),
        )
        _post_import_warm()
        return {"ok": True, "cookies": len(session.cookies), "shop_id": session.shop_id}

    if action == "protocol_status":
        from pigeon_protocol.conv_sign_snapshot import has_fresh_snapshot, snapshot_age_sec
        from pigeon_protocol.foundation.status import foundation_report
        from pigeon_protocol.pure_config import BUNDLE_CONV_SNAPSHOT
        from pigeon_protocol.session import load_session
        from pigeon_protocol.session_readiness import assess_runtime_ready

        session = load_session()
        foundation = foundation_report(session).to_dict()
        ready = assess_runtime_ready(session)
        return _ok(
            {
                "foundation_ok": foundation.get("ok"),
                "pure_ready": foundation.get("pure_ready"),
                "send_ready": ready.get("send_ready"),
                "listen_ready": ready.get("listen_ready"),
                "conv_ready": ready.get("conv_ready"),
                "blockers": ready.get("blockers"),
                "conv_snapshot": has_fresh_snapshot(),
                "conv_snapshot_age": snapshot_age_sec(),
                "conv_snapshot_path": str(BUNDLE_CONV_SNAPSHOT),
                "has_ws": bool(session.ws_urls),
                "has_pigeon_sign": bool(session.query_tokens.get("pigeon_sign")),
                "backstage_ok": ready.get("backstage_ok"),
                "backstage_expired": ready.get("backstage_expired"),
                "recommended_action": ready.get("recommended_action"),
                "needs_cdp_onboard": ready.get("needs_cdp_onboard"),
            }
        )

    if action == "shutdown":
        _listen_stop.set()
        return _ok({"shutdown": True})

    if action == "context":
        from pigeon_protocol.config import AppConfig
        from pigeon_protocol.standalone import StandaloneRuntime

        uid = str(params.get("user_id") or "")
        if not uid:
            return _err("user_id required")
        rt = StandaloneRuntime(config=AppConfig(dry_run=False))
        ctx = rt.get_context(uid)
        return _ok({"context": asdict(ctx), "message_count": len(ctx.messages)})

    if action == "orders":
        from pigeon_protocol.config import AppConfig
        from pigeon_protocol.order_componentized import enrich_order_context
        from pigeon_protocol.standalone import StandaloneRuntime

        uid = str(params.get("user_id") or "")
        if not uid:
            return _err("user_id required")
        rt = StandaloneRuntime(config=AppConfig(dry_run=False))
        orders = rt.get_orders(uid)
        payload = enrich_order_context(orders)
        from pigeon_protocol.pure_runtime import _orders_ok

        if _orders_ok(orders):
            StandaloneRuntime._cache_orders(uid, orders)
        ok = bool(orders.has_order or payload.get("cards"))
        err = ""
        raw = orders.raw if isinstance(getattr(orders, "raw", None), dict) else {}
        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        code = str(data.get("code", ""))
        if code and code not in ("0", "0.0") and not ok:
            err = str(data.get("msg") or orders.summary or f"订单查询失败 ({code})")
        resp: dict[str, Any] = {
            "orders": payload,
            "has_order": orders.has_order,
            "source": orders.source,
            "order_ok": ok,
        }
        if err:
            resp["error"] = err
        return _ok(resp)

    if action == "send":
        from pigeon_protocol.config import AppConfig
        from pigeon_protocol.session import load_session, save_session
        from pigeon_protocol.session_readiness import heal_for_send
        from pigeon_protocol.standalone import StandaloneRuntime

        uid = str(params.get("user_id") or "")
        text = str(params.get("text") or "")
        if not uid or not text:
            return _err("user_id and text required")

        session = load_session()
        if params.get("preflight", True):
            heal = heal_for_send(session, save=True)
            ready = heal.get("readiness") or {}
            if not ready.get("send_ready"):
                action_hint = ready.get("recommended_action") or "cdp_onboard"
                blockers = list(ready.get("blockers") or [])
                return _ok(
                    {
                        "sent": False,
                        "preflight_failed": True,
                        "reason": blockers[0] if blockers else "发信未就绪",
                        "send_ready": False,
                        "recommended_action": action_hint,
                        "needs_cdp_onboard": bool(ready.get("needs_cdp_onboard")),
                        "blockers": blockers,
                        "heal": heal,
                    }
                )

        rt = StandaloneRuntime(config=AppConfig(dry_run=False))
        result = rt.send_text(text, security_user_id=uid)
        if not result.ok:
            reason = str(result.reason or "")
            payload: dict[str, Any] = {
                "sent": False,
                "result": asdict(result),
                "reason": reason,
            }
            raw = result.raw if isinstance(result.raw, dict) else {}
            for k in ("preflight_failed", "recommended_action", "needs_cdp_onboard", "blockers"):
                if k in raw:
                    payload[k] = raw[k]
            if any(x in reason.lower() for x in ("400", "403", "forbidden", "10005", "未就绪")):
                ready = heal_for_send(session, save=True).get("readiness") or {}
                payload["recommended_action"] = ready.get("recommended_action", "cdp_onboard")
                payload["needs_cdp_onboard"] = True
                payload["blockers"] = ready.get("blockers")
                try:
                    save_session(session)
                except OSError:
                    pass
            return _ok(payload)
        return _ok({"result": asdict(result), "sent": result.ok})

    if action == "listen_start":
        from pigeon_protocol.account_context import active_account_id

        aid = str(params.get("account_id") or active_account_id() or "")
        if _listen_thread and _listen_thread.is_alive():
            if _listen_account_id == aid:
                return _ok({"running": True, "note": "already running", "account_id": aid})
            _listen_stop.set()
        _listen_account_id = aid
        _listen_stop.clear()
        _listen_thread = threading.Thread(target=_listen_worker, daemon=True, name="go-bridge-listen")
        _listen_thread.start()
        return _ok({"running": True, "account_id": aid})

    if action == "listen_stop":
        _listen_stop.set()
        _listen_account_id = ""
        return _ok({"running": False})

    if action == "listen_status":
        running = bool(_listen_thread and _listen_thread.is_alive() and not _listen_stop.is_set())
        return _ok({"running": running, "account_id": _listen_account_id if running else ""})

    if action == "events":
        since = int(params.get("since") or 0)
        with _event_lock:
            items = [e for e in _event_queue if e["seq"] > since]
            last = _event_seq
        return _ok({"items": items, "last_seq": last})

    if action == "refresh_conv_snapshot":
        from pigeon_protocol.conv_sign_snapshot import refresh_snapshots_from_cdp
        from pigeon_protocol.session import load_session

        if params.get("cdp_only"):
            report = refresh_snapshots_from_cdp(load_session(), page_size=int(params.get("size") or 20))
        else:
            from pigeon_protocol.conv_list import list_conversations_relay

            raw = list_conversations_relay(load_session(), size=int(params.get("size") or 20))
            report = {"ok": raw.get("ok"), "via": raw.get("via"), "items": len(raw.get("items") or [])}
        return _ok({"report": report})

    if action in ("qr_login_start", "qr_login_status", "ai_suggest"):
        from pigeon_protocol import api_server as api

        if action == "qr_login_start":
            aid = params.get("account_id")
            return api.qr_login_start(account_id=str(aid) if aid else None)
        if action == "qr_login_status":
            st = api.qr_login_status()
            return {"ok": True, **st}
        return api.ai_suggest(params)

    if action == "cdp_onboard_start":
        from pigeon_protocol.cdp_onboard import start_onboard_background

        return start_onboard_background(
            wait_sec=float(params.get("wait") or 300),
            launch=not params.get("no_launch"),
            close_browser=not params.get("keep_browser"),
            warm_inners=not params.get("no_warm"),
            export_pack=not params.get("no_export"),
        )

    if action == "cdp_warm_start":
        from pigeon_protocol.cdp_onboard import start_warm_background

        return start_warm_background(launch=not params.get("no_launch"))

    if action == "cdp_warm_status":
        from pigeon_protocol.cdp_onboard import warm_job_snapshot
        from pigeon_protocol.session_readiness import assess_runtime_ready
        from pigeon_protocol.session import load_session

        snap = warm_job_snapshot()
        ready = assess_runtime_ready(load_session())
        return _ok({"warm": snap, "send_ready": ready.get("send_ready"), "readiness": ready})

    if action == "cdp_onboard_status":
        from pigeon_protocol.cdp_onboard import job_snapshot
        from pigeon_protocol.session import load_session
        from pigeon_protocol.session_readiness import assess_runtime_ready

        snap = job_snapshot()
        ready = assess_runtime_ready(load_session())
        return _ok(
            {
                "onboard": snap,
                "logged_in": bool((load_session().cookies or {}).get("SHOP_ID")),
                "send_ready": ready.get("send_ready"),
                "listen_ready": ready.get("listen_ready"),
                "blockers": ready.get("blockers"),
                "phase": snap.get("phase"),
                "running": snap.get("running"),
                "error": snap.get("error"),
            }
        )

    if action == "readiness_status":
        from pigeon_protocol.session import load_session
        from pigeon_protocol.session_readiness import assess_runtime_ready

        ready = assess_runtime_ready(load_session())
        return _ok(ready)

    if action == "session_renew":
        from pigeon_protocol.session import load_session, save_session
        from pigeon_protocol.session_renewal import establish_im_session_http, renew_session_if_needed

        session = load_session()
        if params.get("full"):
            report = establish_im_session_http(session, persist=True)
        else:
            report = renew_session_if_needed(session, persist=True)
        try:
            save_session(session)
        except OSError:
            pass
        return _ok(report)

    if action == "session_bootstrap":
        from pigeon_protocol.session_startup import bootstrap_on_startup

        def _bg_bootstrap() -> None:
            try:
                bootstrap_on_startup(
                    auto_import_pack=not params.get("no_import"),
                    export_if_ready=not params.get("no_export"),
                )
            except Exception as exc:
                logger.debug("session_bootstrap: %s", exc)

        threading.Thread(target=_bg_bootstrap, daemon=True, name="bridge-bootstrap").start()
        return _ok({"started": True})

    if action == "session_keepalive":
        from pigeon_protocol.session_keepalive import keepalive_tick, last_keepalive

        if params.get("tick", True):
            def _bg_keepalive() -> None:
                try:
                    keepalive_tick(save=True)
                except Exception as exc:
                    logger.debug("keepalive bg: %s", exc)

            threading.Thread(target=_bg_keepalive, daemon=True, name="keepalive-tick").start()
            snap = last_keepalive()
            if snap.get("ts"):
                return _ok(snap)
            return _ok({"started": True, "ok": True})
        return _ok(last_keepalive())

    return _err(f"unknown action: {action}")


def run_daemon() -> int:
    """Line-delimited JSON RPC — one request per line, one response per line."""
    logging.basicConfig(level=logging.WARNING)
    try:
        from pigeon_protocol.runtime_paths import apply_runtime_env

        apply_runtime_env()
    except Exception:
        pass

    def _bg_prepare() -> None:
        try:
            from pigeon_protocol.session_keepalive import start_keepalive_loop

            start_keepalive_loop()
        except Exception as exc:
            logger.debug("daemon bg prepare: %s", exc)

    threading.Thread(target=_bg_prepare, daemon=True, name="bridge-prepare").start()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            resp = _err(f"invalid json: {exc}")
        else:
            action = str(req.get("action") or "")
            params = req.get("params") if isinstance(req.get("params"), dict) else {}
            try:
                resp = handle(action, params)
            except Exception as exc:
                logger.exception("go_bridge daemon %s", action)
                resp = _err(str(exc), action=action)
        sys.stdout.write(json.dumps(resp, ensure_ascii=False, default=str) + "\n")
        sys.stdout.flush()
    return 0


def main() -> int:
    if "--daemon" in sys.argv:
        return run_daemon()

    logging.basicConfig(level=logging.WARNING)
    raw = sys.stdin.read()
    try:
        req = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        sys.stdout.write(json.dumps(_err(f"invalid json: {exc}"), ensure_ascii=False))
        return 1
    action = str(req.get("action") or "")
    params = req.get("params") if isinstance(req.get("params"), dict) else {}
    try:
        resp = handle(action, params)
    except Exception as exc:
        logger.exception("go_bridge %s", action)
        resp = _err(str(exc), action=action)
    sys.stdout.write(json.dumps(resp, ensure_ascii=False, default=str))
    return 0 if resp.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
