"""Local HTTP JSON API for desktop GUI (no browser at runtime)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from dataclasses import asdict, is_dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger("pigeon.api")

ROOT = Path(__file__).resolve().parents[2]
UI_DIR = ROOT / "desktop" / "ui"
DEFAULT_PORT = int(os.getenv("PIGEON_API_PORT", "8765"))
LOCAL_RAG_URL = os.getenv("LOCAL_RAG_URL", "http://127.0.0.1:8798")

_listen_thread: threading.Thread | None = None
_listen_stop = threading.Event()
_listen_account_id: str = ""
_qr_stop_events: dict[str, threading.Event] = {}
_event_queue: list[dict[str, Any]] = []
_event_lock = threading.Lock()
_event_seq = 0

_qr_lock = threading.RLock()
_qr_clients: dict[str, Any] = {}
_qr_jobs: dict[str, dict[str, Any]] = {}
_qr_threads: dict[str, threading.Thread] = {}
_qr_generation: dict[str, int] = {}
_unread_lock = threading.Lock()
_unread_bump: dict[str, dict[str, int]] = {}


def _ensure_accounts() -> None:
    from pigeon_protocol.account_context import init_account_context

    init_account_context(migrate=True)


def _active_account_id() -> str:
    from pigeon_protocol.account_context import active_account_id

    return active_account_id()


def _qr_stop_event(account_id: str) -> threading.Event:
    aid = str(account_id or "_default")
    with _qr_lock:
        ev = _qr_stop_events.get(aid)
        if ev is None:
            ev = threading.Event()
            _qr_stop_events[aid] = ev
        return ev


_qr_generation: dict[str, int] = {}
_qr_job_seq = 0


def _next_qr_job_id() -> str:
    global _qr_job_seq
    with _qr_lock:
        _qr_job_seq += 1
        return f"qr_{int(time.time())}_{_qr_job_seq:x}"


def _empty_qr_job() -> dict[str, Any]:
    return {
        "job_id": "",
        "account_id": "",
        "running": False,
        "phase": "logged_out",
        "error": "",
        "logged_in": False,
        "done": False,
        "send_ready": False,
        "listen_ready": False,
        "blockers": [],
        "post_login": {},
        "needs_cdp_onboard": False,
        "recommended_action": "",
        "token": "",
        "scanned_at": 0,
        "qr_started_at": 0,
        "created_at": 0,
        "last_poll_at": 0,
        "qr_refreshed_at": 0,
        "redirect_url": "",
        "login_subject_uid": "",
        "user_identity_id": "",
        "qrcode_b64": "",
    }


def _fresh_qr_job(*, phase: str = "fetching", running: bool = True, account_id: str = "") -> dict[str, Any]:
    now = time.time()
    job = _empty_qr_job()
    job.update(
        {
            "job_id": _next_qr_job_id(),
            "account_id": str(account_id or ""),
            "running": running,
            "phase": phase,
            "error": "",
            "logged_in": False,
            "done": False,
            "send_ready": False,
            "listen_ready": False,
            "blockers": [],
            "needs_cdp_onboard": False,
            "recommended_action": "",
            "post_login": {},
            "created_at": now,
            "qr_started_at": now if phase != "logged_out" else 0,
        }
    )
    return job


def _qr_job_public(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": job.get("job_id", ""),
        "account_id": job.get("account_id", ""),
        "phase": job.get("phase", "logged_out"),
        "error": job.get("error", ""),
        "running": bool(job.get("running")),
        "done": bool(job.get("done")),
        "logged_in": bool(job.get("logged_in")),
        "send_ready": job.get("send_ready", False),
        "listen_ready": job.get("listen_ready", False),
        "blockers": job.get("blockers") or [],
        "qr_refreshed_at": float(job.get("qr_refreshed_at") or 0),
        "created_at": float(job.get("created_at") or 0),
        "last_poll_at": float(job.get("last_poll_at") or 0),
    }


def _qr_job_for(account_id: str | None = None) -> dict[str, Any]:
    aid = str(account_id or _active_account_id() or "_default")
    with _qr_lock:
        job = _qr_jobs.get(aid)
        if job is None:
            job = _empty_qr_job()
            _qr_jobs[aid] = job
        return job


def _json_default(obj: Any) -> Any:
    if is_dataclass(obj):
        return asdict(obj)
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return str(obj)


def _runtime():
    _ensure_accounts()
    os.environ.setdefault("PIGEON_STANDALONE", "1")
    from pigeon_protocol.config import AppConfig
    from pigeon_protocol.standalone import StandaloneRuntime

    return StandaloneRuntime(config=AppConfig(dry_run=False))


def _push_event(kind: str, payload: dict[str, Any]) -> None:
    global _event_seq
    with _event_lock:
        _event_seq += 1
        _event_queue.append({"seq": _event_seq, "kind": kind, "ts": int(time.time()), **payload})
        if len(_event_queue) > 500:
            del _event_queue[: len(_event_queue) - 500]


def _listen_worker() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        while not _listen_stop.is_set():
            bound_aid = _listen_account_id
            if not bound_aid:
                time.sleep(0.5)
                continue
            rt = _runtime()

            def on_msg(msg) -> None:
                if bound_aid != _listen_account_id:
                    return
                uid = str(getattr(msg, "security_user_id", "") or "")
                if uid:
                    with _unread_lock:
                        acct_bumps = _unread_bump.setdefault(bound_aid, {})
                        acct_bumps[uid] = acct_bumps.get(uid, 0) + 1
                _push_event("message", {"message": asdict(msg), "account_id": bound_aid})

            try:
                loop.run_until_complete(rt.listen(on_msg, timeout_sec=30))
            except Exception as exc:
                if bound_aid == _listen_account_id:
                    _push_event("error", {"error": str(exc), "account_id": bound_aid})
                time.sleep(2)
    finally:
        loop.close()


def start_listen() -> dict[str, Any]:
    global _listen_thread, _listen_account_id
    aid = _active_account_id()
    if _listen_thread and _listen_thread.is_alive():
        if _listen_account_id == aid:
            return {"ok": True, "running": True, "note": "already running", "account_id": aid}
        stop_listen()
    _listen_account_id = aid
    _listen_stop.clear()
    _listen_thread = threading.Thread(target=_listen_worker, daemon=True, name="pigeon-listen")
    _listen_thread.start()
    return {"ok": True, "running": True, "account_id": aid}


def stop_listen() -> dict[str, Any]:
    global _listen_account_id
    _listen_stop.set()
    _listen_account_id = ""
    return {"ok": True, "running": False}


def list_conversations(page: int = 0, size: int = 30) -> dict[str, Any]:
    from pigeon_protocol.conv_list_service import fetch_conversations

    result = fetch_conversations(page=page, size=size)
    items = result.get("items") or []
    aid = _active_account_id()
    with _unread_lock:
        bumps = dict(_unread_bump.get(aid, {}))
    for it in items:
        uid = str(it.get("security_user_id") or "")
        if uid and bumps.get(uid):
            it["unread_count"] = int(it.get("unread_count") or 0) + bumps[uid]
    result["items"] = items
    return result


def qr_active_snapshot() -> dict[str, Any]:
    aid = _active_account_id()
    with _qr_lock:
        job = dict(_qr_jobs.get(aid) or _empty_qr_job())
        phase = str(job.get("phase") or "logged_out")
        running = bool(job.get("running"))
        t = _qr_threads.get(aid)
        if t and t.is_alive() and phase in ("waiting_scan", "scanned", "fetching", "bootstrapping"):
            running = True
    active = running and phase in ("fetching", "waiting_scan", "scanned", "bootstrapping")
    return {"active": active, "phase": phase, "running": running, "job_id": job.get("job_id", "")}


def session_status() -> dict[str, Any]:
    from pigeon_protocol.account_context import account_status
    from pigeon_protocol.session import load_session
    from pigeon_protocol.shop_profile import ensure_shop_name

    session = load_session()
    cookies = session.cookies or {}
    logged_in = bool(cookies.get("sessionid") or cookies.get("sid_tt"))
    shop = cookies.get("SHOP_ID") or session.shop_id or ""
    aid = _active_account_id()
    with _qr_lock:
        job = _qr_jobs.get(aid) or {}
        qr_phase = str(job.get("phase") or "logged_out")
        qr_running = bool(job.get("running"))
        t = _qr_threads.get(aid)
        if t and t.is_alive() and qr_phase in ("waiting_scan", "scanned", "fetching", "bootstrapping"):
            qr_running = True
        qr_active = qr_running and qr_phase in ("fetching", "waiting_scan", "scanned", "bootstrapping")
        if qr_active:
            logged_in = False
        elif logged_in and not qr_running and qr_phase not in ("fetching", "waiting_scan", "scanned", "bootstrapping"):
            qr_phase = "logged_in"
        qr = {
            "phase": qr_phase,
            "error": "" if qr_active else job.get("error", ""),
            "running": qr_running,
            "job_id": job.get("job_id", ""),
        }
    acct = account_status()
    return {
        "logged_in": logged_in,
        "shop_id": shop,
        "shop_name": ensure_shop_name(session, fetch=bool(logged_in)) if shop else "飞鸽客服",
        "cookie_count": len(cookies),
        "qr": qr,
        "active_account_id": acct.get("active_account_id") or aid,
        "accounts": acct.get("accounts") or [],
    }


def _qr_has_auth_cookies(cookies: dict[str, Any]) -> bool:
    from pigeon_protocol.qr_login import qr_session_cookies_ready

    return qr_session_cookies_ready(cookies)


def _qr_has_sso_hint(cookies: dict[str, Any]) -> bool:
    from pigeon_protocol.qr_login import qr_sso_cookies_ready

    return qr_sso_cookies_ready(cookies)


def _qr_worker_stale(account_id: str, generation: int) -> bool:
    with _qr_lock:
        return int(_qr_generation.get(account_id) or 0) != generation


def _qr_stop_worker(account_id: str, *, wait_sec: float = 0.5) -> None:
    _qr_stop_event(account_id).set()
    t = _qr_threads.get(account_id)
    if t and t.is_alive():
        t.join(timeout=wait_sec)
    _qr_stop_event(account_id).clear()


def _qr_spawn_poll(client, token: str, account_id: str) -> None:
    with _qr_lock:
        gen = int(_qr_generation.get(account_id) or 0) + 1
        _qr_generation[account_id] = gen

    def _run() -> None:
        try:
            _qr_poll_worker(client, token, account_id, gen)
        finally:
            with _qr_lock:
                cur = _qr_threads.get(account_id)
                if cur is threading.current_thread():
                    _qr_threads.pop(account_id, None)

    t = threading.Thread(target=_run, daemon=True, name=f"qr-login-{account_id}")
    with _qr_lock:
        _qr_threads[account_id] = t
    t.start()


def _qr_resume_poll_if_needed(account_id: str) -> None:
    with _qr_lock:
        job = _qr_jobs.get(account_id)
        if not job or job.get("running") or job.get("logged_in"):
            return
        phase = str(job.get("phase") or "")
        if phase not in ("waiting_scan", "scanned", "fetching"):
            return
        token = str(job.get("token") or "")
        started = float(job.get("qr_started_at") or 0)
        if not token or not started or time.time() - started > 600:
            return
        t = _qr_threads.get(account_id)
        if t and t.is_alive():
            job["running"] = True
            return
        client = _qr_clients.get(account_id)
    if client is None:
        from pigeon_protocol.qr_login import DoudianSsoQrLoginClient

        client = DoudianSsoQrLoginClient()
        with _qr_lock:
            _qr_clients[account_id] = client
    with _qr_lock:
        job = _qr_jobs.setdefault(account_id, _empty_qr_job())
        job["running"] = True
        job["error"] = ""
    _qr_spawn_poll(client, token, account_id)


def _qr_save_poll_hints(job: dict[str, Any], st: Any) -> None:
    if getattr(st, "redirect_url", ""):
        job["redirect_url"] = st.redirect_url
    if getattr(st, "login_subject_uid", ""):
        job["login_subject_uid"] = st.login_subject_uid
    if getattr(st, "user_identity_id", ""):
        job["user_identity_id"] = st.user_identity_id


def _qr_finish_confirmed(
    client,
    st: Any,
    job: dict[str, Any],
    *,
    skip_fxg_complete: bool = False,
) -> None:
    from pigeon_protocol.qr_login import QR_CONFIRMED
    from pigeon_protocol.session import load_session, save_session

    aid = str(job.get("account_id") or _active_account_id() or "").strip()
    if aid:
        from pigeon_protocol.account_context import apply_account_env, ensure_account_dirs

        ensure_account_dirs(aid)
        apply_account_env(aid)

    job["phase"] = "bootstrapping"
    job["running"] = True
    job["error"] = ""
    session = load_session()
    if str(getattr(st, "status", "") or "") != QR_CONFIRMED:
        st.status = QR_CONFIRMED
    try:
        client.apply_to_session(session, st)
        save_session(session)
        cookies = session.cookies or {}
        shop = str(session.cookies.get("SHOP_ID") or session.shop_id or "")
        cookie_count = len(cookies)
        try:
            from pigeon_protocol.account_context import promote_account_to_shop, register_account, session_file

            if aid and shop:
                canonical = promote_account_to_shop(aid, shop)
                aid = canonical
                job["account_id"] = canonical
            elif aid:
                register_account(aid, set_active=True)
        except Exception as reg_exc:
            logger.warning("update account registry after qr: %s", reg_exc)
        logger.info(
            "qr success account_id=%s session_path=%s cookie_count=%d shop_id=%s",
            aid,
            str(session_file()),
            cookie_count,
            shop,
        )
    except Exception as exc:
        logger.warning("early apply session: %s", exc)

    with _qr_lock:
        job["logged_in"] = True
        job["phase"] = "logged_in"
        job["running"] = False
        job["error"] = ""
        job["done"] = True
        job["listen_ready"] = True

    def _bootstrap() -> None:
        try:
            from pigeon_protocol.session_portable import post_login_bootstrap

            bootstrap_mode = os.environ.get("PIGEON_POST_LOGIN_BOOTSTRAP", "background").strip().lower()
            pl = post_login_bootstrap(
                session,
                qr_client=client,
                qr_state=st,
                skip_fxg_complete=skip_fxg_complete,
                mode=bootstrap_mode,
            )
            with _qr_lock:
                job["post_login"] = {
                    "ok": pl.get("ok"),
                    "steps": pl.get("steps"),
                    "send_ready": pl.get("send_ready"),
                    "listen_ready": pl.get("listen_ready"),
                    "blockers": pl.get("blockers"),
                    "session_pack": pl.get("session_pack"),
                }
                job["send_ready"] = bool(pl.get("send_ready"))
                job["listen_ready"] = bool(pl.get("listen_ready"))
                job["blockers"] = list(pl.get("blockers") or [])
                job["recommended_action"] = pl.get("recommended_action") or ""
                job["needs_cdp_onboard"] = bool(pl.get("needs_cdp_onboard"))
                if pl.get("needs_cdp_onboard") and not job["send_ready"]:
                    from pigeon_protocol.pure_config import cdp_allowed

                    hint = (
                        "抖店二维码无法建立飞鸽 backstage，请使用浏览器登录"
                        if cdp_allowed()
                        else "飞鸽 backstage 未就绪，正在尝试 HTTP 续期（无需浏览器）"
                    )
                    if hint not in job["blockers"]:
                        job["blockers"].insert(0, hint)
        except Exception as exc:
            logger.warning("post-login bootstrap: %s", exc)
            with _qr_lock:
                job["post_login"] = {"error": str(exc)[:200]}
            try:
                from pigeon_protocol.session_health import auto_heal_session

                client.apply_to_session(session, st)
                auto_heal_session(session)
                from pigeon_protocol.session import save_session

                save_session(session)
            except Exception as heal_exc:
                logger.warning("post-login heal: %s", heal_exc)

    import threading

    threading.Thread(target=_bootstrap, daemon=True, name="qr-bootstrap").start()


def _qr_finish_confirmed_async(client, st: Any, job: dict[str, Any]) -> None:
    th = threading.Thread(
        target=_qr_finish_confirmed,
        args=(client, st, job),
        kwargs={"skip_fxg_complete": True},
        daemon=True,
        name="qr-finish",
    )
    th.start()


def _qr_try_recover_after_scan(client, job: dict[str, Any], st: Any, token: str) -> bool:
    """手机已确认但轮询落到 status=5，用 burst + subject/redirect 补全。"""
    from pigeon_protocol.qr_login import QR_CONFIRMED, QrLoginState

    hit = client.burst_confirm_poll(token, rounds=80, interval_sec=0.1)
    if hit and str(hit.status) == QR_CONFIRMED:
        st = hit
    redirect = str(
        getattr(st, "redirect_url", "") or job.get("redirect_url") or ""
    ).strip()
    subject_uid = str(
        getattr(st, "login_subject_uid", "") or job.get("login_subject_uid") or ""
    ).strip()
    identity_id = str(
        getattr(st, "user_identity_id", "") or job.get("user_identity_id") or ""
    ).strip()
    hint = QrLoginState(
        token=token or str(job.get("token") or ""),
        status=str(getattr(st, "status", "") or ""),
        redirect_url=redirect,
        login_subject_uid=subject_uid,
        user_identity_id=identity_id,
        cookies=dict(getattr(st, "cookies", None) or client._cookie_dict()),
    )
    finalized = client.finalize_confirmed_login(hint)
    if finalized:
        _qr_finish_confirmed_async(client, finalized, job)
        return True
    if not redirect and not subject_uid and not _qr_has_auth_cookies(hint.cookies):
        return False
    recovered = QrLoginState(
        token=hint.token,
        status=QR_CONFIRMED,
        cookies=hint.cookies,
        redirect_url=redirect,
        login_subject_uid=subject_uid,
        user_identity_id=identity_id,
    )
    try:
        recovered.cookies = client.complete_fxg_login(recovered)
        recovered.cookies.update(client.open_feige_workspace())
    except Exception as exc:
        logger.warning("qr recover after scan: %s", exc)
        return False
    _qr_finish_confirmed_async(client, recovered, job)
    return True


def _qr_poll_worker(
    client,
    token: str,
    account_id: str,
    generation: int,
    timeout_sec: float = 600.0,
) -> None:
    """后台轮询 — 复用 CLI 已验证的 poll_until_done。"""
    from pigeon_protocol.qr_login import QR_CONFIRMED, QR_EXPIRED, QR_NEW, QR_SCANNED
    from pigeon_protocol.account_context import apply_account_env, qr_png_path

    def should_stop() -> bool:
        if _qr_stop_event(account_id).is_set():
            return True
        return _qr_worker_stale(account_id, generation)

    apply_account_env(account_id)
    qr_path = qr_png_path()

    def on_status(st: Any) -> None:
        if should_stop():
            return
        with _qr_lock:
            if _qr_worker_stale(account_id, generation):
                return
            job = _qr_jobs.setdefault(account_id, _empty_qr_job())
            job["running"] = True
            job["last_poll_at"] = time.time()
            job["account_id"] = account_id
            _qr_save_poll_hints(job, st)
            if st.status == QR_SCANNED:
                job["phase"] = "scanned"
                job["scanned_at"] = time.time()
            elif st.status == QR_CONFIRMED:
                job["phase"] = "bootstrapping"
            elif st.status == QR_EXPIRED:
                if (
                    job.get("scanned_at")
                    or job.get("login_subject_uid")
                    or job.get("redirect_url")
                    or str(job.get("phase") or "") in ("scanned", "bootstrapping")
                ):
                    job["phase"] = "bootstrapping"
                    job["running"] = True
                    job["error"] = ""
                else:
                    job["phase"] = "expired"
                    job["error"] = str(st.error or job.get("error") or "二维码已过期")
            elif st.status in (QR_NEW, ""):
                if str(job.get("phase") or "") != "scanned":
                    job["phase"] = "waiting_scan"

    with _qr_lock:
        if _qr_worker_stale(account_id, generation):
            return
        job = _qr_jobs.setdefault(account_id, _empty_qr_job())
        job.update(
            {
                "running": True,
                "phase": "waiting_scan",
                "error": "",
                "scanned_at": 0,
                "qr_started_at": time.time(),
                "token": token,
                "account_id": account_id,
            }
        )

    try:
        result = client.poll_until_done(
            token,
            timeout_sec=timeout_sec,
            interval_sec=0.35,
            on_status=on_status,
            qrcode_path=qr_path,
            auto_refresh=True,
            should_stop=should_stop,
        )
    except Exception as exc:
        if _qr_worker_stale(account_id, generation):
            return
        logger.warning("qr poll worker: %s", exc)
        with _qr_lock:
            job = _qr_jobs.setdefault(account_id, _empty_qr_job())
            job.update({"phase": "error", "error": str(exc)[:200], "running": False})
        return

    if _qr_worker_stale(account_id, generation):
        return

    with _qr_lock:
        if should_stop():
            return
        job = _qr_jobs.setdefault(account_id, _empty_qr_job())
        err = str(result.error or "").strip()
        if err == "cancelled":
            job["running"] = False
            return
        cookies = dict(result.cookies or client._cookie_dict())
        finish = False
        try_recover = False
        if str(result.status) == QR_CONFIRMED:
            if cookies:
                result.cookies = cookies
            finish = True
        else:
            _qr_save_poll_hints(job, result)
            if str(result.status) == QR_EXPIRED or (
                err and ("过期" in err or "expired" in err.lower())
            ):
                try_recover = True
            elif err:
                try_recover = bool(
                    job.get("scanned_at")
                    or job.get("login_subject_uid")
                    or job.get("redirect_url")
                )
    if finish:
        _qr_finish_confirmed_async(client, result, job)
        return
    if try_recover and _qr_try_recover_after_scan(client, job, result, token):
        return
    with _qr_lock:
        job = _qr_jobs.setdefault(account_id, _empty_qr_job())
        err = str(result.error or "").strip()
        if try_recover:
            job.update(
                {
                    "phase": "error",
                    "error": "登录未完成，请点击刷新二维码重试",
                    "running": False,
                    "done": True,
                }
            )
        elif err and ("过期" in err or "expired" in err.lower()):
            job.update(
                {
                    "phase": "expired",
                    "error": "登录未完成，请点击刷新二维码重试",
                    "running": False,
                    "done": True,
                }
            )
        elif str(result.status) == QR_EXPIRED:
            job.update(
                {
                    "phase": "expired",
                    "error": "登录未完成，请点击刷新二维码重试",
                    "running": False,
                    "done": True,
                }
            )
        elif err:
            job.update({"phase": "error", "error": err[:200], "running": False, "done": True})
        else:
            job.update(
                {
                    "phase": "error",
                    "error": "扫码登录未完成",
                    "running": False,
                    "done": True,
                }
            )


def qr_login_start(*, account_id: str | None = None) -> dict[str, Any]:
    import threading

    from pigeon_protocol.qr_login import DoudianSsoQrLoginClient

    _ensure_accounts()
    from pigeon_protocol.account_context import ensure_qr_login_slot, qr_png_path

    slot = ensure_qr_login_slot(preferred_id=account_id)
    aid = str(slot.get("account_id") or "").strip()
    if not aid:
        return {"ok": False, "error": "无法准备登录账号槽", "qr": _qr_job_public(_empty_qr_job())}

    with _qr_lock:
        existing = _qr_jobs.get(aid) or {}
        ex_phase = str(existing.get("phase") or "")
        if existing.get("running") and ex_phase in ("fetching", "waiting_scan", "scanned", "bootstrapping"):
            pub = _qr_job_public(existing)
            return {
                "ok": True,
                "qr": pub,
                "has_qrcode": bool(existing.get("qrcode_b64")),
                "qrcode_b64": str(existing.get("qrcode_b64") or ""),
                "account_id": aid,
                "switched_from": slot.get("switched_from") or "",
                "note": "already_running",
            }

    logger.info(
        "qr start account_id=%s empty_slot=%s switched_from=%s",
        aid,
        bool(slot.get("empty_slot")),
        slot.get("switched_from") or "",
    )

    _qr_stop_worker(aid)
    with _qr_lock:
        _qr_jobs[aid] = _fresh_qr_job(phase="fetching", running=True, account_id=aid)
        job = _qr_jobs[aid]
    _qr_stop_event(aid).clear()

    def _bg_fetch() -> None:
        from pigeon_protocol.account_context import apply_account_env

        try:
            apply_account_env(aid)
            client = DoudianSsoQrLoginClient()
            with _qr_lock:
                _qr_clients[aid] = client
            state = client.fetch_qrcode(qrcode_path=qr_png_path())
            if state.error or not state.token:
                with _qr_lock:
                    cur = _qr_jobs.get(aid) or _fresh_qr_job(account_id=aid)
                    cur.update(
                        {
                            "phase": "error",
                            "running": False,
                            "error": state.error or "获取二维码失败",
                            "done": True,
                        }
                    )
                    _qr_jobs[aid] = cur
                return
            with _qr_lock:
                cur = _qr_jobs.get(aid) or _fresh_qr_job(account_id=aid)
                cur.update(
                    {
                        "phase": "waiting_scan",
                        "running": True,
                        "error": "",
                        "token": state.token,
                        "qr_started_at": time.time(),
                        "qrcode_b64": state.qrcode_b64 or "",
                    }
                )
                if not cur.get("job_id"):
                    cur["job_id"] = _next_qr_job_id()
                _qr_jobs[aid] = cur
            _qr_spawn_poll(client, state.token, aid)
        except Exception as exc:
            logger.warning("qr fetch worker: %s", exc)
            with _qr_lock:
                cur = _qr_jobs.get(aid) or _fresh_qr_job(account_id=aid)
                cur.update({"phase": "error", "running": False, "error": str(exc)[:200], "done": True})
                _qr_jobs[aid] = cur

    threading.Thread(target=_bg_fetch, daemon=True, name=f"qr-fetch-{aid}").start()
    with _qr_lock:
        pub = _qr_job_public(_qr_jobs[aid])
        b64 = str(_qr_jobs[aid].get("qrcode_b64") or "")
    return {
        "ok": True,
        "qr": pub,
        "has_qrcode": bool(b64),
        "qrcode_b64": b64,
        "account_id": aid,
        "switched_from": slot.get("switched_from") or "",
    }


def qr_login_status() -> dict[str, Any]:
    t0 = time.monotonic()
    from pigeon_protocol.account_context import account_status
    from pigeon_protocol.session import load_session

    aid = _active_account_id()
    acct = account_status()
    resume_poll_aid = ""
    post_login = None
    with _qr_lock:
        job = dict(_qr_jobs.get(aid) or _empty_qr_job())
        job["last_poll_at"] = time.time()
        if aid in _qr_jobs:
            _qr_jobs[aid]["last_poll_at"] = job["last_poll_at"]
        qr_phase = str(job.get("phase") or "logged_out")
        qr_running = bool(job.get("running"))
        t = _qr_threads.get(aid)
        if t and t.is_alive() and qr_phase in ("waiting_scan", "scanned", "fetching", "bootstrapping"):
            qr_running = True
        started = float(job.get("created_at") or job.get("qr_started_at") or 0)
        age = time.time() - started if started else 9999
        if (
            not qr_running
            and not job.get("logged_in")
            and qr_phase in ("fetching", "waiting_scan", "scanned", "expired")
        ):
            token = str(job.get("token") or "")
            if token and age < 600:
                if qr_phase == "expired" and not (job.get("scanned_at") or job.get("login_subject_uid")):
                    qr_phase = "waiting_scan"
                qr_running = True
            elif qr_phase in ("fetching", "waiting_scan", "scanned") and age < 120:
                qr_phase = "fetching" if qr_phase == "fetching" else "waiting_scan"
                qr_running = True
        if qr_phase == "expired":
            scanned_at = float(job.get("scanned_at") or 0)
            subject_uid = str(job.get("login_subject_uid") or "")
            if not scanned_at and not subject_uid and age < 600:
                qr_phase = "waiting_scan"
                qr_running = True
                if aid in _qr_jobs:
                    _qr_jobs[aid].update({"phase": "waiting_scan", "error": "", "running": True})
                if not (t and t.is_alive()) and job.get("token"):
                    resume_poll_aid = aid
            elif scanned_at or subject_uid or (t and t.is_alive()) or age < 180:
                qr_phase = "bootstrapping"
                qr_running = True
        qr_public = _qr_job_public({**job, "phase": qr_phase, "running": qr_running})
        if job.get("post_login"):
            post_login = job.get("post_login")

    if resume_poll_aid:
        _qr_resume_poll_if_needed(resume_poll_aid)

    qr_active = qr_running and qr_phase in ("fetching", "waiting_scan", "scanned", "bootstrapping")
    logged_in = False
    cookie_count = 0
    shop = ""
    shop_name = "飞鸽客服"
    send_ready = job.get("send_ready", False)
    listen_ready = job.get("listen_ready", False)
    blockers = job.get("blockers") or []

    if job.get("done") or job.get("logged_in") or qr_phase == "logged_in":
        if aid:
            from pigeon_protocol.account_context import apply_account_env

            apply_account_env(aid)
        session = load_session()
        cookies = session.cookies or {}
        cookie_count = len(cookies)
        logged_in = bool(cookies.get("sessionid") or cookies.get("sid_tt"))
        shop = str(cookies.get("SHOP_ID") or session.shop_id or "")
        from pigeon_protocol.shop_profile import ensure_shop_name

        shop_name = ensure_shop_name(session, fetch=bool(logged_in))
    elif not qr_active:
        session = load_session()
        cookies = session.cookies or {}
        cookie_count = len(cookies)

    if qr_active:
        logged_in = False

    elapsed_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "qr status account_id=%s job_id=%s phase=%s running=%s ms=%.0f",
        aid,
        qr_public.get("job_id"),
        qr_phase,
        qr_running,
        elapsed_ms,
    )

    out: dict[str, Any] = {
        "logged_in": logged_in,
        "shop_id": shop,
        "shop_name": shop_name,
        "cookie_count": cookie_count,
        "qr": qr_public,
        "active_account_id": acct.get("active_account_id") or aid,
        "accounts": acct.get("accounts") or [],
        "send_ready": send_ready,
        "listen_ready": listen_ready,
        "blockers": blockers,
    }
    if post_login:
        out["post_login"] = post_login
    return out


def switch_active_account(account_id: str, *, restart_listen: bool = True) -> dict[str, Any]:
    from pigeon_protocol.account_context import switch_account

    prev_aid = _active_account_id()
    stop_listen()
    if prev_aid:
        _qr_stop_event(prev_aid).set()
    result = switch_account(account_id)
    if not result.get("ok"):
        return result
    with _unread_lock:
        if prev_aid:
            _unread_bump.pop(prev_aid, None)
    if restart_listen:
        from pigeon_protocol.session import load_session

        session = load_session()
        cookies = session.cookies or {}
        if cookies.get("sessionid") or cookies.get("sid_tt"):
            start_listen()
    return {
        **result,
        "accounts": session_status().get("accounts") or [],
        "logged_in": session_status().get("logged_in"),
    }


def list_accounts_api() -> dict[str, Any]:
    from pigeon_protocol.account_context import account_status

    return account_status()


def create_account_api(*, label: str = "新账号") -> dict[str, Any]:
    from pigeon_protocol.account_context import create_account_slot, list_accounts

    prev_aid = _active_account_id()
    aid = create_account_slot(label=label)
    stop_listen()
    with _unread_lock:
        if prev_aid:
            _unread_bump.pop(prev_aid, None)
    return {"ok": True, "account_id": aid, "accounts": list_accounts()}


def logout_account_api(account_id: str | None = None, *, backup: bool = True) -> dict[str, Any]:
    from pigeon_protocol.account_context import account_logged_in, active_account_id, logout_account

    aid = str(account_id or _active_account_id() or "").strip()
    stop_listen()
    if aid:
        _qr_stop_event(aid).set()
        with _qr_lock:
            _qr_jobs.pop(aid, None)
    with _unread_lock:
        if aid:
            _unread_bump.pop(aid, None)
    result = logout_account(aid or None, backup=backup)
    if not result.get("ok"):
        return result
    switched = str(result.get("switched_to") or active_account_id() or "")
    return {
        **result,
        "active_account_id": active_account_id(),
        "logged_in": account_logged_in(switched) if switched else False,
    }


def remove_account_api(account_id: str | None = None, *, backup: bool = True, confirm: bool = False) -> dict[str, Any]:
    from pigeon_protocol.account_context import account_logged_in, active_account_id, remove_account

    if not confirm:
        return {"ok": False, "error": "confirm=true required"}
    aid = str(account_id or _active_account_id() or "").strip()
    stop_listen()
    if aid:
        _qr_stop_event(aid).set()
        with _qr_lock:
            _qr_jobs.pop(aid, None)
    with _unread_lock:
        if aid:
            _unread_bump.pop(aid, None)
    result = remove_account(aid or None, backup=backup)
    if not result.get("ok"):
        return result
    switched = str(result.get("switched_to") or active_account_id() or "")
    return {
        **result,
        "active_account_id": active_account_id(),
        "logged_in": account_logged_in(switched) if switched else False,
    }


def ai_suggest(body: dict[str, Any]) -> dict[str, Any]:
    import urllib.error
    import urllib.request

    uid = str(body.get("user_id") or "")
    payload = {
        "message": body.get("message") or body.get("current_customer_question") or "",
        "current_customer_question": body.get("current_customer_question") or body.get("message") or "",
        "recent_messages": body.get("recent_messages") or [],
        "buyer_name": body.get("buyer_name") or "",
        "mode": body.get("mode") or "fast",
        "style": body.get("style") or "",
        "customer_hash": uid[:32] if uid else "",
    }
    if body.get("order_context"):
        payload["order_context"] = body["order_context"]

    req = urllib.request.Request(
        f"{LOCAL_RAG_URL.rstrip('/')}/api/local-reply-suggest",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            out = json.loads(resp.read().decode("utf-8"))
            return out
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8"))
        except Exception:
            detail = {"message": str(exc)}
        return {"ok": False, "message": detail.get("message") or "AI 服务暂时不可用", **detail}
    except Exception as exc:
        return {
            "ok": False,
            "message": "连不上本地 AI 服务，请先运行：npm run relay:local（端口 8798）",
            "error": str(exc),
        }


# Legacy minimal UI kept for fallback only
UI_HTML_LEGACY = """<!DOCTYPE html><html><head><meta charset="utf-8"/><meta http-equiv="refresh" content="0;url=/"/></head><body></body></html>"""


def _ui_index_html() -> str:
    index = UI_DIR / "index.html"
    if index.is_file():
        return index.read_text(encoding="utf-8")
    return UI_HTML_LEGACY


def _send_static(handler: BaseHTTPRequestHandler, rel_path: str) -> bool:
    safe = Path(rel_path).name if "/" not in rel_path else rel_path.lstrip("/").replace("..", "")
    file_path = UI_DIR / safe
    if not file_path.is_file():
        return False
    data = file_path.read_bytes()
    ctype = "application/octet-stream"
    if safe.endswith(".css"):
        ctype = "text/css; charset=utf-8"
    elif safe.endswith(".js"):
        ctype = "application/javascript; charset=utf-8"
    elif safe.endswith(".png"):
        ctype = "image/png"
    handler.send_response(200)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-cache")
    handler.end_headers()
    handler.wfile.write(data)
    return True


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "PigeonFeigeAPI/1.0"

    def log_message(self, fmt: str, *args) -> None:
        logger.debug(fmt, *args)

    def _send(self, code: int, body: dict[str, Any]) -> None:
        data = json.dumps(body, ensure_ascii=False, default=_json_default).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _send_html(self, code: int, html: str) -> None:
        data = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        try:
            if path == "/":
                self._send_html(200, _ui_index_html())
                return

            if path.startswith("/static/"):
                if _send_static(self, path.replace("/static/", "", 1)):
                    return
                self._send(404, {"ok": False, "error": "static not found"})
                return

            if path == "/api/session":
                self._send(200, session_status())
                return

            if path == "/api/qr-login/status":
                self._send(200, qr_login_status())
                return

            if path == "/api/accounts":
                self._send(200, list_accounts_api())
                return

            if path == "/api/qr-login/image":
                from pigeon_protocol.account_context import qr_png_path

                qr_file = qr_png_path()
                if qr_file.is_file():
                    data = qr_file.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    self.wfile.write(data)
                    return
                self._send(404, {"ok": False, "error": "qrcode not ready"})
                return

            if path == "/api/health":
                rt = _runtime()
                h = rt.health()
                self._send(200, {"ok": True, "health": h})
                return

            if path == "/api/conversations":
                page = int((qs.get("page") or ["0"])[0])
                size = int((qs.get("size") or ["30"])[0])
                self._send(200, list_conversations(page=page, size=size))
                return

            if path == "/api/context":
                uid = (qs.get("user_id") or [""])[0]
                if not uid:
                    self._send(400, {"ok": False, "error": "user_id required"})
                    return
                rt = _runtime()
                ctx = rt.get_context(uid)
                with _unread_lock:
                    aid = _active_account_id()
                    acct_bumps = _unread_bump.get(aid)
                    if acct_bumps is not None:
                        acct_bumps.pop(uid, None)
                self._send(
                    200,
                    {
                        "ok": bool(ctx.messages),
                        "context": asdict(ctx),
                    },
                )
                return

            if path == "/api/orders":
                uid = (qs.get("user_id") or [""])[0]
                if not uid:
                    self._send(400, {"ok": False, "error": "user_id required"})
                    return
                rt = _runtime()
                orders = rt.get_orders(uid)
                from pigeon_protocol.order_componentized import enrich_order_context
                from pigeon_protocol.pure_runtime import _orders_ok
                from pigeon_protocol.standalone import StandaloneRuntime

                payload = enrich_order_context(orders)
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
                    "ok": ok,
                    "orders": payload,
                    "has_order": orders.has_order,
                    "source": orders.source,
                    "order_ok": ok,
                }
                if err:
                    resp["error"] = err
                self._send(200, resp)
                return

            if path == "/api/events":
                since = int((qs.get("since") or ["0"])[0])
                filter_aid = str((qs.get("account_id") or [""])[0]).strip()
                with _event_lock:
                    items = []
                    for e in _event_queue:
                        if e["seq"] <= since:
                            continue
                        evt_aid = str(e.get("account_id") or "")
                        if filter_aid:
                            if not evt_aid or evt_aid != filter_aid:
                                continue
                        items.append(e)
                    last = _event_seq
                self._send(200, {"ok": True, "items": items, "last_seq": last})
                return

            if path == "/api/listen/status":
                running = bool(_listen_thread and _listen_thread.is_alive() and not _listen_stop.is_set())
                self._send(
                    200,
                    {"ok": True, "running": running, "account_id": _listen_account_id if running else ""},
                )
                return

            if path == "/api/protocol/status":
                from pigeon_protocol.go_bridge import handle as bridge_handle

                self._send(200, bridge_handle("protocol_status", {}))
                return

            self._send(404, {"ok": False, "error": "not found"})
        except Exception as exc:
            logger.exception("GET %s", path)
            self._send(500, {"ok": False, "error": str(exc)})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        body = self._read_json()

        try:
            if path == "/api/import-har":
                har = body.get("path") or body.get("file") or ""
                if not har:
                    self._send(400, {"ok": False, "error": "path required"})
                    return
                from pigeon_protocol.har_session_import import import_har_session

                result = import_har_session(
                    Path(har),
                    merge=not body.get("replace"),
                    run_parse=not body.get("no_captures"),
                )
                try:
                    from pigeon_protocol.go_bridge import _post_import_warm

                    _post_import_warm()
                except Exception as exc:
                    logger.warning("post import-har warm: %s", exc)
                self._send(200, {"ok": True, **result})
                return

            if path == "/api/import-cookies":
                cf = body.get("path") or body.get("file") or ""
                if not cf:
                    self._send(400, {"ok": False, "error": "path required"})
                    return
                from pigeon_protocol.cookie_import import import_cookies

                session = import_cookies(
                    Path(cf),
                    merge=not body.get("replace"),
                    shop_id=body.get("shop_id") or "",
                    user_agent=body.get("user_agent") or "",
                )
                self._send(
                    200,
                    {
                        "ok": True,
                        "cookies": len(session.cookies),
                        "shop_id": session.shop_id,
                    },
                )
                return

            if path == "/api/send":
                uid = body.get("user_id") or ""
                text = body.get("text") or ""
                if not uid or not text:
                    self._send(400, {"ok": False, "error": "user_id and text required"})
                    return
                rt = _runtime()
                result = rt.send_text(text, security_user_id=uid)
                self._send(200, {"ok": result.ok, "result": asdict(result)})
                return

            if path == "/api/listen/start":
                self._send(200, start_listen())
                return

            if path == "/api/listen/stop":
                self._send(200, stop_listen())
                return

            if path == "/api/session-doctor":
                from pigeon_protocol.session import load_session
                from pigeon_protocol.session_health import auto_heal_session, check_session

                session = load_session()
                if body.get("fix", True):
                    health = auto_heal_session(session, refresh_csrf=True)
                else:
                    health = check_session(session)
                self._send(200, {"ok": health.ok, "health": health.to_dict()})
                return

            if path == "/api/qr-login/start":
                account_id = str(body.get("account_id") or "")
                self._send(200, qr_login_start(account_id=account_id or None))
                return

            if path == "/api/accounts/switch":
                account_id = str(body.get("account_id") or body.get("id") or "")
                if not account_id:
                    self._send(400, {"ok": False, "error": "account_id required"})
                    return
                restart = body.get("restart_listen", True)
                self._send(200, switch_active_account(account_id, restart_listen=bool(restart)))
                return

            if path == "/api/accounts/create":
                label = str(body.get("label") or "新账号")
                self._send(200, create_account_api(label=label))
                return

            if path == "/api/accounts/logout":
                account_id = str(body.get("account_id") or "")
                backup = body.get("backup", True)
                self._send(200, logout_account_api(account_id or None, backup=bool(backup)))
                return

            if path == "/api/accounts/remove":
                account_id = str(body.get("account_id") or "")
                backup = body.get("backup", True)
                confirm = bool(body.get("confirm"))
                self._send(200, remove_account_api(account_id or None, backup=bool(backup), confirm=confirm))
                return

            if path == "/api/ai/suggest":
                self._send(200, ai_suggest(body))
                return

            if path == "/api/conversations/ack":
                uid = str(body.get("user_id") or "")
                if uid:
                    aid = _active_account_id()
                    with _unread_lock:
                        acct_bumps = _unread_bump.get(aid)
                        if acct_bumps is not None:
                            acct_bumps.pop(uid, None)
                self._send(200, {"ok": True, "user_id": uid})
                return

            if path == "/api/protocol/prepare":
                from pigeon_protocol.foundation.pure_prepare import prepare_pure_runtime
                from pigeon_protocol.session import load_session

                session = load_session()
                report = prepare_pure_runtime(session, probe_ws=False)
                self._send(200, {"ok": bool(report.get("ok")), "report": report, "ready": bool(report.get("ok"))})
                return

            if path == "/api/session-pack/export":
                from pathlib import Path

                from pigeon_protocol.account_context import session_pack_file
                from pigeon_protocol.session_portable import export_session_pack

                dest = str(body.get("path") or body.get("file") or session_pack_file())
                report = export_session_pack(Path(dest))
                self._send(200, report)
                return

            if path == "/api/session-pack/import":
                from pathlib import Path

                from pigeon_protocol.session_portable import import_session_pack

                src = str(body.get("path") or body.get("file") or "")
                if not src:
                    self._send(400, {"ok": False, "error": "path required"})
                    return
                report = import_session_pack(
                    Path(src),
                    run_prepare=not body.get("no_prepare"),
                    set_active=bool(body.get("set_active", False)),
                )
                self._send(200, report)
                return

            if path == "/api/session/bootstrap":
                from pigeon_protocol.go_bridge import handle as bridge_handle

                self._send(200, bridge_handle("session_bootstrap", body))
                return

            if path == "/api/session/renew":
                from pigeon_protocol.go_bridge import handle as bridge_handle

                self._send(200, bridge_handle("session_renew", body))
                return

            if path == "/api/session/keepalive":
                from pigeon_protocol.go_bridge import handle as bridge_handle

                self._send(200, bridge_handle("session_keepalive", body))
                return

            if path == "/api/cdp-warm/start":
                from pigeon_protocol.go_bridge import handle as bridge_handle

                self._send(200, bridge_handle("cdp_warm_start", body))
                return

            if path == "/api/cdp-warm/status":
                from pigeon_protocol.go_bridge import handle as bridge_handle

                self._send(200, bridge_handle("cdp_warm_status", body))
                return

            if path == "/api/cdp-onboard/start":
                from pigeon_protocol.go_bridge import handle as bridge_handle

                self._send(200, bridge_handle("cdp_onboard_start", body))
                return

            if path == "/api/cdp-onboard/status":
                from pigeon_protocol.go_bridge import handle as bridge_handle

                self._send(200, bridge_handle("cdp_onboard_status", body))
                return

            self._send(404, {"ok": False, "error": "not found"})
        except Exception as exc:
            logger.exception("POST %s", path)
            self._send(500, {"ok": False, "error": str(exc)})


def serve(host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> None:
    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")
    _ensure_accounts()

    def _warm_startup() -> None:
        try:
            from pigeon_protocol.session_startup import bootstrap_on_startup

            boot = bootstrap_on_startup(auto_import_pack=True, export_if_ready=False)
            logger.info(
                "startup bootstrap ok=%s send_ready=%s steps=%s",
                boot.get("ok"),
                boot.get("send_ready"),
                boot.get("steps"),
            )
        except Exception as exc:
            logger.warning("startup bootstrap skipped: %s", exc)
        try:
            from pigeon_protocol.foundation.pure_prepare import prepare_pure_runtime
            from pigeon_protocol.session import load_session

            session = load_session()
            report = prepare_pure_runtime(session, probe_ws=False)
            logger.info("startup prepare_pure ok=%s steps=%s", report.get("ok"), report.get("steps"))
        except Exception as exc:
            logger.warning("startup prepare_pure skipped: %s", exc)

    threading.Thread(target=_warm_startup, daemon=True, name="pigeon-startup-warm").start()

    httpd = ThreadingHTTPServer((host, port), ApiHandler)
    logger.info("Pigeon API http://%s:%s", host, port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    serve()
