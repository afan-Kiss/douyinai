from __future__ import annotations

import asyncio
import base64
import logging
import os
from typing import Any

from pigeon_protocol.capture_loader import find_send_template, index_send_templates, list_send_template_pool
from pigeon_protocol.conversation_meta import resolve_conversation_meta
from pigeon_protocol.models import SendResult
from pigeon_protocol.parsers.ws_frame_builder import WSFrameBuilder
from pigeon_protocol.session import SessionState
from pigeon_protocol.ws_protocol import pick_template_ws_url
from pigeon_protocol.ws_session import WsSession

logger = logging.getLogger("pigeon.send")


class SendService:
    """Pure-protocol outbound WS — template protobuf + signature preservation."""

    def __init__(self, session: SessionState, *, dry_run: bool = True) -> None:
        self.session = session
        self.dry_run = dry_run
        self._builder: WSFrameBuilder | None = None
        self._template: dict[str, Any] | None = None
        from pigeon_protocol.foundation.ws_sign_engine import WsSendEngine

        self._ws_engine = WsSendEngine()

    def ws_capability(self):
        return self._ws_engine.capability()

    def _load_template(self, text: str = "", *, auto_harvest: bool = True) -> dict[str, Any]:
        byte_len = len(text.encode("utf-8")) if text else 0
        if self._template and not text:
            return self._template
        if text:
            from pigeon_protocol.ws_sign_bucket import resolve_template_byte_len

            resolved = resolve_template_byte_len(byte_len)
            if self._template:
                try:
                    cur_len = len(
                        WSFrameBuilder(
                            base64.b64decode(str(self._template.get("payload") or ""))
                        )._extract_template_text().encode("utf-8")
                    )
                except Exception:
                    cur_len = -1
                if resolve_template_byte_len(cur_len) == resolved:
                    return self._template

        template_event = find_send_template(byte_len=byte_len if byte_len else 0)
        if not template_event and byte_len > 0:
            template_event = find_send_template(byte_len=resolve_template_byte_len(byte_len))

        if not template_event and byte_len > 0 and auto_harvest and os.getenv("PIGEON_STANDALONE", "").lower() not in ("1", "true", "yes"):
            from pigeon_protocol.ws_template_harvest import ensure_template_sync

            if ensure_template_sync(byte_len):
                self._template = None
                self._builder = None
                template_event = find_send_template(byte_len=byte_len)

        if not template_event:
            from pigeon_protocol.ws_sign_bucket import coverage_report, unsupported_reason

            reason = unsupported_reason(byte_len) if byte_len else "no default template"
            cov = coverage_report()
            raise RuntimeError(
                f"no ws template for text byte_len={byte_len}; {reason}; "
                f"coverage 1-200: {cov.get('supported_count_1_200')}/200"
            )
        self._template = template_event
        self._builder = WSFrameBuilder.from_template_dict(template_event)
        return template_event

    def _load_builder(self) -> WSFrameBuilder:
        if self._builder:
            return self._builder
        self._load_template()
        assert self._builder is not None
        return self._builder

    def build_payload(
        self,
        text: str,
        *,
        seq: int | None = None,
        security_user_id: str = "",
        talk_id: str = "",
        preserve_signature: bool = True,
    ) -> bytes:
        template = self._load_template(text)
        meta = None
        if security_user_id:
            meta = resolve_conversation_meta(self.session, security_user_id, talk_id=talk_id, use_cdp=False)
        try:
            return self._ws_engine.build_frame(
                text,
                seq=seq,
                security_user_id=meta.security_user_id if meta else security_user_id,
                shop_id=meta.shop_id if meta else self.session.shop_id,
                talk_id=meta.talk_id if meta else talk_id,
                preserve_signature=preserve_signature,
                ws_url=self.pick_ws_url(template),
                session=self.session,
            )
        except (ValueError, RuntimeError) as exc:
            logger.warning("ws engine build failed: %s", exc)
            raise

    def build_replay_exact(self, *, ws_url: str = "") -> bytes:
        """Return template bytes with WS credentials patched and fresh client_message_id."""
        import base64
        import uuid

        from pigeon_protocol.ws_protocol import patch_ws_credentials
        from pigeon_protocol.ws_sign import locate_signature_region, patch_client_message_id, rebuild_dollar_suffix

        template = self._load_template()
        data = bytearray(base64.b64decode(str(template.get("payload") or "")))
        url = ws_url or self.pick_ws_url(template)
        if url:
            patch_ws_credentials(data, url, session=self.session)
        region = locate_signature_region(data)
        if region:
            new_cid = str(uuid.uuid4())
            patch_client_message_id(data, new_cid)
            rebuild_dollar_suffix(data, region, new_cid)
        return bytes(data)

    def list_supported_lengths(self) -> list[dict]:
        return list_send_template_pool()

    def pick_ws_url(self, template: dict[str, Any] | None = None) -> str:
        from pigeon_protocol.ws_protocol import pick_template_ws_url

        tpl = template or self._load_template()
        return pick_template_ws_url(tpl, self.session.ws_urls, session=self.session) or str(tpl.get("url") or "")

    def send_text(
        self,
        text: str,
        *,
        conversation_id: str = "",
        security_user_id: str = "",
        ws_url: str | None = None,
        seq: int | None = None,
        handshake: bool = True,
        replay_exact: bool = False,
        auto_harvest: bool = True,
    ) -> SendResult:
        uid = security_user_id or _uid_from_route(conversation_id)
        if os.getenv("PIGEON_STANDALONE", "").lower() in ("1", "true", "yes"):
            try:
                from pigeon_protocol.session_readiness import heal_for_send

                heal = heal_for_send(self.session, save=True)
                ready = heal.get("readiness") or {}
                if not ready.get("send_ready"):
                    blockers = list(ready.get("blockers") or [])
                    return SendResult(
                        ok=False,
                        mode="ws_pure_send",
                        reason=blockers[0] if blockers else "发信未就绪",
                        dry_run=False,
                        raw={
                            "preflight_failed": True,
                            "recommended_action": ready.get("recommended_action"),
                            "needs_cdp_onboard": ready.get("needs_cdp_onboard"),
                            "blockers": blockers,
                        },
                    )
            except Exception as exc:
                logger.debug("send preflight heal: %s", exc)

        try:
            template = self._load_template(text, auto_harvest=auto_harvest)
        except RuntimeError as exc:
            return SendResult(ok=False, mode="ws_pure_send", reason=str(exc))
        url = ws_url or self.pick_ws_url(template)

        if replay_exact:
            payload = self.build_replay_exact(ws_url=url)
        else:
            try:
                payload = self.build_payload(text, seq=seq, security_user_id=uid)
            except ValueError as exc:
                return SendResult(ok=False, mode="ws_pure_send", reason=str(exc))

        if self.dry_run:
            return SendResult(
                ok=True,
                mode="ws_template_dry_run",
                reason="payload built, not sent",
                payload_length=len(payload),
                dry_run=True,
                raw={"security_user_id": uid[:24] if uid else "", "ws_url": url[:120]},
            )

        session = WsSession(self.session)
        if os.getenv("PIGEON_STANDALONE", "").lower() in ("1", "true", "yes"):
            try:
                from pigeon_protocol.ws_token_refresh import ensure_fresh_ws_token
                from pigeon_protocol.ws_url_builder import find_working_ws_url, pick_live_ws_url

                wr = ensure_fresh_ws_token(self.session, probe=True)
                if wr.get("ok"):
                    url = find_working_ws_url(self.session) or pick_live_ws_url(self.session) or url
            except Exception as exc:
                logger.debug("ws token preflight: %s", exc)

        result = session.send_bytes_sync(payload, ws_url=url, handshake=handshake)
        if (
            not result.ok
            and "400" in str(result.reason or "")
            and os.getenv("PIGEON_STANDALONE", "").lower() in ("1", "true", "yes")
        ):
            try:
                from pigeon_protocol.ws_token_refresh import ensure_fresh_ws_token
                from pigeon_protocol.ws_url_builder import find_working_ws_url, pick_live_ws_url

                wr = ensure_fresh_ws_token(self.session, probe=False)
                if wr.get("ok"):
                    url = find_working_ws_url(self.session) or pick_live_ws_url(self.session) or url
                    result = session.send_bytes_sync(payload, ws_url=url, handshake=handshake)
            except Exception as exc:
                logger.debug("ws send retry after refresh: %s", exc)
        if result.ok:
            try:
                from pigeon_protocol.foundation.ws_session_inner import store_inner_from_frame

                store_inner_from_frame(self.session, payload, text)
            except Exception as exc:
                logger.debug("post-send inner cache skipped: %s", exc)
            return result

        # Last resort: browser UI send (disabled in standalone mode)
        if auto_harvest and os.getenv("PIGEON_STANDALONE", "").lower() not in ("1", "true", "yes"):
            from pigeon_protocol.cdp_ui import CdpUiSender

            if CdpUiSender.available():
                logger.info("pure ws send failed, fallback cdp ui send")
                ui = CdpUiSender().send(text)
                return SendResult(
                    ok=bool(ui.get("ok")),
                    mode="cdp_ui_send",
                    reason=str(ui.get("error") or ui.get("mode") or ""),
                    payload_length=len(text.encode("utf-8")),
                    dry_run=False,
                    raw=ui,
                )
        return result


def _uid_from_route(conversation_id: str) -> str:
    if conversation_id.startswith("AQ"):
        return conversation_id.split(":")[0]
    return ""
