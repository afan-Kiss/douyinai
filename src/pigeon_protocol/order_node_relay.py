"""Order query via foundation relay (Python a_bogus + curl_cffi)."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("pigeon.order_relay")


def query_orders_via_relay(session, security_user_id: str) -> dict[str, Any]:
    from pigeon_protocol.config import ORDER_QUERY_PATH, PIGEON_HOST
    from pigeon_protocol.foundation.relay_client import BackstageRelayClient
    from pigeon_protocol.http_transport import order_api_ok
    from pigeon_protocol.whale_params import backstage_query_base

    client = BackstageRelayClient(session)
    if not client.available():
        return {"ok": False, "error": "relay unavailable", "via": "python_relay"}

    unsigned = f"{PIGEON_HOST}{ORDER_QUERY_PATH}?{backstage_query_base(session=session)}"
    body = {
        "security_user_id": security_user_id,
        "page_no": 0,
        "page_size": 5,
        "search_words": "",
        "is_init_tab": 0,
        "tab_type": 1,
        "biz_type": 2,
        "open_params": {},
        "workstation_opt_version": "v2",
        "service_entity_id": "",
        "version": "1.0",
        "workstation_opt_gray": True,
    }

    relay = client.post(unsigned, body, via="python_relay")
    raw: dict[str, Any] = {
        "ok": relay.ok,
        "status": relay.status,
        "url": relay.url,
        "data": relay.data,
        "via": relay.via,
        "headers": relay.headers,
    }
    if relay.sign:
        raw["_sign"] = relay.sign.tokens

    if not order_api_ok(raw):
        logger.warning("python_relay order failed code=%s", relay.api_code())
    return raw


def query_orders_via_node_relay(session, security_user_id: str) -> dict[str, Any]:
    """Deprecated alias — runtime uses Python relay only in pure mode."""
    return query_orders_via_relay(session, security_user_id)
