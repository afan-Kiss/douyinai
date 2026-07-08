"""Resolve and persist the human-readable shop name (not shop_id)."""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("pigeon.shop_profile")

_PLACEHOLDER_RE = re.compile(r"^店铺\s*\d+$")


def is_placeholder_shop_label(label: str, shop_id: str = "") -> bool:
    text = str(label or "").strip()
    if not text:
        return True
    if _PLACEHOLDER_RE.match(text):
        return True
    sid = str(shop_id or "").strip()
    if sid and text in {sid, f"shop_{sid}", f"店铺{sid}", f"店铺 {sid}"}:
        return True
    if text.startswith("acct_"):
        return True
    if text in {"新账号", "空账号槽", "test", "飞鸽客服"}:
        return True
    return False


def shop_name_from_mapping(data: dict[str, Any] | None, *, shop_id: str = "") -> str:
    if not isinstance(data, dict):
        return ""
    for key in ("shop_name", "ShopName", "shopName", "store_name", "label"):
        value = str(data.get(key) or "").strip()
        if value and not is_placeholder_shop_label(value, shop_id):
            return value
    extra = data.get("extra")
    if isinstance(extra, dict):
        for key in ("shop_name", "ShopName", "shopName"):
            value = str(extra.get(key) or "").strip()
            if value and not is_placeholder_shop_label(value, shop_id):
                return value
    return ""


def cached_shop_name(session=None, *, registry_row: dict[str, Any] | None = None) -> str:
    shop_id = ""
    if registry_row:
        shop_id = str(registry_row.get("shop_id") or "")
        name = shop_name_from_mapping(registry_row, shop_id=shop_id)
        if name:
            return name
    if session is not None:
        cookies = getattr(session, "cookies", None) or {}
        shop_id = shop_id or str(getattr(session, "shop_id", "") or cookies.get("SHOP_ID") or "")
        blob = {
            "shop_name": getattr(session, "shop_name", None),
            "label": getattr(session, "label", None),
            "extra": getattr(session, "extra", None) or {},
        }
        name = shop_name_from_mapping(blob, shop_id=shop_id)
        if name:
            return name
        sess_dict = session.to_dict() if hasattr(session, "to_dict") else None
        name = shop_name_from_mapping(sess_dict, shop_id=shop_id)
        if name:
            return name
    return ""


def display_shop_name(session=None, *, registry_row: dict[str, Any] | None = None, shop_id: str = "") -> str:
    name = cached_shop_name(session, registry_row=registry_row)
    if name:
        return name
    sid = str(
        shop_id
        or (registry_row or {}).get("shop_id")
        or getattr(session, "shop_id", "")
        or ((getattr(session, "cookies", None) or {}).get("SHOP_ID") if session is not None else "")
        or ""
    ).strip()
    return sid or "飞鸽客服"


def persist_shop_name(
    *,
    shop_name: str,
    shop_id: str = "",
    account_id: str = "",
    session=None,
) -> str:
    name = str(shop_name or "").strip()
    sid = str(shop_id or "").strip()
    if not name or is_placeholder_shop_label(name, sid):
        return ""

    if session is not None:
        extra = getattr(session, "extra", None)
        if not isinstance(extra, dict):
            session.extra = {}
            extra = session.extra
        extra["shop_name"] = name
        if sid and not getattr(session, "shop_id", ""):
            session.shop_id = sid
        try:
            from pigeon_protocol.session import save_session

            save_session(session)
        except Exception as exc:
            logger.debug("persist shop_name to session failed: %s", exc)

    try:
        from pigeon_protocol.account_context import active_account_id, register_account

        aid = str(account_id or active_account_id() or "").strip()
        if aid:
            kwargs: dict[str, Any] = {"label": name, "set_active": False}
            if sid:
                kwargs["shop_id"] = sid
            register_account(aid, **kwargs)
    except Exception as exc:
        logger.debug("persist shop_name to registry failed: %s", exc)
    return name


def fetch_current_shop_profile(session) -> dict[str, Any]:
    """GET /backstage/currentuser → ShopName / ShopId / ShopLogo."""
    from pigeon_protocol.config import PIGEON_HOST
    from pigeon_protocol.foundation.relay_client import BackstageRelayClient

    client = BackstageRelayClient(session)
    if not client.available():
        return {"ok": False, "error": "relay unavailable"}

    unsigned = f"{PIGEON_HOST}/backstage/currentuser?biz_type=4&_pms=1&device_platform=web&FUSION=true"
    relay = client.get(unsigned, via="shop_profile/currentuser")
    data = relay.data if isinstance(relay.data, dict) else {}
    payload = data.get("data") if isinstance(data.get("data"), dict) else data
    if not isinstance(payload, dict):
        payload = {}
    shop_name = str(payload.get("ShopName") or payload.get("shop_name") or payload.get("shopName") or "").strip()
    shop_id = str(payload.get("ShopId") or payload.get("shop_id") or payload.get("ShopID") or "").strip()
    shop_logo = str(payload.get("ShopLogo") or payload.get("shop_logo") or "").strip()
    ok = bool(relay.api_ok() or shop_name)
    if ok and shop_name:
        persist_shop_name(shop_name=shop_name, shop_id=shop_id, session=session)
    return {
        "ok": ok,
        "shop_name": shop_name,
        "shop_id": shop_id,
        "shop_logo": shop_logo,
        "via": relay.via,
        "error": "" if ok else (relay.error or "currentuser failed"),
        "raw": data,
    }


def ensure_shop_name(session, *, fetch: bool = True) -> str:
    name = cached_shop_name(session)
    if name:
        return name
    if not fetch:
        return display_shop_name(session)
    try:
        profile = fetch_current_shop_profile(session)
        if profile.get("shop_name"):
            return str(profile["shop_name"])
    except Exception as exc:
        logger.debug("ensure_shop_name fetch failed: %s", exc)
    return display_shop_name(session)
