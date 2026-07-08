from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Callable

from pigeon_protocol.capture_loader import load_capture
from pigeon_protocol.config import WS_HOST_HINTS
from pigeon_protocol.models import InboundMessage
from pigeon_protocol.parsers import parse_http_inbound_messages, parse_inbound_frame
from pigeon_protocol.session import SessionState

logger = logging.getLogger("pigeon.ws")


class WsListener:
    """WebSocket 入站监听 — 优先 live 连接，可用抓包文件离线回放。"""

    def __init__(self, session: SessionState) -> None:
        self.session = session

    def pick_ws_url(self) -> str | None:
        from pigeon_protocol.ws_url_builder import find_working_ws_url, pick_live_ws_url

        working = find_working_ws_url(self.session)
        if working:
            return working
        live = pick_live_ws_url(self.session)
        if live:
            return live
        for url in self.session.ws_urls:
            if any(h in url for h in WS_HOST_HINTS):
                return url
        if not self.session.ws_urls:
            from pigeon_protocol.ws_url_builder import build_ws_url, ensure_ws_url

            ensure_ws_url(self.session)
            built = build_ws_url(self.session)
            if built:
                self.session.ws_urls.append(built)
                return built
        return self.session.ws_urls[0] if self.session.ws_urls else None

    def parse_ws_payload(self, payload: str | bytes, *, url: str = "", direction: str = "in") -> list[InboundMessage]:
        import base64

        raw_bytes: bytes
        if isinstance(payload, (bytes, bytearray)):
            raw_bytes = bytes(payload)
        else:
            text = str(payload or "").strip()
            if not text:
                return []
            try:
                raw_bytes = base64.b64decode(text)
            except Exception:
                raw_bytes = text.encode("latin1", errors="ignore")

        event = {
            "type": "ws_frame_received" if direction == "in" else "ws_frame_sent",
            "direction": direction,
            "url": url,
            "format": "binary",
            "payload_hex": raw_bytes.hex(),
        }
        frame = parse_inbound_frame(event)
        frames = [frame] if isinstance(frame, dict) and frame.get("text") else []
        out: list[InboundMessage] = []
        for frame in frames:
            role = str(frame.get("role") or "buyer")
            text = str(frame.get("text") or "").strip()
            if not text:
                continue
            out.append(
                InboundMessage(
                    role=role,
                    text=text,
                    conversation_id=str(frame.get("conversation_id") or ""),
                    conversation_route=str(frame.get("conversation_route") or ""),
                    security_user_id=str(frame.get("security_receiver_id") or ""),
                    shop_id=str(frame.get("shop_id") or ""),
                    server_message_id=str(frame.get("server_message_id") or ""),
                    nickname=str(frame.get("nickname") or ""),
                    source="ws",
                    raw=frame,
                )
            )
        return out

    def parse_http_capture(self, event: dict[str, Any]) -> list[InboundMessage]:
        parsed = parse_http_inbound_messages(event) or []
        out: list[InboundMessage] = []
        for item in parsed:
            out.append(
                InboundMessage(
                    role=str(item.get("role") or "buyer"),
                    text=str(item.get("text") or ""),
                    conversation_id=str(item.get("conversation_id") or ""),
                    conversation_route=str(item.get("conversation_route") or ""),
                    security_user_id=str(item.get("security_receiver_id") or ""),
                    shop_id=str(item.get("shop_id") or ""),
                    server_message_id=str(item.get("server_message_id") or ""),
                    nickname=str(item.get("nickname") or ""),
                    source="http",
                    raw=item,
                )
            )
        return out

    async def listen_live(
        self,
        on_message: Callable[[InboundMessage], None],
        *,
        timeout_sec: int = 120,
        ws_url: str | None = None,
    ) -> None:
        import websockets

        url = ws_url or self.pick_ws_url()
        if not url:
            raise RuntimeError("no ws url in session — capture ws_created / ws_frame_* first")

        headers = {}
        cookie = self.session.cookie_header()
        if cookie:
            headers["Cookie"] = cookie
        if self.session.user_agent:
            headers["User-Agent"] = self.session.user_agent

        logger.info("connecting %s", url[:120])
        async with websockets.connect(url, additional_headers=headers, ping_interval=25) as ws:
            logger.info("connected, listening %ss", timeout_sec)

            async def _reader() -> None:
                async for message in ws:
                    if isinstance(message, bytes):
                        for item in self.parse_ws_payload(message, url=url, direction="in"):
                            on_message(item)
                    elif isinstance(message, str):
                        for item in self.parse_ws_payload(message.encode(), url=url, direction="in"):
                            on_message(item)

            try:
                await asyncio.wait_for(_reader(), timeout=timeout_sec)
            except asyncio.TimeoutError:
                logger.info("listen timeout")

    def replay_capture_file(self, path: Path, on_message: Callable[[InboundMessage], None]) -> int:
        event = load_capture(path)
        typ = str(event.get("type") or "")
        count = 0
        if typ.startswith("ws_frame"):
            direction = "in" if "received" in typ else "out"
            payload = event.get("payload") or ""
            if event.get("payload_hex") and not payload:
                payload = bytes.fromhex(str(event["payload_hex"]))
            for item in self.parse_ws_payload(payload, url=str(event.get("url") or ""), direction=direction):
                on_message(item)
                count += 1
        elif typ == "http_body":
            for item in self.parse_http_capture(event):
                on_message(item)
                count += 1
        return count
