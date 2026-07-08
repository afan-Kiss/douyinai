from __future__ import annotations

import json
from typing import Any

from pigeon_protocol.http_transport import curl_cffi_available, order_api_ok, request_json
from pigeon_protocol.config import (
    CONV_LIST_PATH,
    DEFAULT_USER_AGENT,
    HISTORY_MSG_PATH,
    IM_HOST,
    ORDER_QUERY_PATH,
    PIGEON_HOST,
    USER_CARD_PATH,
)
from pigeon_protocol.models import ConversationContext, OrderContext
from pigeon_protocol.order_parse import parse_order_response
from pigeon_protocol.session import SessionState, build_signed_url, save_session

# Chrome client hints — required for order/query anti-bot with curl_cffi
_CHROME_SEC_HEADERS = {
    "sec-ch-ua": '"Chromium";v="131", "Google Chrome";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

DEFAULT_CURL_IMPERSONATE = "chrome131"


class BackstageHttpClient:
    """飞鸽 backstage HTTP — 纯协议，不经过浏览器。"""

    def __init__(
        self,
        session: SessionState,
        *,
        timeout: float = 15.0,
        dry_run: bool = False,
        use_cdp_sign: bool = False,
        use_curl_cffi: bool = True,
    ) -> None:
        self.session = session
        self.timeout = timeout
        self.dry_run = dry_run
        self.use_cdp_sign = use_cdp_sign
        self.use_curl_cffi = use_curl_cffi

    def _headers(self, referer: str | None = None, *, browser_hints: bool = False) -> dict[str, str]:
        headers = {
            "User-Agent": self.session.user_agent or DEFAULT_USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Referer": referer or f"{IM_HOST}/pc_seller_v2/main/workspace",
            "Origin": IM_HOST,
        }
        if browser_hints:
            headers.update(_CHROME_SEC_HEADERS)
        headers.update(self.session.headers)
        cookie = self.session.cookie_header()
        if cookie:
            headers["Cookie"] = cookie
        return headers

    def _request(
        self,
        method: str,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
        referer: str | None = None,
        transport: str = "httpx",
        browser_hints: bool = False,
        impersonate: str = DEFAULT_CURL_IMPERSONATE,
    ) -> dict[str, Any]:
        if self.dry_run:
            return {
                "ok": False,
                "dry_run": True,
                "method": method,
                "url": url,
                "body": json_body,
                "message": "dry_run enabled — no network call",
            }

        return request_json(
            method,
            url,
            headers=self._headers(referer, browser_hints=browser_hints),
            json_body=json_body,
            timeout=self.timeout,
            transport=transport,
            impersonate=impersonate,
        )

    def _cdp_fetch_json(
        self,
        url: str,
        *,
        method: str = "POST",
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from pigeon_protocol.cdp_bridge import CdpBridge

        return CdpBridge(self.session).fetch_json(url=url, method=method, body=body)

    def query_orders(
        self,
        security_user_id: str,
        *,
        page_no: int = 0,
        page_size: int = 5,
        tab_type: int = 1,
    ) -> OrderContext:
        base = f"{PIGEON_HOST}{ORDER_QUERY_PATH}"
        from pigeon_protocol.whale_params import backstage_query_base

        unsigned = f"{base}?{backstage_query_base(session=self.session)}"
        body = {
            "security_user_id": security_user_id,
            "page_no": page_no,
            "page_size": page_size,
            "search_words": "",
            "is_init_tab": 0,
            "tab_type": tab_type,
            "biz_type": 2,
            "open_params": {},
            "workstation_opt_version": "v2",
            "service_entity_id": "",
            "version": "1.0",
            "workstation_opt_gray": True,
        }
        if self.use_cdp_sign:
            raw = self._cdp_fetch_json(unsigned, method="POST", body=body)
            return parse_order_response(raw, source="cdp/order/query")

        source = "backstage/order/query"
        result: dict[str, Any]

        if self.use_curl_cffi and curl_cffi_available():
            from pigeon_protocol.foundation.bdms_sign import persist_tokens_to_session, sign_backstage_url
            from pigeon_protocol.order_relay_headers import build_order_relay_headers
            from pigeon_protocol.session_health import ensure_order_ready

            ensure_order_ready(self.session)
            sign = sign_backstage_url(unsigned, method="POST", body=body)
            if sign.ok:
                persist_tokens_to_session(self.session, sign)
                hdr = build_order_relay_headers(self.session, for_method="POST")
                result = request_json(
                    "POST",
                    sign.signed_url,
                    headers=hdr,
                    json_body=body,
                    timeout=self.timeout,
                    transport="curl_cffi",
                    impersonate=DEFAULT_CURL_IMPERSONATE,
                )
                source = "backstage/order/query+relay"
                if not order_api_ok(result):
                    data = result.get("data") if isinstance(result.get("data"), dict) else {}
                    if str(data.get("code", "")) == "10001010A":
                        hdr = build_order_relay_headers(self.session, force_refresh=True, for_method="POST")
                        sign = sign_backstage_url(unsigned, method="POST", body=body)
                        if sign.ok:
                            persist_tokens_to_session(self.session, sign)
                            result = request_json(
                                "POST",
                                sign.signed_url,
                                headers=hdr,
                                json_body=body,
                                timeout=self.timeout,
                                transport="curl_cffi",
                                impersonate=DEFAULT_CURL_IMPERSONATE,
                            )
                            source = "backstage/order/query+relay/retry"
            else:
                result = {"ok": False, "data": {"code": sign.error or "sign_failed"}, "error": sign.error}
        else:
            url = build_signed_url(unsigned, self.session)
            result = self._request("POST", url, json_body=body)

        if not order_api_ok(result) and self.use_curl_cffi and curl_cffi_available():
            url = build_signed_url(unsigned, self.session)
            curl_result = self._request(
                "POST",
                url,
                json_body=body,
                transport="curl_cffi",
                browser_hints=True,
            )
            if order_api_ok(curl_result):
                result = curl_result
                source = "backstage/order/query+curl_cffi"

        headers = result.get("headers") or {}
        new_ms = headers.get("x-ms-token") or headers.get("X-Ms-Token")
        if isinstance(new_ms, str) and new_ms:
            self.session.query_tokens["msToken"] = new_ms
            try:
                save_session(self.session)
            except Exception:
                pass
        return parse_order_response(result, source=source)

    def fetch_history_messages(
        self,
        *,
        conversation_id: str = "",
        security_user_id: str = "",
        cursor: str = "",
        size: int = 20,
    ) -> ConversationContext:
        base = f"{PIGEON_HOST}{HISTORY_MSG_PATH}"
        unsigned = f"{base}?biz_type=4&PIGEON_BIZ_TYPE=2"
        body: dict[str, Any] = {
            "cursor": cursor,
            "size": size,
            "direction": 1,
            "version": "1.0",
        }
        if conversation_id:
            body["conversation_id"] = conversation_id
        if security_user_id:
            body["security_user_id"] = security_user_id

        if self.use_cdp_sign:
            result = self._cdp_fetch_json(unsigned, method="POST", body=body)
        else:
            url = build_signed_url(unsigned, self.session)
            result = self._request("POST", url, json_body=body)
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        inner = data.get("data") if isinstance(data.get("data"), dict) else data
        msg_list = inner.get("msg_body_list") or inner.get("messages") or []
        messages: list[dict[str, Any]] = []
        if isinstance(msg_list, list):
            for item in msg_list:
                if not isinstance(item, dict):
                    continue
                ext = item.get("ext") if isinstance(item.get("ext"), dict) else {}
                messages.append(
                    {
                        "role": "customer" if str(item.get("sender_role") or ext.get("sender_role")) == "1" else "service",
                        "text": str(item.get("content") or "").strip(),
                        "time": str(item.get("create_time") or ext.get("create_time") or ""),
                        "message_id": str(item.get("server_msg_id") or item.get("msg_id") or ""),
                    }
                )
        return ConversationContext(
            conversation_id=conversation_id,
            security_user_id=security_user_id,
            shop_id=self.session.shop_id,
            buyer_name="",
            messages=[m for m in messages if m.get("text")],
            source="backstage/get_history_msg_sub",
            raw=result,
        )

    def fuzzy_search_conversations(self, *, page: int = 0, size: int = 20) -> dict[str, Any]:
        base = f"{PIGEON_HOST}{CONV_LIST_PATH}"
        unsigned = f"{base}?biz_type=4&PIGEON_BIZ_TYPE=2"
        body = {"page": page, "size": size, "version": "1.0"}
        if self.use_cdp_sign:
            return self._cdp_fetch_json(unsigned, method="POST", body=body)
        url = build_signed_url(unsigned, self.session)
        return self._request("POST", url, json_body=body)

    def get_user_card(self, security_user_id: str) -> dict[str, Any]:
        base = f"{PIGEON_HOST}{USER_CARD_PATH}"
        unsigned = f"{base}?biz_type=4&PIGEON_BIZ_TYPE=2&security_user_id={security_user_id}"
        if self.use_cdp_sign:
            return self._cdp_fetch_json(unsigned, method="GET")
        url = build_signed_url(unsigned, self.session)
        return self._request("GET", url)
