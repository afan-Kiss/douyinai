"""抖店/飞鸽 QR 协议登录 — doudian-sso POST (aid=4272)，对齐 登录.har。"""
from __future__ import annotations

import base64
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger("pigeon.qr_login")

FXG_HOST = "https://fxg.jinritemai.com"
SSO_HOST = "https://doudian-sso.jinritemai.com"
IM_HOST = "https://im.jinritemai.com"
FEIGE_WORKSPACE = f"{IM_HOST}/pc_seller_v2/main/workspace"

DOUDIAN_AID = 4272
DEFAULT_SUBJECT_AID = 4966

SSO_GET_QR = f"{SSO_HOST}/passport/sso/get_qrcode/"
SSO_CHECK_QR = f"{SSO_HOST}/passport/sso/check_qrconnect/"
SSO_SUBJECT_LOGIN = f"{SSO_HOST}/passport/sso/aff/subject/login/"

LOGIN_COMMON = f"{FXG_HOST}/login/common"
LOGIN_REFERER = f"{FXG_HOST}/login/common?channel=zhaoshang"

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "analysis" / "login_qr_template.json"

QR_NEW = "1"
QR_SCANNED = "2"
QR_CONFIRMED = "3"
QR_EXPIRED = "5"

# Cookies that must stay in sync after SSO — stale values cause pigeon backstage 10005.
_AUTH_COOKIE_KEYS: frozenset[str] = frozenset(
    {
        "sessionid",
        "sessionid_ss",
        "sid_tt",
        "sid_guard",
        "sid_ucp_v1",
        "ssid_ucp_v1",
        "sid_ucp_sso_v1_doudian",
        "ssid_ucp_sso_v1_doudian",
        "passport_csrf_token",
        "passport_csrf_token_default",
        "passport_auth_status",
        "passport_auth_status_ss",
        "csrf_session_id",
        "PHPSESSID",
        "PHPSESSID_SS",
        "uid_tt",
        "uid_tt_ss",
        "odin_tt",
        "ttwid",
        "toutiao_sso_user_doudian",
        "toutiao_sso_user_ss_doudian",
        "sso_uid_tt_doudian",
        "sso_uid_tt_ss_doudian",
        "ucas_c0",
        "ucas_c0_ss",
        "ucas_sso_c0_doudian",
        "ucas_sso_c0_ss_doudian",
        "SHOP_ID",
        "PIGEON_CID",
        "has_biz_token",
    }
)
# Shop/device identity — keep when QR SSO omits them (complete_fxg_login partial).
_PRESERVE_IF_MISSING: frozenset[str] = frozenset(
    {"SHOP_ID", "PIGEON_CID", "ecom_gray_shop_id", "odin_tt", "ttwid"}
)

_SSO_PROGRESS_KEYS: frozenset[str] = frozenset(
    {
        "sessionid",
        "sid_tt",
        "toutiao_sso_user_doudian",
        "toutiao_sso_user_ss_doudian",
        "ssid_ucp_sso_v1_doudian",
        "sid_ucp_sso_v1_doudian",
        "passport_auth_status",
        "passport_auth_status_ss",
    }
)


def qr_sso_cookies_ready(cookies: dict[str, Any]) -> bool:
    return any(cookies.get(k) for k in _SSO_PROGRESS_KEYS)


def qr_session_cookies_ready(cookies: dict[str, Any]) -> bool:
    return bool(cookies.get("sessionid") or cookies.get("sid_tt"))


def _extract_har_template(har_path: Path) -> dict[str, Any]:
    from urllib.parse import unquote

    har = json.loads(har_path.read_text(encoding="utf-8"))
    out: dict[str, Any] = {}
    for entry in har["log"]["entries"]:
        req = entry["request"]
        url = req["url"]
        if "doudian-sso" not in url or "get_qrcode" not in url:
            continue
        qs = parse_qs(urlparse(url).query)
        for k in ("fp", "account_sdk_source_info", "msToken", "a_bogus"):
            if k in qs and qs[k][0]:
                out[k] = qs[k][0]
        post = (req.get("postData") or {}).get("text") or ""
        if post:
            body = parse_qs(post)
            for k in ("service", "biz_extra", "ewid", "web_did", "pc_did"):
                if k in body and body[k][0]:
                    out[k] = unquote(body[k][0]) if k == "biz_extra" else body[k][0]
        break
    return out


@dataclass
class LoginTemplate:
    """Static fields captured from 登录.har (account_sdk_source_info, biz_extra, …)."""

    sso_host: str = SSO_HOST
    aid: int = DOUDIAN_AID
    subject_aid: int = DEFAULT_SUBJECT_AID
    service: str = LOGIN_COMMON
    referer: str = LOGIN_REFERER
    origin: str = FXG_HOST
    account_sdk_source_info: str = ""
    biz_extra: str = ""
    fp: str = ""

    @classmethod
    def load(cls, har_path: Path | None = None) -> LoginTemplate:
        if har_path and har_path.is_file():
            data = _extract_har_template(har_path)
        elif TEMPLATE_PATH.is_file():
            data = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
        else:
            data = {}
        biz = data.get("biz_extra") or json.dumps(
            {
                "referer": LOGIN_COMMON,
                "path": "/login/common",
                "domain": "fxg.jinritemai.com",
            },
            separators=(",", ":"),
        )
        return cls(
            sso_host=str(data.get("sso_host") or SSO_HOST),
            aid=int(data.get("aid") or DOUDIAN_AID),
            subject_aid=int(data.get("subject_aid") or DEFAULT_SUBJECT_AID),
            service=str(data.get("service") or LOGIN_COMMON),
            referer=str(data.get("referer") or LOGIN_REFERER),
            origin=str(data.get("origin") or FXG_HOST),
            account_sdk_source_info=str(data.get("account_sdk_source_info") or ""),
            biz_extra=biz if isinstance(biz, str) else json.dumps(biz, separators=(",", ":")),
            fp=str(data.get("fp") or ""),
        )

    def default_biz_extra(self) -> str:
        return self.biz_extra


@dataclass
class QrLoginState:
    token: str = ""
    qrcode_b64: str = ""
    qrcode_index_url: str = ""
    status: str = ""
    redirect_url: str = ""
    login_subject_uid: str = ""
    user_identity_id: str = ""
    cookies: dict[str, str] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": self.token[:24] + "…" if len(self.token) > 24 else self.token,
            "status": self.status,
            "has_qrcode": bool(self.qrcode_b64),
            "redirect_url": self.redirect_url[:120] if self.redirect_url else "",
            "cookies": len(self.cookies),
            "error": self.error,
        }


class DoudianSsoQrLoginClient:
    """Pure HTTP 抖店扫码登录 (doudian-sso POST) → cookie jar → session.json."""

    def __init__(
        self,
        *,
        impersonate: str = "chrome131",
        user_agent: str = DEFAULT_UA,
        template: LoginTemplate | None = None,
        har_path: Path | None = None,
    ) -> None:
        self.impersonate = impersonate
        self.user_agent = user_agent
        self.template = template or LoginTemplate.load(har_path)
        self.web_did = secrets.token_hex(16)
        self._session = None
        self._active_token: str = ""

    def seed_from_session(self, session) -> None:
        """Load existing session cookies into the QR curl jar (for post-login warm-up)."""
        c = self._client()
        for name, value in (getattr(session, "cookies", None) or {}).items():
            if name and value is not None:
                try:
                    c.cookies.set(str(name), str(value), domain=".jinritemai.com")
                except Exception:
                    pass

    def _client(self):
        if self._session is None:
            from curl_cffi import requests as curl_requests

            self._session = curl_requests.Session(impersonate=self.impersonate)
            self._session.headers.update(
                {
                    "User-Agent": self.user_agent,
                    "Referer": self.template.referer,
                    "Origin": self.template.origin,
                    "Accept": "application/json, text/plain, */*",
                }
            )
        return self._session

    def _cookie_dict(self) -> dict[str, str]:
        try:
            return dict(self._client().cookies.get_dict())
        except Exception:
            return {}

    def _fp(self) -> str:
        cookies = self._cookie_dict()
        return (
            cookies.get("s_v_web_id")
            or cookies.get("verifyFp")
            or self.template.fp
            or ""
        )

    def _query_params(self, *, account_sdk_source: str = "web") -> dict[str, str]:
        params: dict[str, str] = {
            "aid": str(self.template.aid),
            "language": "zh",
            "account_sdk_source": account_sdk_source,
        }
        fp = self._fp()
        if fp:
            params["fp"] = fp
        if self.template.account_sdk_source_info:
            params["account_sdk_source_info"] = self.template.account_sdk_source_info
        return params

    def _form_body(self, *, token: str = "") -> dict[str, str]:
        body = {
            "service": self.template.service,
            "biz_extra": self.template.default_biz_extra(),
            "ewid": self.web_did,
            "web_did": self.web_did,
            "pc_did": "",
            "seraph_did": "",
        }
        if token:
            body["token"] = token
        return body

    def warmup(self) -> None:
        c = self._client()
        c.get(self.template.referer, timeout=20)
        c.get(LOGIN_COMMON, timeout=20)

    def fetch_qrcode(self, *, qrcode_path: Path | None = None) -> QrLoginState:
        self.warmup()
        c = self._client()
        r = c.post(
            SSO_GET_QR,
            params=self._query_params(),
            data=self._form_body(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=20,
        )
        state = self._parse_qrcode_response(r)
        if state.token:
            self._active_token = state.token
        if state.qrcode_b64 and qrcode_path:
            self._save_qrcode(state.qrcode_b64, qrcode_path)
        return state

    def _parse_qrcode_response(self, r) -> QrLoginState:
        state = QrLoginState(cookies=self._cookie_dict())
        try:
            body = r.json()
        except Exception as exc:
            state.error = f"invalid json: {exc}"
            return state

        data = body.get("data") or {}
        err = body.get("error_code") or data.get("error_code")
        if err not in (0, "0", None):
            state.error = str(data.get("description") or body.get("message") or f"error_code={err}")
            return state

        state.token = str(data.get("token") or "")
        state.qrcode_b64 = str(data.get("qrcode") or "")
        state.qrcode_index_url = str(data.get("qrcode_index_url") or "")
        state.status = QR_NEW
        state.cookies = self._cookie_dict()
        if not state.token:
            state.error = "missing token in get_qrcode response"
        return state

    def check_once(self, token: str = "") -> QrLoginState:
        token = token or self._active_token
        if not token:
            return QrLoginState(error="missing qr token")
        r = self._client().post(
            SSO_CHECK_QR,
            params=self._query_params(),
            data=self._form_body(token=token),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=20,
        )
        state = QrLoginState(token=token, cookies=self._cookie_dict())
        try:
            body = r.json()
        except Exception as exc:
            state.error = f"invalid json: {exc}"
            return state

        data = body.get("data") or body
        err = body.get("error_code") or data.get("error_code")
        if err not in (0, "0", None):
            desc = str(data.get("description") or body.get("message") or f"error_code={err}")
            if "过期" in desc or "expired" in desc.lower():
                state.status = QR_EXPIRED
                state.error = ""
                state.cookies = self._cookie_dict()
                return state
            state.error = desc
            return state

        state.status = str(data.get("status") if data.get("status") is not None else "")
        if state.status.endswith(".0") and state.status[:-2].isdigit():
            state.status = state.status[:-2]
        state.redirect_url = str(
            data.get("redirect_url") or data.get("redirect_uri") or body.get("redirect_url") or ""
        )
        state.login_subject_uid = str(data.get("login_subject_uid") or data.get("subject_uid") or "")
        state.user_identity_id = str(data.get("user_identity_id") or "")
        state.cookies = self._cookie_dict()
        return state

    def burst_confirm_poll(
        self,
        token: str,
        *,
        rounds: int = 35,
        interval_sec: float = 0.18,
    ) -> QrLoginState | None:
        """手机点确认后常会错过 status=3 直接变 5，连扫抓 redirect / 3。"""
        last: QrLoginState | None = None
        for _ in range(max(1, rounds)):
            st = self.check_once(token)
            last = st
            if st.error:
                return None
            if st.status == QR_CONFIRMED:
                return st
            if st.status == QR_EXPIRED:
                redirect = str(st.redirect_url or "").strip()
                if redirect:
                    st.status = QR_CONFIRMED
                    return st
                cookies = self._cookie_dict()
                if qr_session_cookies_ready(cookies) or qr_sso_cookies_ready(cookies):
                    st.status = QR_CONFIRMED
                    st.cookies = cookies
                    return st
                finalized = self.finalize_confirmed_login(st)
                if finalized:
                    return finalized
            time.sleep(interval_sec)
        return last

    def finalize_confirmed_login(self, hint: QrLoginState) -> QrLoginState | None:
        """status=5 或错过 status=3 时，用 redirect / subject_uid / SSO cookie 补全登录。"""
        redirect = str(hint.redirect_url or "").strip()
        subject = str(hint.login_subject_uid or "").strip()
        cookies = dict(hint.cookies or self._cookie_dict())
        if not redirect and not subject and not qr_sso_cookies_ready(cookies):
            return None
        st = QrLoginState(
            token=str(hint.token or self._active_token or ""),
            status=QR_CONFIRMED,
            redirect_url=redirect,
            login_subject_uid=subject,
            user_identity_id=str(hint.user_identity_id or ""),
            cookies=cookies,
        )
        try:
            st.cookies = self.complete_fxg_login(st)
            st.cookies.update(self.open_feige_workspace())
            if qr_session_cookies_ready(st.cookies) or qr_sso_cookies_ready(st.cookies):
                return st
        except Exception as exc:
            logger.warning("finalize_confirmed_login: %s", exc)
        return None

    def follow_redirect(self, redirect_url: str) -> dict[str, str]:
        if not redirect_url:
            return self._cookie_dict()
        self._client().get(redirect_url, timeout=30, allow_redirects=True)
        return self._cookie_dict()

    def _ticket_from_url(self, url: str) -> str:
        if not url:
            return ""
        qs = parse_qs(urlparse(url).query)
        for key in ("ticket",):
            if key in qs and qs[key][0]:
                return qs[key][0]
        return ""

    def complete_fxg_login(self, qr_state: QrLoginState) -> dict[str, str]:
        """Post-QR: ticket callback → optional subject login → fxg home (对齐 登录.har)."""
        c = self._client()
        redirect = qr_state.redirect_url

        if redirect:
            c.get(redirect, timeout=30, allow_redirects=True)
            ticket = self._ticket_from_url(redirect)
            if ticket and FXG_HOST in redirect:
                c.get(
                    f"{FXG_HOST}/passport/sso/login/callback/",
                    params={"next": self.template.service, "ticket": ticket},
                    timeout=30,
                    allow_redirects=True,
                )
        else:
            ticket = self._ticket_from_url(qr_state.redirect_url)
            if ticket:
                c.get(
                    f"{FXG_HOST}/index/login",
                    params={"next": self.template.service, "ticket": ticket},
                    timeout=30,
                    allow_redirects=True,
                )
                c.get(
                    f"{FXG_HOST}/passport/sso/login/callback/",
                    params={"next": self.template.service, "ticket": ticket},
                    timeout=30,
                    allow_redirects=True,
                )

        if qr_state.login_subject_uid:
            sub_params = self._query_params()
            sub_params["subject_aid"] = str(self.template.subject_aid)
            sub_data = {
                "service": f"{FXG_HOST}/login_404_page",
                "subject_aid": str(self.template.subject_aid),
                "mix_mode": "1",
                "login_subject_uid": qr_state.login_subject_uid,
            }
            if qr_state.user_identity_id:
                sub_data["user_identity_id"] = qr_state.user_identity_id
            fp = self._fp()
            if fp:
                sub_data["fp"] = fp
            c.post(
                SSO_SUBJECT_LOGIN,
                params=sub_params,
                data=sub_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
                allow_redirects=True,
            )

        c.get(f"{FXG_HOST}/ffa/mshop/homepage/index", timeout=30, allow_redirects=True)
        return self._cookie_dict()

    def open_feige_workspace(self) -> dict[str, str]:
        from pigeon_protocol.pure_config import cdp_allowed
        from pigeon_protocol.session import load_session, save_session
        from pigeon_protocol.session_renewal import establish_im_session_http

        c = self._client()
        c.get(FEIGE_WORKSPACE, timeout=30, allow_redirects=True)
        c.get(f"{IM_HOST}/pc_seller_v2/main", timeout=30, allow_redirects=True)
        cookies = self._cookie_dict()
        try:
            session = load_session()
            session.cookies.update({k: v for k, v in cookies.items() if v})
            renew = establish_im_session_http(session, persist=True, cdp_fallback=cdp_allowed())
            if renew.get("ok"):
                save_session(session)
            cookies = dict(session.cookies)
        except Exception as exc:
            logger.debug("open_feige_workspace renew: %s", exc)
        return cookies

    def _save_qrcode(self, qrcode_b64: str, path: Path) -> None:
        if not qrcode_b64:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(qrcode_b64))
        logger.info("QR saved: %s", path)

    def _recover_expired(self, token: str, st: QrLoginState, *, on_status: Any | None = None) -> QrLoginState | None:
        redirect = str(st.redirect_url or "").strip()
        if redirect:
            st.status = QR_CONFIRMED
            try:
                st.cookies = self.complete_fxg_login(st)
                st.cookies.update(self.open_feige_workspace())
                return st
            except Exception as exc:
                logger.warning("complete login from redirect: %s", exc)
        cookies = self._cookie_dict()
        if qr_session_cookies_ready(cookies):
            st.status = QR_CONFIRMED
            st.cookies = cookies
            try:
                st.cookies = self.complete_fxg_login(st)
                st.cookies.update(self.open_feige_workspace())
                return st
            except Exception as exc:
                logger.warning("recover login from session cookies: %s", exc)
        finalized = self.finalize_confirmed_login(st)
        if finalized:
            if on_status:
                try:
                    on_status(finalized)
                except Exception:
                    pass
            return finalized
        hit = self.burst_confirm_poll(token, rounds=60, interval_sec=0.1)
        if hit and str(hit.status) == QR_CONFIRMED:
            if on_status:
                try:
                    on_status(hit)
                except Exception:
                    pass
            try:
                hit.cookies = self.complete_fxg_login(hit)
                hit.cookies.update(self.open_feige_workspace())
                return hit
            except Exception as exc:
                logger.warning("complete login after burst: %s", exc)
        finalized = self.finalize_confirmed_login(hit or st)
        if finalized:
            if on_status:
                try:
                    on_status(finalized)
                except Exception:
                    pass
            return finalized
        return None

    def poll_until_done(
        self,
        token: str,
        *,
        timeout_sec: float = 180.0,
        interval_sec: float = 1.5,
        on_status: Any | None = None,
        on_token_refresh: Any | None = None,
        qrcode_path: Path | None = None,
        auto_refresh: bool = True,
        should_stop: Any | None = None,
    ) -> QrLoginState:
        deadline = time.time() + timeout_sec
        last = QrLoginState(token=token, status=QR_NEW)
        qr_path = qrcode_path or __import__(
            "pigeon_protocol.account_context", fromlist=["qr_png_path"]
        ).qr_png_path()
        seen_scanned = False
        poll_started = time.time()
        cur_interval = interval_sec
        while time.time() < deadline:
            if should_stop and should_stop():
                last.error = "cancelled"
                return last
            st = self.check_once(token)
            last = st
            if st.status == QR_SCANNED:
                seen_scanned = True
                cur_interval = min(cur_interval, 0.2)
            if on_status:
                try:
                    on_status(st)
                except Exception:
                    pass
            if st.error and "过期" not in st.error and "expired" not in st.error.lower():
                return st
            if st.error and ("过期" in st.error or "expired" in st.error.lower()):
                st.status = QR_EXPIRED
                st.error = ""
            if st.status == QR_CONFIRMED:
                st.cookies = self.complete_fxg_login(st)
                st.cookies.update(self.open_feige_workspace())
                return st
            if st.status == QR_EXPIRED:
                recovered = self._recover_expired(token, st, on_status=on_status)
                if recovered:
                    return recovered
                elapsed = time.time() - poll_started
                if seen_scanned or elapsed > 8:
                    time.sleep(0.25)
                    continue
                if auto_refresh and time.time() < deadline and not seen_scanned:
                    logger.info("QR expired, fetching new code…")
                    fresh = self.fetch_qrcode(qrcode_path=qr_path)
                    if fresh.error or not fresh.token:
                        st.error = fresh.error or "refresh failed"
                        return st
                    token = fresh.token
                    last = fresh
                    if on_token_refresh:
                        try:
                            on_token_refresh(fresh)
                        except Exception:
                            pass
                    if on_status:
                        try:
                            on_status(fresh)
                        except Exception:
                            pass
                    continue
                st.error = "qrcode expired"
                return st
            time.sleep(cur_interval)
        last.error = "poll timeout"
        return last

    def login_interactive(
        self,
        *,
        qrcode_path: Path | None = None,
        timeout_sec: float = 180.0,
    ) -> QrLoginState:
        state = self.fetch_qrcode()
        if state.error or not state.token:
            return state

        out = qrcode_path or __import__(
            "pigeon_protocol.account_context", fromlist=["qr_png_path"]
        ).qr_png_path()
        self._save_qrcode(state.qrcode_b64, out)

        def _log(st: QrLoginState) -> None:
            labels = {
                QR_NEW: "等待扫码",
                QR_SCANNED: "已扫码，请在手机上确认",
                QR_CONFIRMED: "登录成功",
                QR_EXPIRED: "二维码已过期",
            }
            logger.info("QR status=%s %s", st.status, labels.get(st.status, ""))

        return self.poll_until_done(
            state.token,
            timeout_sec=timeout_sec,
            on_status=_log,
            qrcode_path=out,
            auto_refresh=True,
        )

    def apply_to_session(self, session, qr_state: QrLoginState, *, replace_auth: bool = True) -> None:
        from pigeon_protocol.session import save_session
        from pigeon_protocol.session_backup import backup_session

        if replace_auth and str(qr_state.status or "") == QR_CONFIRMED:
            backup_session(tag="before_qr")

        fresh = {k: str(v) for k, v in (qr_state.cookies or {}).items() if k and v is not None}
        if replace_auth and str(qr_state.status or "") == QR_CONFIRMED:
            for key in _AUTH_COOKIE_KEYS:
                if key in fresh and fresh[key]:
                    session.cookies[key] = fresh[key]
                # Never pop cookies — partial SSO must not wipe existing飞鸽 session
        for k, v in fresh.items():
            if not v:
                continue
            if k in _PRESERVE_IF_MISSING and not v:
                continue
            session.cookies[k] = v

        passport = session.cookies.get("passport_csrf_token") or ""
        csrf_sid = session.cookies.get("csrf_session_id") or ""
        if passport and csrf_sid:
            session.headers["x-secsdk-csrf-token"] = f"000100000001{passport},{csrf_sid}"

        if session.cookies.get("SHOP_ID"):
            session.shop_id = session.cookies["SHOP_ID"]
        if session.cookies.get("s_v_web_id"):
            session.query_tokens["verifyFp"] = session.cookies["s_v_web_id"]
            session.query_tokens["fp"] = session.cookies["s_v_web_id"]
        if session.cookies.get("PIGEON_CID"):
            session.device_id = session.cookies["PIGEON_CID"]

        session.notes.append(
            f"qr_login sso cookies={len(qr_state.cookies)} status={qr_state.status}"
        )
        save_session(session)


# backward-compatible alias
FxgQrLoginClient = DoudianSsoQrLoginClient


def qr_login_to_session(
    session=None,
    *,
    qrcode_path: Path | None = None,
    timeout_sec: float = 180.0,
    har_path: Path | None = None,
) -> dict[str, Any]:
    """High-level: QR login → merge session → auto-heal tokens."""
    from pigeon_protocol.session import load_session

    session = session or load_session()
    client = DoudianSsoQrLoginClient(har_path=har_path)
    state = client.login_interactive(qrcode_path=qrcode_path, timeout_sec=timeout_sec)
    report: dict[str, Any] = {
        "qr": state.to_dict(),
        "sso_host": SSO_HOST,
        "har_template": str(TEMPLATE_PATH) if TEMPLATE_PATH.is_file() else None,
    }

    if state.status == QR_CONFIRMED and state.cookies:
        from pigeon_protocol.session_portable import post_login_bootstrap

        report["post_login"] = post_login_bootstrap(
            session, qr_client=client, qr_state=state, skip_fxg_complete=True
        )
        report["send_ready"] = report["post_login"].get("send_ready")
        report["listen_ready"] = report["post_login"].get("listen_ready")
        report["blockers"] = report["post_login"].get("blockers")
        report["ok"] = bool(report["post_login"].get("ok"))
    else:
        report["ok"] = False
        report["error"] = state.error or f"status={state.status}"

    return report
