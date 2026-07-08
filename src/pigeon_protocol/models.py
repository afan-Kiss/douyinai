from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class InboundMessage:
    role: str
    text: str
    conversation_id: str = ""
    conversation_route: str = ""
    security_user_id: str = ""
    shop_id: str = ""
    server_message_id: str = ""
    nickname: str = ""
    source: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConversationContext:
    conversation_id: str
    security_user_id: str
    shop_id: str
    buyer_name: str
    messages: list[dict[str, Any]]
    source: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class OrderContext:
    has_order: bool
    orders: list[dict[str, Any]]
    summary: str
    source: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class SendResult:
    ok: bool
    mode: str
    reason: str = ""
    payload_length: int = 0
    dry_run: bool = False
    raw: dict[str, Any] = field(default_factory=dict)
