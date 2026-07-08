"""Standalone pure-protocol runtime — zero browser/CDP at call time; Node jsdom bdms OK."""
from __future__ import annotations

from typing import Any

from pigeon_protocol.config import AppConfig
from pigeon_protocol.pure_config import pure_only_mode
from pigeon_protocol.pure_runtime import PureProtocolRuntime


class StandaloneRuntime(PureProtocolRuntime):
    """
    Browser-free runtime.

    Requirements:
    - session/session.json (cookies, ws_urls, query_tokens)
    - standalone_bundle/ws_sign templates (or captures/live/ws_sign)
    - standalone_bundle protobuf bodies (init + get_by_conversation)
    - Node jsdom bdms (preferred) or Python a_bogus for backstage relay
    """

    def get_orders(self, security_user_id: str, **kwargs: Any):
        kwargs.setdefault("use_cdp_fetch", False)
        kwargs.setdefault("use_har_fallback", False)
        kwargs.setdefault("use_user_card_hint", True)

        from pigeon_protocol.http_transport import order_api_ok
        from pigeon_protocol.order_node_relay import query_orders_via_relay
        from pigeon_protocol.order_parse import parse_order_response
        from pigeon_protocol.order_sign_snapshot import query_orders_via_snapshot
        from pigeon_protocol.pure_runtime import _orders_ok

        relay_raw = query_orders_via_relay(self.session, security_user_id)
        if relay_raw and order_api_ok(relay_raw):
            via = relay_raw.get("via") or "python_relay/order/query"
            return parse_order_response(relay_raw, source=via)

        snap_raw = query_orders_via_snapshot(self.session, security_user_id)
        if snap_raw and order_api_ok(snap_raw):
            return parse_order_response(snap_raw, source=snap_raw.get("via") or "snapshot/curl_cffi")

        result = super().get_orders(security_user_id, **kwargs)
        if _orders_ok(result):
            self._cache_orders(security_user_id, result)
            return result

        from pigeon_protocol.offline_order_cache import load_order_cache

        cached = load_order_cache(security_user_id)
        if cached:
            cached.summary = f"{cached.summary} (standalone cache fallback)"
            return cached
        return result

    @staticmethod
    def _cache_orders(security_user_id: str, result) -> None:
        raw = result.raw if isinstance(getattr(result, "raw", None), dict) else {}
        if not raw:
            return
        try:
            from pigeon_protocol.offline_order_cache import save_order_cache

            save_order_cache(security_user_id, raw, source=getattr(result, "source", "") or "live")
        except Exception:
            pass

    def send_text(
        self,
        text: str,
        *,
        security_user_id: str = "",
        conversation_id: str = "",
        ws_url: str | None = None,
        **kwargs: Any,
    ):
        return self.sender.send_text(
            text,
            security_user_id=security_user_id,
            conversation_id=conversation_id,
            ws_url=ws_url,
            auto_harvest=False,
            **kwargs,
        )

    def health(self) -> dict[str, Any]:
        h = super().health()
        h["standalone"] = True
        h["pure_only"] = pure_only_mode()
        h["cdp_available"] = False
        from pigeon_protocol.foundation.bdms_sign import node_available, python_abogus_available, sign_available
        from pigeon_protocol.pure_config import node_sign_allowed

        h["python_abogus"] = python_abogus_available()
        h["node_bdms"] = node_available() and node_sign_allowed()
        h["offline_sign"] = sign_available()
        from pigeon_protocol.pure_config import BUNDLE_CONTEXT_BODY, BUNDLE_INIT_BODY, BUNDLE_WS_SIGN, STANDALONE_BUNDLE
        from pigeon_protocol.ws_template_harvest import missing_lengths, QUICK_LADDER

        miss = missing_lengths(QUICK_LADDER)
        h["template_gaps"] = miss
        h["bundle"] = {
            "ws_sign_dir": BUNDLE_WS_SIGN.is_dir(),
            "context_body": BUNDLE_CONTEXT_BODY.is_file(),
            "init_body": BUNDLE_INIT_BODY.is_file(),
            "ws_inner_canonical": (STANDALONE_BUNDLE / "ws_inner_canonical.json").is_file(),
        }
        from pigeon_protocol.order_relay_headers import load_relay_header_template

        h["order_relay_headers"] = bool(load_relay_header_template())
        h["pure_ready"]["orders"] = h["offline_sign"] and h["order_relay_headers"]
        h["pure_ready"]["conversations"] = h["offline_sign"]
        if h["pure_ready"]["conversations"]:
            try:
                from pigeon_protocol.conv_list import list_conversations_relay

                probe = list_conversations_relay(self.session, size=5)
                h["pure_ready"]["conversations"] = bool(probe.get("ok") and (probe.get("items") or probe.get("data")))
                h["conv_list_probe"] = {
                    "ok": probe.get("ok"),
                    "count": len(probe.get("items") or []),
                    "via": probe.get("via"),
                    "whale_blocked": probe.get("whale_blocked"),
                    "api_code": probe.get("api_code"),
                }
            except Exception as exc:
                h["conv_list_probe"] = {"ok": False, "error": str(exc)}
        from pigeon_protocol.foundation.status import foundation_report

        h["foundation"] = foundation_report(self.session).to_dict()
        h["blockers"] = []
        if not h["offline_sign"]:
            h["blockers"].append("a_bogus: install Node or fix Python FeigeABogus")
        elif not h["node_bdms"] and not h["python_abogus"]:
            h["blockers"].append("a_bogus: no sign backend")
        if not h["bundle"]["context_body"]:
            h["blockers"].append("bundle: export get_by_conversation_body.bin")
        if not h["bundle"]["ws_inner_canonical"]:
            h["blockers"].append("bundle: run prepare-pure to export ws_inner_canonical.json")
        if miss:
            h["blockers"].append(f"ws templates missing lengths: {miss}")
        if not h["pure_ready"].get("send"):
            h["blockers"].append("ws send: run session-doctor --fix or prepare-pure")
        return h
