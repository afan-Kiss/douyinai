"""Shared conversation list fetch — api_server + go_bridge."""
from __future__ import annotations

from typing import Any


def _queue_keys_for_category(category: str) -> tuple[str, ...] | None:
    from pigeon_protocol.config import XUNDAN_QUEUE_KEYS

    cat = str(category or "").strip().lower()
    if cat == "recent":
        return ("all",)
    if cat in ("all", "current"):
        return XUNDAN_QUEUE_KEYS
    return None


def fetch_conversations(*, page: int = 0, size: int = 30, category: str = "", light: bool = False) -> dict[str, Any]:
    from pigeon_protocol.config import XUNDAN_QUEUE_KEYS
    from pigeon_protocol.conv_list import list_conversations_relay, parse_conversation_items
    from pigeon_protocol.session import load_session

    session = load_session()
    queue_keys = _queue_keys_for_category(category)
    raw = list_conversations_relay(
        session,
        page=page,
        size=size,
        queue_keys=queue_keys,
    )
    code = raw.get("code") or raw.get("st")
    items = parse_conversation_items(raw)
    ok = str(code) in ("0", "200") or bool(items) or bool(raw.get("ok"))

    if light:
        return {
            "ok": ok,
            "items": items,
            "raw": raw,
            "count": len(items),
            "light": True,
        }

    if ok and not items:
        try:
            from pigeon_protocol.conv_list_fallback import list_conversations_fallback

            fb = list_conversations_fallback(session, limit=size)
            fb_items = parse_conversation_items(fb)
            if fb_items:
                raw = fb
                items = fb_items
                ok = True
        except Exception as exc:
            if isinstance(raw, dict):
                raw.setdefault("fallback_error", str(exc))

    if not ok and not items and category == "recent":
        raw = list_conversations_relay(
            session,
            page=page,
            size=size,
            queue_keys=XUNDAN_QUEUE_KEYS,
        )
        code = raw.get("code") or raw.get("st")
        items = parse_conversation_items(raw)
        ok = str(code) in ("0", "200") or bool(items) or bool(raw.get("ok"))

    if not ok and not items:
        from pigeon_protocol.session_health import auto_heal_session

        auto_heal_session(session, refresh_csrf=True, refresh_sign=True)
        raw = list_conversations_relay(
            session,
            page=page,
            size=size,
            queue_keys=queue_keys,
        )
        code = raw.get("code") or raw.get("st")
        items = parse_conversation_items(raw)
        ok = str(code) in ("0", "200") or bool(items) or bool(raw.get("ok"))

    if not ok and not items:
        try:
            from pigeon_protocol.config import AppConfig
            from pigeon_protocol.standalone import StandaloneRuntime

            rt = StandaloneRuntime(config=AppConfig(dry_run=False))
            fallback = rt.context.list_conversations(page=page, size=size)
            items = parse_conversation_items(fallback)
            raw = fallback if isinstance(fallback, dict) else {"data": fallback}
            if isinstance(raw, dict):
                raw["via"] = str(raw.get("via") or "fallback/fuzzySearchConversation")
            ok = bool(items) or str((raw or {}).get("code")) in ("0", "200")
        except Exception as exc:
            if isinstance(raw, dict):
                raw.setdefault("fallback_error", str(exc))

    return {
        "ok": ok,
        "items": items,
        "raw": raw,
        "count": len(items),
    }
