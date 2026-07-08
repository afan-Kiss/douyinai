"""curl_cffi + bdms sign + relay headers — all backstage HTTP goes here."""
from __future__ import annotations

import logging
from typing import Any

from pigeon_protocol.foundation.bdms_sign import persist_tokens_to_session, sign_backstage_url
from pigeon_protocol.foundation.types import RelayResponse

logger = logging.getLogger("pigeon.foundation.relay")


class BackstageRelayClient:
    """
    Layer-2 HTTP client for pigeon.jinritemai.com backstage APIs.

    Flow: unsigned URL → bdms sign → relay headers (live CSRF) → curl_cffi
    """

    def __init__(self, session, *, impersonate: str | None = None) -> None:
        from pigeon_protocol.http_client import DEFAULT_CURL_IMPERSONATE

        self.session = session
        self.impersonate = impersonate or DEFAULT_CURL_IMPERSONATE

    def available(self) -> bool:
        from pigeon_protocol.foundation.bdms_sign import sign_available
        from pigeon_protocol.http_transport import curl_cffi_available

        return curl_cffi_available() and sign_available()

    def request(
        self,
        method: str,
        unsigned_url: str,
        *,
        json_body: dict[str, Any] | None = None,
        via: str = "relay",
        retry_on_csrf: bool = True,
    ) -> RelayResponse:
        from pigeon_protocol.http_transport import request_json
        from pigeon_protocol.order_relay_headers import build_order_relay_headers
        from pigeon_protocol.session_health import ensure_order_ready

        if not self.available():
            return RelayResponse(ok=False, error="relay unavailable", via=via)

        if method.upper() == "POST":
            ensure_order_ready(self.session)

        sign = sign_backstage_url(unsigned_url, method=method, body=json_body)
        if not sign.ok:
            return RelayResponse(ok=False, error=sign.error or "sign failed", via=via, sign=sign)

        persist_tokens_to_session(self.session, sign)
        hdr = build_order_relay_headers(self.session, for_method=method.upper())
        from pigeon_protocol.whale_urls import is_whale_backstage_url

        if is_whale_backstage_url(unsigned_url):
            im_ver = str(self.session.query_tokens.get("im_pc_version") or "")
            if not im_ver:
                from pigeon_protocol.whale_version import resolve_whale_versions

                im_ver = resolve_whale_versions(session=self.session).get("im_pc_version") or ""
            if im_ver:
                hdr["X-IM-PC-Version"] = im_ver

        raw = request_json(
            method.upper(),
            sign.signed_url,
            headers=hdr,
            json_body=json_body,
            transport="curl_cffi",
            impersonate=self.impersonate,
        )
        from pigeon_protocol.foundation.bdms_tokens import persist_ms_token_from_response

        persist_ms_token_from_response(self.session, raw.get("headers") if isinstance(raw.get("headers"), dict) else None)
        resp = RelayResponse(
            ok=bool(raw.get("ok")),
            status=int(raw.get("status") or 0),
            data=raw.get("data") if isinstance(raw.get("data"), dict) else {"payload": raw.get("data")},
            via=via,
            url=str(raw.get("url") or sign.signed_url),
            headers={k: str(v) for k, v in (raw.get("headers") or {}).items()},
            sign=sign,
        )
        if isinstance(raw.get("data"), dict):
            resp.data = raw["data"]

        if retry_on_csrf and method.upper() == "POST" and resp.api_code() == "10001010A":
            from pigeon_protocol.pure_config import prefer_python_abogus

            logger.warning("%s anti-bot retry (fresh csrf + re-sign)", via)
            hdr = build_order_relay_headers(self.session, force_refresh=True)
            if is_whale_backstage_url(unsigned_url):
                im_ver = str(self.session.query_tokens.get("im_pc_version") or "")
                if im_ver:
                    hdr["X-IM-PC-Version"] = im_ver
            sign = sign_backstage_url(
                unsigned_url,
                method=method,
                body=json_body,
                prefer_python=prefer_python_abogus(),
            )
            persist_tokens_to_session(self.session, sign)
            raw = request_json(
                method.upper(),
                sign.signed_url,
                headers=hdr,
                json_body=json_body,
                transport="curl_cffi",
                impersonate=self.impersonate,
            )
            resp = RelayResponse(
                ok=bool(raw.get("ok")),
                status=int(raw.get("status") or 0),
                data=raw.get("data") if isinstance(raw.get("data"), dict) else {},
                via=f"{via}/retry",
                url=str(raw.get("url") or sign.signed_url),
                sign=sign,
            )

        if retry_on_csrf and method.upper() == "GET" and resp.api_code() == "11001" and is_whale_backstage_url(unsigned_url):
            from pigeon_protocol.whale_version import resolve_whale_versions

            logger.warning("%s whale 11001 retry (fresh csrf + node sign)", via)
            vers = resolve_whale_versions(session=self.session)
            if vers.get("whale_v"):
                self.session.query_tokens["whale_v"] = vers["whale_v"]
            if vers.get("im_pc_version"):
                self.session.query_tokens["im_pc_version"] = vers["im_pc_version"]

            hdr = build_order_relay_headers(self.session, force_refresh=True, for_method="GET")
            im_ver = str(self.session.query_tokens.get("im_pc_version") or vers.get("im_pc_version") or "")
            if im_ver:
                hdr["X-IM-PC-Version"] = im_ver

            sign = sign_backstage_url(
                unsigned_url,
                method=method,
                body=json_body,
                prefer_python=False,
            )
            persist_tokens_to_session(self.session, sign)
            raw = request_json(
                method.upper(),
                sign.signed_url,
                headers=hdr,
                json_body=json_body,
                transport="curl_cffi",
                impersonate=self.impersonate,
            )
            resp = RelayResponse(
                ok=bool(raw.get("ok")),
                status=int(raw.get("status") or 0),
                data=raw.get("data") if isinstance(raw.get("data"), dict) else {},
                via=f"{via}/whale_retry",
                url=str(raw.get("url") or sign.signed_url),
                sign=sign,
            )

        if resp.api_code():
            resp.ok = resp.api_ok()
        else:
            resp.ok = bool(raw.get("ok")) and resp.status == 200
        return resp

    def get(self, unsigned_url: str, *, via: str = "relay/get") -> RelayResponse:
        return self.request("GET", unsigned_url, via=via)

    def post(
        self,
        unsigned_url: str,
        body: dict[str, Any],
        *,
        via: str = "relay/post",
    ) -> RelayResponse:
        return self.request("POST", unsigned_url, json_body=body, via=via)
