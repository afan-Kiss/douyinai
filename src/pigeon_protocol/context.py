from __future__ import annotations

from typing import Any

from pigeon_protocol.http_client import BackstageHttpClient
from pigeon_protocol.models import ConversationContext
from pigeon_protocol.session import SessionState


class ContextService:
    """会话上下文：HTTP 历史消息 + pigeon_im protobuf + 用户卡片。"""

    def __init__(self, session: SessionState, *, dry_run: bool = False, use_cdp_sign: bool = False) -> None:
        self.session = session
        self.http = BackstageHttpClient(session, dry_run=dry_run, use_cdp_sign=use_cdp_sign)

    def get_context(
        self,
        *,
        conversation_id: str = "",
        security_user_id: str = "",
        via_pigeon_im: bool = False,
        prefer_pure: bool = True,
    ) -> ConversationContext:
        if security_user_id and (via_pigeon_im or prefer_pure):
            from pigeon_protocol.buyer_display_name import extract_buyer_name_from_obj, is_bad_display_name
            from pigeon_protocol.pigeon_im import fetch_context_pure

            ctx = fetch_context_pure(self.session, security_user_id, shop_id=self.session.shop_id)
            if ctx.messages or not via_pigeon_im:
                card = self.http.get_user_card(security_user_id)
                data = card.get("data") if isinstance(card.get("data"), dict) else {}
                inner = data.get("data") if isinstance(data.get("data"), dict) else data
                name = extract_buyer_name_from_obj(inner if isinstance(inner, dict) else {})
                if name and not is_bad_display_name(name):
                    ctx.buyer_name = name
                return ctx

        if via_pigeon_im and security_user_id:
            from pigeon_protocol.cdp_bridge import CdpBridge
            from pigeon_protocol.pigeon_im import context_from_cdp_fetch

            raw = CdpBridge(self.session).fetch_pigeon_im_history(
                security_user_id,
                shop_id=self.session.shop_id,
            )
            return context_from_cdp_fetch(raw, security_user_id=security_user_id)

        ctx = self.http.fetch_history_messages(
            conversation_id=conversation_id,
            security_user_id=security_user_id,
        )
        if security_user_id:
            from pigeon_protocol.buyer_display_name import extract_buyer_name_from_obj, is_bad_display_name

            card = self.http.get_user_card(security_user_id)
            data = card.get("data") if isinstance(card.get("data"), dict) else {}
            inner = data.get("data") if isinstance(data.get("data"), dict) else data
            name = extract_buyer_name_from_obj(inner if isinstance(inner, dict) else {})
            if name and not is_bad_display_name(name):
                ctx.buyer_name = name
        return ctx

    def list_conversations(self, *, page: int = 0, size: int = 20) -> dict[str, Any]:
        return self.http.fuzzy_search_conversations(page=page, size=size)
