"""Shared conversation list fetch — api_server + go_bridge."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

_CONV_CACHE: dict[tuple[str, str, int, int], dict[str, Any]] = {}
_CONV_CACHE_LOCK = threading.Lock()
FRESH_TTL_SEC = 60.0
STALE_TTL_SEC = 600.0
_DISK_CACHE_LOADED = False
_DISK_CACHE_ACCOUNT = ""


def clear_conv_cache(*, account_id: str | None = None) -> None:
    """Drop in-memory conv cache; optionally only for one account."""
    global _DISK_CACHE_LOADED, _DISK_CACHE_ACCOUNT
    aid = str(account_id or "").strip()
    with _CONV_CACHE_LOCK:
        if aid:
            for key in list(_CONV_CACHE.keys()):
                if str(key[0]) == aid:
                    del _CONV_CACHE[key]
        else:
            _CONV_CACHE.clear()
        _DISK_CACHE_LOADED = False
        _DISK_CACHE_ACCOUNT = ""


def _queue_keys_for_category(category: str) -> tuple[str, ...] | None:
    from pigeon_protocol.config import XUNDAN_QUEUE_KEYS

    cat = str(category or "").strip().lower()
    if cat == "recent":
        return ("all",)
    if cat in ("all", "current"):
        return XUNDAN_QUEUE_KEYS
    return None


def _cache_key(*, category: str, page: int, size: int) -> tuple[str, str, int, int]:
    from pigeon_protocol.account_context import active_account_id
    from pigeon_protocol.session import load_session

    session = load_session()
    aid = (
        active_account_id()
        or str(session.shop_id or "")
        or str((session.cookies or {}).get("SHOP_ID") or "")
        or "default"
    )
    return (str(aid), str(category or ""), int(page), int(size))


def _disk_cache_path() -> Path:
    from pigeon_protocol.account_context import bundle_file

    return bundle_file("conv_list_cache.json")


def _load_disk_caches_once() -> None:
    global _DISK_CACHE_LOADED, _DISK_CACHE_ACCOUNT
    if _DISK_CACHE_LOADED:
        return
    _DISK_CACHE_LOADED = True
    path = _disk_cache_path()
    if not path.is_file():
        return
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    entries = doc.get("entries") if isinstance(doc, dict) else None
    if not isinstance(entries, dict):
        return
    with _CONV_CACHE_LOCK:
        for raw_key, payload in entries.items():
            if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                continue
            parts = str(raw_key).split("|", 3)
            if len(parts) != 4:
                continue
            try:
                key = (parts[0], parts[1], int(parts[2]), int(parts[3]))
            except ValueError:
                continue
            _CONV_CACHE[key] = dict(payload)
    try:
        from pigeon_protocol.account_context import active_account_id

        _DISK_CACHE_ACCOUNT = active_account_id()
    except Exception:
        _DISK_CACHE_ACCOUNT = ""


def _disk_cache_key(key: tuple[str, str, int, int]) -> str:
    return "|".join(str(part) for part in key)


def _persist_disk_cache(key: tuple[str, str, int, int], payload: dict[str, Any]) -> None:
    path = _disk_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _CONV_CACHE_LOCK:
        entries: dict[str, Any] = {}
        if path.is_file():
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(doc, dict) and isinstance(doc.get("entries"), dict):
                    entries = dict(doc["entries"])
            except (OSError, json.JSONDecodeError):
                entries = {}
        entries[_disk_cache_key(key)] = {
            "items": payload.get("items") or [],
            "raw": payload.get("raw") if isinstance(payload.get("raw"), dict) else {},
            "count": payload.get("count", 0),
            "updated_at": payload.get("updated_at", time.time()),
            "source": payload.get("source") or "cache",
        }
        path.write_text(
            json.dumps({"version": 1, "entries": entries}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _cache_get(key: tuple[str, str, int, int], *, allow_stale: bool = True) -> dict[str, Any] | None:
    _load_disk_caches_once()
    now = time.time()
    with _CONV_CACHE_LOCK:
        entry = _CONV_CACHE.get(key)
        if not entry:
            return None
        age = now - float(entry.get("updated_at") or 0)
        if age <= FRESH_TTL_SEC:
            return dict(entry)
        if allow_stale and age <= STALE_TTL_SEC:
            return dict(entry)
        return None


def _cache_set(key: tuple[str, str, int, int], payload: dict[str, Any]) -> None:
    with _CONV_CACHE_LOCK:
        _CONV_CACHE[key] = dict(payload)
    _persist_disk_cache(key, payload)


def _cache_payload(*, items: list[dict[str, Any]], raw: dict[str, Any], source: str) -> dict[str, Any]:
    return {
        "items": items,
        "raw": raw,
        "count": len(items),
        "updated_at": time.time(),
        "source": source,
    }


def _conv_ok(raw: dict[str, Any], items: list[dict[str, Any]]) -> bool:
    code = raw.get("code") or raw.get("st")
    return str(code) in ("0", "200") or bool(items) or bool(raw.get("ok"))


def _finalize_conversation_items(
    items: list[dict[str, Any]],
    *,
    session=None,
    enrich_user_card: bool = True,
) -> list[dict[str, Any]]:
    from pigeon_protocol.buyer_display_name import enrich_items_with_user_card, normalize_conversation_items

    normalized = normalize_conversation_items(items)
    if enrich_user_card and session is not None and normalized:
        return enrich_items_with_user_card(session, normalized)
    return normalized


def _light_response(
    *,
    ok: bool,
    items: list[dict[str, Any]],
    raw: dict[str, Any] | None,
    source: str,
    warning: str = "",
    needs_repair: bool = False,
    error: str = "",
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": ok,
        "items": items,
        "raw": raw or {},
        "count": len(items),
        "light": True,
        "source": source,
    }
    if warning:
        out["warning"] = warning
    if needs_repair:
        out["needs_repair"] = True
    if error:
        out["error"] = error
    return out


def _fetch_conversations_light(
    *,
    page: int,
    size: int,
    category: str,
    queue_keys: tuple[str, ...] | None,
) -> dict[str, Any]:
    from pigeon_protocol.conv_list import list_conversations_relay, parse_conversation_items
    from pigeon_protocol.session import load_session

    session = load_session()
    cache_key = _cache_key(category=category, page=page, size=size)

    fresh = _cache_get(cache_key, allow_stale=False)
    if fresh and fresh.get("items"):
        items = _finalize_conversation_items(list(fresh["items"]), session=session)
        return _light_response(
            ok=True,
            items=items,
            raw=fresh.get("raw") if isinstance(fresh.get("raw"), dict) else {},
            source="cache_fresh",
        )

    snap_raw = list_conversations_relay(
        session,
        page=page,
        size=size,
        queue_keys=queue_keys,
        skip_warm=True,
        snapshot_only=True,
    )
    snap_items = _finalize_conversation_items(parse_conversation_items(snap_raw), session=session)
    if snap_items:
        payload = _cache_payload(items=snap_items, raw=snap_raw, source="snapshot")
        _cache_set(cache_key, payload)
        return _light_response(ok=True, items=snap_items, raw=snap_raw, source="snapshot")

    try:
        from pigeon_protocol.conv_list_fallback import list_conversations_fallback

        local_raw = list_conversations_fallback(session, limit=size)
        local_items = _finalize_conversation_items(parse_conversation_items(local_raw), session=session)
        if local_items:
            payload = _cache_payload(items=local_items, raw=local_raw, source="local_snapshot")
            _cache_set(cache_key, payload)
            return _light_response(ok=True, items=local_items, raw=local_raw, source="local_snapshot")
    except Exception:
        pass

    live_raw: dict[str, Any] = {}
    live_items: list[dict[str, Any]] = []
    try:
        live_raw = list_conversations_relay(
            session,
            page=page,
            size=size,
            queue_keys=queue_keys,
            skip_warm=True,
            request_timeout_sec=2.5,
        )
        live_items = _finalize_conversation_items(parse_conversation_items(live_raw), session=session)
    except Exception as exc:
        live_raw = {"ok": False, "error": str(exc)}

    if live_items and _conv_ok(live_raw, live_items):
        payload = _cache_payload(items=live_items, raw=live_raw, source="live")
        _cache_set(cache_key, payload)
        return _light_response(ok=True, items=live_items, raw=live_raw, source="live")

    stale = _cache_get(cache_key, allow_stale=True)
    if stale and stale.get("items"):
        items = _finalize_conversation_items(list(stale["items"]), session=session)
        return _light_response(
            ok=True,
            items=items,
            raw=stale.get("raw") if isinstance(stale.get("raw"), dict) else {},
            source="cache_stale",
            warning="会话刷新慢，已显示上次列表",
        )

    err = str(live_raw.get("error") or "暂时没拉到会话，可点击修复连接")
    return _light_response(
        ok=False,
        items=[],
        raw=live_raw,
        source="empty",
        needs_repair=True,
        error=err,
    )


def fetch_conversations(*, page: int = 0, size: int = 30, category: str = "", light: bool = False) -> dict[str, Any]:
    from pigeon_protocol.config import XUNDAN_QUEUE_KEYS
    from pigeon_protocol.conv_list import list_conversations_relay, parse_conversation_items
    from pigeon_protocol.session import load_session

    queue_keys = _queue_keys_for_category(category)
    if light:
        return _fetch_conversations_light(page=page, size=size, category=category, queue_keys=queue_keys)

    session = load_session()
    cache_key = _cache_key(category=category, page=page, size=size)
    raw = list_conversations_relay(
        session,
        page=page,
        size=size,
        queue_keys=queue_keys,
    )
    code = raw.get("code") or raw.get("st")
    items = _finalize_conversation_items(parse_conversation_items(raw), session=session)
    ok = str(code) in ("0", "200") or bool(items) or bool(raw.get("ok"))

    if ok and not items:
        try:
            from pigeon_protocol.conv_list_fallback import list_conversations_fallback

            fb = list_conversations_fallback(session, limit=size)
            fb_items = _finalize_conversation_items(parse_conversation_items(fb), session=session)
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
        items = _finalize_conversation_items(parse_conversation_items(raw), session=session)
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
        items = _finalize_conversation_items(parse_conversation_items(raw), session=session)
        ok = str(code) in ("0", "200") or bool(items) or bool(raw.get("ok"))

    if not ok and not items:
        try:
            from pigeon_protocol.config import AppConfig
            from pigeon_protocol.standalone import StandaloneRuntime

            rt = StandaloneRuntime(config=AppConfig(dry_run=False))
            fallback = rt.context.list_conversations(page=page, size=size)
            items = _finalize_conversation_items(parse_conversation_items(fallback), session=session)
            raw = fallback if isinstance(fallback, dict) else {"data": fallback}
            if isinstance(raw, dict):
                raw["via"] = str(raw.get("via") or "fallback/fuzzySearchConversation")
            ok = bool(items) or str((raw or {}).get("code")) in ("0", "200")
        except Exception as exc:
            if isinstance(raw, dict):
                raw.setdefault("fallback_error", str(exc))

    if ok and items:
        _cache_set(cache_key, _cache_payload(items=items, raw=raw, source="live"))

    return {
        "ok": ok,
        "items": items,
        "raw": raw,
        "count": len(items),
    }
