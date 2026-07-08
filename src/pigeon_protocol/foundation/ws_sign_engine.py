"""WS 226B blob signing strategies — computed inner + bucket templates."""
from __future__ import annotations

import logging
from typing import Any, Protocol

from pigeon_protocol.foundation.types import WsSendCapability

logger = logging.getLogger("pigeon.foundation.ws_sign")


class WsSignStrategy(Protocol):
    name: str

    def capability(self) -> WsSendCapability: ...

    def can_send(self, text: str) -> bool: ...

    def build_frame(
        self,
        text: str,
        *,
        security_user_id: str = "",
        shop_id: str = "",
        talk_id: str = "",
        seq: int | None = None,
        preserve_signature: bool = True,
        ws_url: str = "",
        session: Any = None,
    ) -> bytes: ...


class BucketCanonicalStrategy:
    """Template protobuf + optional bucket inner reuse."""

    name = "bucket_canonical"

    def capability(self) -> WsSendCapability:
        from pigeon_protocol.capture_loader import index_send_templates
        from pigeon_protocol.foundation.ws_blob_re import re_status
        from pigeon_protocol.ws_sign_bucket import BUCKET_SPECS, coverage_report, is_supported_text_len

        pool = index_send_templates()
        cov = coverage_report()
        re_info = re_status()
        canonical_ok = all(s.canonical_len in pool for s in BUCKET_SPECS)
        gaps = cov.get("gaps_1_200") or []

        return WsSendCapability(
            strategy=self.name,
            ready=canonical_ok or cov.get("supported_count_1_200", 0) >= 180,
            template_lengths=sorted(pool.keys()),
            bucket_count=len(BUCKET_SPECS),
            computed_blob=False,
            missing_lengths=[n for n in gaps if n <= 80][:20],
            notes=[
                "Template protobuf skeleton + optional captured inner",
                f"Coverage 1-200: {cov.get('supported_count_1_200')}/200 lengths",
                *re_info.get("notes", [])[:2],
            ],
        )

    def can_send(self, text: str) -> bool:
        from pigeon_protocol.ws_sign_bucket import is_supported_text_len

        return is_supported_text_len(len(text.encode("utf-8")))

    def build_frame(
        self,
        text: str,
        *,
        security_user_id: str = "",
        shop_id: str = "",
        talk_id: str = "",
        seq: int | None = None,
        preserve_signature: bool = True,
        ws_url: str = "",
        session: Any = None,
    ) -> bytes:
        from pigeon_protocol.capture_loader import find_send_template
        from pigeon_protocol.parsers.ws_frame_builder import WSFrameBuilder
        from pigeon_protocol.ws_sign_bucket import resolve_template_byte_len, unsupported_reason

        bl = len(text.encode("utf-8"))
        if not self.can_send(text):
            raise RuntimeError(unsupported_reason(bl) or f"unsupported textB={bl}")

        resolved = resolve_template_byte_len(bl)
        ev = find_send_template(byte_len=resolved)
        if not ev:
            raise RuntimeError(f"no template for textB={bl} (canonical={resolved})")

        builder = WSFrameBuilder.from_template_dict(ev)
        frame = builder.build_pure(
            text,
            seq=seq,
            security_user_id=security_user_id,
            shop_id=shop_id,
            talk_id=talk_id,
            ws_url=ws_url,
            preserve_signature=preserve_signature,
            session=session,
        )
        if session is not None:
            try:
                from pigeon_protocol.foundation.ws_session_inner import store_inner_from_frame

                store_inner_from_frame(session, frame, text)
            except Exception as exc:
                logger.debug("inner cache store skipped: %s", exc)
        return frame


class ComputedBlobStrategy:
    """
    169B inner via equivalence-class formula:

      class_id = class(text_byte_length)   # 7 groups for lengths 1-200
      inner    = session_cache[class_id] | pool[class_id]
      blob     = base64(inner) → 226 ASCII bytes patched into frame

    Protobuf skeleton still from shortest template in class; inner is computed/resolved
    independently of per-length harvest when class is known.
    """

    name = "computed_blob"

    def capability(self) -> WsSendCapability:
        from pigeon_protocol.foundation.ws_blob_compute import inner_class_registry, registry_report
        from pigeon_protocol.foundation.ws_blob_re import re_status
        from pigeon_protocol.ws_sign_bucket import coverage_report, is_supported_text_len

        cov = coverage_report()
        reg = inner_class_registry()
        rep = registry_report()
        re_info = re_status()

        return WsSendCapability(
            strategy=self.name,
            ready=len(reg) >= 4 and cov.get("supported_count_1_200", 0) >= 1,
            computed_blob=True,
            bucket_count=len(reg),
            template_lengths=sorted({c.canonical_text_b for c in reg.values()}),
            notes=[
                "Formula: inner(textB) = session_constant[class(textB)]",
                f"Equivalence classes: {len(reg)} (coverage {cov.get('supported_count_1_200')}/200)",
                rep.get("formula", ""),
                "Crypto body (bytes 8-168) = IM SDK session seed — pool/cache bootstrap",
            ]
            + re_info.get("notes", [])[:1],
        )

    def can_send(self, text: str) -> bool:
        from pigeon_protocol.foundation.ws_blob_compute import inner_class_for_text_b
        from pigeon_protocol.ws_sign_bucket import is_supported_text_len

        bl = len(text.encode("utf-8"))
        if bl > 200:
            return False
        if inner_class_for_text_b(bl):
            return True
        return is_supported_text_len(bl)

    def build_frame(self, text: str, **kwargs: Any) -> bytes:
        from pigeon_protocol.foundation.ws_blob_compute import (
            InnerComputeError,
            compute_inner_bytes,
            inner_class_for_text_b,
        )
        from pigeon_protocol.foundation.ws_blob_store import patch_inner_blob

        session = kwargs.get("session")
        bl = len(text.encode("utf-8"))
        inner_class = inner_class_for_text_b(bl)
        if not inner_class:
            return BucketCanonicalStrategy().build_frame(text, **kwargs)

        skeleton = BucketCanonicalStrategy()
        frame = skeleton.build_frame(text, **kwargs)

        try:
            inner = compute_inner_bytes(session, bl)
        except InnerComputeError:
            logger.debug("computed inner fallback to template blob textB=%s", bl)
            return frame

        data = bytearray(frame)
        if not patch_inner_blob(data, inner):
            logger.warning("patch computed inner failed textB=%s", bl)
            return frame

        out = bytes(data)
        if session is not None:
            try:
                from pigeon_protocol.foundation.ws_blob_compute import _store_session_class_inner
                from pigeon_protocol.foundation.ws_session_inner import store_inner_from_frame

                _store_session_class_inner(session, inner_class.class_id, inner)
                store_inner_from_frame(session, out, text)
            except Exception as exc:
                logger.debug("post-compute cache skipped: %s", exc)
        logger.debug(
            "computed_blob class=%s textB=%s inner=%s…",
            inner_class.name,
            bl,
            inner[:8].hex(),
        )
        return out


class SessionInnerReuseStrategy(ComputedBlobStrategy):
    """Deprecated alias — computed blob supersedes session-inner reuse."""

    name = "session_inner_reuse"


class WsSendEngine:
    """Prefer computed inner (equivalence-class formula); fallback bucket templates."""

    def __init__(self, strategies: list[WsSignStrategy] | None = None) -> None:
        self.strategies: list[WsSignStrategy] = strategies or [
            ComputedBlobStrategy(),
            BucketCanonicalStrategy(),
        ]

    def active(self) -> WsSignStrategy:
        for strat in self.strategies:
            cap = strat.capability()
            if cap.ready and strat.name == "computed_blob":
                return strat
        for strat in self.strategies:
            if strat.capability().ready:
                return strat
        return self.strategies[-1]

    def capability(self) -> WsSendCapability:
        for strat in self.strategies:
            cap = strat.capability()
            if cap.ready:
                cap.notes.append(f"active: {strat.name}")
                return cap
        return WsSendCapability(strategy="none", ready=False)

    def build_frame(self, text: str, **kwargs: Any) -> bytes:
        strat = self.active()
        if not strat.can_send(text):
            bl = len(text.encode("utf-8"))
            from pigeon_protocol.ws_sign_bucket import unsupported_reason

            raise RuntimeError(
                unsupported_reason(bl)
                or f"ws send not ready textB={bl}; strategy={strat.name}"
            )
        return strat.build_frame(text, **kwargs)
