from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Callable

from pigeon_protocol.capture_loader import extract_session_from_captures, index_captures
from pigeon_protocol.config import AppConfig, SESSION_FILE
from pigeon_protocol.context import ContextService
from pigeon_protocol.models import InboundMessage
from pigeon_protocol.order import OrderService
from pigeon_protocol.send import SendService
from pigeon_protocol.session import load_session, save_session as persist_session
from pigeon_protocol.ws_client import WsListener

logger = logging.getLogger("pigeon.service")


class PigeonProtocolService:
    """纯协议服务门面 — 监听 / 上下文 / 订单 / 发送。"""

    def __init__(self, config: AppConfig | None = None) -> None:
        self.config = config or AppConfig()
        self.session = load_session()
        self.listener = WsListener(self.session)
        self.context = ContextService(self.session, dry_run=self.config.dry_run)
        self.orders = OrderService(self.session, dry_run=self.config.dry_run)
        self.sender = SendService(self.session, dry_run=self.config.dry_run)

    def refresh_session_from_captures(self) -> Path:
        self.session = extract_session_from_captures(self.config.capture_dirs)
        self.listener = WsListener(self.session)
        self.context = ContextService(self.session, dry_run=self.config.dry_run)
        self.orders = OrderService(self.session, dry_run=self.config.dry_run)
        self.sender = SendService(self.session, dry_run=self.config.dry_run)
        return persist_session(self.session)

    def status(self) -> dict:
        from pigeon_protocol.account_context import session_file

        sf = session_file()
        idx = index_captures(self.config.capture_dirs)
        return {
            "session_file": str(sf),
            "session_loaded": sf.exists(),
            "cookies": len(self.session.cookies),
            "query_tokens": list(self.session.query_tokens.keys()),
            "ws_urls": len(self.session.ws_urls),
            "dry_run": self.config.dry_run,
            "cdp_ready": __import__("pigeon_protocol.cdp_bridge", fromlist=["cdp_ready"]).cdp_ready(),
            "captures": {
                "ws_created": len(idx.ws_created),
                "ws_received": len(idx.ws_received),
                "ws_sent": len(idx.ws_sent),
                "http_bodies": len(idx.http_bodies),
                "order_requests": len(idx.order_requests),
                "history_requests": len(idx.history_requests),
            },
            "notes": self.session.notes,
        }

    def on_inbound(self, handler: Callable[[InboundMessage], None]) -> Callable[[InboundMessage], None]:
        def _wrap(msg: InboundMessage) -> None:
            logger.info("[%s] %s: %s", msg.source, msg.role, msg.text[:80])
            handler(msg)

        return _wrap

    async def listen(self, handler: Callable[[InboundMessage], None], *, timeout_sec: int | None = None) -> None:
        await self.listener.listen_live(
            self.on_inbound(handler),
            timeout_sec=timeout_sec or self.config.listen_timeout_sec,
        )

    def replay(self, capture_path: Path, handler: Callable[[InboundMessage], None]) -> int:
        return self.listener.replay_capture_file(capture_path, self.on_inbound(handler))

    def dump_json(self, obj: object) -> str:
        if hasattr(obj, "__dict__"):
            data = obj.__dict__
        else:
            data = obj
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)
