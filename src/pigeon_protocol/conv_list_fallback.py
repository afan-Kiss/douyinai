"""Conversation list fallback when xundan / current_conv APIs return whale 11001."""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from pigeon_protocol.buyer_display_name import (
    buyer_label_from_uid,
    extract_buyer_name_from_obj,
    is_bad_display_name,
    sanitize_conv_preview,
)

logger = logging.getLogger("pigeon.conv_fallback")

ROOT = Path(__file__).resolve().parents[2]
AQ_UID_RE = re.compile(r"AQ[A-Za-z0-9_\-]{50,96}")
_DEBUG_CARD_MAX_LINES = 200


def _bundle_dir() -> Path:
    from pigeon_protocol.account_context import bundle_dir

    return bundle_dir()


def _init_bin() -> Path:
    return _bundle_dir() / "get_message_by_init_response.bin"


def _normalize_uid(raw: str) -> str:
    uid = str(raw or "").strip()
    if not uid.startswith("AQ"):
        return ""
    if len(uid) > 88:
        uid = uid[:88]
    return uid if len(uid) >= 50 else ""


def _uids_from_init_bin(path: Path | None = None) -> list[str]:
    path = path or _init_bin()
    if not path.is_file():
        return []
    try:
        data = path.read_bytes()
    except OSError:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in AQ_UID_RE.finditer(data.decode("latin-1", errors="ignore")):
        uid = _normalize_uid(m.group(0))
        if uid and uid not in seen:
            seen.add(uid)
            out.append(uid)
    return out


def _uids_from_bundle_orders() -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    bundle = _bundle_dir()
    orders_dir = bundle / "orders"
    if not orders_dir.is_dir():
        return out
    for path in orders_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        uid = _normalize_uid(str(data.get("security_user_id") or ""))
        if uid and uid not in seen:
            seen.add(uid)
            out.append(uid)
    snap = bundle / "order_sign_snapshot.json"
    if snap.is_file():
        try:
            data = json.loads(snap.read_text(encoding="utf-8"))
            uid = _normalize_uid(str(data.get("security_user_id") or ""))
            if uid and uid not in seen:
                seen.add(uid)
                out.append(uid)
        except (OSError, json.JSONDecodeError):
            pass
    return out


def _uids_from_session_notes(session) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for note in getattr(session, "notes", []) or []:
        text = str(note)
        if "security_user_id=" not in text and "security_uid" not in text:
            for m in AQ_UID_RE.finditer(text):
                uid = _normalize_uid(m.group(0))
                if uid and uid not in seen:
                    seen.add(uid)
                    out.append(uid)
            continue
        chunk = text.split("=", 1)[-1]
        uid = _normalize_uid(chunk)
        if uid and uid not in seen:
            seen.add(uid)
            out.append(uid)
    return out


def _uids_from_analysis_json() -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for rel in (
        "analysis/feige_invoke_out.json",
        "analysis/feige_rust_invoke_report.json",
    ):
        path = ROOT / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
            data = json.loads(text)
        except (OSError, json.JSONDecodeError):
            continue
        for key in ("conversation_id", "security_user_id"):
            val = str(data.get(key) or "")
            if val:
                uid = _normalize_uid(val.split(":")[0])
                if uid and uid not in seen:
                    seen.add(uid)
                    out.append(uid)
        for m in AQ_UID_RE.finditer(text):
            uid = _normalize_uid(m.group(0))
            if uid and uid not in seen:
                seen.add(uid)
                out.append(uid)
    return out


def discover_security_uids(session=None, *, limit: int = 50) -> list[str]:
    """Collect buyer security UIDs from offline session artifacts (no xundan)."""
    seen: set[str] = set()
    merged: list[str] = []
    for src in (
        _uids_from_bundle_orders(),
        _uids_from_init_bin(),
        _uids_from_session_notes(session) if session is not None else [],
        _uids_from_analysis_json(),
    ):
        for uid in src:
            if uid not in seen:
                seen.add(uid)
                merged.append(uid)
            if len(merged) >= limit:
                return merged
    return merged


def _collect_name_candidates(inner: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in (
        "user_name",
        "nick_name",
        "nickname",
        "display_name",
        "buyer_name",
        "user_from_desc",
    ):
        val = inner.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()[:80]
    nested = extract_buyer_name_from_obj(inner)
    if nested:
        out["extracted"] = nested[:80]
    return out


def _debug_conv_card_dump(uid: str, inner: dict[str, Any], name: str, source: str) -> None:
    if os.getenv("PIGEON_DEBUG_CONV_CARD", "").strip() != "1":
        return
    try:
        from pigeon_protocol.account_context import logs_dir

        debug_dir = logs_dir() / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        path = debug_dir / "conv_user_card_keys.jsonl"
        uid_tail = uid[-6:] if len(uid) >= 6 else "short"
        row = {
            "ts": int(time.time()),
            "uid_tail": uid_tail,
            "top_keys": sorted(str(k) for k in inner.keys())[:40],
            "candidate_names": _collect_name_candidates(inner),
            "source": source,
            "has_card": bool(inner),
            "resolved_name": name[:40] if name else "",
        }
        lines: list[str] = []
        if path.is_file():
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                lines = []
        lines.append(json.dumps(row, ensure_ascii=False))
        if len(lines) > _DEBUG_CARD_MAX_LINES:
            lines = lines[-_DEBUG_CARD_MAX_LINES :]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        logger.debug("conv card debug dump failed: %s", exc)


def _user_card_hint(session, uid: str) -> dict[str, Any]:
    from pigeon_protocol.config import PIGEON_HOST, USER_CARD_PATH
    from pigeon_protocol.foundation.relay_client import BackstageRelayClient

    client = BackstageRelayClient(session)
    if not client.available():
        return {}
    url = (
        f"{PIGEON_HOST}{USER_CARD_PATH}"
        f"?biz_type=4&PIGEON_BIZ_TYPE=2&_pms=1&device_platform=web&FUSION=true"
        f"&security_user_id={uid}"
    )
    relay = client.get(url, via="conv_fallback/user_card")
    if not relay.api_ok():
        return {}
    data = relay.data if isinstance(relay.data, dict) else {}
    inner = data.get("data") if isinstance(data.get("data"), dict) else {}
    trade = inner.get("shop_trade_info") if isinstance(inner.get("shop_trade_info"), dict) else {}
    deal = int(trade.get("shop_deal_count") or 0)
    repeat = int(inner.get("repeat_come_count") or 0)

    name = extract_buyer_name_from_obj(inner)
    if name and is_bad_display_name(name):
        name = ""

    buyer_source = str(inner.get("user_from_desc") or "").strip()
    preview = ""
    if deal > 0:
        preview = f"成交{deal}单"
    elif repeat > 0:
        preview = f"复访{repeat}次"
    elif buyer_source and not is_bad_display_name(buyer_source):
        preview = buyer_source

    _debug_conv_card_dump(uid, inner, name, buyer_source or "user_card")

    return {
        "name": name,
        "buyer_source": buyer_source,
        "preview": preview,
        "card": inner,
    }


def list_conversations_fallback(session, *, limit: int = 30) -> dict[str, Any]:
    """Build minimal conv list from known UIDs + get_user_card (non-whale)."""
    uids = discover_security_uids(session, limit=limit)
    if not uids:
        return {
            "ok": False,
            "error": "no known security_user_id in bundle/init/session",
            "via": "conv_list/fallback",
            "api_code": 11001,
        }

    items: list[dict[str, Any]] = []
    for uid in uids[:limit]:
        hint = _user_card_hint(session, uid)
        name = str(hint.get("name") or "").strip()
        if not name or is_bad_display_name(name):
            name = buyer_label_from_uid(uid)
        preview_raw = str(hint.get("preview") or "已知买家")
        preview = sanitize_conv_preview(preview_raw) or preview_raw
        items.append(
            {
                "security_user_id": uid,
                "name": name,
                "buyer_name": name,
                "display_name": name,
                "buyer_source": str(hint.get("buyer_source") or ""),
                "preview": preview[:120],
                "talk_id": "",
                "queue_key": "fallback",
                "last_time": "",
                "last_time_ms": 0,
                "unread_count": 0,
            }
        )

    return {
        "ok": True,
        "items": items,
        "data": {"code": 0, "data": {"user_list": items}, "fallback": True},
        "via": "conv_list/fallback",
        "sources": {
            "uid_count": len(uids),
            "used": len(items),
        },
        "whale_blocked": True,
        "api_code": 11001,
    }
