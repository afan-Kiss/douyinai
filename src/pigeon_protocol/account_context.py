"""Multi-account path resolution, registry, and runtime switching."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any

from pigeon_protocol.config import ROOT

logger = logging.getLogger("pigeon.account")

REGISTRY_VERSION = 1
ACCOUNTS_ROOT = ROOT / "accounts"
REGISTRY_FILE = ACCOUNTS_ROOT / "registry.json"
LEGACY_SESSION_DIR = ROOT / "session"
LEGACY_BUNDLE_DIR = ROOT / "standalone_bundle"

_PACK_REL_FILES = (
    "session.json",
    "ws_inner_cache.json",
    "ws_inner_portable.json",
    "bundle/ws_inner_canonical.json",
    "bundle/bdms_browser_env.json",
    "bundle/conv_sign_snapshot.json",
    "bundle/order_sign_snapshot.json",
)

# Legacy zip layout (pre multi-account)
_LEGACY_PACK_FILES = (
    "session/session.json",
    "session/ws_inner_cache.json",
    "session/ws_inner_portable.json",
    "standalone_bundle/ws_inner_canonical.json",
    "standalone_bundle/bdms_browser_env.json",
    "standalone_bundle/conv_sign_snapshot.json",
    "standalone_bundle/order_sign_snapshot.json",
)

_LEGACY_IMPORT_MAP = {
    "session/session.json": "session.json",
    "session/ws_inner_cache.json": "ws_inner_cache.json",
    "session/ws_inner_portable.json": "ws_inner_portable.json",
    "standalone_bundle/ws_inner_canonical.json": "bundle/ws_inner_canonical.json",
    "standalone_bundle/bdms_browser_env.json": "bundle/bdms_browser_env.json",
    "standalone_bundle/conv_sign_snapshot.json": "bundle/conv_sign_snapshot.json",
    "standalone_bundle/order_sign_snapshot.json": "bundle/order_sign_snapshot.json",
}

_initialized = False


def _now() -> int:
    return int(time.time())


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def load_registry() -> dict[str, Any]:
    doc = _read_json(REGISTRY_FILE)
    if not doc:
        return {"version": REGISTRY_VERSION, "active_account_id": "", "accounts": []}
    doc.setdefault("version", REGISTRY_VERSION)
    doc.setdefault("active_account_id", "")
    doc.setdefault("accounts", [])
    return doc


def save_registry(doc: dict[str, Any]) -> None:
    doc["version"] = REGISTRY_VERSION
    _write_json(REGISTRY_FILE, doc)


def _account_entry(doc: dict[str, Any], account_id: str) -> dict[str, Any] | None:
    for row in doc.get("accounts") or []:
        if isinstance(row, dict) and str(row.get("id") or "") == account_id:
            return row
    return None


def derive_account_id(*, shop_id: str = "", sessionid: str = "") -> str:
    sid = str(shop_id or "").strip()
    if sid:
        return f"shop_{sid}"
    raw = str(sessionid or "").strip()
    if raw:
        h = hashlib.sha256(raw.encode()).hexdigest()[:12]
        return f"acct_{h}"
    return f"acct_{int(time.time()):x}"


def account_home(account_id: str | None = None) -> Path:
    aid = str(account_id or active_account_id() or "").strip()
    if not aid:
        return LEGACY_SESSION_DIR
    return ACCOUNTS_ROOT / aid


def active_account_id() -> str:
    env = os.getenv("PIGEON_ACCOUNT_ID", "").strip()
    if env:
        return env
    doc = load_registry()
    return str(doc.get("active_account_id") or "").strip()


def session_dir() -> Path:
    global _initialized
    if not _initialized and not os.getenv("PIGEON_SESSION_DIR"):
        init_account_context(migrate=True)
    return Path(os.getenv("PIGEON_SESSION_DIR", account_home()))


def session_file() -> Path:
    return session_dir() / "session.json"


def bundle_dir() -> Path:
    return Path(os.getenv("PIGEON_BUNDLE_DIR", account_home() / "bundle"))


def logs_dir() -> Path:
    return Path(os.getenv("PIGEON_LOGS_DIR", account_home() / "logs"))


def inner_cache_file() -> Path:
    return session_dir() / "ws_inner_cache.json"


def portable_inner_file() -> Path:
    return session_dir() / "ws_inner_portable.json"


def session_pack_file() -> Path:
    return session_dir() / "pigeon_session_pack.zip"


def qr_png_path() -> Path:
    return logs_dir() / "fxg_login_qr.png"


def bundle_file(name: str) -> Path:
    return bundle_dir() / name


def analysis_env_file() -> Path:
    return ROOT / "analysis" / "bdms_browser_env.json"


def backup_dir() -> Path:
    return session_dir() / "backups"


def pack_rel_files() -> tuple[str, ...]:
    return _PACK_REL_FILES


def pack_file_path(rel: str, *, home: Path | None = None) -> Path:
    base = home or account_home()
    return base / rel


def refresh_runtime_paths() -> None:
    """Sync config/pure_config module globals from current env."""
    from pigeon_protocol import config as cfg
    from pigeon_protocol import pure_config as pc

    cfg.refresh_paths()
    pc.refresh_paths()
    try:
        from pigeon_protocol import session_portable as sp

        sp.refresh_paths()
    except Exception as exc:
        logger.warning("refresh session_portable paths: %s", exc)
    try:
        from pigeon_protocol import session_backup as sb

        sb.refresh_paths()
    except Exception as exc:
        logger.warning("refresh session_backup paths: %s", exc)
    try:
        from pigeon_protocol.foundation import ws_session_inner as wsi

        wsi.refresh_paths()
    except Exception as exc:
        logger.warning("refresh ws_session_inner paths: %s", exc)


def apply_account_env(account_id: str | None) -> None:
    aid = str(account_id or "").strip()
    home = account_home(aid) if aid else LEGACY_SESSION_DIR
    os.environ["PIGEON_ACCOUNT_ID"] = aid
    os.environ["PIGEON_SESSION_DIR"] = str(home)
    os.environ["PIGEON_BUNDLE_DIR"] = str(home / "bundle")
    os.environ["PIGEON_LOGS_DIR"] = str(home / "logs")
    refresh_runtime_paths()


def ensure_account_dirs(account_id: str) -> Path:
    home = account_home(account_id)
    home.mkdir(parents=True, exist_ok=True)
    (home / "bundle").mkdir(parents=True, exist_ok=True)
    (home / "logs").mkdir(parents=True, exist_ok=True)
    return home


def account_dedupe_key(row: dict[str, Any], session_doc: dict[str, Any] | None = None) -> str:
    """Stable key for merging duplicate shop rows in the account picker."""
    shop = str(row.get("shop_id") or "").strip()
    if shop:
        return f"shop:{shop}"
    if session_doc is None:
        aid = str(row.get("id") or "").strip()
        session_doc = _read_json(account_home(aid) / "session.json") or {}
    cookies = dict(session_doc.get("cookies") or {})
    shop = str(session_doc.get("shop_id") or cookies.get("SHOP_ID") or "").strip()
    if shop:
        return f"shop:{shop}"
    sid = str(cookies.get("sessionid") or cookies.get("sid_tt") or "").strip()
    if sid and (row.get("logged_in") or cookies.get("sessionid")):
        return f"sid:{hashlib.sha256(sid.encode()).hexdigest()[:16]}"
    return f"empty:{str(row.get('id') or '').strip()}"


def _account_canonical_rank(row: dict[str, Any], active_id: str) -> tuple[int, ...]:
    aid = str(row.get("id") or "")
    return (
        1 if aid == active_id else 0,
        1 if row.get("logged_in") else 0,
        1 if aid.startswith("shop_") else 0,
        int(row.get("updated_at") or 0),
        int(row.get("created_at") or 0),
    )


def dedupe_account_rows(rows: list[dict[str, Any]], *, active_id: str = "") -> list[dict[str, Any]]:
    """One row per shop; collapse empty slots to a single picker entry."""
    active = active_id or active_account_id()
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = account_dedupe_key(row)
        groups.setdefault(key, []).append(row)
    merged: list[dict[str, Any]] = []
    empty_rows: list[dict[str, Any]] = []
    for key, group in groups.items():
        if key.startswith("empty:"):
            empty_rows.extend(group)
            continue
        best = max(group, key=lambda r: _account_canonical_rank(r, active))
        aliases = [str(r.get("id") or "") for r in group if str(r.get("id") or "") != best.get("id")]
        if aliases:
            best = {**best, "alias_ids": aliases}
        merged.append(best)
    if empty_rows:
        pick = max(empty_rows, key=lambda r: _account_canonical_rank(r, active))
        pick = {
            **pick,
            "label": "扫码登录新店铺",
            "is_empty_slot": True,
        }
        merged.append(pick)
    merged.sort(
        key=lambda r: (
            0 if r.get("id") == active else 1,
            0 if r.get("logged_in") else 1,
            str(r.get("label") or r.get("id") or ""),
        )
    )
    return merged


def _copy_account_session_tree(src_home: Path, dest_home: Path) -> None:
    dest_home.mkdir(parents=True, exist_ok=True)
    (dest_home / "bundle").mkdir(parents=True, exist_ok=True)
    (dest_home / "logs").mkdir(parents=True, exist_ok=True)
    for rel in _PACK_REL_FILES:
        src = src_home / rel
        if not src.is_file():
            continue
        dest = dest_home / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.is_file() or src.stat().st_mtime >= dest.stat().st_mtime:
            shutil.copy2(src, dest)


def promote_account_to_shop(account_id: str, shop_id: str) -> str:
    """After QR login on an empty slot, merge into shop_{id} when possible."""
    src = str(account_id or "").strip()
    shop = str(shop_id or "").strip()
    if not src or not shop:
        return src
    canonical = derive_account_id(shop_id=shop)
    if src == canonical:
        register_account(canonical, label=f"店铺 {shop}", shop_id=shop, set_active=True)
        return canonical
    src_home = account_home(src)
    dest_home = ensure_account_dirs(canonical)
    if src_home.is_dir():
        _copy_account_session_tree(src_home, dest_home)
    doc = load_registry()
    doc["accounts"] = [
        row for row in (doc.get("accounts") or []) if isinstance(row, dict) and str(row.get("id") or "") != src
    ]
    save_registry(doc)
    register_account(canonical, label=f"店铺 {shop}", shop_id=shop, set_active=True)
    apply_account_env(canonical)
    logger.info("promoted account %s -> %s (shop %s)", src, canonical, shop)
    return canonical


def consolidate_registry_duplicates() -> dict[str, Any]:
    """Remove duplicate registry rows that refer to the same logged-in shop."""
    doc = load_registry()
    active = str(doc.get("active_account_id") or "").strip()
    raw_rows: list[dict[str, Any]] = []
    for row in doc.get("accounts") or []:
        if not isinstance(row, dict):
            continue
        aid = str(row.get("id") or "").strip()
        if not aid:
            continue
        home = account_home(aid)
        sess = _read_json(home / "session.json") or {}
        cookies = dict(sess.get("cookies") or {})
        logged_in = bool(cookies.get("sessionid") or cookies.get("sid_tt"))
        shop = str(row.get("shop_id") or cookies.get("SHOP_ID") or sess.get("shop_id") or "")
        raw_rows.append(
            {
                "id": aid,
                "shop_id": shop,
                "logged_in": logged_in,
                "updated_at": int(row.get("updated_at") or 0),
                "created_at": int(row.get("created_at") or 0),
                "registry_row": row,
            }
        )
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in raw_rows:
        if not row.get("logged_in"):
            continue
        key = account_dedupe_key(row, None)
        if key.startswith("empty:"):
            continue
        groups.setdefault(key, []).append(row)
    removed: list[str] = []
    for group in groups.values():
        if len(group) < 2:
            continue
        best = max(group, key=lambda r: _account_canonical_rank(r, active))
        keep_id = str(best.get("id") or "")
        for row in group:
            rid = str(row.get("id") or "")
            if rid and rid != keep_id:
                removed.append(rid)
    if removed:
        keep_set = set(removed)
        doc["accounts"] = [
            row
            for row in (doc.get("accounts") or [])
            if isinstance(row, dict) and str(row.get("id") or "") not in keep_set
        ]
        if str(doc.get("active_account_id") or "") in keep_set:
            doc["active_account_id"] = active if active and active not in keep_set else ""
        save_registry(doc)
    return {"removed": removed, "count": len(removed)}


def _build_account_row(row: dict[str, Any], *, active: str) -> dict[str, Any]:
    aid = str(row.get("id") or "")
    home = account_home(aid)
    sess = _read_json(home / "session.json") or {}
    cookies = dict(sess.get("cookies") or {})
    logged_in = bool(cookies.get("sessionid") or cookies.get("sid_tt"))
    shop = str(row.get("shop_id") or cookies.get("SHOP_ID") or sess.get("shop_id") or "")
    label = str(row.get("label") or "")
    if shop:
        display = f"店铺 {shop}"
    elif label and label not in (aid, "test", "新账号") and not label.startswith("acct_"):
        display = label
    elif label == "新账号" or label == "test" or not label:
        display = "空账号槽"
    else:
        display = label or aid
    return {
        "id": aid,
        "label": display,
        "shop_id": shop,
        "active": aid == active,
        "logged_in": logged_in,
        "is_empty_slot": not logged_in,
        "created_at": int(row.get("created_at") or 0),
        "updated_at": int(row.get("updated_at") or 0),
        "home": str(home),
    }


def list_accounts(*, dedupe: bool = True) -> list[dict[str, Any]]:
    consolidate_registry_duplicates()
    doc = load_registry()
    active = active_account_id()
    out: list[dict[str, Any]] = []
    for row in doc.get("accounts") or []:
        if not isinstance(row, dict):
            continue
        aid = str(row.get("id") or "")
        if not aid:
            continue
        out.append(_build_account_row(row, active=active))
    if dedupe:
        return dedupe_account_rows(out, active_id=active)
    return out


def register_account(
    account_id: str,
    *,
    label: str = "",
    shop_id: str = "",
    set_active: bool = False,
) -> dict[str, Any]:
    aid = str(account_id or "").strip()
    if not aid:
        raise ValueError("account_id required")
    ensure_account_dirs(aid)
    doc = load_registry()
    now = _now()
    row = _account_entry(doc, aid)
    if not row:
        row = {"id": aid, "created_at": now}
        doc.setdefault("accounts", []).append(row)
    if label:
        row["label"] = label
    if shop_id:
        row["shop_id"] = str(shop_id)
    row["updated_at"] = now
    if set_active:
        doc["active_account_id"] = aid
    save_registry(doc)
    if set_active:
        apply_account_env(aid)
    return row


def register_account_from_session(session, *, set_active: bool = False, source_account_id: str = "") -> str:
    cookies = getattr(session, "cookies", None) or {}
    shop = str(getattr(session, "shop_id", "") or cookies.get("SHOP_ID") or "")
    sid = str(cookies.get("sessionid") or cookies.get("sid_tt") or "")
    src = str(source_account_id or active_account_id() or "").strip()
    if shop and src and src != derive_account_id(shop_id=shop):
        return promote_account_to_shop(src, shop)
    aid = derive_account_id(shop_id=shop, sessionid=sid)
    label = f"店铺 {shop}" if shop else aid
    register_account(aid, label=label, shop_id=shop, set_active=set_active)
    return aid


def create_account_slot(*, label: str = "新账号") -> str:
    aid = f"acct_{int(time.time()):x}"
    while _account_entry(load_registry(), aid):
        aid = f"acct_{int(time.time() * 1000):x}"
    register_account(aid, label=label, set_active=True)
    apply_account_env(aid)
    return aid


def account_logged_in(account_id: str) -> bool:
    aid = str(account_id or "").strip()
    if not aid:
        return False
    home = account_home(aid)
    sess = _read_json(home / "session.json") or {}
    cookies = dict(sess.get("cookies") or {})
    return bool(cookies.get("sessionid") or cookies.get("sid_tt"))


def find_empty_account_slot() -> str | None:
    for row in list_accounts():
        if not row.get("logged_in"):
            return str(row.get("id") or "")
    return None


def ensure_qr_login_slot(*, preferred_id: str | None = None) -> dict[str, Any]:
    """Prepare an empty account slot for QR login; switch away from logged-in active slot."""
    preferred = str(preferred_id or active_account_id() or "").strip()
    switched_from = ""
    aid = preferred
    if aid and account_logged_in(aid):
        empty = find_empty_account_slot()
        if empty and empty != aid:
            switch_account(empty)
            switched_from = aid
            aid = empty
        else:
            aid = create_account_slot(label="新账号")
            switched_from = preferred
    elif not aid:
        aid = create_account_slot(label="新账号")
    else:
        switch_account(aid)
    apply_account_env(aid)
    ensure_account_dirs(aid)
    return {
        "account_id": aid,
        "switched_from": switched_from,
        "empty_slot": not account_logged_in(aid),
    }


def switch_account(account_id: str) -> dict[str, Any]:
    aid = str(account_id or "").strip()
    if not aid:
        return {"ok": False, "error": "account_id required"}
    doc = load_registry()
    if not _account_entry(doc, aid):
        return {"ok": False, "error": f"unknown account: {aid}"}
    doc["active_account_id"] = aid
    save_registry(doc)
    apply_account_env(aid)
    ensure_account_dirs(aid)
    return {"ok": True, "account_id": aid, "home": str(account_home(aid))}


def _copy_tree_files(src_dir: Path, dest_dir: Path, names: tuple[str, ...]) -> list[str]:
    copied: list[str] = []
    if not src_dir.is_dir():
        return copied
    for name in names:
        src = src_dir / name
        if not src.is_file():
            continue
        dest = dest_dir / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        copied.append(name)
    return copied


def _migrate_legacy_session() -> dict[str, Any]:
    report: dict[str, Any] = {"migrated": False}
    legacy_session = LEGACY_SESSION_DIR / "session.json"
    if not legacy_session.is_file():
        return report

    sess = _read_json(legacy_session) or {}
    cookies = dict(sess.get("cookies") or {})
    shop = str(sess.get("shop_id") or cookies.get("SHOP_ID") or "")
    sid = str(cookies.get("sessionid") or cookies.get("sid_tt") or "")
    aid = derive_account_id(shop_id=shop, sessionid=sid)
    home = ensure_account_dirs(aid)

    copied = _copy_tree_files(
        LEGACY_SESSION_DIR,
        home,
        ("session.json", "ws_inner_cache.json", "ws_inner_portable.json", "pigeon_session_pack.zip"),
    )
    if LEGACY_BUNDLE_DIR.is_dir():
        bundle_dest = home / "bundle"
        bundle_dest.mkdir(parents=True, exist_ok=True)
        for child in LEGACY_BUNDLE_DIR.iterdir():
            if child.is_file():
                shutil.copy2(child, bundle_dest / child.name)
                copied.append(f"bundle/{child.name}")

    register_account(aid, label=f"店铺 {shop}" if shop else aid, shop_id=shop, set_active=True)
    report.update({"migrated": True, "account_id": aid, "copied": copied, "home": str(home)})
    logger.info("migrated legacy session → accounts/%s (%s files)", aid, len(copied))
    return report


def init_account_context(*, migrate: bool = True) -> dict[str, Any]:
    """Idempotent startup: ensure registry, migrate legacy layout, apply active account env."""
    global _initialized
    ACCOUNTS_ROOT.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"initialized": True}
    doc = load_registry()
    if migrate and not doc.get("accounts") and LEGACY_SESSION_DIR.joinpath("session.json").is_file():
        report["migration"] = _migrate_legacy_session()
        doc = load_registry()

    active = str(doc.get("active_account_id") or "").strip()
    if not active and doc.get("accounts"):
        active = str(doc["accounts"][0].get("id") or "")
        doc["active_account_id"] = active
        save_registry(doc)

    if active:
        apply_account_env(active)
        ensure_account_dirs(active)
        report["active_account_id"] = active
    else:
        apply_account_env("")
        report["active_account_id"] = ""

    _initialized = True
    report["accounts"] = len(doc.get("accounts") or [])
    return report


def legacy_pack_rel_files() -> tuple[str, ...]:
    return _LEGACY_PACK_FILES


def resolve_import_target(rel: str) -> Path:
    """Map pack entry (new or legacy layout) to active account home path."""
    rel = rel.replace("\\", "/").lstrip("/")
    parts = [p for p in rel.split("/") if p]
    if not parts or any(p == ".." for p in parts):
        raise ValueError(f"invalid pack path: {rel!r}")
    mapped = _LEGACY_IMPORT_MAP.get(rel, rel)
    mapped_parts = [p for p in mapped.replace("\\", "/").split("/") if p]
    if any(p == ".." for p in mapped_parts):
        raise ValueError(f"invalid mapped pack path: {mapped!r}")
    allowed = set(_PACK_REL_FILES) | set(_LEGACY_PACK_FILES) | set(_LEGACY_IMPORT_MAP.values())
    if mapped not in allowed:
        raise ValueError(f"unexpected pack entry: {mapped!r}")
    target = pack_file_path(mapped)
    home = account_home().resolve()
    try:
        target.resolve().relative_to(home)
    except ValueError as exc:
        raise ValueError(f"pack path escapes account home: {mapped!r}") from exc
    return target


def account_status() -> dict[str, Any]:
    init_account_context(migrate=False)
    aid = active_account_id()
    return {
        "active_account_id": aid,
        "accounts": list_accounts(),
        "session_dir": str(session_dir()),
        "bundle_dir": str(bundle_dir()),
    }
