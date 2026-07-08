"""In-memory WS message cache — merge with HTTP context for real-time view."""
from __future__ import annotations

from dataclasses import dataclass, field

from pigeon_protocol.models import ConversationContext, InboundMessage


def _msg_key(msg: dict) -> str:
    mid = str(msg.get("server_message_id") or msg.get("message_id") or "")
    if mid:
        return f"id:{mid}"
    return f"{msg.get('role', '')}:{msg.get('text', '')}:{msg.get('time', '')}"


def _inbound_to_dict(msg: InboundMessage) -> dict:
    return {
        "role": msg.role,
        "text": msg.text,
        "time": "",
        "server_message_id": msg.server_message_id,
        "source": msg.source or "ws",
        "nickname": msg.nickname,
    }


@dataclass
class WsMessageStore:
    """Per-buyer WS message buffer keyed by security_user_id."""

    _by_user: dict[str, list[dict]] = field(default_factory=dict)
    max_per_user: int = 500

    def add(self, msg: InboundMessage) -> None:
        uid = msg.security_user_id or _uid_from_route(msg.conversation_id)
        if not uid:
            return
        bucket = self._by_user.setdefault(uid, [])
        item = _inbound_to_dict(msg)
        key = _msg_key(item)
        if any(_msg_key(existing) == key for existing in bucket):
            return
        bucket.append(item)
        if len(bucket) > self.max_per_user:
            del bucket[: len(bucket) - self.max_per_user]

    def get_messages(self, security_user_id: str) -> list[dict]:
        return list(self._by_user.get(security_user_id, []))

    def merge_context(self, ctx: ConversationContext) -> ConversationContext:
        """Merge HTTP history with WS cache; WS-only messages appended."""
        uid = ctx.security_user_id
        if not uid:
            return ctx
        ws_msgs = self.get_messages(uid)
        if not ws_msgs:
            return ctx

        seen = {_msg_key(m) for m in ctx.messages}
        merged = list(ctx.messages)
        added = 0
        for item in ws_msgs:
            key = _msg_key(item)
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
            added += 1

        if not added:
            return ctx

        return ConversationContext(
            conversation_id=ctx.conversation_id,
            security_user_id=ctx.security_user_id,
            shop_id=ctx.shop_id,
            buyer_name=ctx.buyer_name,
            messages=merged,
            source=f"{ctx.source}+ws_store({added})",
            raw={**ctx.raw, "ws_store_added": added},
        )

    def clear(self, security_user_id: str = "") -> None:
        if security_user_id:
            self._by_user.pop(security_user_id, None)
        else:
            self._by_user.clear()


def _uid_from_route(conversation_id: str) -> str:
    if conversation_id.startswith("AQ"):
        return conversation_id.split(":")[0]
    return ""
