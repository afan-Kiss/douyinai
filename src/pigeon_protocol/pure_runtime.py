"""Pure-protocol runtime — listen/send/context without CDP at call time."""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any, Callable

from pigeon_protocol.config import AppConfig, LIVE_CAPTURES
from pigeon_protocol.context import ContextService
from pigeon_protocol.har_replay import order_from_har_for_user
from pigeon_protocol.models import ConversationContext, InboundMessage, OrderContext, SendResult
from pigeon_protocol.order import OrderService
from pigeon_protocol.send import SendService
from pigeon_protocol.session import SessionState, load_session, save_session
from pigeon_protocol.session_sync import CdpSessionSync
from pigeon_protocol.ws_client import WsListener
from pigeon_protocol.ws_message_store import WsMessageStore

logger = logging.getLogger("pigeon.pure")


class PureProtocolRuntime:
    """
    Orchestrator for pure-protocol operations.

    CDP is only used for optional bootstrap (prepare / sign refresh / orders fallback),
    never for WS send.
    """

    def __init__(self, session: SessionState | None = None, *, config: AppConfig | None = None) -> None:
        self.config = config or AppConfig()
        self.session = session or load_session()
        self.store = WsMessageStore()
        self._reload()

    def _reload(self) -> None:
        self.listener = WsListener(self.session)
        self.sender = SendService(self.session, dry_run=self.config.dry_run)
        self.orders = OrderService(self.session, dry_run=self.config.dry_run)
        self.context = ContextService(self.session, dry_run=self.config.dry_run)

    def prepare(self, *, force_cdp: bool = True) -> dict[str, Any]:
        if force_cdp and CdpSessionSync.available():
            sync = CdpSessionSync(self.session)
            report = sync.sync()
            self.session = load_session()
            self._reload()
            return {"mode": "cdp_sync", **report}
        return {
            "mode": "session_only",
            "cookies": len(self.session.cookies),
            "ws_urls": len(self.session.ws_urls),
        }

    def bootstrap(
        self,
        *,
        prepare: bool = True,
        harvest: bool = True,
        quick: bool = True,
    ) -> dict[str, Any]:
        """
        One-shot setup: CDP session sync + auto-harvest WS templates + sign tokens.
        Requires Feige Chrome (9222) with a buyer chat open for template harvest.
        """
        from pigeon_protocol.ws_template_harvest import (
            DEFAULT_LADDER,
            QUICK_LADDER,
            bootstrap_templates_sync,
            missing_lengths,
        )

        report: dict[str, Any] = {"health_before": self.health()}
        if prepare:
            report["prepare"] = self.prepare(force_cdp=CdpSessionSync.available())

        ladder = QUICK_LADDER if quick else DEFAULT_LADDER
        report["template_missing_before"] = missing_lengths(ladder)
        if harvest and report["template_missing_before"]:
            if CdpSessionSync.available():
                report["template_harvest"] = bootstrap_templates_sync(lengths=list(ladder))
            else:
                report["template_harvest"] = {"error": "CDP not available — skip auto harvest"}

        # Refresh order sign tokens into session (still need browser fetch at call time)
        if CdpSessionSync.available():
            try:
                from pigeon_protocol.sign import CdpSigner
                from pigeon_protocol.cdp_bridge import CdpBridge
                from pigeon_protocol.offline_order_cache import save_order_cache
                from pigeon_protocol.http_transport import order_api_ok
                from pigeon_protocol.order_curl_relay import query_orders_via_curl_relay
                from pigeon_protocol.order_sign_snapshot import save_sign_snapshot

                test_uid = "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk"
                unsigned = _order_unsigned_url(self.session)
                body = _order_body(test_uid)
                tokens = CdpSigner().sign_tokens(unsigned, method="POST", body=body)
                if tokens.get("a_bogus"):
                    self.session.query_tokens.update(tokens)
                    save_session(self.session)
                    report["sign_tokens_refreshed"] = list(tokens.keys())

                relay = query_orders_via_curl_relay(self.session, test_uid)
                if order_api_ok(relay):
                    cap = relay.get("_capture") if isinstance(relay.get("_capture"), dict) else None
                    if cap:
                        save_sign_snapshot(url=cap["url"], headers=cap["headers"], sample_body=body)
                        report["order_sign_snapshot"] = True
                    path = save_order_cache(test_uid, relay, source="curl_relay/bootstrap")
                    report["order_cache_exported"] = str(path.name)
                else:
                    raw = CdpBridge(self.session).query_orders(test_uid)
                    if raw.get("ok"):
                        path = save_order_cache(test_uid, raw, source="cdp/bootstrap")
                        report["order_cache_exported"] = str(path.name)

                # Node offline env: fingerprint + relay headers
                try:
                    from pigeon_protocol.subprocess_util import run_hidden

                    cap_script = Path(__file__).resolve().parents[2] / "scripts" / "cdp_capture_bdms_env.py"
                    if cap_script.exists():
                        run_hidden(
                            [sys.executable, str(cap_script)],
                            cwd=str(cap_script.parents[1]),
                            timeout=30,
                            check=False,
                        )
                        report["bdms_browser_env"] = cap_script.parents[1].joinpath(
                            "analysis", "bdms_browser_env.json"
                        ).exists()
                except Exception as env_exc:
                    report["bdms_env_error"] = str(env_exc)
            except Exception as exc:
                report["sign_refresh_error"] = str(exc)

        self._reload()
        report["health_after"] = self.health()
        report["template_pool"] = self.sender.list_supported_lengths()
        return report

    def pick_ws_url(self) -> str | None:
        return self.listener.pick_ws_url()

    def _on_ws_message(self, handler: Callable[[InboundMessage], None] | None = None):
        def _wrap(msg: InboundMessage) -> None:
            self.store.add(msg)
            uid = str(msg.security_user_id or "").strip()
            nick = str(msg.nickname or "").strip()
            if uid and nick:
                from pigeon_protocol.buyer_display_name import is_bad_display_name, remember_buyer_display_name

                if not is_bad_display_name(nick, uid=uid):
                    remember_buyer_display_name(self.session, uid, nick, save=True)
            if handler:
                handler(msg)

        return _wrap

    async def listen(
        self,
        handler: Callable[[InboundMessage], None] | None = None,
        *,
        timeout_sec: int | None = None,
        ws_url: str | None = None,
    ) -> None:
        await self.listener.listen_live(
            self._on_ws_message(handler),
            timeout_sec=timeout_sec or self.config.listen_timeout_sec,
            ws_url=ws_url,
        )

    def send_text(
        self,
        text: str,
        *,
        security_user_id: str = "",
        conversation_id: str = "",
        ws_url: str | None = None,
    ) -> SendResult:
        uid = security_user_id or _uid_from_route(conversation_id)
        result = self.sender.send_text(
            text,
            conversation_id=conversation_id,
            security_user_id=uid,
            ws_url=ws_url or self.pick_ws_url(),
        )
        if result.ok and not result.dry_run and uid:
            self.store.add(
                InboundMessage(
                    role="service",
                    text=text,
                    security_user_id=uid,
                    conversation_id=conversation_id,
                    source="ws_sent",
                )
            )
        return result

    def get_context(
        self,
        security_user_id: str = "",
        *,
        conversation_id: str = "",
        merge_ws: bool = True,
        use_cdp_fallback: bool = False,
    ) -> ConversationContext:
        if not security_user_id:
            return self.context.get_context(
                security_user_id=security_user_id,
                conversation_id=conversation_id,
            )
        ctx = self.context.get_context(
            security_user_id=security_user_id,
            prefer_pure=True,
            via_pigeon_im=False,
        )
        if merge_ws:
            ctx = self.store.merge_context(ctx)
        if ctx.messages or not use_cdp_fallback:
            return ctx
        if CdpSessionSync.available():
            logger.info("context empty via pure, try pigeon_im cdp")
            ctx = self.context.get_context(
                security_user_id=security_user_id,
                via_pigeon_im=True,
                prefer_pure=False,
            )
            if merge_ws:
                ctx = self.store.merge_context(ctx)
        return ctx

    def _refresh_order_sign_tokens(self, unsigned: str, body: dict[str, Any]) -> bool:
        """Sign via node/headless/cdp → write tokens to session, retry httpx."""
        from pigeon_protocol.sign import get_signer

        try:
            signer = get_signer(prefer="auto")
            tokens = signer.sign_tokens(unsigned, method="POST", body=body)
            if not tokens.get("a_bogus"):
                return False
            self.session.query_tokens.update(tokens)
            save_session(self.session)
            return True
        except Exception as exc:
            logger.warning("sign refresh failed: %s", exc)
            return False

    def get_orders(
        self,
        security_user_id: str,
        *,
        use_har_fallback: bool = True,
        use_cdp_fetch: bool = True,
        use_cdp_fallback: bool | None = None,
        use_user_card_hint: bool = True,
    ) -> OrderContext:
        if use_cdp_fallback is not None:
            use_cdp_fetch = use_cdp_fallback
        self.orders.http.use_cdp_sign = False
        result = self.orders.get_orders(security_user_id)
        if _orders_ok(result):
            return result

        unsigned = _order_unsigned_url(self.session)
        body = _order_body(security_user_id)
        if self._refresh_order_sign_tokens(unsigned, body):
            self.orders.http.use_cdp_sign = False
            retry = self.orders.get_orders(security_user_id)
            if _orders_ok(retry):
                retry.source = "backstage/order/query+sign_refresh"
                return retry

        # Python relay (preferred in pure mode)
        from pigeon_protocol.http_transport import order_api_ok
        from pigeon_protocol.order_node_relay import query_orders_via_relay
        from pigeon_protocol.order_parse import parse_order_response
        from pigeon_protocol.pure_config import pure_only_mode

        relay_raw = query_orders_via_relay(self.session, security_user_id)
        if order_api_ok(relay_raw):
            return parse_order_response(relay_raw, source=relay_raw.get("via") or "python_relay/order/query")

        from pigeon_protocol.order_sign_snapshot import query_orders_via_snapshot

        snap_raw = query_orders_via_snapshot(self.session, security_user_id)
        if order_api_ok(snap_raw):
            return parse_order_response(snap_raw, source=snap_raw.get("via") or "snapshot/curl_cffi")

        if pure_only_mode():
            if use_user_card_hint:
                hint = _user_card_order_hint(self.orders.http, security_user_id)
                if hint:
                    return hint
            return result

        # Node/jsdom bdms fetch sign (legacy bootstrap path)
        from pigeon_protocol.order_node_relay import query_orders_via_node_relay

        node_raw = query_orders_via_node_relay(self.session, security_user_id)
        if order_api_ok(node_raw):
            return parse_order_response(node_raw, source="node_relay/order/query")

        # CDP sign URL + curl_cffi (Chrome TLS + exact browser headers)
        if use_cdp_fetch and CdpSessionSync.available():
            from pigeon_protocol.http_transport import order_api_ok
            from pigeon_protocol.order_curl_relay import query_orders_via_curl_relay
            from pigeon_protocol.order_parse import parse_order_response
            from pigeon_protocol.order_sign_snapshot import save_sign_snapshot

            relay = query_orders_via_curl_relay(self.session, security_user_id)
            if order_api_ok(relay):
                cap = relay.get("_capture") if isinstance(relay.get("_capture"), dict) else None
                if cap and cap.get("url") and cap.get("headers"):
                    save_sign_snapshot(
                        url=cap["url"],
                        headers=cap["headers"],
                        sample_body=_order_body(security_user_id),
                    )
                return parse_order_response(relay, source="curl_relay/order/query")

        if use_cdp_fetch and CdpSessionSync.available():
            logger.info("orders curl_relay failed, try browser fetch (existing Feige page)")
            self.orders.http.use_cdp_sign = True
            cdp_result = self.orders.get_orders(security_user_id)
            if _orders_ok(cdp_result):
                return cdp_result

        if use_har_fallback:
            har = order_from_har_for_user(security_user_id, root=LIVE_CAPTURES / "from_har")
            if har and har.has_order:
                har.summary = f"{har.summary} (HAR offline, buyer matched)"
                return har

        if use_user_card_hint:
            hint = _user_card_order_hint(self.orders.http, security_user_id)
            if hint:
                return hint

        from pigeon_protocol.offline_order_cache import load_order_cache

        cached = load_order_cache(security_user_id)
        if cached:
            cached.summary = f"{cached.summary} (offline cache)"
            return cached

        return result

    def health(self) -> dict[str, Any]:
        from pigeon_protocol.capture_loader import find_send_template, list_send_template_pool
        from pigeon_protocol.foundation.bdms_sign import python_abogus_available
        from pigeon_protocol.http_transport import curl_cffi_available
        from pigeon_protocol.order_relay_headers import load_relay_header_template
        from pigeon_protocol.pure_config import pure_only_mode

        pool = list_send_template_pool()
        tpl = find_send_template()
        tpl_len = 0
        if tpl:
            import base64

            try:
                tpl_len = len(base64.b64decode(str(tpl.get("payload") or "")))
            except Exception:
                pass

        return {
            "cookies": len(self.session.cookies),
            "ws_url": (self.pick_ws_url() or "")[:120],
            "has_a_bogus": bool(self.session.query_tokens.get("a_bogus")),
            "has_pigeon_sign": bool(self.session.query_tokens.get("pigeon_sign")),
            "send_template_len": tpl_len,
            "send_template_pool": pool,
            "ws_store_users": len(self.store._by_user),
            "curl_cffi_available": curl_cffi_available(),
            "cdp_available": CdpSessionSync.available(),
            "dry_run": self.config.dry_run,
            "pure_ready": {
                "listen": bool(self.session.cookies and self.session.ws_urls),
                "send": bool(pool) or tpl_len >= 3000,
                "context": bool(self.session.cookies),
                "orders": python_abogus_available() and bool(load_relay_header_template()) if curl_cffi_available() else False,
                "conversations": python_abogus_available() and curl_cffi_available(),
            },
            "pure_only": pure_only_mode(),
            "python_abogus": python_abogus_available(),
            "blockers": [
                "a_bogus: Python FeigeABogus + live CSRF HEAD",
                "ws_sign: per-length templates + session inner cache",
            ],
        }

    def run_demo(
        self,
        security_user_id: str,
        *,
        listen_sec: int = 10,
        send_text: str = "",
        skip_prepare: bool = False,
    ) -> dict[str, Any]:
        report: dict[str, Any] = {"health_before": self.health()}
        if not skip_prepare:
            report["prepare"] = self.prepare(force_cdp=CdpSessionSync.available())
            report["health_after_prepare"] = self.health()

        orders_result = self.get_orders(security_user_id)
        report["orders"] = {
            "has_order": orders_result.has_order,
            "source": orders_result.source,
            "summary": orders_result.summary,
        }

        ctx = self.get_context(security_user_id)
        report["context"] = {
            "message_count": len(ctx.messages),
            "source": ctx.source,
            "preview": [m.get("text", "")[:60] for m in ctx.messages[:5]],
        }

        if send_text:
            report["send"] = self.send_text(send_text, security_user_id=security_user_id).__dict__

        if listen_sec > 0 and not self.config.dry_run:
            seen: list[dict[str, str]] = []

            def _on(msg: InboundMessage) -> None:
                seen.append({"role": msg.role, "text": msg.text[:120]})

            try:
                asyncio.run(self.listen(_on, timeout_sec=listen_sec))
            except Exception as exc:
                report["listen_error"] = str(exc)
            report["listen_messages"] = seen
        else:
            report["listen"] = "skipped (dry_run or listen_sec=0)"

        report["health_final"] = self.health()
        return report


def _uid_from_route(conversation_id: str) -> str:
    if conversation_id.startswith("AQ"):
        return conversation_id.split(":")[0]
    return ""


def _orders_ok(result: OrderContext) -> bool:
    raw = result.raw if isinstance(result.raw, dict) else {}
    if raw.get("dry_run"):
        return False
    if result.has_order:
        return True
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    code = str(data.get("code", "0"))
    return code in ("0", "0.0")


def _order_unsigned_url(session=None) -> str:
    from pigeon_protocol.config import ORDER_QUERY_PATH, PIGEON_HOST
    from pigeon_protocol.whale_params import backstage_query_base

    base = f"{PIGEON_HOST}{ORDER_QUERY_PATH}"
    return f"{base}?{backstage_query_base(session=session)}"


def _order_body(security_user_id: str) -> dict[str, Any]:
    return {
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


def _user_card_order_hint(http, security_user_id: str) -> OrderContext | None:
    card = http.get_user_card(security_user_id)
    if not isinstance(card, dict) or not card.get("ok"):
        return None
    data = card.get("data") if isinstance(card.get("data"), dict) else {}
    inner = data.get("data") if isinstance(data.get("data"), dict) else data
    count = int(inner.get("valid_order_count") or 0)
    trade = inner.get("shop_trade_info") if isinstance(inner.get("shop_trade_info"), dict) else {}
    deal = int(trade.get("shop_deal_count") or 0)
    if count <= 0 and deal <= 0:
        return OrderContext(
            has_order=False,
            orders=[],
            summary="user_card: 暂无有效订单",
            source="user_card/hint",
            raw={"card": inner},
        )
    return OrderContext(
        has_order=True,
        orders=[{"hint": "partial", "valid_order_count": count, "shop_deal_count": deal}],
        summary=f"user_card 提示: valid_order_count={count}, shop_deal_count={deal} (非完整订单列表)",
        source="user_card/hint",
        raw={"card": inner},
    )
