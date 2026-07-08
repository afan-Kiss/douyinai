"""Pure-protocol WebSocket session — long connection + sync handshake."""
from __future__ import annotations

import asyncio
import base64
import logging
from typing import Callable

from pigeon_protocol.capture_loader import index_captures, load_capture
from pigeon_protocol.models import InboundMessage, SendResult
from pigeon_protocol.session import SessionState
from pigeon_protocol.ws_client import WsListener
from pigeon_protocol.ws_protocol import pick_template_ws_url

logger = logging.getLogger("pigeon.ws_session")


class WsSession:
    """Maintain frontier WS connection for pure-protocol send/receive."""

    def __init__(self, session: SessionState) -> None:
        self.session = session
        self.listener = WsListener(session)
        self._ws = None
        self._seq_hint = 0

    def pick_url(self, template: dict | None = None) -> str:
        if template:
            return pick_template_ws_url(template, self.session.ws_urls, session=self.session)
        return self.listener.pick_ws_url() or ""

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        cookie = self.session.cookie_header()
        if cookie:
            headers["Cookie"] = cookie
        if self.session.user_agent:
            headers["User-Agent"] = self.session.user_agent
        return headers

    def _sync_templates(self, ws_url: str = "") -> list[bytes]:
        from pigeon_protocol.ws_protocol import patch_ws_credentials

        out: list[bytes] = []
        for path in index_captures().ws_sent:
            try:
                ev = load_capture(path)
            except Exception:
                continue
            payload = str(ev.get("payload") or "")
            if not payload:
                continue
            try:
                raw = bytearray(base64.b64decode(payload))
            except Exception:
                continue
            if len(raw) >= 2500:
                continue
            text = raw.decode("utf-8", errors="ignore")
            if "request_log" not in text and "feat/" not in text:
                continue
            if ws_url:
                patch_ws_credentials(raw, ws_url, session=self.session)
            out.append(bytes(raw))
        return out[:5]

    async def connect(self, ws_url: str | None = None) -> str:
        import websockets

        url = ws_url or self.pick_url()
        if not url:
            raise RuntimeError("no ws url — run prepare or extract-session")
        self._ws = await websockets.connect(url, additional_headers=self._headers(), ping_interval=25)
        logger.info("ws session connected %s", url[:100])
        return url

    async def handshake(self, ws_url: str = "") -> int:
        """Send inbox/sync frames like browser before first text message."""
        if not self._ws:
            raise RuntimeError("not connected")
        count = 0
        for payload in self._sync_templates(ws_url):
            await self._ws.send(payload)
            count += 1
            await asyncio.sleep(0.05)
        logger.info("ws handshake sent %s sync frames", count)
        return count

    async def send_bytes(self, payload: bytes) -> None:
        if not self._ws:
            raise RuntimeError("not connected")
        await self._ws.send(payload)
        logger.info("ws sent %s bytes", len(payload))

    async def recv_one(self, timeout: float = 3.0) -> bytes | str | None:
        if not self._ws:
            return None
        try:
            return await asyncio.wait_for(self._ws.recv(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def close(self) -> None:
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def listen(
        self,
        handler: Callable[[InboundMessage], None],
        *,
        timeout_sec: int = 120,
    ) -> None:
        await self.listener.listen_live(handler, timeout_sec=timeout_sec)

    def send_bytes_sync(self, payload: bytes, *, ws_url: str | None = None, handshake: bool = True) -> SendResult:
        async def _run() -> SendResult:
            url = await self.connect(ws_url)
            if handshake:
                await self.handshake(url)
            await self.send_bytes(payload)
            ack = await self.recv_one(timeout=2.0)
            await self.close()
            return SendResult(
                ok=True,
                mode="ws_session_send",
                payload_length=len(payload),
                dry_run=False,
                raw={"url": url[:120], "ack_type": type(ack).__name__, "ack_len": len(ack) if ack else 0},
            )

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            try:
                return asyncio.run(_run())
            except Exception as exc:
                return SendResult(ok=False, mode="ws_session_send", reason=str(exc))
        else:
            import concurrent.futures

            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    return pool.submit(lambda: asyncio.run(_run())).result(timeout=30)
            except Exception as exc:
                return SendResult(ok=False, mode="ws_session_send", reason=str(exc))
