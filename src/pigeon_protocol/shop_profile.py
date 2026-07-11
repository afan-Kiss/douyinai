"""Resolve and persist the human-readable shop name (not shop_id)."""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("pigeon.shop_profile")

_PLACEHOLDER_RE = re.compile(r"^店铺\s*\d+$")
_ROUTE_SHOP_RE = re.compile(r":(\d{5,})::")

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


def infer_shop_id_from_session(session) -> str:
    cookies = getattr(session, "cookies", None) or {}
    for key in ("SHOP_ID", "ecom_gray_shop_id"):
        value = str(cookies.get(key) or "").strip()
        if value.isdigit():
            return value
    sid = str(getattr(session, "shop_id", "") or "").strip()
    if sid.isdigit():
        return sid
    extra = getattr(session, "extra", None) or {}
    if not isinstance(extra, dict):
        return ""

    def scan_text(text: str) -> str:
        m = _ROUTE_SHOP_RE.search(str(text or ""))
        return m.group(1) if m else ""

    for key in ("edbx_init_route_hint", "edbx_route_sample", "route_hint"):
        found = scan_text(str(extra.get(key) or ""))
        if found:
            return found
    derive = extra.get("edbx_derive")
    if isinstance(derive, dict):
        for key in ("route_hint", "edbx_init_route_hint"):
            found = scan_text(str(derive.get(key) or ""))
            if found:
                return found
    for val in extra.values():
        if isinstance(val, str):
            found = scan_text(val)
            if found:
                return found
        elif isinstance(val, dict):
            for inner in val.values():
                if isinstance(inner, str):
                    found = scan_text(inner)
                    if found:
                        return found
    return ""


def load_stored_shop_profile(*, account_id: str = "", shop_id: str = "") -> dict[str, str]:
    from pigeon_protocol.account_context import _read_json, account_home, load_registry

    sid = str(shop_id or "").strip()
    aids: list[str] = []
    if sid:
        aids.append(f"shop_{sid}")
    aid = str(account_id or "").strip()
    if aid and aid not in aids:
        aids.append(aid)

    for row_id in aids:
        home = account_home(row_id)
        paths = [home / "session.json"]
        backups = home / "backups"
        if backups.is_dir():
            paths.extend(sorted(backups.glob("*/session.json"), reverse=True))
        for path in paths:
            if not path.is_file():
                continue
            doc = _read_json(path) or {}
            cookies = doc.get("cookies") if isinstance(doc.get("cookies"), dict) else {}
            extra = doc.get("extra") if isinstance(doc.get("extra"), dict) else {}
            found_shop = str(
                doc.get("shop_id") or cookies.get("SHOP_ID") or cookies.get("ecom_gray_shop_id") or sid
            ).strip()
            name = str(extra.get("shop_name") or doc.get("shop_name") or "").strip()
            if name and not is_placeholder_shop_label(name, found_shop or sid):
                return {"shop_id": found_shop or sid, "shop_name": name}

    if sid:
        for row in load_registry().get("accounts") or []:
            if not isinstance(row, dict):
                continue
            row_shop = str(row.get("shop_id") or "").strip()
            row_id = str(row.get("id") or "")
            if row_shop != sid and row_id != f"shop_{sid}":
                continue
            label = str(row.get("label") or "").strip()
            if label and not is_placeholder_shop_label(label, sid):
                return {"shop_id": sid, "shop_name": label}
    return {"shop_id": sid, "shop_name": ""}


def repair_session_shop_identity(session, *, account_id: str = "", set_active: bool = False) -> dict[str, Any]:
    from pigeon_protocol.session import save_session

    report: dict[str, Any] = {"ok": False}
    cookies = getattr(session, "cookies", None) or {}
    if not (cookies.get("sessionid") or cookies.get("sid_tt")):
        return report

    shop = infer_shop_id_from_session(session)
    if not shop:
        stored = load_stored_shop_profile(account_id=account_id)
        shop = str(stored.get("shop_id") or "").strip()
    stored = load_stored_shop_profile(account_id=account_id, shop_id=shop)
    shop = shop or str(stored.get("shop_id") or "").strip()
    shop_name = str(stored.get("shop_name") or cached_shop_name(session) or "").strip()

    if shop:
        session.shop_id = shop
        session.cookies["SHOP_ID"] = shop
        report["shop_id"] = shop
        try:
            save_session(session)
        except OSError as exc:
            logger.debug("repair_session_shop_identity save: %s", exc)

    if not shop_name and shop:
        try:
            profile = fetch_current_shop_profile(session)
            shop_name = str(profile.get("shop_name") or "").strip()
            if not shop and str(profile.get("shop_id") or "").strip().isdigit():
                shop = str(profile.get("shop_id") or "").strip()
                session.shop_id = shop
                session.cookies["SHOP_ID"] = shop
        except Exception as exc:
            logger.debug("repair_session_shop_identity fetch: %s", exc)

    if shop_name:
        persist_shop_name(shop_name=shop_name, shop_id=shop, session=session, account_id=account_id)
        report["shop_name"] = shop_name

    if shop:
        from pigeon_protocol.account_context import register_account_from_session

        report["account_id"] = register_account_from_session(
            session,
            set_active=set_active,
            source_account_id=account_id,
        )
        report["ok"] = True
    return report


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
    if session is not None and sid:
        stored = load_stored_shop_profile(shop_id=sid)
        stored_name = str(stored.get("shop_name") or "").strip()
        if stored_name:
            return stored_name
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
    shop = infer_shop_id_from_session(session)
    if shop:
        stored = load_stored_shop_profile(shop_id=shop)
        stored_name = str(stored.get("shop_name") or "").strip()
        if stored_name:
            persist_shop_name(shop_name=stored_name, shop_id=shop, session=session)
            return stored_name
    if not fetch:
        return display_shop_name(session)
    try:
        profile = fetch_current_shop_profile(session)
        if profile.get("shop_name"):
            return str(profile["shop_name"])
    except Exception as exc:
        logger.debug("ensure_shop_name fetch failed: %s", exc)
    return display_shop_name(session)


def sync_session_shop_identity(
    session,
    *,
    set_active: bool = False,
    source_account_id: str = "",
) -> str:
    """Resolve ShopId/ShopName after login and register canonical shop_* account."""
    from pigeon_protocol.session import save_session

    cookies = getattr(session, "cookies", None) or {}
    if not (cookies.get("sessionid") or cookies.get("sid_tt")):
        return str(source_account_id or "").strip()

    shop = infer_shop_id_from_session(session) or str(getattr(session, "shop_id", "") or cookies.get("SHOP_ID") or "").strip()
    stored = load_stored_shop_profile(account_id=source_account_id, shop_id=shop)
    shop = shop or str(stored.get("shop_id") or "").strip()
    profile: dict[str, Any] = {}
    need_profile = not shop or not (cached_shop_name(session) or stored.get("shop_name"))
    if need_profile:
        try:
            profile = fetch_current_shop_profile(session)
        except Exception as exc:
            logger.debug("sync_session_shop_identity profile: %s", exc)
            profile = {}

    shop = shop or str(profile.get("shop_id") or "").strip()
    if shop:
        session.shop_id = shop
        session.cookies["SHOP_ID"] = shop
        try:
            save_session(session)
        except OSError as exc:
            logger.debug("sync_session_shop_identity save: %s", exc)

    shop_name = str(profile.get("shop_name") or stored.get("shop_name") or "").strip()
    if shop_name:
        persist_shop_name(shop_name=shop_name, shop_id=shop, session=session, account_id=source_account_id)
    elif shop:
        ensure_shop_name(session, fetch=True)

    from pigeon_protocol.account_context import register_account_from_session

    return register_account_from_session(
        session,
        set_active=set_active,
        source_account_id=source_account_id,
    )
